"""终止覆盖、候选重试与脱敏证据的边界反例。"""

import pytest


def result(store, failures=None, observations=None, attempt_limit=False):
    from app.routes import gateway

    return gateway._unavailability("fixture-id", "zai", "GLM-5.3-Flash",
                                   observations or {}, failures or {}, attempt_limit)


def test_same_code_different_origin_and_mixed(fresh_app):
    from app.routes.gateway import _AccountFailure
    from tests.conftest import seed_account

    first = seed_account(fresh_app, "first.fixture.sig")
    second = seed_account(fresh_app, "second.fixture.sig")
    first.status = second.status = "invalid"
    failures = {first.id: _AccountFailure("credential_invalid", "upstream", state_code="credential_invalid")}
    error, _ = result(fresh_app, failures)
    assert error["code"] == "credential_invalid"
    assert error["causes"] == [{"code": "credential_invalid", "origin": "account_state"},
                                {"code": "credential_invalid", "origin": "upstream"}]
    second.status = "exhausted"
    assert result(fresh_app, failures)[0]["code"] == "mixed_unavailability"


def test_old_state_recovered_and_unknown_candidate(fresh_app):
    from app.routes.gateway import _AccountFailure
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "recover.fixture.sig")
    error, _ = result(fresh_app, observations={account.id: {"code": "account_cooling", "at": 1}})
    assert error["code"] == "unknown_unavailability" and error["causes"] == []
    error, _ = result(fresh_app, {account.id: _AccountFailure("quota_exhausted", "upstream", state_code="quota_exhausted")})
    assert error["code"] == "unknown_unavailability" and error["causes"] == []
    error, _ = result(fresh_app, {account.id: _AccountFailure(None, "local_scheduler")})
    assert error["code"] == "unknown_unavailability" and error["retryable"] is None


@pytest.mark.parametrize("value,expected", [("0", 100), ("1.25", 101.25), ("7200", 7300),
    ("-1", None), ("nan", None), ("inf", None), ("bad", None), (None, None)])
def test_retry_deadline(value, expected):
    from app.routes.gateway import _retry_deadline

    assert _retry_deadline({"retry-after": value}, 100) == expected


def test_http_date_reference():
    from app.routes.gateway import _retry_deadline

    assert _retry_deadline({"date": "Fri, 09 Oct 2026 00:00:00 GMT",
                            "retry-after": "Fri, 09 Oct 2026 00:00:15 GMT"}, 100) == 115
    assert _retry_deadline({"date": "bad", "retry-after": "Fri, 09 Oct 2026 00:00:15 GMT"}, 100) is None
    assert _retry_deadline({"retry-after": "Fri, 09 Oct 2026 00:00:15"}, 100) is None


def test_candidate_max_min_and_unknown_time(fresh_app, monkeypatch):
    from app.routes import gateway
    from tests.conftest import seed_account

    monkeypatch.setattr(gateway.time, "time", lambda: 100)
    first = seed_account(fresh_app, "first.fixture.sig")
    second = seed_account(fresh_app, "second.fixture.sig")
    first.status, first.cooling_until = "cooling", 110
    second.status, second.cooling_until = "cooling", 108.2
    failures = {first.id: gateway._AccountFailure("upstream_server_error", "upstream", deadline=115.1,
                                                 state_code="account_cooling")}
    error, _ = result(fresh_app, failures)
    assert error["retryable"] is True and error["retry_after"] == 9
    second.status = "active"
    fresh_app.set_setting("account_concurrency", "1")
    monkeypatch.setitem(gateway._inflight, second.id, 1)
    failures[second.id] = gateway._AccountFailure("local_concurrency_limit", "local_scheduler")
    assert result(fresh_app, failures)[0]["retry_after"] is None
    second.status = "exhausted"
    failures.pop(second.id)
    assert result(fresh_app, failures)[0]["retry_after"] == 16
    first.quota = {"GLM-5.3-Flash": {"remaining": 0, "expires_at": 101}}
    assert result(fresh_app, failures)[0]["retryable"] is False


@pytest.mark.parametrize("code,deadline,retry", [("upstream_429_unknown", None, None),
    ("upstream_429_unknown", 100, True), ("captcha_retry_exhausted", None, None),
    ("upstream_concurrency_limit", None, True)])
def test_uncertain_retry(fresh_app, monkeypatch, code, deadline, retry):
    from app.routes import gateway
    from tests.conftest import seed_account

    monkeypatch.setattr(gateway.time, "time", lambda: 100)
    account = seed_account(fresh_app, "retry.fixture.sig")
    failures = {account.id: gateway._AccountFailure(code, "upstream", deadline=deadline)}
    error, _ = result(fresh_app, failures)
    assert error["retryable"] is retry
    assert error["retry_after"] == (0 if deadline == 100 else None)
    assert result(fresh_app, failures, attempt_limit=True)[0]["retryable"] is None


def test_evidence_bound_does_not_truncate_causes(fresh_app):
    from tests.conftest import seed_account

    for index in range(40):
        account = seed_account(fresh_app, f"fixture{index}.token.sig", name="private-name")
        account.status = "invalid" if index < 39 else "exhausted"
    error, diagnostics = result(fresh_app)
    assert len(diagnostics["evidence"]) == 32
    assert diagnostics["evidence_truncated"] == 8
    assert diagnostics["coverage_complete"] is True
    assert error["code"] == "mixed_unavailability"
    assert "private-name" not in str(diagnostics) and "token.sig" not in str(diagnostics)


@pytest.mark.parametrize("value,expected", [(None, None), ("private-secret", None), ("1234567", None),
    ("123456", "123456"), ("3008", "3008"), ("rate_limit_error", "rate_limit_error")])
def test_business_code_whitelist(value, expected):
    from app.routes.gateway import _safe_business_code

    assert _safe_business_code(value) == expected


def test_historical_text_is_not_classification(fresh_app):
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "text.fixture.sig")
    account.status = "disabled"
    account.last_error = "quota exhausted 3012 unusual activity private token"
    error, evidence = result(fresh_app)
    assert error["code"] == "account_disabled"
    assert error["causes"] == [{"code": "account_disabled", "origin": "account_state"}]
    assert "private token" not in str(error) + str(evidence)


def test_wait_consumed_and_successive_deadlines(fresh_app, monkeypatch):
    from app.routes import gateway
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "wait.fixture.sig")
    monkeypatch.setattr(gateway.time, "time", lambda: 105)
    history = [gateway._AccountFailure("upstream_rate_limited", "upstream", at=100, deadline=108)]
    failure = gateway._AccountFailure("upstream_rate_limited", "upstream", deadline=106, history=history)
    error, _ = result(fresh_app, {account.id: failure})
    assert error["retry_after"] == 3
    monkeypatch.setattr(gateway.time, "time", lambda: 110)
    assert result(fresh_app, {account.id: failure})[0]["retry_after"] == 0


@pytest.mark.parametrize("state,reason", [("disabled", "account_disabled"),
    ("invalid", "credential_invalid"), ("exhausted", "quota_exhausted")])
def test_same_observed_hard_barrier_blocks_temporary_retry(fresh_app, state, reason):
    from app.routes.gateway import _AccountFailure
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "barrier.fixture.sig")
    account.status = state
    failure = _AccountFailure("upstream_rate_limited", "upstream", state_code=reason)
    error, _ = result(fresh_app, {account.id: failure})
    assert error["retryable"] is False
    assert {c["code"] for c in error["causes"]} == {reason, "upstream_rate_limited"}
