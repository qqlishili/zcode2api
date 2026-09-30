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
    # 验证全池顺序抢领触发（同优先级随机打散后全集覆盖）
    assert sorted(claimed_accounts) == ["acc1", "acc2"]
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


@pytest.mark.asyncio
async def test_sentinel_rotates_probe_across_active_accounts(monkeypatch):
    """验证多轮巡检时探针在多个 ACTIVE 账号间随机打散轮转，不会永远固定消耗第 1 个账号。"""
    accounts = []
    for i in range(4):
        acc = Account.create("zai", f"active_{i}", f"t{i}")
        acc.mode = "jwt"
        acc.jwt_token = f"ey...jwt{i}"
        acc.status = Status.ACTIVE
        accounts.append(acc)

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: list(accounts))
    store.add_seen_plan_ids(["plan_0930"])

    probed_names: set[str] = set()

    async def mock_preview(a):
        probed_names.add(a.name)
        return [{"plan_id": "plan_0930", "name": "Trust Build", "priority": 10, "grants": []}]

    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview)
    monkeypatch.setattr(asyncio, "sleep", _noop)

    s = Sentinel()
    for _ in range(20):
        await s.check_once()

    # 20 轮巡检在 4 个 ACTIVE 账号间随机轮转，必然覆盖超过 1 个探针账号
    assert len(probed_names) > 1


def test_solver_js_polymorphic_self_consistent_fingerprint():
    """验证 captcha_node/solver.js 已彻底移除跨层 Linux/Intel 硬编码矛盾，且具备种子驱动的多维深度混淆特征。"""
    from pathlib import Path

    solver_path = Path(__file__).resolve().parents[2] / "captcha_node" / "solver.js"
    content = solver_path.read_text(encoding="utf-8")

    # 1. 不再存在任何 sec-ch-ua-platform / userAgentData 的 "Linux" 硬编码或 WebGL 兜底 "Intel Inc."
    assert '"sec-ch-ua-platform", \'"Linux"\'' not in content
    assert '"sec-ch-ua-platform": \'"Linux"\'' not in content
    assert 'platform: "Linux"' not in content
    assert 'return "Intel Inc."' not in content

    # 2. 统一引用 fp.seed / splitMix32 确定性微扰，覆盖 Canvas 2D getImageData / Audio / ClientRects / MediaDevices / Voices / Google Chrome Brand
    assert "function splitMix32(" in content
    assert "function seedHex64(" in content
    assert "fp.chPlatform" in content
    assert "fp.platformVersion" in content
    assert "fp.arch" in content
    assert "fp.rectJitter" in content
    assert "generateCanvasPngDataUrl(seed)" in content
    assert "DESKTOP_SKUS" in content
    assert '"Google Chrome"' in content
    assert "defaultDevices" in content
    assert "defaultVoices" in content


def test_sentinel_midnight_rollover_and_grace_window_wakeup(monkeypatch):
    """验证跨北京时间 00:00 零点破冰截断、00:00~00:10 窗口快速复查以及 1005 解封对齐唤醒。"""
    from datetime import datetime

    from app.claim import _TZ_BEIJING

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [])
    s = Sentinel()

    # 1. 23:45:00 CST（距零点 900s），interval=1800s -> 截断至 915 ~ 960s（00:00:15 ~ 00:01:00 唤醒）
    ts_2345 = datetime(2026, 9, 30, 23, 45, 0, tzinfo=_TZ_BEIJING).timestamp()
    w_rollover = s._next_sleep_seconds(1800, now=ts_2345)
    assert 915.0 <= w_rollover <= 960.0

    # 2. 00:01:00 CST，今日 1001 活动尚未出现 -> 快速复查间隔收敛至 90 ~ 150s
    ts_0001 = datetime(2026, 10, 1, 0, 1, 0, tzinfo=_TZ_BEIJING).timestamp()
    w_grace = s._next_sleep_seconds(1800, last_result={"new_plans_count": 0}, now=ts_0001)
    assert 90.0 <= w_grace <= 150.0

    # 3. 00:02:30 CST，今日活动 zcode-v3-start-plan-trust-1001 已入 seen_ids -> 恢复常规 1800s ± 10%
    store.add_seen_plan_ids(["zcode-v3-start-plan-trust-1001"])
    ts_0002 = datetime(2026, 10, 1, 0, 2, 30, tzinfo=_TZ_BEIJING).timestamp()
    w_normal = s._next_sleep_seconds(1800, last_result={"new_plans_count": 0}, now=ts_0002)
    assert 1620.0 <= w_normal <= 1980.0

    # 4. 池内存在 1005 避让账号将于 180s 后（如 00:05:30）解封 -> 唤醒时间对齐至 185 ~ 200s
    acc = Account.create("zai", "blocked_acc", "t1")
    acc.mode = "jwt"
    acc.jwt_token = "ey...jwt1"
    acc.status = Status.ACTIVE
    acc.claim_blocked_until = ts_0002 + 180.0
    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc])
    w_unblock = s._next_sleep_seconds(1800, last_result={"new_plans_count": 0}, now=ts_0002)
    assert 185.0 <= w_unblock <= 200.0


