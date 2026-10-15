"""上游请求通道：决定一次请求「长什么样」并把它发出去。

两条并列实现：
  - native：本仓库手写拼装的请求（app/channels/native.py）
  - plugin：经官方插件宿主组装（app/channels/plugin_channel.py）

契约（见 openspec/changes/dual-channel-plugin-upstream/specs/dual-channel-upstream）：
通道只负责「发一次」，返回未进入的响应上下文；重试、换账号、冷却、错误分流一律留在
gateway.py。当前通道由 settings.REQUEST_CHANNEL 决定，plugin 不可用时每次请求降级 native。

注意：这里**不导出** plugin 通道的实例。实例名与子模块名同名会在包属性上互相遮蔽
（`from app.channels import plugin_channel` 到底是模块还是实例将取决于导入顺序），
因此对外只暴露 get_channel()，需要实例时走 `from . import plugin_channel as mod`。
"""

from __future__ import annotations

from .base import (
    UpstreamChannel,
    UpstreamResponse,
    UpstreamSendError,
    get_channel,
    start_plugin_host,
    stop_plugin_host,
)
from .native import NativeChannel, native_channel
from .plugin_auth import (
    build_auth_doc,
    refresh_plugin_auth,
    sync_plugin_auth,
    write_plugin_auth,
)
from .plugin_auth import (
    status as plugin_auth_status,
)
from .plugin_channel import (
    PluginChannel,
    PluginHost,
    PluginHostError,
    PluginResponseContext,
    slot_label,
    slot_of,
)

__all__ = [
    "UpstreamChannel",
    "UpstreamResponse",
    "UpstreamSendError",
    "get_channel",
    "start_plugin_host",
    "stop_plugin_host",
    "NativeChannel",
    "native_channel",
    "PluginChannel",
    "PluginHost",
    "PluginHostError",
    "PluginResponseContext",
    "slot_of",
    "slot_label",
    "build_auth_doc",
    "write_plugin_auth",
    "refresh_plugin_auth",
    "plugin_auth_status",
    "sync_plugin_auth",
]
