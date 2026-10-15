"""通道契约测试：native 通道与 gateway 的对接形状。

覆盖 openspec/changes/dual-channel-plugin-upstream 的 REQ-DUAL-01/02/04/05：
通道返回的是「未进入的响应上下文」，gateway 自行 __aenter__ / __aexit__。
本阶段只有 native，故断言 native 与 httpx.stream 语义等价。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.channels import NativeChannel, get_channel, native_channel
from app.channels.base import UpstreamChannel, UpstreamResponse, UpstreamSendError
from app.models import Account

_JWT = "h.eyJzdWIiOiJ0ZXN0In0.sig"


def _account() -> Account:
    return Account(id="test-acc", name="测试账号", provider="zai", mode="jwt", jwt_token=_JWT)


class _FakeCm:
    """最小 stream 上下文管理器替身。"""

    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> httpx.Response:
        self.entered += 1
        return httpx.Response(200, content=b"{}")

    async def __aexit__(self, *exc: object) -> bool:
        self.exited += 1
        return False


def test_native_is_the_default_channel() -> None:
    """未配置时走 native（REQ-DUAL-01 场景 1）。"""
    assert get_channel() is native_channel
    assert get_channel("native") is native_channel


def test_get_channel_falls_back_to_native_for_unknown_name() -> None:
    """plugin 尚未就绪时一律回退 native，不得抛错（REQ-DUAL-09）。"""
    assert get_channel("plugin") is native_channel
    assert get_channel("不存在的通道") is native_channel


def test_native_satisfies_channel_protocol() -> None:
    assert isinstance(native_channel, UpstreamChannel)
    assert native_channel.name == "native"


def test_send_returns_unentered_context_manager() -> None:
    """send() 返回的是 stream 上下文管理器，不是已建连的响应（保持既有语义）。"""
    calls: list[tuple] = []
    fake = _FakeCm()

    class _FakeClient:
        def stream(self, method, url, headers=None, content=None):  # noqa: ANN001, ANN201
            calls.append((method, url, headers, content))
            return fake

    cm = asyncio.run(
        native_channel.send(_account(), "https://up.example/v1/messages", {"x": "1"}, b"{}", _FakeClient())
    )
    assert cm is fake, "必须是未进入的上下文管理器"
    assert calls == [("POST", "https://up.example/v1/messages", {"x": "1"}, b"{}")]
    assert isinstance(cm, UpstreamResponse)
    assert fake.entered == 0, "通道不得自行进入上下文"


def test_native_requires_a_client() -> None:
    """native 依赖调用方提供客户端；缺失时立刻失败而不是发到默认地址。"""
    with pytest.raises(AssertionError, match="native 通道需要调用方提供"):
        asyncio.run(native_channel.send(_account(), "https://up.example/x", {}, b"{}", None))


def test_send_does_not_swallow_transport_errors() -> None:
    """连接层异常必须原样冒泡（gateway 依赖 except httpx.HTTPError 分支）。"""

    class _BoomClient:
        def stream(self, *a: object, **kw: object) -> object:
            raise httpx.ConnectError("连接被拒")

    with pytest.raises(httpx.HTTPError):
        asyncio.run(native_channel.send(_account(), "https://up.example/x", {}, b"{}", _BoomClient()))


def test_upstream_send_error_is_an_httpx_error() -> None:
    """自定义错误包装必须是 httpx.HTTPError 子类（REQ-DUAL-05 场景 3）。"""
    assert issubclass(UpstreamSendError, httpx.HTTPError)


def test_channel_class_exported() -> None:
    assert NativeChannel is type(native_channel)
