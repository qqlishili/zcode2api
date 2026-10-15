"""活动下线自愈：1001「套餐不存在」剔除僵尸待领项。

背景：上游活动下线后，`claimable_plans` 会残留永久无法领取的候选（领取报
1001 target not found），导致后台常亮待领图标、哨兵每轮空跑一次验证码求解。
`_post_claim` 收到 1001 时必须剔除该候选。
"""

import pytest

from app.claim import ClaimError, _norm_plan_id, _post_claim, drop_stale_claimable


class _Account:
    """最小账号替身：只保留剔除逻辑需要的字段。"""

    def __init__(self, claimable=None):
        self.name = "stale-fixture"
        self.provider = "zai"
        self.id = "stale-fixture-1"
        self.claimable_plans = list(claimable or [])


def test_drop_stale_claimable_removes_only_target():
    """只剔除目标 plan_id，其余候选原样保留。"""
    acc = _Account([
        {"plan_id": "zcode-v3-start-plan-1010", "name": "Weekend Build"},
        {"plan_id": "zcode-v3-start-plan-0817", "name": "Start Plan"},
    ])
    drop_stale_claimable(acc, "zcode-v3-start-plan-1010")
    remaining = [_norm_plan_id(p) for p in acc.claimable_plans]
    assert remaining == ["zcode-v3-start-plan-0817"]


def test_drop_stale_claimable_matches_case_insensitively():
    """归一化匹配：大小写差异不影响剔除。"""
    acc = _Account([{"plan_id": "ZCode-V3-Start-Plan-1010"}])
    drop_stale_claimable(acc, "zcode-v3-start-plan-1010")
    assert acc.claimable_plans == []


def test_drop_stale_claimable_keeps_account_without_claimable():
    """无待领列表的账号调用必须安全（不抛异常）。"""
    acc = _Account(None)
    drop_stale_claimable(acc, "whatever")
    assert acc.claimable_plans == []


def test_post_claim_1001_drops_candidate(monkeypatch):
    """1001（套餐不存在）必须触发剔除，且仍以 ClaimError 上报失败。"""
    acc = _Account([{"plan_id": "zcode-v3-start-plan-1010"}])
    dropped = []

    async def fake_billing_request(*args, **kwargs):
        return {"code": 1001, "msg": "target not found"}

    monkeypatch.setattr("app.claim._billing_request", fake_billing_request)
    monkeypatch.setattr("app.claim.drop_stale_claimable",
                        lambda account, plan_id: dropped.append(plan_id))

    with pytest.raises(ClaimError) as err:
        import asyncio
        asyncio.run(_post_claim(acc, {}, "zcode-v3-start-plan-1010"))
    assert err.value.code == 1001
    assert dropped == ["zcode-v3-start-plan-1010"]
