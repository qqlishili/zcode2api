"""上游请求通道：决定一次请求「长什么样」并把它发出去。

两条并列实现，对外契约同构：
  - native：本仓库手写拼装的请求（app/channels/native.py）
  - plugin：经官方插件宿主组装（app/channels/plugin_channel.py）

契约（见 openspec/changes/dual-channel-plugin-upstream/specs/dual-channel-upstream）：

1. 通道只负责「发一次」：返回一个**响应上下文**（见 UpstreamResponse），或抛
   httpx.HTTPError。重试、换账号、冷却、错误码分流一律留在 gateway.py —— 那里
   有 next_account 闭包与 _dispatch 外层 while 构成的既有调度核心。
2. 响应上下文必须同时支持 resp.aread() 与 resp.aiter_bytes()
   （非流式与流式两条下游路径都依赖它）。
3. 不得引入 gateway 不认识的新错误类型（否则「gateway 零改动」契约破裂）。

关于「响应上下文」而不是裸 httpx.Response：
    流式请求要在返回给客户端之后继续读上游，直到流结束才关闭连接。因此通道的产物
    不是「一个响应对象」，而是「一次已建立、等待消费的连接」——httpx 用 stream 上下文
    管理器表达这件事（__aenter__ 建连、__aexit__ 释放）。gateway 的 _Upstream 正是
    按这个语义持有并关闭它（cm / cm_closed / _cm_closed）。为保持 native 侧「零行为
    变化」，这里沿用同一语义，把「拿到上下文」收进通道层。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx

from ..models import Account


@runtime_checkable
class UpstreamResponse(Protocol):
    """一次已建立的上游连接（httpx 的 stream 上下文管理器语义）。

    gateway 按既有方式使用：
        cm = await channel.send(...)
        resp = await cm.__aenter__()      # 建连，得到 httpx.Response
        ...
        await cm.__aexit__(None, None, None)   # 释放
    """

    async def __aenter__(self) -> httpx.Response: ...
    async def __aexit__(self, exc_type, exc, tb) -> object: ...


@runtime_checkable
class UpstreamChannel(Protocol):
    """上游通道契约。"""

    name: str

    async def send(
        self,
        account: Account,
        url: str,
        headers: dict[str, str],
        payload: bytes,
        client: httpx.AsyncClient | None,
    ) -> UpstreamResponse:
        """向 url 发起一次 POST，返回未进入的响应上下文。

        不换账号、不重试、不修改账号状态；连接层异常以 httpx.HTTPError 抛出。
        """
        ...

    async def aclose(self) -> None:
        """释放通道自身持有的资源。"""
        ...


class UpstreamSendError(httpx.HTTPError):
    """通道侧的发送失败（错误包装层，保证仍是 httpx.HTTPError 语义）。

    保留 httpx.HTTPError 基类，使 gateway 既有的 `except httpx.HTTPError` 分支
    无需改动即可捕获。
    """


# 上次降级告警时的状态：None = 还没告警过。只需在「可用→不可用」翻转时告警一次，
# 避免每请求刷屏（get_channel 在请求路径上，不能无脑打日志）。
_degraded = False


def get_channel(name: str | None = None) -> UpstreamChannel:
    """按配置返回当前通道；plugin 不可用时降级 native。

    降级是「每次请求重新判定」而不是启动时一次决定：宿主进程可能在运行中死亡，
    而判定成本只是一次 returncode 检查（spec REQ-PLUG-07）。

    注意：保留降级不等于降级无害。在当前上游状态下 native 会被 405 拒绝
    （缺官方 dress，见 design.md §1.2），所以降级事实上是"请求将失败"。
    因此在状态翻转时记一条 warn，让它在日志里可见 —— 否则只看到请求失败、
    看不到原因。
    """
    global _degraded
    from .. import logs, settings
    from .native import native_channel

    if name is None:
        name = getattr(settings, "REQUEST_CHANNEL", "native")
    if name != "plugin":
        return native_channel

    from . import plugin_channel as plugin_mod

    if plugin_mod.plugin_host.is_alive():
        if _degraded:
            _degraded = False
            logs.ok("plugin", "插件宿主已恢复，请求回到 plugin 通道")
        return plugin_mod.plugin_channel

    if not _degraded:
        _degraded = True
        logs.warn("plugin", "插件宿主不可用，请求降级到 native 通道"
                            "（native 缺官方 dress，上游很可能返回 405）")
    return native_channel


async def start_plugin_host() -> dict | None:
    """启动插件宿主（lifespan 调用）。

    只应在配置为 plugin 时启动：native 模式下不引入 Bun 依赖与额外进程。
    失败不抛错——plugin 通道标记不可用并降级 native，服务照常启动
    （spec REQ-DUAL-09 场景 1）。
    """
    from .. import logs, settings

    if getattr(settings, "REQUEST_CHANNEL", "native") != "plugin":
        return None

    from .plugin_auth import write_plugin_auth
    from .plugin_channel import plugin_host

    try:
        path = write_plugin_auth()
        result = await plugin_host.start(str(path), timeout=settings.PLUGIN_INIT_TIMEOUT)
    except Exception as err:  # noqa: BLE001 - 启动期任何失败都只降级，不阻断服务
        logs.warn("plugin", f"插件宿主启动失败，本次将走 native 通道: {err}")
        return None

    # 记录「宿主已加载当前指纹」：派生凭证变化后，管理页据此提示需要同步
    from .plugin_auth import mark_loaded

    mark_loaded()
    logs.ok("plugin", f"插件宿主就绪（{settings.REQUEST_CHANNEL} 通道）")
    return result


async def stop_plugin_host(*, drain: bool = False) -> None:
    """关闭插件宿主。

    `drain=True` 时先等在飞请求结束（重启场景），否则直接关（服务退出场景 ——
    进程都没了，等也没有意义）。
    """
    from .. import settings
    from .plugin_channel import plugin_host

    await plugin_host.aclose(
        drain_timeout=settings.PLUGIN_DRAIN_TIMEOUT if drain else None,
    )
