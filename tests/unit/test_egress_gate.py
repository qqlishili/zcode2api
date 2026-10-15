"""门禁单测：探针 confirmed 集合的维护，以及请求侧只走已确认出口。"""
from __future__ import annotations

import asyncio

import httpx
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
    monkeypatch.setattr(account_client_pool, "resolve_proxy", lambda _a: bound["cur"])

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
    """跑一次 ch.send，返回宿主实际拿到的 proxy 列表。

    confirmed 现在是 **(账号 id, 出口) 组合** 的集合（不再是出口集合）——
    实测同出口对不同账号结果不同，门禁必须按组合判定。
    """
    host = _CapturingHost()
    ch = PluginChannel(host)  # type: ignore[arg-type]
    monkeypatch.setattr(mod.settings, "EGRESS_MODE", mode)

    from app.client_pool import account_client_pool

    monkeypatch.setattr(account_client_pool, "client_proxy", lambda _c: candidate)
    monkeypatch.setattr(egress_probe.egress_probe, "_confirmed", confirmed)

    # payload 是原始请求体字节；base64 编码在 PluginHost.fetch 内部完成
    payload = b'{"model":"GLM-5.3-Flash"}'
    client = httpx.AsyncClient()

    async def run() -> None:
        try:
            await ch.send(_account(), "https://x/", {}, payload, client)
        except RuntimeError:
            pass
        finally:
            await client.aclose()

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
    assert seen == [None], "别的账号确认过的出口，对本账号不算确认"


def test_gate_falls_back_to_direct_when_unconfirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """proxy 模式但该出口未被确认 → 直连兜底，绝不拿未验证端口去撞。

    这是本门禁存在的理由：断路器在进程内为空时信任所有端口，重启后的首批请求
    会挨个撞上已被拉黑的绑定端口并把账号打成风控禁用。
    """
    seen = _call_send(monkeypatch, "proxy", "http://127.0.0.1:21101", {"http://127.0.0.1:21200"})
    assert seen == [None]


def test_gate_falls_back_to_direct_when_no_result_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    """探针还没跑出结果（confirmed 为空）→ 直连兜底。"""
    seen = _call_send(monkeypatch, "proxy", "http://127.0.0.1:21200", set())
    assert seen == [None]
