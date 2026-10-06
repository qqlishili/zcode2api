"""AFF-001~005 完整 dispatch 轮转边界（真实 Mock 上游）。"""

from __future__ import annotations

import pytest

from app import reqlog
from app.models import Status
from app.routes import gateway as gw

_BODY = {"model": "GLM-5.3-Flash", "max_tokens": 64,
         "messages": [{"role": "user", "content": "rotation fixture"}]}
_HEADERS = {"x-session-id": "rotation-fixture"}
_KEY = "zai:sid:rotation-fixture"


@pytest.fixture(autouse=True)
def _clean_state(gateway_client):
    _, mock = gateway_client
    prior_sequences = dict(mock.state.sequences)
    prior_counters = dict(mock.state.counters)
    mock.state.sequences.clear()
    mock.state.counters.clear()
    prior_affinity = dict(gw._session_affinity)
    prior_inflight = dict(gw._inflight)
    gw._session_affinity.clear()
    gw._inflight.clear()
    reqlog.clear()
    yield
    gw._session_affinity.clear()
    gw._session_affinity.update(prior_affinity)
    gw._inflight.clear()
    gw._inflight.update(prior_inflight)
    reqlog.clear()
    mock.state.sequences.clear()
    mock.state.sequences.update(prior_sequences)
    mock.state.counters.clear()
    mock.state.counters.update(prior_counters)


def _seed(store, count=2):
    return [store.add_account("zai", f"rotation-{i}", f"hRot{i}.eyJzdWIiOiJmIn0.sig")
            for i in range(count)]


@pytest.mark.integration
@pytest.mark.parametrize("elapsed, count, rotate", [(599, 19, False), (600, 19, False),
                                                      (601, 19, True), (1, 20, True)])
async def test_window_dispatch_avoids_previous_account(gateway_client, fresh_app, monkeypatch,
                                                       elapsed, count, rotate):
    client, _ = gateway_client
    a, b = _seed(fresh_app)
    monkeypatch.setattr(gw.time, "time", lambda: 10000.0)
    gw._session_affinity[_KEY] = (a.id, 10000.0 - elapsed, count)
    fresh_app._rotation["zai"] = 0  # 新窗口的游标故意指回旧号
    response = await client.post("/v1/messages", headers=_HEADERS, json=_BODY)
    assert response.status_code == 200
    assert gw._session_affinity[_KEY][0] == (b.id if rotate else a.id)
    assert gw._session_affinity[_KEY][1] == (10000.0 if rotate else 10000.0 - elapsed)
    assert not gw._inflight


@pytest.mark.integration
@pytest.mark.parametrize("scenario", ["single", "full", "failed", "all_invalid", "model"])
async def test_rotation_fallback_and_model_priority(gateway_client, fresh_app, monkeypatch, scenario):
    client, mock = gateway_client
    accounts = _seed(fresh_app, 1 if scenario == "single" else 2)
    a = accounts[0]
    monkeypatch.setattr(gw.time, "time", lambda: 10000.0)
    gw._session_affinity[_KEY] = (a.id, 9000.0, 20)
    if scenario == "full":
        gw._inflight[accounts[1].id] = 2
    elif scenario == "failed":
        mock.state.sequences[accounts[1].jwt_token[:16]] = ["auth_invalid"]
    elif scenario == "all_invalid":
        for account in accounts:
            account.status = Status.INVALID
    elif scenario == "model":
        a.quota = {"GLM-5.3-Flash": {"remaining": 100}}
        accounts[1].quota = {"GLM-5.3": {"remaining": 100}}
    response = await client.post("/v1/messages", headers=_HEADERS, json=_BODY)
    assert response.status_code == (503 if scenario == "all_invalid" else 200)
    if scenario != "all_invalid":
        assert gw._session_affinity[_KEY][0] == a.id
    if scenario == "failed":
        assert accounts[1].status == Status.INVALID
    assert gw._inflight == ({accounts[1].id: 2} if scenario == "full" else {})


@pytest.mark.integration
@pytest.mark.parametrize("state", [Status.EXHAUSTED, Status.COOLING, Status.INVALID])
async def test_expired_unhealthy_account_does_not_return(gateway_client, fresh_app, monkeypatch, state):
    client, _ = gateway_client
    a, b = _seed(fresh_app)
    monkeypatch.setattr(gw.time, "time", lambda: 10000.0)
    a.status = state
    a.cooling_until = 11000.0
    gw._session_affinity[_KEY] = (a.id, 9000.0, 20)
    response = await client.post("/v1/messages", headers=_HEADERS, json=_BODY)
    assert response.status_code == 200
    assert gw._session_affinity[_KEY][0] == b.id
    assert not gw._inflight


@pytest.mark.integration
async def test_alternative_failure_respects_attempt_budget(gateway_client, fresh_app, monkeypatch):
    client, mock = gateway_client
    a, b = _seed(fresh_app)
    monkeypatch.setattr(gw, "MAX_ACCOUNT_ATTEMPTS", 1)
    gw._session_affinity[_KEY] = (a.id, 0.0, 20)
    mock.state.sequences[b.jwt_token[:16]] = ["auth_invalid"]
    response = await client.post("/v1/messages", headers=_HEADERS, json=_BODY)
    assert response.status_code == 503
    assert a.use_count == 0 and b.status == Status.INVALID
    assert not gw._inflight


@pytest.mark.integration
async def test_other_session_binding_and_thinking_history_remain_stable(gateway_client, fresh_app):
    client, _ = gateway_client
    a, b = _seed(fresh_app)
    other = "zai:sid:other-fixture"
    gw._session_affinity[_KEY] = (a.id, 0.0, 20)
    gw._bind_sticky_account(other, a.id)
    other_entry = gw._session_affinity[other]
    body = {**_BODY, "messages": [*_BODY["messages"], {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "fixture", "signature": "fixture-signature"},
        {"type": "text", "text": "fixture reply"}]}, {"role": "user", "content": "next fixture"}]}
    response = await client.post("/v1/messages", headers=_HEADERS, json=body)
    assert response.status_code == 200 and gw._session_affinity[_KEY][0] == b.id
    assert gw._session_affinity[other] == other_entry
    response = await client.post("/v1/messages", headers={"x-session-id": "other-fixture"}, json=_BODY)
    assert response.status_code == 200 and gw._session_affinity[other][0] == a.id
    assert not gw._inflight
