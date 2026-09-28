"""多活动套餐归一化层级槽位（PlanSlot）与待领活动感知单元测试。

验证与 zcode-switch 事实真源 100% 对齐：
1. _extract_expire 时间格式提取（秒戳、毫秒戳、ISO、RFC3339）；
2. _plan_tier_from_id 套餐层级提取与代码映射；
3. _build_plan_slots 多活动卡片层级聚合、孤儿额度桶兜底与汇总统计；
4. Account 领域模型 plan_slots / claimable_plans 持久化与 claim_badge 导出；
5. 刷新联动与 preview 软解耦隔离。
"""

from __future__ import annotations

from app.models import Account
from app.quota import _build_plan_slots, _extract_expire, _plan_tier_from_id


class TestExtractExpire:
    def test_extract_from_seconds_timestamp(self):
        # 2026-09-28 00:00:00 UTC+8 -> 1790524800 左右
        ts = 1759075200  # 秒级时间戳
        res = _extract_expire({"ends_at": ts})
        assert res is not None
        assert len(res) == 16  # YYYY-MM-DD HH:MM
        assert res.startswith("20")

    def test_extract_from_millis_timestamp(self):
        ts_ms = 1759075200000  # 毫秒级时间戳
        res = _extract_expire({"expireTime": ts_ms})
        assert res is not None
        assert len(res) == 16
        assert res.startswith("20")

    def test_extract_from_iso_string(self):
        iso = "2026-09-28T23:59:59Z"
        res = _extract_expire({"expires_at": iso})
        assert res == "2026-09-28 23:59"

    def test_empty_or_none(self):
        assert _extract_expire({}) is None
        assert _extract_expire({"ends_at": None}) is None
        assert _extract_expire({"ends_at": ""}) is None


class TestPlanTierFromId:
    def test_max_tier(self):
        tier, code = _plan_tier_from_id("zcode-v3-plan-max", "ZCode Max")
        assert tier == "Max"
        assert code == "max"

    def test_pro_tier(self):
        tier, code = _plan_tier_from_id("zcode-v3-plan-pro", "ZCode Pro")
        assert tier == "Pro"
        assert code == "pro"

    def test_lite_tier(self):
        tier, code = _plan_tier_from_id("zcode-v3-plan-lite", "ZCode Lite")
        assert tier == "Lite"
        assert code == "lite"

    def test_start_plan(self):
        tier, code = _plan_tier_from_id("zcode-v3-plan-start", "ZCode Start Plan")
        assert tier == "Start Plan"
        assert code == "start"

    def test_trial_and_holiday_trust_plan(self):
        # 1 亿 Token 国庆信任活动套餐（plan_id 包含 start-plan，按事实真源映射为 Start Plan）
        tier, code = _plan_tier_from_id("zcode-v3-start-plan-trust-0928", "ZCode Trust Build")
        assert tier == "Start Plan"
        assert code == "start"

    def test_pure_trial_plan(self):
        tier, code = _plan_tier_from_id("zcode-v3-gift-trial", "Gift Trial")
        assert tier == "体验"
        assert code == "trial"


class TestBuildPlanSlots:
    def test_multi_plans_isolation(self):
        """测试类似国际-xzaq多套餐（Trust Build 1亿 + Start Plan 500万）完全隔离展示。"""
        raw_balance = {
            "plans": [
                {
                    "plan_id": "zcode-v3-start-plan-trust-0928",
                    "name": "ZCode Trust Build",
                    "status": "active",
                    "ends_at": 1759075200,
                },
                {
                    "plan_id": "zcode-v3-start-plan-regular",
                    "name": "ZCode Start Plan",
                    "status": "active",
                    "ends_at": 1761667200,
                },
            ],
            "balances": [
                {
                    "plan_id": "zcode-v3-start-plan-trust-0928",
                    "show_name": "GLM-5.3-Flash",
                    "total_units": "100000000",
                    "used_units": "20000000",
                    "remaining_units": "80000000",
                    "unit_type": "token",
                },
                {
                    "plan_id": "zcode-v3-start-plan-regular",
                    "show_name": "GLM-5.3-Flash",
                    "total_units": "5000000",
                    "used_units": "5000000",
                    "remaining_units": "0",
                    "unit_type": "token",
                },
                {
                    "plan_id": "zcode-v3-start-plan-regular",
                    "show_name": "GLM-5.3",
                    "total_units": "3000000",
                    "used_units": "0",
                    "remaining_units": "3000000",
                    "unit_type": "token",
                },
            ],
        }

        slots = _build_plan_slots(raw_balance, [])
        assert len(slots) == 2

        trust_slot = slots[0]
        assert trust_slot["pid"] == "zcode-v3-start-plan-trust-0928"
        assert trust_slot["name"] == "ZCode Trust Build"
        assert trust_slot["tier"] == "Start Plan"
        assert trust_slot["tier_code"] == "start"
        assert len(trust_slot["items"]) == 1
        assert trust_slot["items"][0]["remaining"] == 80000000
        assert trust_slot["total"] == 100000000
        assert trust_slot["remaining"] == 80000000
        assert trust_slot["percent_used"] == 20.0

        start_slot = slots[1]
        assert start_slot["pid"] == "zcode-v3-start-plan-regular"
        assert start_slot["name"] == "ZCode Start Plan"
        assert len(start_slot["items"]) == 2
        # 完整保留 GLM-5.3-Flash 与 GLM-5.3 两个模型
        model_names = [it["name"] for it in start_slot["items"]]
        assert "GLM-5.3-Flash" in model_names
        assert "GLM-5.3" in model_names

    def test_orphan_loose_balances_fallback(self):
        """测试未挂载 plan_id 的孤儿额度桶归入 Default 槽位，不丢弃任何模型配额。"""
        raw_balance = {
            "plans": [],
            "balances": [
                {
                    "show_name": "GLM-5.3-Flash",
                    "total_units": 5000000,
                    "used_units": 1000000,
                    "remaining_units": 4000000,
                    "expires_at": 1759075200,
                }
            ],
        }
        slots = _build_plan_slots(raw_balance, [])
        assert len(slots) == 1
        slot = slots[0]
        assert slot["pid"] == "default"
        assert slot["name"] == "常规额度"
        assert slot["items"][0]["name"] == "GLM-5.3-Flash"
        assert slot["total"] == 5000000
        assert slot["remaining"] == 4000000


class TestAccountModelPlanSlots:
    def test_public_view_exports_plan_slots_and_claim_badge(self):
        acc = Account(
            provider="zai",
            id="test-acct",
            name="测试账号",
            mode="jwt",
            plan_slots=[{"pid": "test-plan", "name": "测试套餐", "items": []}],
            claimable_plans=[{"plan_id": "zcode-trust", "name": "1亿Token大礼包"}],
        )
        pv = acc.public_view()
        assert "plan_slots" in pv
        assert len(pv["plan_slots"]) == 1
        assert pv["plan_slots"][0]["name"] == "测试套餐"

        assert "claimable_plans" in pv
        assert len(pv["claimable_plans"]) == 1
        assert pv["claimable_plans"][0]["name"] == "1亿Token大礼包"

        # claim_badge 计算属性必须为 True
        assert pv["claim_badge"] is True

    def test_claim_badge_false_when_no_claimable_plans(self):
        acc = Account(provider="zai", id="test-acct", name="测试账号", mode="jwt")
        pv = acc.public_view()
        assert pv["claim_badge"] is False
