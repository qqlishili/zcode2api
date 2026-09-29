"""app/sentinel.py 单元测试：哨兵探测、差量计算、有界探针与抢领闭环。"""

from __future__ import annotations

import asyncio

import pytest

from app.models import Account, Status
from app.sentinel import Sentinel
from app.store import store


@pytest.fixture(autouse=True)
def clean_sentinel_state():
    """每次测试重置 sentinel 相关配置与已见活动。"""
    store.set_setting("sentinel_interval", "1800")
    store.set_setting("sentinel_auto_claim", "1")
    store.set_setting("sentinel_seen_plans", "[]")
    store.set_setting("bark_device_key", "test_device_key_1234")
    store.set_setting("bark_server_url", "https://api.day.app")
    yield
    store.set_setting("sentinel_seen_plans", "[]")


@pytest.mark.asyncio
async def test_sentinel_no_active_accounts(monkeypatch):
    """当账号池中没有任何活跃 JWT 账号时，平稳退出。"""
    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [])
    s = Sentinel()
    res = await s.check_once()
    assert res["ok"] is False
    assert res["reason"] == "no_active_sentinel"


@pytest.mark.asyncio
async def test_sentinel_no_new_plans(monkeypatch):
    """当上游套餐列表无新活动时，不触发领券与通知。"""
    acc = Account.create("zai", "acc1", "token1")
    acc.mode = "jwt"
    acc.jwt_token = "ey...mock.jwt"
    acc.status = Status.ACTIVE
    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc])

    mock_plans = [{"plan_id": "plan_basic", "name": "基础日配额", "priority": 1, "grants": []}]

    async def mock_preview(account):
        return mock_plans

    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview)
    # 模拟 plan_basic 已经在已见集合中
    store.add_seen_plan_ids(["plan_basic"])

    s = Sentinel()
    res = await s.check_once()
    assert res["ok"] is True
    assert res["new_plans_count"] == 0


@pytest.mark.asyncio
async def test_sentinel_discover_new_plan_and_claim(monkeypatch):
    """当发现全新活动套餐时，差量识别并触发全池顺序抢领，且投递 Bark 通知。"""
    acc1 = Account.create("zai", "acc1", "token1")
    acc1.mode = "jwt"
    acc1.jwt_token = "ey...jwt1"
    acc1.status = Status.ACTIVE

    acc2 = Account.create("zai", "acc2", "token2")
    acc2.mode = "jwt"
    acc2.jwt_token = "ey...jwt2"
    acc2.status = Status.ACTIVE

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc1, acc2])

    mock_plans = [
        {
            "plan_id": "plan_spring_5m",
            "name": "新春特惠赠送",
            "priority": 10,
            "grants": [{"name": "GLM-5.3-Flash", "units": 5000000.0, "period": "one_time"}],
        }
    ]

    async def mock_preview(account):
        return mock_plans

    claimed_accounts = []

    async def mock_claim_all(account):
        claimed_accounts.append(account.name)
        return [{"ok": True, "plan_id": "plan_spring_5m"}]

    sent_notifications = []

    async def mock_send_bark(**kwargs):
        sent_notifications.append(kwargs)
        return True, "推送成功"

    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview)
    monkeypatch.setattr("app.sentinel.auto_claim_all_plans", mock_claim_all)
    monkeypatch.setattr("app.sentinel.send_bark_notification", mock_send_bark)
    async def mock_sleep(sec):
        return None

    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    s = Sentinel()
    res = await s.check_once()

    assert res["ok"] is True
    assert len(res["new_plans"]) == 1
    assert res["new_plans"][0]["plan_id"] == "plan_spring_5m"
    # 验证全池顺序抢领触发
    assert claimed_accounts == ["acc1", "acc2"]
    # 验证 Bark 通知触发
    assert len(sent_notifications) == 1
    assert "新春特惠赠送" in sent_notifications[0]["body"]
    assert "2/2 成功" in sent_notifications[0]["body"]
    # 验证时序安全落库
    assert "plan_spring_5m" in store.get_seen_plan_ids()


@pytest.mark.asyncio
async def test_sentinel_probe_max_retries_exhausted(monkeypatch):
    """测试当候选账号连续报错达到最大探针上限（3次）时，熔断退出，防死锁。"""
    accs = []
    for i in range(5):
        acc = Account.create("zai", f"acc{i}", f"token{i}")
        acc.mode = "jwt"
        acc.jwt_token = f"ey...jwt{i}"
        acc.status = Status.ACTIVE
        accs.append(acc)

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: accs)

    probe_count = 0

    async def mock_preview_fail(account):
        nonlocal probe_count
        probe_count += 1
        raise RuntimeError("上游 401 凭证失效")

    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview_fail)

    s = Sentinel()
    res = await s.check_once()

    assert res["ok"] is False
    assert res["reason"] == "probe_exhausted"
    # 严格确保探针尝试次数不超过 3 次有界限制
    assert probe_count == 3


async def _noop(val=None):
    return val


