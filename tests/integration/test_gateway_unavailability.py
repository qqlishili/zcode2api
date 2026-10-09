"""最终503的原因、重试与监控契约。仅使用隔离账号和Mock上游。"""

import pytest


@pytest.fixture(autouse=True)
def clean_runtime():
    from app import reqlog
    from app.routes import gateway

    reqlog.clear()
    gateway._inflight.clear()
    gateway._session_affinity.clear()
    yield
    gateway._inflight.clear()
    gateway._session_affinity.clear()


@pytest.mark.integration
@pytest.mark.parametrize("endpoint", ["messages", "chat/completions", "responses"])
@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("state,code", [(None, "pool_empty"), ("disabled", "account_disabled"),
                                      ("invalid", "credential_invalid"), ("exhausted", "quota_exhausted")])
async def test_final_state(gateway_client, fresh_app, endpoint, stream, state, code):
    from app import reqlog
    from tests.conftest import seed_account

    reqlog.clear()
    if state:
        account = seed_account(fresh_app, "diag.fixture.sig", name="private-fixture")
        account.status = state
    client, _ = gateway_client
    body = {"model": "GLM-5.3-Flash", "stream": stream}
    body["input" if endpoint == "responses" else "messages"] = (
        "fixture" if endpoint == "responses" else [{"role": "user", "content": "fixture"}])
    response = await client.post("/v1/" + endpoint, json=body)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["type"] == "no_available_account"
    assert error["code"] == code
    assert error["causes"] == [{"code": code, "origin": "account_state" if state else "local_scheduler"}]
    assert error["retryable"] is False and error["retry_after"] is None
    assert error["request_id"] == response.headers["x-request-id"]
    entry = reqlog.snapshot()[0]
    assert entry["req_id"] == error["request_id"]
    assert entry["error_code"] == code and entry["error_causes"] == error["causes"]
    if state is None:
        import json
        print("FINAL_503_SAMPLE " + json.dumps({"endpoint": endpoint, "stream": stream,
              "status": response.status_code, "error": error, "x_request_id": response.headers["x-request-id"],
              "monitoring": {k: entry[k] for k in ("req_id", "error_code", "error_causes", "retryable", "retry_after", "stop_reason")}},
              ensure_ascii=False))


@pytest.mark.integration
@pytest.mark.parametrize("raw,business,expected,retryable", [
    (402, None, "quota_exhausted", False), (200, "1304", "quota_exhausted", False),
    (401, None, "credential_invalid", False), (403, None, "credential_invalid", False),
    (200, "3012", "account_risk_blocked", False), (400, "3007", "captcha_retry_exhausted", None),
    (503, None, "upstream_server_error", True), (200, "1120", "upstream_server_error", True),
    (429, "3008", "upstream_concurrency_limit", True), (200, "3009", "upstream_concurrency_limit", True),
    (429, "3010", "upstream_concurrency_limit", True), (429, "1302", "upstream_rate_limited", True),
    (429, None, "upstream_429_unknown", None)])
