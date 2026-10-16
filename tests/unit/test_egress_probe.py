"""出口探针单测：候选筛选、探测结果写回断路器、判据语义、失败隔离。

不发起真实网络请求：plugin_host 用替身，只验证探针自身的决策与副作用。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app import egress_probe
from app.egress_probe import EgressProbe, _candidate_egresses, _probe_accounts
from app.models import Account

_JWT = "h.eyJzdWIiOiJ0ZXN0In0.sig"


def _account(acc_id: str, name: str = "账号") -> Account:
    return Account(id=acc_id, name=name, provider="zai", mode="jwt", jwt_token=_JWT)


# ── 候选筛选 ───────────────────────────────────────────────────────────────


def test_probe_accounts_accepts_exhausted_but_valid_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """额度耗尽的账号凭证仍有效，且 max_tokens=1 的探测不烧额度，是最低成本身份。"""
    exhausted = _account("a1")
    exhausted.status = "exhausted"
    disabled = _account("a2")
    disabled.enabled = False
    apikey = Account(id="a3", name="k", provider="zai", mode="apiKey", api_key="sk")

    monkeypatch.setattr(egress_probe, "_probe_accounts",
                        lambda: [a for a in (exhausted, disabled, apikey)
                                 if a.enabled and a.mode == "jwt" and (a.jwt_token or "").strip()] or [])
    # 直接验证筛选条件本身
    from app.store import store

    monkeypatch.setattr(store, "list_accounts", lambda _p: [exhausted, disabled, apikey])
    got = _probe_accounts()
    assert [a.id for a in got] == ["a1"], "耗尽账号可用；禁用与非 jwt 不可用"


def test_candidate_egresses_scoped_to_used_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    """只探账号实际使用的大区，且排除已隔离节点。

    为什么要限定大区：出口按大区绑定，且隔离时只在本区环形避让（不跨区）。
    探未使用的大区等于无用流量——实测全局池 127 个，账号实际只涉及 32 个。
    """
    from app.client_pool import account_client_pool
    from app.store import store

    acc = _account("a1")
    acc.assigned_region = "HK"
    monkeypatch.setattr(store, "list_accounts", lambda _p: [acc])

    registry = {
        "HK": ["http://127.0.0.1:21100", "http://127.0.0.1:21101"],
        # 未使用的大区，其端口不应出现在候选里
        "US": ["http://127.0.0.1:21500"],
    }
    monkeypatch.setattr(account_client_pool, "load_registry", lambda: registry)
    monkeypatch.setattr(account_client_pool, "proxy_eligible", lambda _p: True)
    monkeypatch.setattr(account_client_pool, "is_proxy_quarantined",
                        lambda p: p.endswith(":21100"))

    got = _candidate_egresses()
    assert got == ["http://127.0.0.1:21101"], f"应只含 HK 未隔离端口，实际 {got}"


def test_candidate_egresses_falls_back_to_global_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """账号都没绑大区时退回全局池（与 resolve_proxy 的第三条回退路径一致）。"""
    from app.client_pool import account_client_pool
    from app.store import store

    acc = _account("a1")  # assigned_region 为空
    monkeypatch.setattr(store, "list_accounts", lambda _p: [acc])
    monkeypatch.setattr(egress_probe.settings, "ZCODE_PROXIES",
                        ["http://127.0.0.1:1", "http://127.0.0.1:2"])
    monkeypatch.setattr(account_client_pool, "proxy_eligible", lambda _p: True)
    monkeypatch.setattr(account_client_pool, "is_proxy_quarantined", lambda _p: False)

    assert _candidate_egresses() == ["http://127.0.0.1:1", "http://127.0.0.1:2"]


# ── 探测与写回 ─────────────────────────────────────────────────────────────


class _FakeCm:
    def __init__(self, status: int, body: bytes) -> None:
        self._status = status
        self._body = body

    async def __aenter__(self) -> httpx.Response:
        return httpx.Response(self._status, content=self._body,
                              request=httpx.Request("POST", "https://x/"))

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeHost:
    def __init__(self, status: int, body: bytes) -> None:
        self._status, self._body = status, body

    def is_alive(self) -> bool:
        return True

    def head_of(self, _fid: int) -> dict:
        return {"status": self._status, "headers": {}}

    async def fetch(self, **_kw: object) -> _FakeCm:
        return _FakeCm(self._status, self._body)


@pytest.mark.parametrize(
    ("status", "body", "expect_reachable"),
    [
        # 200 正常受理 → 出口可达
        (200, b'{"type":"message"}', True),
        # 402/额度耗尽：归一后 <500，说明链路端到端打通（连接、受理、鉴权都过了）→ 可达
        (402, b'{"code":1005,"msg":"quota"}', True),
        # 403 验证码挑战：同上，出口本身是好的 → 可达
        (403, b'{"code":3007,"msg":"captcha"}', True),
        # 405/3012 风控：**不可达** —— 这正是「该出口已被上游拉黑」，探针的首要判据
        (405, b'{"code":3012,"msg":"unusual activity"}', False),
        (405, b'{"error":"forbidden"}', False),
        # 500 上游故障：无法证明出口可用 → 不可达
        (500, b'{"error":"boom"}', False),
        (503, b'{"error":"unavailable"}', False),
    ],
)
def test_probe_classification(
    monkeypatch: pytest.MonkeyPatch, status: int, body: bytes, expect_reachable: bool
) -> None:
    """判据：200 / 402 / 403 可达（障碍在账号侧）；3012、405、5xx 不可达。"""
    from app.channels import plugin_channel as mod

    monkeypatch.setattr(mod, "plugin_host", _FakeHost(status, body))
    monkeypatch.setattr(egress_probe.settings, "EGRESS_MODE", "direct")

    reachable, detail, extra = asyncio.run(egress_probe._probe_one(_account("a1"), "http://127.0.0.1:1"))
    assert reachable is expect_reachable, f"{status} → {detail}"
    assert extra.get("status") == status


def test_probe_requires_live_plugin_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """无 plugin 通道时不探测：native 在当前规则下必然 405，用它探测会误判全部出口。"""
    from app.channels import plugin_channel as mod

    class _Dead:
        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(mod, "plugin_host", _Dead())
    reachable, detail, _ = asyncio.run(egress_probe._probe_one(_account("a1"), "http://127.0.0.1:1"))
    assert reachable is False
    assert "plugin 通道未就绪" in detail


def test_run_once_feeds_circuit_breaker(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测结果必须交给 record_proxy_result（复用既有断路器，不另建白名单）。"""
    from app.channels import plugin_channel as mod
    from app.client_pool import account_client_pool

    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: [_account("a1")])
    monkeypatch.setattr(egress_probe, "_candidate_egresses", lambda: ["http://127.0.0.1:1"])
    # run_once 现在用 resolve_proxy 决定"该账号真正会走的出口"（与生产同一算法）
    monkeypatch.setattr(account_client_pool, "resolve_proxy",
                        lambda _a, **_kw: "http://127.0.0.1:1")
    monkeypatch.setattr(mod, "plugin_host", _FakeHost(200, b'{"type":"message"}'))

    calls: list[tuple] = []
    monkeypatch.setattr(account_client_pool, "record_proxy_result",
                        lambda p, success, is_waf_block=False, **_kw: calls.append((p, success, is_waf_block)))
    monkeypatch.setattr(egress_probe.EgressProbe, "_persist", lambda self, _r: None)

    out = asyncio.run(EgressProbe().run_once())
    assert calls == [("http://127.0.0.1:1", True, False)]
    assert out["http://127.0.0.1:1"]["reachable"] is True


