"""vendor_check 单测：两处比对逻辑、只告警不自动应用、应用失败回滚。

不发起真实网络/ git：用替身验证决策与副作用。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import vendor_check as mod
from app.vendor_check import VendorCheck


def _patch_git(monkeypatch: pytest.MonkeyPatch, table: dict[tuple, tuple]) -> list[list[str]]:
    """替换底层 git 调用。table 的键是参数字典首项元组。"""
    calls: list[list[str]] = []

    async def fake_git(args, cwd=None):  # noqa: ANN001, ARG001
        calls.append(list(args))
        for pattern, result in table.items():
            if all(p in args for p in pattern):
                return result
        return 1, "", "no-match"

    monkeypatch.setattr(mod, "_git", fake_git)
    return calls


# ── 插件比对 ───────────────────────────────────────────────────────────────


def test_plugin_reports_update(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_git(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa111", ""),
        ("config",): (0, "https://example.invalid/p.git", ""),
        ("ls-remote",): (0, "bbb222\tHEAD", ""),
    })
    out = asyncio.run(VendorCheck().check_plugin())
    assert out["ok"] is True
    assert out["update_available"] is True
    assert out["current"] == "aaa111" and out["remote"] == "bbb222"


def test_plugin_no_update_when_same(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_git(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "same123", ""),
        ("config",): (0, "https://example.invalid/p.git", ""),
        ("ls-remote",): (0, "same123\tHEAD", ""),
    })
    out = asyncio.run(VendorCheck().check_plugin())
    assert out["update_available"] is False


def test_plugin_handles_git_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """ls-remote 失败（无网/仓库不可达）不能抛异常，只报 ok=False。"""
    _patch_git(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa111", ""),
        ("config",): (0, "https://example.invalid/p.git", ""),
        ("ls-remote",): (128, "", "could not resolve host"),
    })
    out = asyncio.run(VendorCheck().check_plugin())
    assert out["ok"] is False
    assert "ls-remote 失败" in out["error"]


def test_plugin_handles_missing_submodule_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_git(monkeypatch, {
        ("rev-parse", "HEAD"): (0, "aaa111", ""),
        ("config",): (1, "", "not found"),
    })
    out = asyncio.run(VendorCheck().check_plugin())
    assert out["ok"] is False
    assert "submodule URL" in out["error"]


# ── host.js 比对 ───────────────────────────────────────────────────────────


def test_hostjs_reports_difference(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import plugin_channel

    monkeypatch.setattr(plugin_channel, "host_source_sha256", lambda: "localAAA")
    monkeypatch.setattr(VendorCheck, "_fetch_remote_sha256",
                        lambda self: asyncio.sleep(0, result="remoteBBB"))
    out = asyncio.run(VendorCheck().check_hostjs())
    assert out["ok"] is True and out["update_available"] is True


def test_hostjs_same(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import plugin_channel

    monkeypatch.setattr(plugin_channel, "host_source_sha256", lambda: "same")
    monkeypatch.setattr(VendorCheck, "_fetch_remote_sha256",
                        lambda self: asyncio.sleep(0, result="same"))
    out = asyncio.run(VendorCheck().check_hostjs())
    assert out["update_available"] is False


def test_hostjs_local_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import plugin_channel

    monkeypatch.setattr(plugin_channel, "host_source_sha256", lambda: "")
    out = asyncio.run(VendorCheck().check_hostjs())
    assert out["ok"] is False


def test_hostjs_remote_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import plugin_channel

    monkeypatch.setattr(plugin_channel, "host_source_sha256", lambda: "localAAA")
    monkeypatch.setattr(VendorCheck, "_fetch_remote_sha256",
                        lambda self: asyncio.sleep(0, result=None))
    out = asyncio.run(VendorCheck().check_hostjs())
    assert out["ok"] is False
    assert out["local"] == "localAAA"


# ── 只告警、不自动应用 ─────────────────────────────────────────────────────


def test_run_once_persists_and_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store import store

    vc = VendorCheck()
    monkeypatch.setattr(vc, "check_plugin",
                        lambda: asyncio.sleep(0, result={"ok": True, "update_available": True,
                                                         "current": "a" * 40, "remote": "b" * 40}))
    monkeypatch.setattr(vc, "check_hostjs",
                        lambda: asyncio.sleep(0, result={"ok": True, "update_available": False}))

    written: dict = {}
    monkeypatch.setattr(store, "set_setting", lambda k, v: written.update({k: v}))
    alerted: list = []
    monkeypatch.setattr(vc, "_alert",
                        lambda *a, **k: alerted.append(a) or asyncio.sleep(0))

    asyncio.run(vc.run_once())
    assert "vendor_status" in written
    assert json.loads(written["vendor_status"])["plugin"]["update_available"] is True
    assert len(alerted) == 1, "检测到更新时必须告警"


def test_run_once_silent_when_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store import store

    vc = VendorCheck()
    monkeypatch.setattr(vc, "check_plugin",
                        lambda: asyncio.sleep(0, result={"ok": True, "update_available": False}))
    monkeypatch.setattr(vc, "check_hostjs",
                        lambda: asyncio.sleep(0, result={"ok": True, "update_available": False}))
    monkeypatch.setattr(store, "set_setting", lambda *_a: None)
    alerted: list = []
    monkeypatch.setattr(vc, "_alert", lambda *a, **k: alerted.append(a) or asyncio.sleep(0))

    asyncio.run(vc.run_once())
    assert alerted == [], "无更新时不打扰"


def test_run_once_never_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """核心约束：run_once 只检查与告警，绝不调用 apply。"""
    from app.store import store

    vc = VendorCheck()
    monkeypatch.setattr(vc, "check_plugin",
                        lambda: asyncio.sleep(0, result={"ok": True, "update_available": True}))
    monkeypatch.setattr(vc, "check_hostjs",
                        lambda: asyncio.sleep(0, result={"ok": True, "update_available": True}))
    monkeypatch.setattr(store, "set_setting", lambda *_a: None)
    monkeypatch.setattr(vc, "_alert", lambda *a, **k: asyncio.sleep(0))

    applied: list = []
    monkeypatch.setattr(vc, "apply_plugin_update",
                        lambda: applied.append(1) or asyncio.sleep(0, result={}))

    asyncio.run(vc.run_once())
    assert applied == [], "自动应用会绕过人工确认，必须禁止"


# ── 应用与回滚 ─────────────────────────────────────────────────────────────


def test_apply_rolls_back_when_host_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """更新后宿主起不来 → 必须回滚 submodule 并再起一次。"""
    seq: list[list[str]] = []

    async def fake_git(args, cwd=None):  # noqa: ANN001, ARG001
        seq.append(list(args))
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, "before1", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "_git", fake_git)

    from app.channels import base as chbase

    calls: list[str] = []

    async def fake_stop(*, drain: bool = False) -> None:  # noqa: ARG001
        calls.append("stop")

    async def fake_start() -> None:
        calls.append("start")
        return None      # 启动失败

    monkeypatch.setattr(chbase, "stop_plugin_host", fake_stop)
    monkeypatch.setattr(chbase, "start_plugin_host", fake_start)

    out = asyncio.run(VendorCheck().apply_plugin_update())
    assert out["ok"] is False
    assert "回滚" in out["error"]
    assert any(a[:2] == ["reset", "--hard"] and "before1" in a for a in seq), "必须 reset 回原 commit"
    assert calls.count("stop") >= 2 and calls.count("start") >= 2


def test_apply_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_git(args, cwd=None):  # noqa: ANN001, ARG001
        if args[:2] == ["rev-parse", "HEAD"]:
            return 0, "abc123", ""
        return 0, "", ""

    monkeypatch.setattr(mod, "_git", fake_git)
    from app.channels import base as chbase

    async def fake_stop(*, drain: bool = False) -> None:  # noqa: ARG001
        return None

    async def fake_start() -> dict:
        return {"plugins": []}

    monkeypatch.setattr(chbase, "stop_plugin_host", fake_stop)
    monkeypatch.setattr(chbase, "start_plugin_host", fake_start)

    out = asyncio.run(VendorCheck().apply_plugin_update())
    assert out["ok"] is True


# ── 原始文件读取与快照 ─────────────────────────────────────────────────────


def test_snapshot_tolerates_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store import store

    monkeypatch.setattr(store, "get_setting", lambda *_a, **_k: "不是 JSON")
    assert VendorCheck.snapshot() == {}
