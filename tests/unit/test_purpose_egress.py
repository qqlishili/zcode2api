"""用途分流回归：只创建隔离客户端，不访问生产上游。"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app import settings
from app.channels.plugin_channel import PluginChannel
from app.client_pool import RegionOfflineError
from app.egress_policy import ClaimBinding, EgressPolicy
from app.models import Account
from tests.unit.test_egress_operations import write_registry


async def test_global_direct_must_not_swallow_admitted_proxy(split_pool):
    account = Account.create("zai", "claim", "a.b.c")
    account.assigned_region = "HK"
    async with split_pool.operation(account, purpose="claim") as operation:
        assert operation.proxy == "http://127.0.0.1:21100"
        assert operation.identity == (21100, "a" * 64, 1)
    async with split_pool.operation(account, purpose="chat") as operation:
        assert operation.proxy is None


async def test_async_nested_purposes_have_distinct_clients(isolated_client_pool):
    account = Account.create("zai", "parallel", "a.b.c")
    async with isolated_client_pool.operation(account, purpose="chat") as chat:
        async def refresh():
            async with isolated_client_pool.operation(account, purpose="claim") as claim:
                assert claim is not chat
                assert claim.client is not chat.client
                assert claim.purpose == "claim"

        await asyncio.create_task(refresh())
        assert not chat.released
    assert not isolated_client_pool._operations


async def test_plugin_unconfirmed_proxy_must_not_become_direct(isolated_client_pool, monkeypatch):
    from app.egress_probe import egress_probe

    monkeypatch.setattr(settings, "EGRESS_MODE", "proxy")
    monkeypatch.setattr(egress_probe, "is_confirmed", lambda *_: False)
    host = AsyncMock()
    channel = PluginChannel(host)
    account = Account.create("zai", "unconfirmed", "a.b.c")
    account.proxy = "http://127.0.0.1:40000"
    async with isolated_client_pool.operation(account) as operation:
        with pytest.raises(RegionOfflineError):
            await channel.send(account, "https://unused.invalid", {}, json.dumps({"model": "test"}).encode(),
                               operation.client)
    host.fetch.assert_not_awaited()

@pytest.fixture
def split_pool(isolated_client_pool, monkeypatch):
    pool = isolated_client_pool
    monkeypatch.setattr(settings, "EGRESS_MODE", "direct")
    binding = ClaimBinding("HK", 21100, "a" * 64, 1)
    pool._policy = EgressPolicy(chat_mode="direct", claim_mode="proxy", claim_bindings=(binding,))
    write_registry(pool._registry_path, [(21100, binding.node_id, 1, "draining")])
    (pool._registry_path.parent / "node_lifecycle.json").write_text(json.dumps({"nodes": {
        binding.node_id: {"port": 21100, "epoch": 1, "region": "HK", "sources": ["isolated-source"]},
    }}), encoding="utf-8")
    return pool


async def test_claim_rejects_missing_or_changed_identity(split_pool):
    account = Account.create("zai", "missing", "a.b.c")
    account.assigned_region = "TW"
    with pytest.raises(RegionOfflineError, match="未配置"):
        await split_pool.acquire_operation(account, purpose="claim")
    account.assigned_region = "HK"
    write_registry(split_pool._registry_path, [(21100, "b" * 64, 2, "draining")])
    with pytest.raises(RegionOfflineError, match="不可用"):
        await split_pool.acquire_operation(account, purpose="claim")
    async with split_pool.operation(account, purpose="chat") as operation:
        assert operation.proxy is None


@pytest.mark.parametrize("change", ["sources", "retiring", "reclaimed", "missing"])
async def test_claim_rejects_removed_source_and_retirement(split_pool, change):
    path = split_pool._registry_path.parent / "node_lifecycle.json"
    if change == "missing":
        path.unlink()
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["nodes"]["a" * 64][change] = [] if change == "sources" else True
        path.write_text(json.dumps(data), encoding="utf-8")
    account = Account.create("zai", "closed", "a.b.c")
    account.assigned_region = "HK"
    with pytest.raises(RegionOfflineError):
        await split_pool.acquire_operation(account, purpose="claim")


async def test_breakers_separate_purposes_and_cancel_half_open(split_pool):
    proxy = "http://127.0.0.1:21100"
    split_pool.record_proxy_result(proxy, False, True, purpose="chat")
    assert split_pool.is_proxy_quarantined(proxy, purpose="chat")
    assert not split_pool.is_proxy_quarantined(proxy, purpose="claim")
    account = Account.create("zai", "recovery", "a.b.c")
    account.assigned_region = "HK"
    async with split_pool.operation(account, purpose="claim") as operation:
        split_pool.record_proxy_result(proxy, False, True, purpose="claim")
        state = split_pool._circuit_breakers[split_pool._breaker_key(proxy, "claim")]
        state["quarantine_until"] = 0
        probe = split_pool.begin_request(operation.client)
        assert probe is True
        with pytest.raises(RegionOfflineError):
            split_pool.begin_request(operation.client)
        split_pool.end_request(operation.client, probe)
        assert split_pool.begin_request(operation.client) is True
        split_pool.end_request(operation.client, True, success=True)
    assert not split_pool.is_proxy_quarantined(proxy, purpose="claim")
    assert split_pool.is_proxy_quarantined(proxy, purpose="chat")


async def test_close_account_drains_all_purpose_clients(split_pool):
    account = Account.create("zai", "delete", "a.b.c")
    account.assigned_region = "HK"
    chat = await split_pool.acquire_operation(account, purpose="chat")
    claim = await split_pool.acquire_operation(account, purpose="claim")
    producer = await split_pool.acquire_operation(parent=claim, kind="producer")
    await split_pool.close_account(account.id)
    assert not chat.client.is_closed and not claim.client.is_closed
    with pytest.raises(RegionOfflineError, match="关闭"):
        await split_pool.acquire_operation(account, purpose="chat")
    await split_pool.release_operation(chat)
    await split_pool.release_operation(chat)
    await split_pool.release_operation(claim)
    assert chat.client.is_closed and not claim.client.is_closed
    await split_pool.release_operation(producer)
    assert claim.client.is_closed
    assert not split_pool._operations

async def test_pending_binding_removal_keeps_fixed_reference(split_pool, monkeypatch):
    from types import SimpleNamespace

    from app.egress import write_private_json
    from app.store import Store

    monkeypatch.setattr(settings, "DB_PATH", split_pool._registry_path.parent / "accounts.db")
    monkeypatch.setattr(settings, "DATA_DIR", split_pool._registry_path.parent)
    monkeypatch.setattr(settings, "EGRESS_POLICY", split_pool.policy)
    store = Store()
    split_pool._policy = store.active_egress_policy
    store.save_egress_policy(EgressPolicy(chat_mode="direct", claim_mode="direct").as_dict())
    assert store.egress_settings()["pending_restart"]
    account = Account.create("zai", "held", "a.b.c")
    account.assigned_region = "HK"
    operation = await split_pool.acquire_operation(account, purpose="claim")
    producer = await split_pool.acquire_operation(parent=operation, kind="producer")
    consumer = {"protocol": 1, "pid": 1, "start": "isolated", "boot_id": "isolated"}
    split_pool._consumer = consumer
    registry = split_pool._registry
    request = {"request_id": "isolated", "generation": registry["generation"],
               "slots": {"21100": registry["slots"]["21100"]}, "consumers": {"isolated": consumer}}
    write_private_json(split_pool._registry_path.parent / "egress_retirement.json", request)
    with pytest.raises(RegionOfflineError):
        await split_pool.acquire_operation(account, purpose="claim")
    captcha = SimpleNamespace(retire_egress=lambda _: None, egress_token_count=lambda _: 0)
    await split_pool.coordinate_retirement(captcha, [])
    ack_path = split_pool._registry_path.parent / "egress_consumers/isolated.ack.json"
    counts = json.loads(ack_path.read_text())["slots"]["21100"]
    assert counts["fixed"] == counts["operations"] == counts["producers"] == 1
    assert counts["caches"] == 1 and counts["admission_closed"]
    await split_pool.release_operation(operation)
    await split_pool.release_operation(producer)
    await split_pool.coordinate_retirement(captcha, [])
    counts = json.loads(ack_path.read_text())["slots"]["21100"]
    assert counts["fixed"] == 1
    assert counts["operations"] == counts["producers"] == counts["caches"] == 0
    await split_pool.stop_coordinator()

async def test_captcha_actual_direct_and_claim_have_no_refill_leak(split_pool, monkeypatch):
    from app.captcha import CaptchaManager, _current_solve, _Token

    manager = CaptchaManager()
    monkeypatch.setattr(manager, "fetch_config", AsyncMock(return_value={"region": "fixture"}))
    refill = []
    monkeypatch.setattr(manager, "_schedule_refill", lambda proxy: refill.append(proxy))
    seen = []

    async def solve(*args, proxy=None):
        operation = _current_solve.get().operation
        seen.append((operation.purpose, operation.proxy, operation.identity))
        return "isolated-token"

    monkeypatch.setattr(manager, "_run_solver", solve)
    account = Account.create("zai", "tokens", "a.b.c")
    account.assigned_region = "HK"
    account.proxy = "http://127.0.0.1:21100"
    try:
        async with split_pool.operation(account, purpose="claim") as operation:
            assert await manager.get_verify_param(account=account, operation=operation) == ("isolated-token", "fixture")
            manager._put(_Token("claim-hit", "fixture", operation.proxy, identity=operation.identity))
            assert (await manager.get_verify_param(operation=operation))[0] == "claim-hit"
            assert not refill
        async with split_pool.operation(account, purpose="chat") as operation:
            assert (await manager.get_verify_param(account=account, operation=operation))[0] == "isolated-token"
        assert seen == [("claim", "http://127.0.0.1:21100", (21100, "a" * 64, 1)), ("chat", None, None)]
        assert refill == [None]
        manager._put(_Token("direct", "fixture"))
        manager._put(_Token("claim-retained", "fixture", "http://127.0.0.1:21100", identity=(21100, "a" * 64, 1)))
        manager.invalidate(proxy=None)
        assert not manager._proxy_pools["__default__"]
        assert manager._proxy_pools["http://127.0.0.1:21100"]
    finally:
        await manager.close()


async def test_plugin_explicit_local_proxy_is_forwarded_even_with_global_direct(isolated_client_pool, monkeypatch):
    monkeypatch.setattr(settings, "EGRESS_MODE", "direct")
    isolated_client_pool._policy = EgressPolicy(chat_mode="proxy", chat_proxy_url="http://127.0.0.1:40000")
    host = AsyncMock()
    account = Account.create("zai", "plugin-explicit", "a.b.c")
    async with isolated_client_pool.operation(account, purpose="chat") as operation:
        await PluginChannel(host).send(account, "https://unused.invalid", {},
                                       json.dumps({"model": "test"}).encode(), operation.client)
    assert host.fetch.await_args.kwargs["proxy"] == "http://127.0.0.1:40000"

async def test_chat_task_refresh_and_auto_claim_keep_claim_route(split_pool, fresh_app, monkeypatch):
    import httpx

    from app import claim, quota
    from app.captcha import CaptchaManager
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "h.eyJzdWIiOiJhIn0.sig", name="workflow")
    account.assigned_region = "HK"
    events = []
    admitted = split_pool._get_client_locked
    outer_claim = []

    def get_client(*args, **kwargs):
        client = admitted(*args, **kwargs)
        def handle(request):
            purpose = split_pool._client_purposes[client]
            events.append((request.url.path, purpose, split_pool.client_proxy(client), client))
            if request.url.path.endswith("/billing/preview"):
                return httpx.Response(200, json={"code": 0, "data": {"plans": [{
                    "plan_id": "fixture-plan", "name": "isolated",
                    "entitlements": [{"meter": "model_usage", "unit": "token", "grant_units": 100,
                                      "show_name": "GLM-5.3"}],
                }]}})
            return httpx.Response(200, json={"code": 0, "data": {}})
        # 只替换传输边界；保留真实策略、客户端缓存、操作引用和调用链。
        client._mounts.clear()
        client._transport = httpx.MockTransport(handle)
        return client

    monkeypatch.setattr(split_pool, "_get_client_locked", get_client)
    manager = CaptchaManager()
    monkeypatch.setattr(manager, "fetch_config", AsyncMock(return_value={"region": "fixture"}))
    monkeypatch.setattr(manager, "_run_solver", AsyncMock(return_value="isolated-token"))
    monkeypatch.setattr(claim, "captcha_manager", manager)
    try:
        async with split_pool.operation(account, purpose="chat") as chat:
            await asyncio.create_task(quota.fetch_quota(account))
            assert events and all(purpose == "claim" and proxy == "http://127.0.0.1:21100" and client is not chat.client
                                  for _, purpose, proxy, client in events)
            events.clear()
            async with split_pool.operation(account, purpose="claim") as operation:
                outer_claim.append(operation.client)
                outcomes = await claim.auto_claim_all_plans(account)
            assert outcomes and outcomes[0]["ok"]
            assert any(path.endswith("/billing/preview") for path, *_ in events)
            assert any(path.endswith("/billing/claim") for path, *_ in events)
            assert all(purpose == "claim" and proxy == "http://127.0.0.1:21100" and client is outer_claim[0]
                       for _, purpose, proxy, client in events)
        assert not split_pool._operations
    finally:
        await manager.close()

@pytest.mark.parametrize("failed_purpose", ["chat", "claim"])
async def test_waf_response_only_trips_its_own_purpose(split_pool, failed_purpose):
    binding = split_pool.policy.claim_bindings[0]
    write_registry(split_pool._registry_path, [(21100, binding.node_id, 1, "active")])
    split_pool._policy = EgressPolicy(chat_mode="proxy", chat_proxy_url=binding.proxy,
                                      claim_mode="proxy", claim_bindings=(binding,))
    account = Account.create("zai", "same-physical-route", "a.b.c")
    account.assigned_region = "HK"
    operations = {purpose: await split_pool.acquire_operation(account, purpose=purpose)
                  for purpose in ("chat", "claim")}
    other = "claim" if failed_purpose == "chat" else "chat"
    try:
        failed = operations[failed_purpose]
        probe = split_pool.begin_request(failed.client)
        split_pool.end_request(failed.client, probe, success=False, is_waf_block=True)
        assert split_pool.is_proxy_quarantined(binding.proxy, purpose=failed_purpose)
        assert not split_pool.is_proxy_quarantined(binding.proxy, purpose=other)
        assert split_pool.begin_request(operations[other].client) is False
    finally:
        for operation in operations.values():
            await split_pool.release_operation(operation)

async def test_nested_billing_keeps_request_timeout(split_pool):
    import httpx

    from app.claim import _billing_request

    account = Account.create("zai", "bounded-preview", "a.b.c")
    account.assigned_region = "HK"
    async with split_pool.operation(account, purpose="claim") as operation:
        def handle(request):
            assert request.extensions["timeout"]["read"] == 25.0
            return httpx.Response(200, json={"code": 0, "data": {}})
        operation.client._mounts.clear()
        operation.client._transport = httpx.MockTransport(handle)
        assert await _billing_request(account, "GET", "/billing/preview", headers={}) == {
            "code": 0, "data": {},
        }
