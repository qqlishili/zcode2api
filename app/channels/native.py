"""native 通道：本仓库手写拼装的请求形态。

本文件是 gateway._try_account_admitted 中原有发送逻辑的**原样搬移**，行为不做任何
改动 —— Phase 2 的目标就是「搬走但不变」，用全量单测证明这一点。

为什么返回上下文管理器而不是 httpx.Response：
    gateway 的 _Upstream 需要持有上游连接的打开状态，让流式响应在返回给客户端后
    继续读取，读完才关闭。因此它拿到的是 stream 上下文管理器（cm），由
    _Upstream.close() 在流结束时 __aexit__。换成 client.post 会把整个响应体读进
    内存，破坏流式语义。故这里沿用同一语义，只是把「拿 cm」这一步收进通道层。
"""

from __future__ import annotations

from typing import Any

import httpx

from ..models import Account


class NativeChannel:
    """手写请求通道：直接把构造好的 url/headers/payload 发出去。"""

    name = "native"

    async def send(
        self,
        account: Account,
        url: str,
        headers: dict[str, str],
        payload: bytes,
        client: httpx.AsyncClient | None,
    ) -> Any:
        """返回 httpx 的 stream 上下文管理器（调用方负责 __aenter__ / __aexit__）。

        与搬移前完全一致：不吞异常、不改状态、不做任何判定。
        """
        assert client is not None, "native 通道需要调用方提供 httpx 客户端"
        return client.stream("POST", url, headers=headers, content=payload)

    async def aclose(self) -> None:
        """native 通道不持有独立资源（client 由 client_pool / gateway 管理）。"""
        return None


native_channel = NativeChannel()
