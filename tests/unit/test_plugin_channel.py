"""plugin 通道的单测：槽位命名、凭证派生、选路降级、流式包装、RPC 形状。

不启动真实 Bun 进程：宿主交互用假宿主替身，只验证我们这一侧的契约与逻辑
（真实链路由 Phase 9 的端到端烟测覆盖）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
import sys
import tempfile

import httpx
import pytest

from app.channels import plugin_auth
from app.channels.base import get_channel
from app.channels.native import native_channel
from app.channels.plugin_channel import (
    PluginChannel,
    PluginHost,
    PluginHostError,
    PluginResponseContext,
    _PluginStream,
    slot_label,
    slot_of,
)
from app.models import Account

_JWT = "h.eyJzdWIiOiJ0ZXN0In0.sig"


def _account(acc_id: str = "acc-1", name: str = "测试账号") -> Account:
    """构造测试账号。

    必须带 fingerprint.device_mid：build_auth_doc 会**跳过**缺设备指纹的账号
    （不编造随机设备 —— 那会让同一账号每次生成的凭证内容不同、指纹失去确定性）。
    """
    acc = Account(id=acc_id, name=name, provider="zai", mode="jwt", jwt_token=_JWT)
    # device 由 id 派生，保证同一账号多次构造内容一致
    digits = f"{abs(hash(acc_id)) % 10**12:012d}"[:12]
    acc.fingerprint = {"device_mid": f"00000000-0000-4000-8000-{digits}"}
    return acc


# ── 槽位命名（REQ-DUAL-10 / REQ-PLUG-01）────────────────────────────────────


def test_slot_of_is_deterministic_and_fixed_length() -> None:
    s = slot_of("sample-long-id-88634403")
    assert s == slot_of("sample-long-id-88634403"), "纯函数：同 id 同槽位"
    assert s.startswith("zcode#h")
    assert len(s) == len("zcode#h") + 10
    assert s.isascii(), "槽位必须纯 ASCII（避免 JSON 转义与日志乱码）"


def test_slot_of_handles_chinese_and_uuid_ids() -> None:
    """实测这三类 id 都能作槽位；短哈希把它们统一成定长形态。"""
    for acc_id in ("账号B-309e57a3", "11111111-2222-3333-4444-555555555555"):
        s = slot_of(acc_id)
        assert s.isascii() and len(s) == len("zcode#h") + 10


def test_slot_of_differs_between_accounts() -> None:
    assert slot_of("acc-1") != slot_of("acc-2")


def test_slot_label_carries_account_name() -> None:
    """槽位是哈希，日志必须同时带账号名（否则运维无法定位）。"""
    label = slot_label(_account("acc-1", "账号B"))
    assert slot_of("acc-1") in label
    assert "账号B" in label


# ── 凭证派生（REQ-PLUG-11 / REQ-PLUG-12）───────────────────────────────────


def test_build_auth_doc_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    accs = [_account("acc-1"), _account("acc-2")]
    monkeypatch.setattr(plugin_auth.store, "list_accounts", lambda _p: accs)

    doc = plugin_auth.build_auth_doc()
    assert set(doc) == {slot_of("acc-1"), slot_of("acc-2")}
    entry = doc[slot_of("acc-1")]
    assert entry["type"] == "oauth"
    assert entry["access"] == "zcode"
    refresh = json.loads(entry["refresh"])
    assert refresh["jwt"] == _JWT
    assert refresh["site"] == "zai"
    assert "zcode-plan" in refresh["base"]


def test_build_auth_doc_skips_disabled_and_apikey(monkeypatch: pytest.MonkeyPatch) -> None:
    """只导出可用于 Plan 通道的账号：jwt 模式且启用。"""
    off = _account("acc-off")
    off.enabled = False
    key_mode = Account(id="acc-key", name="key号", provider="zai", mode="apiKey", api_key="sk-x")
    monkeypatch.setattr(plugin_auth.store, "list_accounts", lambda _p: [_account("acc-on"), off, key_mode])

    doc = plugin_auth.build_auth_doc()
    assert set(doc) == {slot_of("acc-on")}


def test_build_auth_doc_uses_account_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """两通道同设备：device 必须取账号自己的 device_mid（避免同账号多设备特征）。"""
    acc = _account("acc-1")
    acc.fingerprint = {"device_mid": "11111111-2222-3333-4444-555555555555"}
    monkeypatch.setattr(plugin_auth.store, "list_accounts", lambda _p: [acc])

    doc = plugin_auth.build_auth_doc()
    refresh = json.loads(doc[slot_of("acc-1")]["refresh"])
    assert refresh["device"] == "11111111-2222-3333-4444-555555555555"


def test_write_plugin_auth_sets_600_and_is_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    acc = _account("acc-1")
    monkeypatch.setattr(plugin_auth.store, "list_accounts", lambda _p: [acc])

    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "plugin-auth.json")
        monkeypatch.setattr(plugin_auth.settings, "PLUGIN_AUTH_PATH", target)
        path = plugin_auth.write_plugin_auth()

        assert os.path.exists(path)
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600, f"JWT 明文落盘，权限必须是 600，实际 {oct(mode)}"
        assert not [f for f in os.listdir(tmp) if f.endswith(".tmp")], "临时文件必须已清理"


def test_refresh_plugin_auth_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """重建失败不应影响请求路径（凭证文件不是硬依赖）。"""
    def boom() -> object:
        raise OSError("磁盘满")

    monkeypatch.setattr(plugin_auth, "write_plugin_auth", boom)
    plugin_auth.refresh_plugin_auth()  # 不抛即通过


# ── 选路与降级（REQ-DUAL-01 / REQ-DUAL-09）─────────────────────────────────


def test_default_channel_is_native() -> None:
    assert get_channel() is native_channel
    assert get_channel("native") is native_channel


def test_plugin_unavailable_falls_back_to_native() -> None:
    """宿主未启动（is_alive 为假）时，请求必须走 native 而不是报错。"""
    assert get_channel("plugin") is native_channel


def test_plugin_used_when_host_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """宿主存活时走 plugin；这是 plugin 通道能被真正启用的唯一条件。"""
    mod = sys.modules["app.channels.plugin_channel"]

    monkeypatch.setattr(mod.plugin_host, "is_alive", lambda: True)
    assert get_channel("plugin") is mod.plugin_channel


# ── 流式包装（REQ-DUAL-02 / REQ-PLUG-03 / REQ-PLUG-04）─────────────────────


class _FakeHost(PluginHost):
    """替身：用预置事件序列替代真实宿主。"""

    def __init__(self, events: list[dict], head: dict) -> None:
        super().__init__()
        self._fake_events = events
        self._fake_head = head
        self.credits: list[tuple[int, int]] = []
        self.aborted: list[int] = []

    def is_alive(self) -> bool:
        return True

    def head_of(self, fetch_id: int) -> dict | None:  # noqa: ARG002
        return self._fake_head

    async def wait_head(self, fetch_id: int, timeout: float) -> dict:  # noqa: ARG002
        return self._fake_head

    async def iter_chunks(self, fetch_id: int):  # noqa: ANN201, ARG002
        for ev in self._fake_events:
            if ev.get("event") == "chunk":
                yield base64.b64decode(ev["data"])

    async def credit(self, fetch_id: int, n: int) -> None:
        self.credits.append((fetch_id, n))

    async def abort(self, fetch_id: int) -> None:
        self.aborted.append(fetch_id)

    def release(self, fetch_id: int) -> None:
        pass


def test_stream_reassembles_chunks_and_credits_payload() -> None:
    body = b'{"ok":true}'
    evs = [{"event": "chunk", "data": base64.b64encode(body).decode()}]
    host = _FakeHost(evs, {"status": 200, "headers": {"content-type": "application/json"}})

    async def run() -> tuple[bytes, list]:
        cm = PluginResponseContext(host, 7)
        resp = await cm.__aenter__()
        assert resp.status_code == 200
        data = b""
        async for chunk in resp.aiter_bytes():
            data += chunk
        await cm.__aexit__(None, None, None)
        return data, host.credits

    data, credits = asyncio.run(run())
    assert data == body
    assert credits == [(7, len(body))], "必须按 payload 长度回补 credit"
    assert host.aborted == [7], "关闭时必须 abort 释放宿主资源（由流自身发出一次）"


def test_stream_aclose_aborts() -> None:
    host = _FakeHost([], {"status": 200, "headers": {}})
    asyncio.run(_PluginStream(host, 9).aclose())
    assert host.aborted == [9]


def test_response_context_is_unicode_decode_ready() -> None:
    """head 的 status/headers 必须变成可用的 httpx.Response（下游按 headers 判流式）。"""
    body = b'{"type":"message"}'
    evs = [{"event": "chunk", "data": base64.b64encode(body).decode()}]
    host = _FakeHost(evs, {"status": 200, "headers": {"Content-Type": "text/event-stream"}})

    async def run() -> httpx.Response:
        cm = PluginResponseContext(host, 11)
        return await cm.__aenter__()

    resp = asyncio.run(run())
    assert resp.headers.get("content-type") == "text/event-stream"
    assert isinstance(resp, httpx.Response)


# ── fetch 参数形状（REQ-PLUG-02）───────────────────────────────────────────


def test_fetch_params_include_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 model/npm 会得上游 400/3001，故必须传齐（实测）。"""
    sent: list[dict] = []

    class _H(PluginHost):
        def is_alive(self) -> bool:
            return True

        async def _write(self, msg: dict) -> None:
            sent.append(msg)

    host = _H()
    payload = json.dumps({"model": "GLM-5.3-Flash"}).encode()
    asyncio.run(host.fetch(
        account=_account("acc-1"), model="GLM-5.3-Flash",
        url="https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages",
        method="POST", headers={"content-type": "application/json"},
        payload=payload, proxy=None,
    ))

    params = sent[0]["params"]
    for field in ("provider", "account", "model", "npm", "url", "method", "headers", "body", "window", "proxy"):
        assert field in params, f"fetch 参数缺少 {field}"
    assert params["npm"] == "@ai-sdk/anthropic"
    assert params["proxy"] == "direct", "直连必须显式传 direct（省略会让宿主读自己的环境变量）"
    assert params["provider"] == "zcode"
    assert params["account"] == slot_of("acc-1")


def test_fetch_requires_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求体缺 model 时立刻失败，不发无效请求。"""
    async def run() -> None:
        await PluginChannel(PluginHost()).send(
            _account("acc-1"), "https://x/", {}, b"{}", None,
        )

    with pytest.raises(PluginHostError, match="缺少 model"):
        asyncio.run(run())


def test_host_unavailable_raises_plugin_error() -> None:
    """宿主不可用时 fetch 抛 PluginHostError（由 get_channel 提前降级兜住）。"""
    async def run() -> None:
        await PluginHost().fetch(
            account=_account(), model="GLM-5.3-Flash", url="https://x/",
            method="POST", headers={}, payload=b"{}", proxy=None,
        )

    with pytest.raises(PluginHostError):
        asyncio.run(run())