@pytest.mark.asyncio
async def test_sentinel_claims_for_exhausted_accounts(monkeypatch):
    """验证处于 EXHAUSTED（额度用完）状态的账号在新活动发现时同样被纳入自动抢领。"""
    acc_active = Account.create("zai", "acc_active", "t1")
    acc_active.mode = "jwt"
    acc_active.jwt_token = "ey...jwt1"
    acc_active.status = Status.ACTIVE

    acc_exhausted = Account.create("zai", "acc_exhausted", "t2")
    acc_exhausted.mode = "jwt"
    acc_exhausted.jwt_token = "ey...jwt2"
    acc_exhausted.status = Status.EXHAUSTED

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc_active, acc_exhausted])

    async def mock_preview(acc):
        return [{"plan_id": "zcode-v3-start-plan-trust-0930", "name": "Trust Build", "priority": 10, "grants": []}]

    claimed = []

    async def mock_claim_all(acc):
        claimed.append(acc.name)
        return [{"ok": True, "plan_id": "zcode-v3-start-plan-trust-0930"}]

    async def mock_bark(**kw):
        return True, "ok"

    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview)
    monkeypatch.setattr("app.sentinel.auto_claim_all_plans", mock_claim_all)
    monkeypatch.setattr("app.sentinel.send_bark_notification", mock_bark)
    monkeypatch.setattr(asyncio, "sleep", _noop)

    s = Sentinel()
    res = await s.check_once()
    assert res["ok"] is True
    assert claimed == ["acc_active", "acc_exhausted"]


@pytest.mark.asyncio
async def test_sentinel_catchup_unclaimed_accounts_and_cooldown(monkeypatch):
    """当活动已在 seen_ids 中时，自动为池内尚未持有该活动的 EXHAUSTED/ACTIVE 漏领账号补领，失败后进入冷却防死循环。"""
    acc_held = Account.create("zai", "acc_held", "t1")
    acc_held.mode = "jwt"
    acc_held.jwt_token = "ey...jwt1"
    acc_held.status = Status.ACTIVE
    acc_held.plans = [{"plan_id": "zcode-v3-start-plan-trust-0930", "name": "Trust Build"}]

    acc_missed = Account.create("zai", "acc_missed", "t2")
    acc_missed.mode = "jwt"
    acc_missed.jwt_token = "ey...jwt2"
    acc_missed.status = Status.EXHAUSTED
    acc_missed.plans = []
    acc_missed.claimable_plans = [{"plan_id": "zcode-v3-start-plan-trust-0930", "name": "Trust Build"}]

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc_held, acc_missed])
    store.add_seen_plan_ids(["zcode-v3-start-plan-trust-0930"])

    async def mock_preview(acc):
        return [{"plan_id": "zcode-v3-start-plan-trust-0930", "name": "Trust Build", "priority": 10, "grants": []}]

    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview)
    monkeypatch.setattr(asyncio, "sleep", _noop)

    call_log = []

    async def mock_claim_fail(acc):
        call_log.append(acc.name)
        return [{"ok": False, "plan_id": "zcode-v3-start-plan-trust-0930", "message": "3012 风控"}]

    monkeypatch.setattr("app.sentinel.auto_claim_all_plans", mock_claim_fail)

    s = Sentinel()
    # 第一轮：acc_held 已持有跳过，acc_missed 触发自动补领，因失败被记入冷却
    res1 = await s.check_once()
    assert res1["ok"] is True
    assert call_log == ["acc_missed"]
    assert len(res1["catchup_reports"]) == 1

    # 第二轮：acc_missed 处于 6 小时冷却期内，不再重复撞击上游
    res2 = await s.check_once()
    assert res2["ok"] is True
    assert call_log == ["acc_missed"]
    assert res2["catchup_reports"] == []


@pytest.mark.asyncio
async def test_sentinel_bark_failure_retains_unseen_until_delivered(monkeypatch):
    """验证当发现新活动但 Bark 推送超时失败时，不提前写入 seen_ids，下一轮巡检成功补推后再落库。"""
    acc = Account.create("zai", "acc1", "t1")
    acc.mode = "jwt"
    acc.jwt_token = "ey...jwt1"
    acc.status = Status.ACTIVE

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc])

    async def mock_preview(a):
        return [{"plan_id": "plan_0930", "name": "Trust Build", "priority": 10, "grants": []}]

    async def mock_claim_all(a):
        a.plans = [{"plan_id": "plan_0930", "name": "Trust Build"}]
        return [{"ok": True, "plan_id": "plan_0930", "plan_name": "Trust Build"}]

    bark_attempt = 0

    async def mock_bark(**kw):
        nonlocal bark_attempt
        bark_attempt += 1
        if bark_attempt == 1:
            return False, "推送请求超时"
        return True, "推送成功"

    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview)
    monkeypatch.setattr("app.sentinel.auto_claim_all_plans", mock_claim_all)
    monkeypatch.setattr("app.sentinel.send_bark_notification", mock_bark)
    monkeypatch.setattr(asyncio, "sleep", _noop)

    s = Sentinel()
    # 第 1 轮：Bark 推送失败 -> plan_0930 不写入 seen_ids
    await s.check_once()
    assert "plan_0930" not in store.get_seen_plan_ids()

    # 第 2 轮：Bark 补推成功 -> plan_0930 写入 seen_ids
    await s.check_once()
    assert "plan_0930" in store.get_seen_plan_ids()
    assert bark_attempt == 2