async def test_upstream_failure(gateway_client, fresh_app, mock_server, monkeypatch, raw, business, expected, retryable):
    from app import reqlog, settings
    from app.routes import gateway
    from tests.conftest import seed_account
    from fastapi import Response
    from fastapi.routing import APIRoute
    import json

    async def no_wait(_):
        pass

    monkeypatch.setattr(gateway, "_sleep", no_wait)
    monkeypatch.setattr(settings, "RETRY_429_TIMES", 1)
    monkeypatch.setattr(settings, "RETRY_5XX_TIMES", 1)
    # 真实TCP上游，HTTP200包装沿现有消息端点响应路径。
    calls = []
    async def upstream():
        calls.append(raw)
        return Response(json.dumps({"code": business, "error": {"message": "fixture", "type": "unlisted"}}),
                        status_code=raw, media_type="application/json")

    mock, port = mock_server
    monkeypatch.setattr(mock.router, "routes", mock.router.routes + [APIRoute("/diagnostic", upstream, methods=["POST"])])
    monkeypatch.setitem(settings.UPSTREAM, "zai", f"http://127.0.0.1:{port}/diagnostic")
    account = seed_account(fresh_app, "reason.fixture.sig", name="private-fixture")
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"model": "GLM-5.3-Flash", "stream": False,
                                                       "messages": [{"role": "user", "content": "fixture"}]})
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == expected
    assert error["causes"] == [{"code": expected, "origin": "upstream"}]
    assert error["retryable"] is retryable
    entry = reqlog.snapshot()[0]
    assert entry["error_causes"] == error["causes"]
    event = next(e for e in entry["evidence"] if e["stage"] == "upstream")
    assert event["raw_http_status"] == raw
    assert event["effective_status"] == (402 if business == "1304" else 405 if business == "3012"
                                         else 500 if business == "1120" else 429 if business == "3009" else raw)
    assert event["business_code"] == business
    assert not gateway._inflight
    assert "private-fixture" not in response.text and account.id not in response.text
    assert len(calls) == (3 if business == "3007" else 2 if expected in {
        "upstream_server_error", "upstream_rate_limited", "upstream_429_unknown"} else 1)
    if expected in {"upstream_concurrency_limit", "upstream_rate_limited", "upstream_429_unknown"}:
        assert account.status == "active"


@pytest.mark.integration
async def test_transport_failover_evidence(gateway_client, fresh_app):
    from app import reqlog
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "transportDiagUnique.fixture.sig")
    client, mock = gateway_client
    mock.state.sequences[account.jwt_token[:16]] = ["connect_fail_first"]
    mock.state.counters.pop(account.jwt_token[:16], None)
    response = await client.post("/v1/messages", json={"model": "GLM-5.3-Flash", "messages": []})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_transport_error"
    assert response.json()["error"]["causes"] == [{"code": "upstream_transport_error", "origin": "transport"}]
    assert reqlog.snapshot()[0]["evidence"][0]["raw_http_status"] is None


@pytest.mark.integration
async def test_credential_build_failure(gateway_client, fresh_app):
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "credential.fixture.sig")
    account.jwt_token = None
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"messages": []})
    assert response.json()["error"]["causes"] == [{"code": "credential_invalid", "origin": "local_scheduler"}]


@pytest.mark.integration
@pytest.mark.parametrize("state,quota,code,retry", [
    ("cooling", {}, "account_cooling", True),
    ("cooling", {"GLM-5.3-Flash": {"remaining": 0}}, "mixed_unavailability", False),
    ("active", {"GLM-5.3-Flash": {"remaining": 0}}, "model_quota_exhausted", False),
    ("missing_cooling", {}, "unknown_unavailability", None)])
async def test_cooling_and_model(gateway_client, fresh_app, state, quota, code, retry):
    import time
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "cooling.fixture.sig")
    account.status = "cooling" if state == "missing_cooling" else state
    account.cooling_until = None if state == "missing_cooling" else time.time() + 60
    account.quota = quota
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"messages": []})
    error = response.json()["error"]
    assert error["code"] == code and error["retryable"] is retry
    if retry:
        assert 0 < error["retry_after"] <= 60
        assert response.headers["retry-after"] == str(error["retry_after"])
    else:
        assert "retry-after" not in response.headers


@pytest.mark.integration
async def test_local_full_slot(gateway_client, fresh_app):
    from app.routes import gateway
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "local.fixture.sig")
    fresh_app.set_setting("account_concurrency", "1")
    gateway._inflight[account.id] = 1
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"messages": []})
    error = response.json()["error"]
    assert error["code"] == "local_concurrency_limit" and error["retryable"] is True
    assert error["retry_after"] is None and "retry-after" not in response.headers
    assert gateway._inflight[account.id] == 1


