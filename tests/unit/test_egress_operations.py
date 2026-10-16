"""出口版本、实际操作与 producer 引用的行为回归。"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app import client_pool, egress
from app.captcha import CaptchaManager
from app.client_pool import AccountClientPool, RegionOfflineError


def write_registry(path, nodes):
    slots = {str(p): {"node_id": None, "epoch": 0, "region": r, "admission": "empty"}
             for r, (low, high) in egress.REGION_PORT_TIERS.items() for p in range(low, high + 1)}
    regions = {r: [] for r in egress.REGION_PORT_TIERS}
    for port, node, epoch, admission in nodes:
        region = slots[str(port)]["region"]
        slots[str(port)] = {"node_id": node, "epoch": epoch, "region": region, "admission": admission}
        if admission in {"active", "fallback"}:
            regions[region].append(f"http://127.0.0.1:{port}")
    value = {"regions": regions, "slots": slots, "updated_at": 1}
    value["generation"] = egress.registry_generation(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


@pytest.fixture
async def pool(tmp_path):
    path = tmp_path / "registry.json"
    write_registry(path, [(21100, "old", 1, "active")])
    pool = AccountClientPool(registry_path=path, idle_timeout=0)
    yield pool
    await pool.aclose()


def account(name="account"):
    return SimpleNamespace(id=name, assigned_region="HK", proxy=None)


async def test_direct_mode_does_not_filter_accounts_by_proxy_region(pool, monkeypatch):
    monkeypatch.setattr(client_pool.settings, "EGRESS_MODE", "direct")
    write_registry(pool._registry_path, [(21100, "old", 1, "draining")])

    assert pool.get_healthy_regions() is None
    assert pool.is_region_healthy("HK")


@pytest.mark.parametrize("explicit_proxy", [None, "http://127.0.0.1:21100"])
async def test_direct_mode_acquires_and_reuses_direct_client(pool, monkeypatch, explicit_proxy):
    monkeypatch.setattr(client_pool.settings, "EGRESS_MODE", "direct")
    write_registry(pool._registry_path, [(21100, "old", 1, "draining")])
    acc = account()
    acc.proxy = explicit_proxy

    assert pool.resolve_proxy(acc) is None
    async with pool.operation(acc) as operation:
        assert operation.proxy is None
        assert operation.identity is None
        assert pool.client_proxy(operation.client) is None
        assert await pool.get_client(acc) is operation.client
    async with pool.operation(acc, proxy=explicit_proxy) as operation:
        assert operation.proxy is None
        assert operation.account_id == acc.id


async def test_old_stream_protected_new_operation_uses_replacement(pool):
    old = await pool.acquire_operation(account())
    await pool.prune_idle()
    assert not old.client.is_closed
    write_registry(pool._registry_path, [(21100, "old", 1, "draining"), (21101, "new", 1, "active")])
    new = await pool.acquire_operation(account())
    assert new.proxy.endswith(":21101") and old.proxy.endswith(":21100")
    assert old.client is not new.client
    await pool.close_account("account")
    assert not new.client.is_closed
    await pool.release_operation(new)
    assert new.client.is_closed
    assert not old.client.is_closed
    await pool.release_operation(old)
    await pool.release_operation(old)
    assert old.client.is_closed and not pool._operations


async def test_unchanged_identity_reuses_client_when_other_node_added(pool):
    old = await pool.acquire_operation(account())
    write_registry(pool._registry_path, [(21100, "old", 1, "active"), (21101, "new", 1, "active")])
    new = await pool.acquire_operation(account())
    assert old.client is new.client and old.identity == new.identity
    await pool.release_operation(old)
    await pool.release_operation(new)


async def test_cancelled_upstream_close_keeps_reference_until_actual_close(pool):
    from app.routes.gateway import _Upstream

    entered, finish = asyncio.Event(), asyncio.Event()
    operation = await pool.acquire_operation(account())

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'body'

        async def aclose(self):
            entered.set()
            await finish.wait()

    response = httpx.Response(200, stream=Body())

    class Context:
        async def __aexit__(self, *args):
            await response.aclose()

    upstream = _Upstream(response, Context(), operation.client)
    upstream.operation = operation
    task = asyncio.create_task(upstream.close())
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert operation in pool._operations
    finish.set()
    await upstream.close()
    await upstream.close()
    assert response.is_closed and operation.released and not pool._operations


async def test_context_exit_exception_still_closes_body_and_releases(pool):
    from app.routes.gateway import _Upstream

    operation = await pool.acquire_operation(account())
    response = httpx.Response(200, stream=httpx.ByteStream(b'body'))

    class Context:
        async def __aexit__(self, *args):
            raise RuntimeError('isolated context exit failure')

    upstream = _Upstream(response, Context(), operation.client)
    upstream.operation = operation
    with pytest.raises(RuntimeError, match='context exit failure'):
        await upstream.close()
    assert response.is_closed and operation.released and not pool._operations


async def test_same_mtime_content_change_and_delayed_identity_fence(pool):
    import os
    before = pool._registry_path.stat()
    old = await pool.acquire_operation(account())
    write_registry(pool._registry_path, [(21100, "new", 2, "active")])
    os.utime(pool._registry_path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(RegionOfflineError):
        await pool.acquire_operation(proxy=old.proxy, identity=old.identity)
    new = await pool.acquire_operation(account())
    assert new.identity == (21100, "new", 2) and new.client is not old.client
    pool.end_request(old.client, False, success=True)
    assert not pool._circuit_breakers
    await pool.release_operation(old)
    await pool.release_operation(new)


async def test_retired_parent_can_derive_producer_but_new_operation_cannot(pool):
    parent = await pool.acquire_operation(account())
    write_registry(pool._registry_path, [(21100, "old", 1, "draining"), (21101, "new", 1, "active")])
    with pytest.raises(RegionOfflineError):
        await pool.acquire_operation(proxy=parent.proxy)
    producer = await pool.acquire_operation(parent=parent, proxy=parent.proxy, kind="producer")
    assert producer.identity == parent.identity
    await pool.close_account("account")
    await pool.release_operation(parent)
    assert not producer.client.is_closed
    with pytest.raises(RegionOfflineError):
        await pool.acquire_operation(parent=parent, kind="producer")
    await pool.release_operation(producer)
    assert producer.client.is_closed


async def test_singleflight_cancellation_keeps_producer_reference(pool, monkeypatch):
    monkeypatch.setattr(client_pool, "account_client_pool", pool)
    manager = CaptchaManager()
    entered, finish = asyncio.Event(), asyncio.Event()
    async def config(**kwargs):
        return {}
    async def run(*args, **kwargs):
        entered.set()
        await finish.wait()
        return "isolated-token"
    monkeypatch.setattr(manager, "fetch_config", config)
    monkeypatch.setattr(manager, "_run_solver", run)
    monkeypatch.setattr(manager, "_schedule_refill", lambda _: None)
    parent = await pool.acquire_operation(account())
    write_registry(pool._registry_path, [(21100, "old", 1, "draining"), (21101, "new", 1, "active")])
    waiters = [asyncio.create_task(manager.get_verify_param(operation=parent)) for _ in range(2)]
    await asyncio.wait_for(entered.wait(), 1)
    waiters[0].cancel()
    await asyncio.gather(waiters[0], return_exceptions=True)
    assert sum(op.kind == "producer" for op in pool._operations) == 1
    await pool.release_operation(parent)
    await pool.prune_idle()
    assert not parent.client.is_closed
    finish.set()
    assert await waiters[1] == ("isolated-token", "cn")
    await manager.close()
    assert not pool._operations and parent.client.is_closed


async def test_corrupt_or_missing_registry_closes_admission(pool):
    op = await pool.acquire_operation(account())
    pool._registry_path.write_text('{"regions":', encoding="utf-8")
    with pytest.raises(RegionOfflineError):
        await pool.acquire_operation(account())
    await pool.release_operation(op)


async def test_nested_operation_reuses_identity_and_exception_releases(pool):
    with pytest.raises(ValueError):
        async with pool.operation(account()) as outer:
            async with pool.operation(account()) as inner:
                assert inner is outer and len(pool._operations) == 1
                raise ValueError("isolated")
    assert not pool._operations
