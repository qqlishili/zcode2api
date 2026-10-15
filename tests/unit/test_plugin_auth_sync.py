"""plugin_auth 一致性单测：指纹、stale 判定、入口接线、一键同步。"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import tempfile

import pytest

from app.channels import plugin_auth
from app.models import Account
from app.store import store


def _account(acc_id: str = "acc-1", name: str = "账号") -> Account:
    """账号必须带 device_mid：缺它会被 build_auth_doc 跳过（不编造随机设备）。"""
    acc = Account(id=acc_id, name=name, provider="zai", mode="jwt",
                  jwt_token="h.eyJzdWIiOiJ0ZXN0In0.sig")
    # device 由 id 决定，保证同一账号多次构造内容一致（指纹确定性）
    acc.fingerprint = {"device_mid": f"00000000-0000-4000-8000-{abs(hash(acc_id)) % 10**12:012d}"}
    return acc


def set_accounts(accounts: list) -> None:
    store.list_accounts = lambda _p="zai": accounts  # type: ignore[assignment]


@pytest.fixture()
def isolated(monkeypatch: pytest.MonkeyPatch):
    """把凭证文件与 DB meta 都换成临时实现。"""
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "plugin-auth.json")
    monkeypatch.setattr(plugin_auth.settings, "PLUGIN_AUTH_PATH", path)

    meta: dict = {}
    monkeypatch.setattr(store, "get_setting", lambda k, d=None: meta.get(k, d))
    monkeypatch.setattr(store, "set_setting", lambda k, v: meta.update({k: v}))
    return meta, path


# ── 指纹 ──────────────────────────────────────────────────────────────────


def test_fingerprint_ignores_key_order() -> None:
    """内容哈希必须与键顺序无关（否则 DB 读取顺序变化会假报 stale）。"""
    a = {"x": {"a": 1}, "y": {"b": 2}}
    b = {"y": {"b": 2}, "x": {"a": 1}}
    assert plugin_auth._fingerprint(a) == plugin_auth._fingerprint(b)


def test_fingerprint_changes_with_content() -> None:
    assert plugin_auth._fingerprint({"x": {"a": 1}}) != plugin_auth._fingerprint({"x": {"a": 2}})


# ── 落盘 ──────────────────────────────────────────────────────────────────


def test_write_records_fingerprint_and_sets_600(isolated) -> None:
    meta, path = isolated
    set_accounts([_account("acc-1")])
    plugin_auth.write_plugin_auth()

    assert os.path.exists(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert meta.get("plugin_auth_fingerprint"), "必须记录内容指纹"


def test_doc_roundtrip_is_valid_json(isolated) -> None:
    """落盘内容必须是合法 JSON（曾因 Path 未转 str 出过问题）。"""
    set_accounts([_account("acc-1")])
    path = plugin_auth.write_plugin_auth()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert plugin_auth.slot_of("acc-1") in data


def test_build_doc_skips_disabled_and_apikey(isolated) -> None:
    off = _account("acc-off")
    off.enabled = False
    key_mode = Account(id="acc-key", name="k", provider="zai", mode="apiKey", api_key="sk")
    set_accounts([_account("acc-on"), off, key_mode])

    doc = plugin_auth.build_auth_doc()
    assert set(doc) == {plugin_auth.slot_of("acc-on")}


def test_refresh_swallows_errors(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    """重建失败只记 warn：凭证文件不是请求路径上的硬依赖。"""
    def boom() -> object:
        raise OSError("磁盘满")

    monkeypatch.setattr(plugin_auth, "write_plugin_auth", boom)
    plugin_auth.refresh_plugin_auth()   # 不抛即通过


# ── stale 判定 ────────────────────────────────────────────────────────────


def test_not_stale_before_any_load(isolated) -> None:
    """从未启动过宿主时不报 stale（没有「已加载」基线可比）。"""
    plugin_auth.write_plugin_auth()
    assert plugin_auth.is_stale() is False


def test_stale_after_account_change(isolated) -> None:
    """新增账号 → 重建文件 → 指纹变了 → 报 stale。"""
    set_accounts([_account("acc-1")])
    plugin_auth.write_plugin_auth()
    plugin_auth.mark_loaded()
    assert plugin_auth.is_stale() is False

    set_accounts([_account("acc-1"), _account("acc-2", "新账号")])
    plugin_auth.refresh_plugin_auth()
    assert plugin_auth.is_stale() is True, "新增账号后必须报 stale"


def test_not_stale_when_content_returns_to_same(isolated) -> None:
    """加一个账号再删掉 → 内容相同 → 不该报 stale（避免假提示）。"""
    set_accounts([_account("acc-1")])
    plugin_auth.write_plugin_auth()
    plugin_auth.mark_loaded()

    set_accounts([_account("acc-1"), _account("acc-2", "临时")])
    plugin_auth.refresh_plugin_auth()
    assert plugin_auth.is_stale() is True

    set_accounts([_account("acc-1")])
    plugin_auth.refresh_plugin_auth()
    assert plugin_auth.is_stale() is False, "内容回到一致就不该再提示"


def test_status_shape(isolated) -> None:
    set_accounts([_account("acc-1")])
    plugin_auth.write_plugin_auth()
    plugin_auth.mark_loaded()

    out = plugin_auth.status()
    assert set(out) >= {"stale", "current_fingerprint", "recorded_fingerprint",
                        "loaded_fingerprint", "loaded_at", "host_alive"}
    assert out["stale"] is False


def test_stale_uses_disk_not_db_record(isolated) -> None:
    """关键回归：DB 里的 current 记录是派生值，可能被别的进程写脏。

    is_stale 必须以**磁盘实际内容**为准，否则会出现"文件与宿主一致却报 stale"
    （2026-10-15 在 P9 部署闭环时实测踩到）。
    """
    meta, path = isolated
    set_accounts([_account("acc-1")])
    plugin_auth.write_plugin_auth()
    plugin_auth.mark_loaded()
    assert plugin_auth.is_stale() is False

    # 把 DB 里的 current 记录改成垃圾值 —— 模拟"被其他进程写过"
    meta["plugin_auth_fingerprint"] = "deadbeef" * 4

    assert plugin_auth.is_stale() is False, "磁盘未变就不该报 stale"
    assert plugin_auth.status()["recorded_fingerprint"] == "deadbeef" * 4


def test_status_tolerates_legacy_garbage(isolated) -> None:
    """历史 meta 值不是 JSON 时不得抛异常（升级路径）。"""
    set_accounts([_account("acc-1")])
    plugin_auth.write_plugin_auth()
    store.set_setting("plugin_auth_loaded", "不是 JSON")
    assert plugin_auth.status()["stale"] is False


def test_stale_false_when_file_unreadable(isolated) -> None:
    """文件读不到时不谎报 stale（宿主可能仍持有可用凭证正常工作）。"""
    import os

    meta, path = isolated
    set_accounts([_account("acc-1")])
    plugin_auth.write_plugin_auth()
    plugin_auth.mark_loaded()
    os.unlink(path)

    assert plugin_auth.is_stale() is False
    assert plugin_auth.file_fingerprint() == ""


# ── 一键同步 ──────────────────────────────────────────────────────────────


def test_sync_refuses_on_native_channel(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_auth.settings, "REQUEST_CHANNEL", "native")
    out = asyncio.run(plugin_auth.sync_plugin_auth())
    assert out["ok"] is False
    assert "plugin 通道" in out["error"]


def test_sync_rebuilds_and_restarts(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import base as chbase

    monkeypatch.setattr(plugin_auth.settings, "REQUEST_CHANNEL", "plugin")
    set_accounts([_account("acc-1"), _account("acc-2", "新")])

    plugin_auth.write_plugin_auth()
    plugin_auth.mark_loaded()

    calls: list[str] = []

    async def fake_stop(*, drain: bool = False) -> None:
        calls.append(f"stop(drain={drain})")

    async def fake_start() -> dict:
        calls.append("start")
        plugin_auth.mark_loaded()
        return {"plugins": []}

    monkeypatch.setattr(chbase, "stop_plugin_host", fake_stop)
    monkeypatch.setattr(chbase, "start_plugin_host", fake_start)

    out = asyncio.run(plugin_auth.sync_plugin_auth())
    assert out["ok"] is True
    assert out["stale"] is False, "同步后不应再 stale"
    assert calls == ["stop(drain=True)", "start"], "重启必须先 drain（避免掐断在飞流）"


def test_sync_reports_restart_failure(isolated, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.channels import base as chbase

    monkeypatch.setattr(plugin_auth.settings, "REQUEST_CHANNEL", "plugin")
    set_accounts([_account("acc-1")])

    async def fake_stop(*, drain: bool = False) -> None:  # noqa: ARG001
        return None

    async def fake_start() -> None:
        return None          # 启动失败

    monkeypatch.setattr(chbase, "stop_plugin_host", fake_stop)
    monkeypatch.setattr(chbase, "start_plugin_host", fake_start)

    out = asyncio.run(plugin_auth.sync_plugin_auth())
    assert out["ok"] is False
    assert "重启失败" in out["error"]


# ── device 取值必须兼容 DeviceProfile 对象（回归防护）────────────────────


def test_device_of_accepts_device_profile_object() -> None:
    """内存态的 fingerprint 是 DeviceProfile 对象，必须能取到 device_mid。

    这条曾经失败：只判 dict 会把对象态当成「缺 device」，进而把派生凭证写成 {}。
    """
    from app.fingerprint import DeviceProfile

    acc = _account("acc-obj")
    acc.fingerprint = DeviceProfile(
        platform="Win32", arch="x64", os_version="10.0.0",
        language="zh-CN", timezone="Asia/Shanghai",
        screen="1366x768", device_mid="11111111-2222-4333-8444-555555555555",
    )
    assert plugin_auth._device_of(acc) == "11111111-2222-4333-8444-555555555555"


def test_build_doc_with_device_profile_objects(isolated) -> None:
    """对象态账号必须照常导出（否则凭证文件会被写成空）。"""
    from app.fingerprint import DeviceProfile

    acc = _account("acc-obj")
    acc.fingerprint = DeviceProfile(
        platform="Win32", arch="x64", os_version="10.0.0",
        language="zh-CN", timezone="Asia/Shanghai",
        screen="1366x768", device_mid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    )
    set_accounts([acc])

    doc = plugin_auth.build_auth_doc()
    assert len(doc) == 1, "对象态账号不能被跳过"
    assert plugin_auth.slot_of("acc-obj") in doc
