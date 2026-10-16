"""实际网络准入拥有半开资格，纯查询和本地异常不占用它。"""


import httpx
import pytest

from app.client_pool import AccountClientPool, RegionOfflineError


@pytest.fixture
def pool(tmp_path):
    pool = AccountClientPool(registry_path=tmp_path / "registry.json")
    pool._circuit_breakers[pool._breaker_key("fixture-egress")] = {"state": "OPEN", "fails": 3,
        "quarantine_until": 0, "half_open_active": False}
    return pool


def test_only_one_probe_and_neutral_release(pool):
    clients = [object(), object()]
    for client in clients:
        pool._client_proxies[client] = "fixture-egress"
    probe = pool.begin_request(clients[0])
    assert probe is True
    with pytest.raises(RegionOfflineError):
        pool.begin_request(clients[1])
    pool.end_request(clients[0], probe)
    assert pool._circuit_breakers[pool._breaker_key("fixture-egress")]["fails"] == 3
    assert pool.begin_request(clients[1]) is True
    pool.end_request(clients[1], True, success=True)
    assert pool._circuit_breakers[pool._breaker_key("fixture-egress")]["state"] == "CLOSED"


async def test_cached_client_keeps_actual_egress_and_needs_admission(pool, monkeypatch):
    monkeypatch.setattr(pool, "resolve_proxy", lambda _: "http://fixture.invalid")
    client = await pool.get_client("fixture")
    monkeypatch.setattr(pool, "resolve_proxy", lambda _: "http://changed.invalid")
    assert await pool.get_client("fixture") is client
    assert pool.client_proxy(client) == "http://fixture.invalid"
    pool._circuit_breakers[pool._breaker_key(pool.client_proxy(client))] = {"state": "OPEN", "fails": 3,
        "quarantine_until": 0, "half_open_active": False}
    assert not pool.is_proxy_quarantined(pool.client_proxy(client))
    probe = pool.begin_request(client)
    assert probe is True
    pool.end_request(client, probe, success=False)
    assert pool.is_proxy_quarantined(pool.client_proxy(client))
    await pool.aclose()
    assert not pool._client_proxies


@pytest.mark.parametrize("fail", [True, False])
async def test_quota_three_requests_settle_one_probe(pool, fresh_app, monkeypatch, fail):
    from app import quota
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "quota.fixture.sig")
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if fail and request.url.path.endswith("/usage"):
            raise httpx.ConnectError("fixture", request=request)
        return httpx.Response(200, json={"code": 0, "data": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pool._client_proxies[client] = "fixture-egress"
        monkeypatch.setattr(pool, "_get_client_locked", lambda *a, **kw: client)
        monkeypatch.setattr(quota, "account_client_pool", pool)
        settled = []
        end = pool.end_request

        def record(*args, **kwargs):
            settled.append(kwargs.get("success"))
            end(*args, **kwargs)

        monkeypatch.setattr(pool, "end_request", record)
        await quota.fetch_quota(account)
        assert len(requests) == 3
        assert settled == [not fail]
        assert pool._circuit_breakers[pool._breaker_key("fixture-egress")]["state"] == ("OPEN" if fail else "CLOSED")


@pytest.mark.parametrize("stage", ["factory", "enter", "exit", "cancel_exit"])
async def test_gateway_local_stream_error_releases_probe(pool, fresh_app, monkeypatch, stage):
    import asyncio

    from app.routes import gateway
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "stream.fixture.sig")
    error = asyncio.CancelledError if stage == "cancel_exit" else RuntimeError

    class Context:
        async def __aenter__(self):
            if stage == "enter":
                raise error("fixture")
            return httpx.Response(200, json={"content": []})

        async def __aexit__(self, *args):
            if stage in ("exit", "cancel_exit"):
                raise error("fixture")

    class Client:
        def stream(self, *args, **kwargs):
            if stage == "factory":
                raise error("fixture")
            return Context()

    client = Client()
    pool._client_proxies[client] = "fixture-egress"
    monkeypatch.setattr(gateway, "account_client_pool", pool)
    monkeypatch.setattr(gateway, "_get_shared_client", lambda: client)
    with pytest.raises(error):
        await gateway._try_account("fixture-probe", account,
            {"model": "GLM-5.3-Flash", "messages": [{"role": "user", "content": "fixture"}]},
            {}, None, False)
    state = pool._circuit_breakers[pool._breaker_key("fixture-egress")]
    assert state["half_open_active"] is False
    assert state["fails"] == 3
    assert pool.begin_request(client) is True
    pool.end_request(client, True)