@pytest.mark.integration
async def test_reacquire_failure(gateway_client, fresh_app, monkeypatch):
    from app import reqlog, settings
    from app.routes import gateway
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "reacquire.fixture.sig")
    fresh_app.set_setting("account_concurrency", "1")
    monkeypatch.setattr(settings, "RETRY_429_TIMES", 1)

    async def occupy(_):
        gateway._inflight[account.id] = 1

    monkeypatch.setattr(gateway, "_sleep", occupy)
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"messages": []},
                                 headers={"x-mock-scenario": "rate_limited"})
    assert response.json()["error"]["code"] == "local_concurrency_limit"
    assert response.json()["error"]["retry_after"] is None
    events = reqlog.snapshot()[0]["evidence"]
    assert events[0]["code"] == "upstream_429_unknown"
    assert events[-1]["code"] == "local_concurrency_limit"
    assert account.status == "active"


@pytest.mark.integration
async def test_attempt_limit_and_success(gateway_client, fresh_app, monkeypatch):
    from app import reqlog
    from tests.conftest import seed_account

    client, mock = gateway_client
    for index in range(6):
        account = seed_account(fresh_app, f"limit{index}.fixture.sig")
        mock.state.sequences[account.jwt_token[:16]] = ["auth_invalid"]
    response = await client.post("/v1/messages", json={"messages": []})
    assert response.json()["error"]["code"] == "dispatch_attempt_limit"
    assert response.json()["error"]["retryable"] is None
    assert reqlog.snapshot()[0]["stop_reason"] == "attempt_limit"
    assert reqlog.snapshot()[0]["coverage_complete"] is False
    # 尚未尝试的第六账号仍可成功；前次失败诊断不污染成功记录。
    live = next(a for a in fresh_app.list_accounts("zai") if a.status == "active")
    mock.state.sequences[live.jwt_token[:16]] = ["ok"]
    response = await client.post("/v1/messages", json={"messages": []})
    assert response.status_code == 200
    entry = reqlog.snapshot()[0]
    assert entry["ok"] is True and "error_code" not in entry


@pytest.mark.integration
async def test_failure_then_success(gateway_client, fresh_app):
    from app import reqlog
    from tests.conftest import seed_account

    client, mock = gateway_client
    account = seed_account(fresh_app, "firstFailUnique.fixture.sig")
    seed_account(fresh_app, "secondOkUnique.fixture.sig")
    mock.state.sequences[account.jwt_token[:16]] = ["auth_invalid"]
    response = await client.post("/v1/messages", json={"messages": []})
    assert response.status_code == 200
    assert reqlog.snapshot()[0]["ok"] is True and "error_code" not in reqlog.snapshot()[0]


@pytest.mark.integration
async def test_pre_read_failure_and_captcha_exception(gateway_client, fresh_app, monkeypatch):
    import httpx
    from app import reqlog
    from app.routes import gateway
    from tests.conftest import seed_account

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.ReadError("fixture")
            yield b""  # 异步流接口；只注入读取失败。

    account = seed_account(fresh_app, "read.fixture.sig")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=BrokenBody(), headers={"content-type": "application/json"})
    )) as upstream:
        monkeypatch.setattr(gateway, "_get_shared_client", lambda: upstream)
        client, _ = gateway_client
        response = await client.post("/v1/messages", json={"messages": []})
    error = response.json()["error"]
    assert error["code"] == "upstream_transport_error"
    event = next(e for e in reqlog.snapshot()[0]["evidence"] if e["stage"] == "read")
    assert event["raw_http_status"] == 200 and event["effective_status"] is None
    account.status, account.cooling_until = "active", None

    async def captcha_error(_):
        raise RuntimeError("fixture")

    monkeypatch.setattr(gateway.captcha_manager, "get_verify_param", captcha_error)
    response = await client.post("/v1/messages", json={"messages": []})
    assert response.status_code == 500 and response.json()["error"]["type"] == "captcha_error"
    assert "error_code" not in reqlog.snapshot()[0]


