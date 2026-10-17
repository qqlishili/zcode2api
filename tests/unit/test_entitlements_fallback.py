"""entitlements fallback：新激活套餐 balances 未结算时的额度识别测试。"""

import pytest

from app.quota import _has_granted_entitlements


class TestHasGrantedEntitlements:
    def test_active_plan_with_grant_units(self):
        plans = [{
            "status": "active",
            "plan_id": "zcode-v3-start-plan-referral",
            "entitlements": [{"grant_units": 100000000, "show_name": "GLM-5.3-Flash"}],
        }]
        assert _has_granted_entitlements(plans) is True

    def test_inactive_plan_ignored(self):
        plans = [{
            "status": "expired",
            "plan_id": "zcode-v3-start-plan-referral",
            "entitlements": [{"grant_units": 100000000}],
        }]
        assert _has_granted_entitlements(plans) is False

    def test_grant_units_zero_or_missing(self):
        plans = [{
            "status": "active",
            "entitlements": [{"grant_units": 0}],
        }]
        assert _has_granted_entitlements(plans) is False
        assert _has_granted_entitlements([{"status": "active", "entitlements": []}]) is False
        assert _has_granted_entitlements([{"status": "active"}]) is False

    def test_grant_units_as_string(self):
        plans = [{
            "status": "active",
            "entitlements": [{"grant_units": "100000000"}],
        }]
        assert _has_granted_entitlements(plans) is True

    def test_empty_plans(self):
        assert _has_granted_entitlements([]) is False
        assert _has_granted_entitlements(None) is False


class TestFetchQuotaEntitlementsFallback:
    """fetch_quota 在 balances 空时的「待结算」vs「真用完」区分。"""

    def _patch_client(self, monkeypatch, *, balance_plans, balance_balances):
        """monkeypatch httpx.AsyncClient，按 URL 返回 current/balance/usage。"""
        import httpx

        class _Resp:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                self.text = ""
            def json(self):
                return self._payload

        class _FakeClient:
            def __init__(self, *a, **k):
                self.is_closed = False
            async def aclose(self):
                self.is_closed = True
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                await self.aclose()
            async def get(self, url, headers=None):
                if url.endswith("/billing/current"):
                    return _Resp(200, {"data": {"plans": balance_plans}})
                if url.endswith("/billing/balance"):
                    return _Resp(200, {"data": {"plans": balance_plans, "balances": balance_balances}})
                return _Resp(200, {"data": {}})

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    async def test_balances_empty_with_granted_entitlements_marks_pending(self, fresh_app, monkeypatch):
        """balances 空 + entitlements 有 grant_units → 文案「待结算」而非「已用完」。"""
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "ok-li", "a.b.c")
        plans = [{
            "status": "active",
            "plan_id": "zcode-v3-start-plan-referral",
            "entitlements": [{"grant_units": 100000000, "show_name": "GLM-5.3-Flash"}],
        }]
        self._patch_client(monkeypatch, balance_plans=plans, balance_balances=[])

        await quota.fetch_quota(acc)
        updated = fresh_app.find("zai", acc.id)
        assert updated.status == Status.EXHAUSTED
        assert updated.last_error == "额度已授予，等待结算"
        assert updated.is_selectable() is False  # 待结算不可选
        # 保留套餐槽位（含 pending 授予额度），供前端展示「待结算」而非「额度未获取」
        assert len(updated.plan_slots) == 1
        assert len(updated.plan_slots[0]["pending"]) == 1
        assert updated.plan_slots[0]["pending"][0]["grant_units"] == 100000000

    async def test_balances_empty_without_entitlements_marks_exhausted(self, fresh_app, monkeypatch):
        """balances 空 + 无 entitlements → 维持「额度已用完」，清空套餐槽位。"""
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "ok-li", "a.b.c")
        self._patch_client(monkeypatch, balance_plans=[], balance_balances=[])

        await quota.fetch_quota(acc)
        updated = fresh_app.find("zai", acc.id)
        assert updated.status == Status.EXHAUSTED
        assert updated.last_error == "额度已用完"
        assert updated.is_selectable() is False
        assert updated.plan_slots == []


