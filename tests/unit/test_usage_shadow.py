"""额度旁路对照单测：只落库、不参与决策、失败隔离、统计口径。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.usage_shadow import UsageShadow, usage_shadow


def _accounts(monkeypatch: pytest.MonkeyPatch, n: int = 2) -> list:
    from app.models import Account
    from app.store import store

    accs = [
        Account(id=f"a{i}", name=f"账号{i}", provider="zai", mode="jwt",
                jwt_token="h.eyJzdWIiOiJ0ZXN0In0.sig")
        for i in range(n)
    ]
    monkeypatch.setattr(store, "list_accounts", lambda _p: accs)
    return accs


# ── 统计口径（曾在生产把 4 个成功账号记成失败的坑）────────────────────────


def test_empty_error_is_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常返回里 error 是空字符串，不能按「键是否存在」判失败。"""
    from app.channels import plugin_channel as mod
    from app.store import store

    _accounts(monkeypatch, 2)
    monkeypatch.setattr(mod.plugin_host, "is_alive", lambda: True)

    async def fake_usage(_acc):  # noqa: ANN001
        return {"plan": "ZCode Start Plan", "error": "", "windows": [{"name": "GLM-5.3"}]}

    monkeypatch.setattr(mod.plugin_host, "usage", fake_usage)
    written: dict = {}
    monkeypatch.setattr(store, "set_setting", lambda k, v: written.update({k: v}))

    out = asyncio.run(UsageShadow().run_once())
    assert set(out) == {"账号0", "账号1"}
    assert "error" in out["账号0"] and out["账号0"]["error"] == ""


def test_non_empty_error_counts_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import plugin_channel as mod
    from app.store import store

    _accounts(monkeypatch, 1)
    monkeypatch.setattr(mod.plugin_host, "is_alive", lambda: True)

    async def fake_usage(_acc):  # noqa: ANN001
        return {"error": "Start Plan has ended or was never started"}

    monkeypatch.setattr(mod.plugin_host, "usage", fake_usage)
    monkeypatch.setattr(store, "set_setting", lambda *_a: None)

    out = asyncio.run(UsageShadow().run_once())
    assert out["账号0"]["error"].startswith("Start Plan has ended")


# ── 失败隔离与零影响 ───────────────────────────────────────────────────────


def test_single_account_failure_does_not_abort_others(monkeypatch: pytest.MonkeyPatch) -> None:
    """单个账号抛异常时，其余账号继续对照，且异常被收进快照而不是抛出。"""
    from app.channels import plugin_channel as mod
    from app.store import store

    _accounts(monkeypatch, 3)

    async def fake_usage(acc):  # noqa: ANN001
        if acc.name == "账号1":
            raise RuntimeError("宿主超时")
        return {"plan": "p", "error": ""}

    monkeypatch.setattr(mod.plugin_host, "is_alive", lambda: True)
    monkeypatch.setattr(mod.plugin_host, "usage", fake_usage)
    monkeypatch.setattr(store, "set_setting", lambda *_a: None)

    out = asyncio.run(UsageShadow().run_once())
    assert len(out) == 3
    assert "宿主超时" in out["账号1"]["error"]
    assert out["账号0"].get("error") == ""


def test_run_once_skips_when_host_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主不可用时直接跳过，不发请求、不落库。"""
    from app.channels import plugin_channel as mod
    from app.store import store

    _accounts(monkeypatch, 2)
    monkeypatch.setattr(mod.plugin_host, "is_alive", lambda: False)

    called = []
    monkeypatch.setattr(store, "set_setting", lambda *_a: called.append(1))

    assert asyncio.run(UsageShadow().run_once()) == {}
    assert called == []


def test_persist_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """落库失败只记 warn，不影响对照本身返回。"""
    from app.channels import plugin_channel as mod
    from app.store import store

    _accounts(monkeypatch, 1)
    monkeypatch.setattr(mod.plugin_host, "is_alive", lambda: True)

    async def fake_usage(_acc):  # noqa: ANN001
        return {"error": ""}

    monkeypatch.setattr(mod.plugin_host, "usage", fake_usage)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("磁盘满")

    monkeypatch.setattr(store, "set_setting", boom)
    assert asyncio.run(UsageShadow().run_once()) is not None


# ── 原始格式不得改写 ───────────────────────────────────────────────────────


def test_payload_keeps_raw_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """原样保存插件返回，不做格式转换（转换会引入两边口径不一致的风险）。"""
    from app.channels import plugin_channel as mod
    from app.store import store

    _accounts(monkeypatch, 1)
    monkeypatch.setattr(mod.plugin_host, "is_alive", lambda: True)

    raw = {
        "plan": "ZCode Start Plan",
        "until": "2026-10-18T15:59:59.000Z",
        "error": "",
        "windows": [{"name": "GLM-5.3", "display": "5332 / 3000000",
                     "models": ["glm-5.3", "GLM-5.3-Trial"]}],
    }
    monkeypatch.setattr(mod.plugin_host, "usage", lambda _a: asyncio.sleep(0, result=raw))

    written: dict = {}
    monkeypatch.setattr(store, "set_setting", lambda k, v: written.update({k: v}))

    asyncio.run(UsageShadow().run_once())
    payload = json.loads(written["plugin_usage_shadow"])
    assert payload["accounts"]["账号0"] == raw, "必须原样保存"
    assert "at" in payload


def test_snapshot_reads_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.store import store

    monkeypatch.setattr(store, "get_setting",
                        lambda _k, _d=None: json.dumps({"at": 1.0, "accounts": {"x": {}}}))
    assert UsageShadow.snapshot()["accounts"] == {"x": {}}

    monkeypatch.setattr(store, "get_setting", lambda _k, _d=None: "不是 JSON")
    assert UsageShadow.snapshot() == {}


def test_start_is_noop_on_native_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """native 模式下不启动：宿主没起来，跑了只会每次失败并刷 warn。"""
    from app import usage_shadow as mod

    monkeypatch.setattr(mod.settings, "REQUEST_CHANNEL", "native")
    probe = UsageShadow()
    probe.start()
    assert probe._task is None


def test_singleton_exported() -> None:
    assert isinstance(usage_shadow, UsageShadow)