@pytest.mark.integration
@pytest.mark.parametrize("value,expected", [("0", 0), ("1.25", 2), ("7200", 7200), ("bad", None), (None, None)])
async def test_retry_headers_and_internal_cap(gateway_client, fresh_app, mock_server, monkeypatch, value, expected):
    import json
    from fastapi import Response
    from fastapi.routing import APIRoute
    from app import reqlog, settings
    from app.routes import gateway
    from tests.conftest import seed_account

    waits = []
    async def no_wait(seconds):
        waits.append(seconds)

    async def upstream():
        return Response(json.dumps({"error": {"message": "quota exceeded", "type": "private-type"}}),
                        status_code=429, media_type="application/json",
                        headers={"retry-after": value} if value is not None else {})

    mock, port = mock_server
    monkeypatch.setattr(mock.router, "routes", mock.router.routes + [APIRoute("/retry-diagnostic", upstream, methods=["POST"])])
    monkeypatch.setitem(settings.UPSTREAM, "zai", f"http://127.0.0.1:{port}/retry-diagnostic")
    monkeypatch.setattr(settings, "RETRY_429_TIMES", 1)
    monkeypatch.setattr(gateway, "_sleep", no_wait)
    account = seed_account(fresh_app, "retryHeader.fixture.sig")
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"messages": []})
    error = response.json()["error"]
    assert error["code"] == "upstream_429_unknown"
    assert account.status == "active"
    assert error["retryable"] is (True if expected is not None else None)
    assert error["retry_after"] == expected
    assert response.headers.get("retry-after") == (str(expected) if expected is not None else None)
    assert len(waits) == 1
    assert waits[0] == (gateway._parse_retry_after(value) or settings.RETRY_429_WAIT)
    assert all(e["business_code"] is None for e in reqlog.snapshot()[0]["evidence"])
    assert "private-type" not in str(reqlog.snapshot()[0]["evidence"])


@pytest.mark.integration
async def test_captcha_reacquire_failure(gateway_client, fresh_app, monkeypatch):
    from app.routes import gateway
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "captchaSlot.fixture.sig")
    fresh_app.set_setting("account_concurrency", "1")
    async def occupy(_):
        gateway._inflight[account.id] = 1
        return "fixture", None

    monkeypatch.setattr(gateway.captcha_manager, "get_verify_param", occupy)
    client, _ = gateway_client
    response = await client.post("/v1/messages", json={"messages": []})
    assert response.json()["error"]["code"] == "local_concurrency_limit"
    assert gateway._inflight[account.id] == 1


@pytest.mark.integration
@pytest.mark.parametrize("endpoint", ["messages", "chat/completions", "responses"])
@pytest.mark.parametrize("stream", [True, False])
async def test_disabled_during_upstream_response(gateway_client, fresh_app, mock_server, monkeypatch, endpoint, stream):
    import json
    from fastapi import Response
    from fastapi.routing import APIRoute
    from app import reqlog, settings
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "barrier.fixture.sig")
    async def upstream():
        account.enabled = False
        return Response(json.dumps({"code": "1302", "message": "fixture"}),
                        status_code=429, media_type="application/json")

    mock, port = mock_server
    monkeypatch.setattr(mock.router, "routes", mock.router.routes + [APIRoute("/barrier", upstream, methods=["POST"])])
    monkeypatch.setitem(settings.UPSTREAM, "zai", f"http://127.0.0.1:{port}/barrier")
    monkeypatch.setattr(settings, "RETRY_429_TIMES", 0)
    client, _ = gateway_client
    body = {"model": "GLM-5.3-Flash", "stream": stream}
    body["input" if endpoint == "responses" else "messages"] = (
        "fixture" if endpoint == "responses" else [{"role": "user", "content": "fixture"}])
    response = await client.post("/v1/" + endpoint, json=body)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["retryable"] is False
    assert error["code"] == "mixed_unavailability"
    assert error["causes"] == [{"code": "account_disabled", "origin": "account_state"},
                                {"code": "upstream_rate_limited", "origin": "upstream"}]
    assert error["retry_after"] is None and "retry-after" not in response.headers
    entry = reqlog.snapshot()[0]
    assert entry["retryable"] is False and entry["error_causes"] == error["causes"]
    assert entry["req_id"] == error["request_id"] == response.headers["x-request-id"]
