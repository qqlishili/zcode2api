"""筛选证据不改变既有健康、模型偏好与轮询行为。"""

import pytest

from app.models import Account, Status


@pytest.mark.parametrize("quota,expected", [({}, True), ({"other": {"remaining": 0}}, True),
    ({"GLM-5.3-Flash": {"remaining": None}}, True), ({"glm-5.3-flash": {"remaining": 0}}, False)])
def test_model_window(quota, expected):
    account = Account.create("zai", "fixture", "fixture")
    account.quota = quota
    assert account.has_model_quota("GLM-5.3-Flash") is expected
    assert account.model_quota_exclusion("GLM-5.3-Flash") == (None if expected else "model_quota_exhausted")


@pytest.mark.parametrize("status,until,expected,reason", [
    (Status.COOLING, None, False, "unknown_unavailability"),
    (Status.COOLING, 200, False, "account_cooling"),
    (Status.COOLING, 50, True, None), ("unknown", None, True, None)])
def test_health_predicate(status, until, expected, reason):
    account = Account.create("zai", "fixture", "fixture")
    account.status, account.cooling_until = status, until
    assert account.is_selectable(100) is expected
    assert account.selection_exclusion(100) == reason


def test_observation_keeps_preference_cursor_and_skip(fresh_app):
    from tests.conftest import seed_account

    missing = seed_account(fresh_app, "missing.fixture.sig", "missing")
    missing.quota = {"other": {"remaining": 1}}
    first = seed_account(fresh_app, "first.fixture.sig", "first")
    second = seed_account(fresh_app, "second.fixture.sig", "second")
    blocked = seed_account(fresh_app, "blocked.fixture.sig", "blocked")
    blocked.quota = {"GLM-5.3-Flash": {"remaining": 0}}
    evidence = {}
    assert fresh_app.select("zai", model="GLM-5.3-Flash", observations=evidence) is first
    assert evidence[blocked.id]["code"] == "model_quota_exhausted"
    cursor = dict(fresh_app._rotation)
    snapshot = fresh_app.selection_snapshot("zai", "GLM-5.3-Flash")
    assert fresh_app._rotation == cursor and snapshot[blocked.id]["model_code"] == "model_quota_exhausted"
    assert fresh_app.select("zai", model="GLM-5.3-Flash", observations={}) is second
    assert fresh_app.select("zai", model="GLM-5.3-Flash", skip_ids={first.id}, avoid_id=second.id) is second
    assert fresh_app.select("zai", model="GLM-5.3-Flash", skip_ids={first.id, second.id}) is missing