class TestPendingQuotaLifecycle:
    _patch_client = TestFetchQuotaEntitlementsFallback._patch_client

    async def test_future_grant_stays_unselectable(self, fresh_app, monkeypatch):
        import time

        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "future-grant", "a.b.c")
        plans = [{
            "status": "active", "plan_id": "activity",
            "entitlements": [{"grant_units": 100000000, "show_name": "GLM-5.3-Flash",
                              "effective_at": time.time() + 3600}],
        }]
        self._patch_client(monkeypatch, balance_plans=plans, balance_balances=[])
        await quota.fetch_quota(acc)
        assert acc.status == Status.EXHAUSTED
        assert acc.last_error == "额度已授予，等待生效"
        assert acc.quota == {}
        assert acc.allows_billing()
        assert not acc.is_selectable()

    @pytest.mark.parametrize("available_model", ["GLM-5.3-Flash", "GLM-5.3"])
    async def test_other_activity_balance_remains_selectable(self, fresh_app, monkeypatch, available_model):
        import time

        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "multiple-activities", "a.b.c")
        plans = [
            {"status": "active", "plan_id": "future-activity",
             "entitlements": [{"grant_units": 100000000, "show_name": "GLM-5.3-Flash",
                               "effective_at": time.time() + 3600}]},
            {"status": "active", "plan_id": "available-activity",
             "entitlements": [{"grant_units": 5000000, "show_name": available_model}]},
        ]
        balances = [{"plan_id": "available-activity", "show_name": available_model,
                     "total_units": 5000000, "used_units": 1000000, "remaining_units": 4000000}]
        if available_model != "GLM-5.3-Flash":
            balances.append({"plan_id": "available-activity", "show_name": "GLM-5.3-Flash",
                             "total_units": 5000000, "used_units": 5000000, "remaining_units": 0})
        self._patch_client(monkeypatch, balance_plans=plans, balance_balances=balances)
        await quota.fetch_quota(acc)
        assert acc.status == Status.ACTIVE
        assert acc.is_selectable()
        assert acc.quota[available_model]["remaining"] == 4000000
        assert acc.model_quota_exclusion(available_model) is None
        if available_model != "GLM-5.3-Flash":
            assert acc.model_quota_exclusion("GLM-5.3-Flash") == "model_quota_exhausted"
        assert acc.plan_slots[0]["pending"][0]["grant_units"] == 100000000
        assert acc.plan_slots[1]["pending"] == []

    async def test_monitor_restores_only_after_real_balance(self, fresh_app, monkeypatch):
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "settlement", "a.b.c")
        plans = [{"status": "active", "plan_id": "activity", "entitlements": [
            {"grant_units": 5000000, "show_name": "GLM-5.3-Flash", "effective_at": 1},
        ]}]
        balances = []
        self._patch_client(monkeypatch, balance_plans=plans, balance_balances=balances)
        await quota.fetch_quota(acc)
        assert acc.status == Status.EXHAUSTED
        assert acc.last_error == "额度已授予，等待结算"
        assert not acc.is_selectable()
        assert acc.allows_billing()
        assert acc.quota == {}
        balances.append({"plan_id": "activity", "show_name": "GLM-5.3-Flash",
                         "total_units": 5000000, "used_units": 0, "remaining_units": 5000000})
        acc.last_checked_at = 0
        result = await quota.QuotaMonitor().check_once(interval=60)
        assert result["ok"] == 1
        assert acc.status == Status.ACTIVE
        assert acc.last_error is None
        assert acc.is_selectable()
        assert acc.quota["GLM-5.3-Flash"]["remaining"] == 5000000
        assert acc.plan_slots[0]["pending"] == []

    async def test_removed_grant_clears_pending_label(self, fresh_app, monkeypatch):
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "removed-grant", "a.b.c")
        acc.status = Status.EXHAUSTED
        acc.last_error = "额度已授予，等待结算"
        self._patch_client(monkeypatch, balance_plans=[], balance_balances=[])
        await quota.fetch_quota(acc)
        assert acc.status == Status.EXHAUSTED
        assert acc.last_error == "额度已用完"
        assert acc.plan_slots == []