@pytest.mark.asyncio
async def test_sentinel_probe_reports_activation_and_records_fail_cooldown(monkeypatch):
    """验证探针前置上报日活事件，且新活动抢领遇 3012 失败时准确展示失败原因并写入冷却。"""
    acc = Account.create("zai", "acc_dom", "t1")
    acc.mode = "jwt"
    acc.jwt_token = "ey...jwt1"
    acc.status = Status.EXHAUSTED
    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc])

    events_order: list[str] = []

    async def mock_act(a):
        events_order.append(f"act:{a.name}")
        return None

    async def mock_preview(a):
        events_order.append(f"preview:{a.name}")
        return [{"plan_id": "zcode-v3-start-plan-trust-1001", "name": "ZCode Trust Build", "priority": 110, "grants": []}]

    async def mock_claim_all(a):
        return [{
            "ok": False,
            "plan_id": "zcode-v3-start-plan-trust-1001",
            "code": 3012,
            "message": "机房环境被上游风控拦截(3012)，请使用浏览器滑块手动领取（request has been blocked）",
        }]

    async def mock_bark(**kw):
        return True, "ok"

    monkeypatch.setattr("app.sentinel.report_activation_events", mock_act)
    monkeypatch.setattr("app.sentinel.preview_plans", mock_preview)
    monkeypatch.setattr("app.sentinel.auto_claim_all_plans", mock_claim_all)
    monkeypatch.setattr("app.sentinel.send_bark_notification", mock_bark)
    monkeypatch.setattr(asyncio, "sleep", _noop)

    s = Sentinel()
    res = await s.check_once()
    assert res["ok"] is True
    assert events_order == ["act:acc_dom", "preview:acc_dom"]
    assert res["claim_reports"] == [{
        "name": "acc_dom",
        "result": "失败(机房环境被上游风控拦截(3012)，请使用浏览器滑块手动领取)",
    }]
    assert (acc.id, "zcode-v3-start-plan-trust-1001") in s._catchup_cooldown
    # 即使自动抢领遇 3012 失败，账号的 claimable_plans 也必须保留该待领活动供前端渲染 Icon
    assert [p["plan_id"] for p in acc.claimable_plans] == ["zcode-v3-start-plan-trust-1001"]


@pytest.mark.asyncio
async def test_preview_and_failed_claim_retain_claimable_plans_for_ui(monkeypatch):
    """验证 preview_plans 探查即同步全池未持有账号的 claimable_plans，且 claim 遇 3012 失败保留待领项、成功或 1003 则剔除。"""
    from app import claim as claim_module

    acc_probe = Account.create("zai", "probe_acc", "t1")
    acc_probe.mode = "jwt"
    acc_probe.jwt_token = "ey...jwt1"
    acc_probe.status = Status.ACTIVE
    acc_probe.plans = [{"plan_id": "zcode-v3-start-plan-trust-1001", "name": "ZCode Trust Build"}]

    acc_unclaimed = Account.create("zai", "国内-5735", "t2")
    acc_unclaimed.mode = "jwt"
    acc_unclaimed.jwt_token = "ey...jwt2"
    acc_unclaimed.status = Status.EXHAUSTED
    acc_unclaimed.plans = []
    acc_unclaimed.claimable_plans = []

    monkeypatch.setattr(store, "list_accounts", lambda provider=None: [acc_probe, acc_unclaimed])
    monkeypatch.setattr(
        store,
        "find",
        lambda provider, acc_id: acc_probe if acc_id == acc_probe.id else (acc_unclaimed if acc_id == acc_unclaimed.id else None),
    )
    monkeypatch.setattr(store, "update_account", lambda a: True)

    async def mock_billing_req(account, method, path, **kwargs):
        if path == "/billing/preview":
            return {
                "code": 0,
                "data": {
                    "plans": [{
                        "plan_id": "zcode-v3-start-plan-trust-1001",
                        "name": "ZCode Trust Build",
                        "priority": 110,
                        "entitlements": [{
                            "meter": "model_usage",
                            "unit_type": "token",
                            "show_name": "GLM-5.3-Flash",
                            "grant_units": 100000000,
                            "period": "one_time",
                        }],
                    }]
                },
            }
        # 模拟 POST /billing/claim 命中 3012 风控拦截
        return {"code": 3012, "msg": "request has been blocked"}

    class _DummyCaptcha:
        async def get_verify_param(self):
            return "v-param", "cn"

        async def fetch_config(self):
            return {"region": "cn"}

        def invalidate(self):
            pass

    monkeypatch.setattr(claim_module, "_billing_request", mock_billing_req)
    monkeypatch.setattr(claim_module, "captcha_manager", _DummyCaptcha())

    # 1. 探针账号调用 preview_plans：自身已持有该活动故 claimable_plans 为空，而池内未持有的 国内-5735 立即获得待领项
    plans = await claim_module.preview_plans(acc_probe)
    assert len(plans) == 1
    assert acc_probe.claimable_plans == []
    assert [p["plan_id"] for p in acc_unclaimed.claimable_plans] == ["zcode-v3-start-plan-trust-1001"]

    # 2. 国内-5735 调用 claim 遭遇 3012 抛错：claimable_plans 依然保留，供页面展示活动领取 Icon
    acc_unclaimed.claimable_plans = []
    with pytest.raises(claim_module.ClaimError) as exc_info:
        await claim_module.claim(acc_unclaimed, report_activation=False)
    assert exc_info.value.code == 3012
    assert [p["plan_id"] for p in acc_unclaimed.claimable_plans] == ["zcode-v3-start-plan-trust-1001"]

    # 3. 当手动滑块领取成功（code=0）时，claimable_plans 中的该活动立即被剔除
    async def mock_billing_claim_ok(account, method, path, **kwargs):
        return {"code": 0, "data": {"plan": {"starts_at": 1700000000, "ends_at": 1700086400}}}

    monkeypatch.setattr(claim_module, "_billing_request", mock_billing_claim_ok)
    await claim_module.claim_with_captcha(acc_unclaimed, "slider-ok", "cn", "zcode-v3-start-plan-trust-1001")
    assert acc_unclaimed.claimable_plans == []


