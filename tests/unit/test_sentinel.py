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
