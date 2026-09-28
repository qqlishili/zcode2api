"""quota 额度耗尽判定的赠送池逻辑单元测试（GW-013 / 2026-09-06 数据审计）。

背景：billing/balance 只含周期性日窗口，不含 one_time 赠送池。旧判定在日窗口
耗尽时把账号标 EXHAUSTED → store.select 跳过 → 请求 503，而赠送池仍有 3 亿级
可用额度。修复后赠送池有效时不判耗尽。

_bonus_active 为纯函数，直接覆盖各时间/period 形态。
"""

from __future__ import annotations

import time
import httpx
import pytest

from app.quota import _bonus_active

_NOW = 1_000_000.0


def _plan(period="one_time", eff=None, ends=None):
    ent = {"period": period, "effective_at": _NOW - 100 if eff is None else eff}
    if ends is not None:
        ent["ends_at"] = ends
    return {"entitlements": [ent]}


class TestBonusActive:
    def test_active_bonus(self):
        assert _bonus_active(_plan(), _NOW) is True

    def test_no_end_means_open_ended(self):
        assert _bonus_active(_plan(ends=None), _NOW) is True

    def test_future_effective_not_active(self):
        assert _bonus_active(_plan(eff=_NOW + 100), _NOW) is False

    def test_expired_bonus_not_active(self):
        assert _bonus_active(_plan(eff=_NOW - 200, ends=_NOW - 100), _NOW) is False

    def test_expired_at_boundary(self):
        # ends == now 视为已结束（now <= ends 时有效）
        assert _bonus_active(_plan(eff=_NOW - 200, ends=_NOW), _NOW) is True

    def test_recurring_period_ignored(self):
        assert _bonus_active(_plan(period="daily"), _NOW) is False

    def test_empty_or_malformed_plan(self):
        assert _bonus_active({}, _NOW) is False
        assert _bonus_active({"entitlements": []}, _NOW) is False
        assert _bonus_active({"entitlements": [{"period": "one_time"}]}, _NOW) is False
        assert _bonus_active(None, _NOW) is False

    async def test_exhausted_balance_marks_exhausted_even_with_active_one_time_plan(self, fresh_app, monkeypatch):
        """上游 balance 已并入 3 亿赠送包后，若 remaining == 0，即使 one_time 套餐未到期也必须标为 EXHAUSTED。"""
        import httpx
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "ok-li", "a.b.c")
        now = 1_700_000_000.0

        class _FakeResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload
                self.text = ""

            def json(self):
                return self._payload

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url: str, headers=None):
                if url.endswith("/billing/current"):
                    return _FakeResp(200, {
                        "data": {
                            "plans": [{
                                "entitlements": [{
                                    "period": "one_time",
                                    "effective_at": now - 86400,
                                    "ends_at": now + 86400 * 30,
                                }]
                            }]
                        }
                    })
                if url.endswith("/billing/balance"):
                    return _FakeResp(200, {
                        "data": {
                            "balances": [
                                {
                                    "show_name": "GLM-5.3-Flash",
                                    "total_units": 300_000_000,
                                    "used_units": 300_000_000,
                                    "remaining_units": 0,
                                    "expires_at": now + 86400 * 30,
                                }
                            ]
                        }
                    })
                return _FakeResp(200, {"data": {}})

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        await quota.fetch_quota(acc)
        updated = fresh_app.find("zai", acc.id)
        assert updated.status == Status.EXHAUSTED
        assert updated.is_selectable() is False

    async def test_flash_exhausted_marks_exhausted_even_when_other_model_has_quota(
        self, fresh_app, monkeypatch
    ):
        """新账号虽自带 300 万 GLM-5.3 额度，但主免费池 GLM-5.3-Flash 耗尽时必须判为 EXHAUSTED 且不得误恢复。"""
        import httpx
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "abk", "a.b.c")
        now = 1_700_000_000.0

        class _FakeResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload
                self.text = ""

            def json(self):
                return self._payload

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url: str, headers=None):
                if url.endswith("/billing/current"):
                    return _FakeResp(200, {"data": {"plans": []}})
                if url.endswith("/billing/balance"):
                    return _FakeResp(200, {
                        "data": {
                            "balances": [
                                {
                                    "show_name": "GLM-5.3-Flash",
                                    "total_units": 305_000_000,
                                    "used_units": 305_000_000,
                                    "remaining_units": 0,
                                    "expires_at": now + 86400 * 30,
                                },
                                {
                                    "show_name": "GLM-5.3",
                                    "total_units": 3_000_000,
                                    "used_units": 0,
                                    "remaining_units": 3_000_000,
                                    "expires_at": now + 86400,
                                },
                            ]
                        }
                    })
                return _FakeResp(200, {"data": {}})

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        await quota.fetch_quota(acc)
        updated = fresh_app.find("zai", acc.id)
        assert updated.status == Status.EXHAUSTED
        assert updated.is_selectable() is False

        # 再次刷新，验证已 EXHAUSTED 的账号不会因闲置的 300 万 GLM-5.3 而被误恢复为 ACTIVE
        await quota.fetch_quota(updated)
        updated_again = fresh_app.find("zai", acc.id)
        assert updated_again.status == Status.EXHAUSTED
        assert updated_again.is_selectable() is False

    @pytest.mark.asyncio
    async def test_fetch_quota_clears_expired_balances_and_marks_exhausted(self, fresh_app, monkeypatch):
        """当账号套餐自然到期、上游 balances 返回空列表时，应彻底清空 account.quota 并标记 EXHAUSTED。"""
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "expired_user", "jwt.token.here")
        # 预设该账号此前曾有 4800 万有效额度
        acc.quota = {
            "GLM-5.3-Flash": {
                "total": 300_000_000,
                "used": 251_784_007,
                "remaining": 48_215_993,
                "expires_at": 1790557200,
            }
        }
        acc.status = Status.ACTIVE
        fresh_app.update_account(acc)

        class _FakeResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload
                self.text = ""

            def json(self):
                return self._payload

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url: str, headers=None):
                if url.endswith("/billing/current"):
                    return _FakeResp(200, {"code": 0, "data": {"plans": []}})
                if url.endswith("/billing/balance"):
                    return _FakeResp(200, {"code": 0, "data": {"plans": [], "balances": []}})
                return _FakeResp(200, {"code": 0, "data": {}})

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        await quota.fetch_quota(acc)

        updated = fresh_app.find("zai", acc.id)
        # 验证历史残留的 4821 万已被彻底清除为空字典，且状态转为 EXHAUSTED
        assert updated.quota == {}
        assert updated.status == Status.EXHAUSTED
        assert updated.last_error == "额度已用完"
        assert updated.is_selectable() is False

        # 验证二次刷新稳定维持空状态与 EXHAUSTED，不发生状态抖动
        await quota.fetch_quota(updated)
        updated_again = fresh_app.find("zai", acc.id)
        assert updated_again.quota == {}
        assert updated_again.status == Status.EXHAUSTED

    @pytest.mark.asyncio
    async def test_fetch_quota_recovers_when_new_plan_granted(self, fresh_app, monkeypatch):
        """验证处于空配额 EXHAUSTED 状态的账号，在上游重新下发有效额度后能平滑恢复为 ACTIVE。"""
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "reloaded_user", "jwt.token.here")
        acc.quota = {}
        acc.status = Status.EXHAUSTED
        acc.last_error = "额度已用完"
        fresh_app.update_account(acc)

        class _FakeResp:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload
                self.text = ""

            def json(self):
                return self._payload

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url: str, headers=None):
                if url.endswith("/billing/current"):
                    return _FakeResp(200, {"code": 0, "data": {"plans": [{"name": "ZCode Daily"}]}})
                if url.endswith("/billing/balance"):
                    return _FakeResp(200, {
                        "code": 0,
                        "data": {
                            "balances": [
                                {
                                    "show_name": "GLM-5.3-Flash",
                                    "total_units": 5_000_000,
                                    "used_units": 0,
                                    "remaining_units": 5_000_000,
                                    "expires_at": 1790697599,
                                }
                            ]
                        },
                    })
                return _FakeResp(200, {"code": 0, "data": {}})

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        await quota.fetch_quota(acc)

        updated = fresh_app.find("zai", acc.id)
        assert updated.status == Status.ACTIVE
        assert updated.last_error is None
        assert "GLM-5.3-Flash" in updated.quota
        assert updated.quota["GLM-5.3-Flash"]["remaining"] == 5_000_000
        assert updated.is_selectable() is True

    @pytest.mark.asyncio
    async def test_fetch_quota_network_or_malformed_json_preserves_old_quota(self, fresh_app, monkeypatch):
        """验证网络故障或上游 200 返回畸形非 JSON 数据时，防御性保留原有配额，防止误清空。"""
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "protected_user", "jwt.token.here")
        acc.quota = {
            "GLM-5.3-Flash": {
                "total": 5_000_000,
                "used": 100_000,
                "remaining": 4_900_000,
                "expires_at": 1790697599,
            }
        }
        acc.status = Status.ACTIVE
        fresh_app.update_account(acc)

        class _MalformedResp:
            def __init__(self, status_code: int):
                self.status_code = status_code
                self.text = "<html>502 Bad Gateway</html>"

            def json(self):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url: str, headers=None):
                # 模拟上游网关异常返回 200 HTML 页面
                return _MalformedResp(200)

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        await quota.fetch_quota(acc)

        updated = fresh_app.find("zai", acc.id)
        # 配额与状态应安全保留，不被误清空
        assert updated.quota["GLM-5.3-Flash"]["remaining"] == 4_900_000
        assert updated.status == Status.ACTIVE

    @pytest.mark.asyncio
    async def test_capabilities_and_show_name_canonicalized_to_flash(self, fresh_app, monkeypatch):
        """验证 capabilities: ['model:glm-5.3-flash'] 及异形 show_name 归一化聚合为 GLM-5.3-Flash。"""
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "flash_canonical_user", "jwt.token.here")
        acc.status = Status.EXHAUSTED
        fresh_app.update_account(acc)

        future_exp = time.time() + 86400

        class _MockResp:
            def __init__(self, data: dict):
                self.status_code = 200
                self._data = data

            def json(self):
                return self._data

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url: str, headers=None):
                if "/billing/balance" in url:
                    return _MockResp({
                        "code": 0,
                        "data": {
                            "balances": [
                                # 窗口 1：通过 capabilities 标记 Flash，但 show_name 叫 "Daily Free Pool"
                                {
                                    "show_name": "Daily Free Pool",
                                    "capabilities": ["model:glm-5.3-flash", "context:128k"],
                                    "total_units": "2000000",
                                    "used_units": "500000",
                                    "remaining_units": "1500000",
                                    "expires_at": future_exp,
                                },
                                # 窗口 2：show_name 带异名营销词 "GLM-5.3-Flash 体验版"
                                {
                                    "show_name": "GLM-5.3-Flash 体验版",
                                    "capabilities": [],
                                    "total_units": 3000000,
                                    "used_units": 100000,
                                    "remaining_units": 2900000,
                                    "expires_at": future_exp + 3600,
                                },
                                # 窗口 3：无关模型，应独立保留
                                {
                                    "show_name": "GLM-5.3",
                                    "capabilities": ["model:glm-5.3"],
                                    "total_units": 1000000,
                                    "used_units": 0,
                                    "remaining_units": 1000000,
                                    "expires_at": future_exp,
                                },
                            ]
                        }
                    })
                return _MockResp({"code": 0, "data": {}})

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        await quota.fetch_quota(acc)

        updated = fresh_app.find("zai", acc.id)
        assert "GLM-5.3-Flash" in updated.quota
        flash_win = updated.quota["GLM-5.3-Flash"]
        # 窗口 1 (1.5M) + 窗口 2 (2.9M) 累加为 4.4M
        assert flash_win["remaining"] == 4_400_000
        assert flash_win["total"] == 5_000_000
        assert flash_win["used"] == 600_000
        assert flash_win["expires_at"] == future_exp + 3600
        # 额度恢复且自动激活
        assert updated.status == Status.ACTIVE
        # GLM-5.3 独立记录
        assert "GLM-5.3" in updated.quota
        assert updated.quota["GLM-5.3"]["remaining"] == 1_000_000

    @pytest.mark.asyncio
    async def test_expired_windows_are_filtered_during_accumulation(self, fresh_app, monkeypatch):
        """验证 expires_at <= now 的过期残留窗口被过滤，不污染可用配额。"""
        from app import quota
        from app.models import Status

        acc = fresh_app.add_account("zai", "expired_filter_user", "jwt.token.here")
        acc.status = Status.ACTIVE
        fresh_app.update_account(acc)

        past_exp = time.time() - 3600

        class _MockResp:
            def __init__(self, data: dict):
                self.status_code = 200
                self._data = data

            def json(self):
                return self._data

        class _FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url: str, headers=None):
                if "/billing/balance" in url:
                    return _MockResp({
                        "code": 0,
                        "data": {
                            "balances": [
                                # 历史残留过期窗口（上游未清理），remaining 虽然 > 0 但已过期
                                {
                                    "show_name": "GLM-5.3-Flash",
                                    "capabilities": ["model:glm-5.3-flash"],
                                    "total_units": 5000000,
                                    "used_units": 0,
                                    "remaining_units": 5000000,
                                    "expires_at": past_exp,
                                }
                            ]
                        }
                    })
                return _MockResp({"code": 0, "data": {}})

        monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
        await quota.fetch_quota(acc)

        updated = fresh_app.find("zai", acc.id)
        # 过期窗口被过滤后 quota 为空，账号自动转为 EXHAUSTED
        assert updated.quota == {}
        assert updated.status == Status.EXHAUSTED


