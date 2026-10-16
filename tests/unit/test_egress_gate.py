"""门禁单测：探针 confirmed 集合的维护，以及请求侧只走已确认出口。"""
from __future__ import annotations

import asyncio

import pytest

from app import egress_probe
from app.channels import plugin_channel as mod
from app.channels.plugin_channel import PluginChannel
from app.egress_probe import EgressProbe
from app.models import Account


def _account(acc_id: str = "acc-1") -> Account:
    return Account(id=acc_id, name="测试账号", provider="zai", mode="jwt",
                   jwt_token="h.eyJzdWIiOiJ0ZXN0In0.sig")


# ── 探针侧：confirmed 集合 ─────────────────────────────────────────────────


def test_confirmed_starts_empty() -> None:
    """尚无探测结果时集合为空 —— 请求侧据此直连兜底，而不是拿未验证的端口去撞。"""
    assert EgressProbe().confirmed() == set()


def test_is_confirmed_rejects_none_and_unknown() -> None:
    p = EgressProbe()
    # 组合语义：(账号 id, 出口)
    p._confirmed = {("acc-1", "http://127.0.0.1:21200")}
    assert p.is_confirmed("acc-1", "http://127.0.0.1:21200") is True
    assert p.is_confirmed("acc-1", "http://127.0.0.1:21100") is False
    # 关键：同出口、不同账号 → 未确认（实测同出口对不同账号结果不同）
    assert p.is_confirmed("acc-2", "http://127.0.0.1:21200") is False
    assert p.is_confirmed("acc-1", None) is False
    assert p.is_confirmed("", "http://127.0.0.1:21200") is False


def test_confirmed_is_replaced_not_merged(monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: D401
    """每轮整体替换：并集会让已失效端口永远留在白名单里。"""
    from app.client_pool import account_client_pool

    acc = _account()
    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: [acc])
    monkeypatch.setattr(egress_probe, "_candidate_egresses",
                        lambda: ["http://127.0.0.1:1", "http://127.0.0.1:2"])
    monkeypatch.setattr(account_client_pool, "record_proxy_result", lambda *_a, **_k: None)
    monkeypatch.setattr(EgressProbe, "_persist", lambda self, _r: None)

    probe = EgressProbe()
    calls = {"n": 0}
    # 每轮都让该账号走 :1（模拟 resolve_proxy 的稳定绑定）
    bound = {"cur": "http://127.0.0.1:1"}
    monkeypatch.setattr(account_client_pool, "resolve_proxy", lambda _a, **_kw: bound["cur"])

    async def fake(_account_arg, proxy):  # noqa: ANN001, ARG001
        calls["n"] += 1
        # 第一轮可达；第二轮该出口失效
        ok = calls["n"] == 1 or not proxy.endswith(":1")
        return ok, "stub", {}

    monkeypatch.setattr(egress_probe, "_probe_one", fake)

    asyncio.run(probe.run_once())
    assert probe.confirmed() == {(acc.id, "http://127.0.0.1:1")}

    asyncio.run(probe.run_once())
    assert probe.confirmed() == set(), "第二轮失效的组合必须被移除（整体替换，不并集）"


def test_confirmed_persists_to_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测结果落 DB meta（管理页观测用），且不影响集合语义。"""
    from app.client_pool import account_client_pool
    from app.store import store

    monkeypatch.setattr(egress_probe, "_probe_accounts", lambda: [_account()])
    monkeypatch.setattr(egress_probe, "_candidate_egresses", lambda: ["http://127.0.0.1:9"])
    monkeypatch.setattr(account_client_pool, "record_proxy_result", lambda *_a, **_k: None)
    monkeypatch.setattr(egress_probe, "_probe_one",
                        lambda *a, **k: asyncio.sleep(0, result=(True, "ok", {})))

    written: dict = {}
    monkeypatch.setattr(store, "set_setting",
                        lambda k, v: written.update({k: v}))

    asyncio.run(EgressProbe().run_once())
    assert "egress_probe" in written


# ── 请求侧：门禁 ───────────────────────────────────────────────────────────


class _CapturingHost:
    """记录 fetch 收到的 proxy，供断言门禁行为。"""

    def __init__(self) -> None:
        self.seen: list[str | None] = []

    def is_alive(self) -> bool:
        return True

    async def fetch(self, **kw: object) -> object:
        self.seen.append(kw.get("proxy"))  # type: ignore[arg-type]
        raise RuntimeError("stop-after-capture")


def _call_send(monkeypatch: pytest.MonkeyPatch, mode: str, candidate: str | None,
               confirmed: set[tuple[str, str]]) -> list[str | None]:
    """实际出口由连接池准入；门禁不得重新选路，失败不得调用宿主。"""
    from app.client_pool import RegionOfflineError, account_client_pool

    host = _CapturingHost()
    ch = PluginChannel(host)
    monkeypatch.setattr(mod.settings, "EGRESS_MODE", mode)
    monkeypatch.setattr(egress_probe.egress_probe, "_confirmed", confirmed)
    payload = b'{"model":"GLM-5.3-Flash"}'

    async def run() -> None:
        account = _account()
        account.proxy = candidate
        async with account_client_pool.operation(account, purpose="chat") as operation:
            unavailable = operation.proxy and (account.id, operation.proxy) not in confirmed
            if unavailable:
                with pytest.raises(RegionOfflineError):
                    await ch.send(account, "https://unused.invalid", {}, payload, operation.client)
            else:
                # 替身宿主用 RuntimeError 中断，不发生网络外呼。
                with pytest.raises(RuntimeError):
                    await ch.send(account, "https://unused.invalid", {}, payload, operation.client)

    asyncio.run(run())
    return host.seen


def test_gate_direct_mode_forces_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    """direct 模式忽略候选出口，一律直连。"""
    # direct 模式：即使组合已确认也一律直连
    seen = _call_send(monkeypatch, "direct", "http://127.0.0.1:21200",
                      {("acc-1", "http://127.0.0.1:21200")})
    assert seen == [None]


def test_gate_uses_confirmed_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """proxy 模式且**该账号的该出口组合**已确认 → 走代理。"""
    seen = _call_send(monkeypatch, "proxy", "http://127.0.0.1:21200",
                      {("acc-1", "http://127.0.0.1:21200")})
    assert seen == ["http://127.0.0.1:21200"]


def test_gate_rejects_same_proxy_for_other_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """同出口被**别的账号**确认过，不能给本账号用（组合语义的核心）。"""
    seen = _call_send(monkeypatch, "proxy", "http://127.0.0.1:21200",
                      {("acc-999", "http://127.0.0.1:21200")})
    assert seen == [], "别的账号确认过的出口，对本账号不算确认，不能直连兜底"


def test_gate_rejects_unconfirmed_without_direct_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """未确认组合必须拒绝，宿主不能收到一次直连请求。"""
    seen = _call_send(monkeypatch, "proxy", "http://127.0.0.1:21101", {("acc-1", "http://127.0.0.1:21200")})
    assert seen == []


def test_gate_rejects_until_first_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针尚未跑出结果时明确不可用。"""
    seen = _call_send(monkeypatch, "proxy", "http://127.0.0.1:21200", set())
    assert seen == []
