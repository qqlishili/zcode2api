"""1005 名额避让与活动退避状态收敛单元测试。

验证第一性原理与二阶思维：
1. format_claim_blocked_time: 消除跨天与时间歧义（今日 / 次日 / YYYY-MM-DD）；
2. billing_block_reason: 消除虚假重置承诺，提示时序避让与活动下线自动跳过；
3. sync_account_claimable_plans: 状态自愈归一，账号已无未持有活动时自动解除陈旧避让戳；
4. _ensure_claimable_account 与 _auto_pick_plan: 已持有套餐优先抛 1003 已领，不被 1005 避让误伤。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.claim import (
    ClaimError,
    _auto_pick_plan,
    _ensure_claimable_account,
    billing_block_reason,
    format_claim_blocked_time,
    sync_account_claimable_plans,
)
from app.models import Account, Status

_TZ = timezone(timedelta(hours=8))


def _make_jwt_account(name: str = "test-acc", held_plans: list[dict] | None = None) -> Account:
    return Account(
        id=f"{name}-id",
        name=name,
        provider="zai",
        mode="jwt",
        jwt_token="h1.eyJzdWIiOiJ1MSIsInVzZXJfaWQiOiJ1MSJ9.sig",
        status=Status.ACTIVE,
        plans=held_plans or [],
    )


def test_format_claim_blocked_time_same_day():
    now_dt = datetime(2026, 10, 11, 8, 30, 0, tzinfo=_TZ)
    now_ts = now_dt.timestamp()
    target_ts = datetime(2026, 10, 11, 10, 0, 0, tzinfo=_TZ).timestamp()
    formatted = format_claim_blocked_time(target_ts, now_ts)
    assert formatted == "今日 10:00:00"


def test_format_claim_blocked_time_next_day():
    now_dt = datetime(2026, 10, 11, 9, 2, 0, tzinfo=_TZ)
    now_ts = now_dt.timestamp()
    target_ts = datetime(2026, 10, 12, 9, 0, 0, tzinfo=_TZ).timestamp()
    formatted = format_claim_blocked_time(target_ts, now_ts)
    assert formatted == "次日 09:00:00"


def test_format_claim_blocked_time_future_days():
    now_dt = datetime(2026, 10, 11, 9, 2, 0, tzinfo=_TZ)
    now_ts = now_dt.timestamp()
    target_ts = datetime(2026, 10, 15, 12, 0, 0, tzinfo=_TZ).timestamp()
    formatted = format_claim_blocked_time(target_ts, now_ts)
    assert formatted == "2026-10-15 12:00:00"


def test_billing_block_reason_avoids_false_reset_promise():
    acc = _make_jwt_account()
    now_ts = time.time()
    acc.claim_blocked_until = now_ts + 3600
    reason = billing_block_reason(acc, action="领取")
    assert reason is not None
    assert "今日领取名额已用完" in reason
    assert "跳过领取" in reason
    assert "若届时活动下线将自动跳过" in reason
    assert "预计重置时间" not in reason


def test_sync_account_claimable_plans_clears_stale_blocked_when_all_held():
    acc = _make_jwt_account(held_plans=[{"plan_id": "promo-plan-1", "name": "Plan 1"}])
    acc.claim_blocked_until = time.time() + 7200

    # 探查发现只有 promo-plan-1，账号已经全部持有
    unclaimed = sync_account_claimable_plans(acc, [{"plan_id": "promo-plan-1"}], persist=False)
    assert unclaimed == []
    # 状态自愈：名额避让锁自动解除
    assert acc.claim_blocked_until is None


def test_ensure_claimable_account_allows_held_plan():
    acc = _make_jwt_account(held_plans=[{"plan_id": "promo-plan-1"}])
    acc.claim_blocked_until = time.time() + 7200
    # 指定已持有套餐时，不应被 1005 拦截
    _ensure_claimable_account(acc, plan_id="promo-plan-1")

    # 未指定或指定未持有套餐时，正常被拦截
    with pytest.raises(ClaimError) as exc_info:
        _ensure_claimable_account(acc, plan_id="promo-plan-2")
    assert "今日领取名额已用完" in str(exc_info.value)


@pytest.mark.asyncio
async def test_auto_pick_plan_held_plan_raises_1003_without_upstream():
    acc = _make_jwt_account(held_plans=[{"plan_id": "promo-plan-1"}])
    with pytest.raises(ClaimError) as exc_info:
        await _auto_pick_plan(acc, plan_id="promo-plan-1")
    assert exc_info.value.code == 1003
    assert "该套餐已经领取过" in str(exc_info.value)