def test_run_once_records_failure_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import plugin_channel as mod
    from app.client_pool import account_client_pool

    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: [_account("a1")])
    monkeypatch.setattr(egress_probe, "_candidate_egresses", lambda: ["http://127.0.0.1:1"])
    monkeypatch.setattr(account_client_pool, "resolve_proxy",
                        lambda _a, **_kw: "http://127.0.0.1:1")
    monkeypatch.setattr(mod, "plugin_host", _FakeHost(502, b'{"error":"bad gateway"}'))

    calls: list[tuple] = []
    monkeypatch.setattr(account_client_pool, "record_proxy_result",
                        lambda p, success, is_waf_block=False, **_kw: calls.append((p, success)))
    monkeypatch.setattr(egress_probe.EgressProbe, "_persist", lambda self, _r: None)

    asyncio.run(EgressProbe().run_once())
    assert calls == [("http://127.0.0.1:1", False)], "502 必须记为失败，才能驱动断路器隔离"


def test_run_once_without_accounts_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """池内无有效凭证时跳过，不得抛错（探测是尽力而为的后台任务）。"""
    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: [])
    assert asyncio.run(EgressProbe().run_once()) == {}


def test_each_account_probed_on_its_own_bound_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个账号测**它真正会走**的出口（resolve_proxy），不是 round-robin。

    旧实现把「候选出口第 i 个」配给「账号第 i 个」，与生产的一致性哈希选路错位
    （实测 13 个账号里 12 个不一致）。本用例钉死新语义。
    """
    from app.channels import plugin_channel as mod
    from app.client_pool import account_client_pool

    accs = [_account("a1", "甲"), _account("a2", "乙")]
    bound = {"a1": "http://127.0.0.1:1", "a2": "http://127.0.0.1:2"}
    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: accs)
    monkeypatch.setattr(egress_probe, "_candidate_egresses",
                        lambda: ["http://127.0.0.1:1", "http://127.0.0.1:2"])
    monkeypatch.setattr(account_client_pool, "resolve_proxy",
                        lambda a, **_kw: bound[a.id])
    monkeypatch.setattr(mod, "plugin_host", _FakeHost(200, b'{"type":"message"}'))
    monkeypatch.setattr(account_client_pool, "record_proxy_result",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(egress_probe.EgressProbe, "_persist", lambda self, _r: None)

    probe = EgressProbe()
    out = asyncio.run(probe.run_once())

    # 两个账号各自测自己的出口
    assert out["http://127.0.0.1:1"]["account"] == "甲"
    assert out["http://127.0.0.1:2"]["account"] == "乙"
    # 确认集记录的是 (账号, 出口) 组合
    assert probe.is_confirmed("a1", "http://127.0.0.1:1") is True
    assert probe.is_confirmed("a2", "http://127.0.0.1:1") is False, "a2 没走过 :1，不该被确认"


# ── WAF 封禁识别：3012/405 必须按 is_waf_block 上报（阈值 1，不是默认 3）────


def test_is_waf_block_recognizes_3012_and_405() -> None:
    """3012 与 405 是「出口被上游拉黑」的明确信号，不是普通网络失败。"""
    assert egress_probe._is_waf_block(405, "3012") is True
    assert egress_probe._is_waf_block(405, "") is True
    assert egress_probe._is_waf_block(403, "") is True
    # 普通故障不算 WAF
    assert egress_probe._is_waf_block(502, "") is False
    assert egress_probe._is_waf_block(200, "1005") is False
    assert egress_probe._is_waf_block(0, "") is False


def test_run_once_reports_waf_block_for_3012(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针遇到 3012 时必须带 is_waf_block=True，否则要 3 轮才熔断。

    实测踩到：服务重启后内存态清零，若按默认阈值 3，需要 3 轮（30 分钟）才隔离
    坏出口，期间所有账号都被分到它们（2026-10-15）。
    """
    from app.channels import plugin_channel as mod
    from app.client_pool import account_client_pool

    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: [_account("a1")])
    monkeypatch.setattr(egress_probe, "_candidate_egresses", lambda: ["http://127.0.0.1:1"])
    monkeypatch.setattr(account_client_pool, "resolve_proxy", lambda _a, **_kw: "http://127.0.0.1:1")
    monkeypatch.setattr(mod, "plugin_host", _FakeHost(405, b'{"code":3012,"msg":"risk"}'))

    calls: list[tuple] = []
    monkeypatch.setattr(account_client_pool, "record_proxy_result",
                        lambda p, success, is_waf_block=False, **_kw: calls.append((p, success, is_waf_block)))
    monkeypatch.setattr(egress_probe.EgressProbe, "_persist", lambda self, _r: None)

    asyncio.run(egress_probe.EgressProbe().run_once())
    assert calls == [("http://127.0.0.1:1", False, True)], "3012 必须按 WAF 封禁上报"


