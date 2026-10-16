"""真实 TCP 网关与实际 manager：请求内同出口失败、证据与协议兼容。"""

import json
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
async def solver(gateway_client, monkeypatch):
    from app import settings
    from app.captcha import CaptchaManager
    from app.routes import gateway

    manager = CaptchaManager()
    monkeypatch.setattr(manager, "fetch_config", AsyncMock(return_value={"region": "fixture"}))
    monkeypatch.setattr(manager, "_refill_single_proxy", AsyncMock())
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    monkeypatch.setattr(gateway, "captcha_manager", manager)
    yield manager
    await manager.close()


@pytest.mark.parametrize("endpoint", ["messages", "chat/completions", "responses"])
@pytest.mark.parametrize("stream", [True, False])
async def test_safe_failure_visible_only_to_admin(solver, gateway_client, fresh_app, monkeypatch, endpoint, stream, capsys):
    from app import reqlog
    from app.captcha import _SolverError
    from app.routes import gateway
    from tests.conftest import seed_account

    reqlog.clear()
    gateway._inflight.clear()
    seed_account(fresh_app, "private.fixture.sig")
    exhausted = seed_account(fresh_app, "exhausted.fixture.sig")
    exhausted.status = "exhausted"
    monkeypatch.setattr(solver, "_run_solver", AsyncMock(side_effect=_SolverError(
        "param_too_short", "extract", exit_code=6, param_chars=76)))
    body = {"model": "GLM-5.3-Flash", "stream": stream}
    body["input" if endpoint == "responses" else "messages"] = (
        "fixture" if endpoint == "responses" else [{"role": "user", "content": "fixture"}])
    client, _ = gateway_client
    response = await client.post("/v1/" + endpoint, json=body)
    assert response.status_code == 503
    error = response.json()["error"]
    assert {c["code"] for c in error["causes"]} == {"captcha_error", "quota_exhausted"}
    assert error["request_id"] == response.headers["x-request-id"]
    entry = reqlog.snapshot()[0]
    evidence = next(v for v in entry["evidence"] if v["code"] == "captcha_error")
    diag = evidence["captcha"]
    assert diag["last_solver_code"] == "param_too_short"
    assert diag["attempts"] == 1 and diag["param_chars"] == 76
    assert diag["stop_reason"] == "attempts_exhausted"
    assert error["request_id"] in capsys.readouterr().out
    for private in ("solve_id", "param_chars", "param_too_short", "private.fixture.sig"):
        assert private not in response.text
    assert not gateway._inflight
    reqlog.snapshot()[0]["evidence"][0]["captcha"]["reason"] = "modified"
    assert reqlog.snapshot()[0]["evidence"][0]["captcha"]["reason"] != "modified"


async def test_same_egress_memo_does_not_exhaust_healthier_candidate(solver, gateway_client, fresh_app, monkeypatch):
    from app import reqlog
    from app.captcha import _SolverError
    from app.routes import gateway
    from tests.conftest import seed_account

    reqlog.clear()
    gateway._session_affinity.clear()
    pool = gateway.account_client_pool
    accounts = [seed_account(fresh_app, f"candidate{i}.fixture.sig", name=f"fixture-{i}") for i in range(7)]
    # TCP 仍直达本地 Mock；只在 Node 边界使用合成出口标签。
    for i, account in enumerate(accounts):
        client = await pool.get_client(account, purpose="chat")
        pool._client_proxies[client] = "a" if i < 6 else "b"
    calls = []

    async def run(*args, proxy=None):
        calls.append(proxy)
        if proxy == "a":
            raise _SolverError("param_too_short", "extract", exit_code=6)
        return "fixture-token"

    monkeypatch.setattr(solver, "_run_solver", run)
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"model": "GLM-5.3-Flash",
        "stream": False, "messages": [{"role": "user", "content": "fixture"}]})
    assert response.status_code == 200
    assert calls == ["a", "b"]
    assert reqlog.snapshot()[0]["ok"] is True
    assert "error_code" not in reqlog.snapshot()[0]
    assert not gateway._inflight
    assert "captcha" not in json.dumps(response.json())