def test_run_once_reports_non_waf_for_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """502 是普通上游故障，不应按 WAF 上报（阈值保持 3）。"""
    from app.channels import plugin_channel as mod
    from app.client_pool import account_client_pool

    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: [_account("a1")])
    monkeypatch.setattr(egress_probe, "_candidate_egresses", lambda: ["http://127.0.0.1:1"])
    monkeypatch.setattr(account_client_pool, "resolve_proxy", lambda _a, **_kw: "http://127.0.0.1:1")
    monkeypatch.setattr(mod, "plugin_host", _FakeHost(502, b'{"error":"bad gateway"}'))

    calls: list[tuple] = []
    monkeypatch.setattr(account_client_pool, "record_proxy_result",
                        lambda p, success, is_waf_block=False, **_kw: calls.append((p, success, is_waf_block)))
    monkeypatch.setattr(egress_probe.EgressProbe, "_persist", lambda self, _r: None)

    asyncio.run(egress_probe.EgressProbe().run_once())
    assert calls == [("http://127.0.0.1:1", False, False)]


def test_waf_threshold_is_one_shot() -> None:
    """WAF 封禁阈值必须是 1 —— 见到 3012 立即熔断。

    依据（2026-10-15 实测，各 4-6 次连续探测）：
      坏组合 5735@21200 → 405/3012 ×4（4/4 一致）
      好组合 9149@21200 → 200/1005 ×6（6/6 一致）
    即 3012 是确定性组合判定而非抖动，误杀风险极低。
    """
    from app.client_pool import account_client_pool

    p = "http://127.0.0.1:1"
    account_client_pool._circuit_breakers.pop(p, None)
    # 一次 WAF 失败即应熔断
    account_client_pool.record_proxy_result(p, success=False, is_waf_block=True)
    assert account_client_pool.is_proxy_quarantined(p) is True, "WAF 应一次即熔断"

    # 对照：普通失败一次不熔断（阈值仍为 3）
    q = "http://127.0.0.1:2"
    account_client_pool._circuit_breakers.pop(q, None)
    account_client_pool.record_proxy_result(q, success=False, is_waf_block=False)
    assert account_client_pool.is_proxy_quarantined(q) is False, "普通失败需累计 3 次"

    # 清理，避免影响其他用例
    account_client_pool._circuit_breakers.pop(p, None)
    account_client_pool._circuit_breakers.pop(q, None)
