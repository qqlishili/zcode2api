"""账号专属独立客户端连接池（物理隔离 + 多出口哈希 + 优雅直连降级）。

设计原则：
1. 第一性原理：连接复用与隔离的粒度为账号（account_id）。
   杜绝多账号交替复用同一条底层 TCP/TLS 链路，消除上游 WAF 针对同一链路多凭证的关联风控判定。
2. 归一化路由：
   - 优先级 1: 账号显式绑定代理（account.proxy）
   - 优先级 2: 全局多出口代理列表（ZCODE_PROXIES）按 account_id 一致性哈希绑定（单账号固定出口 IP）
   - 优先级 3: 优雅后备降级（未配置代理时自动降级为账号专属独立直连 Client，绝不断网）
3. 资源自愈回收：
   空闲超过 TTL（默认 300 秒）自动释放与关闭 Client。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

import httpx

from . import settings

_logger = logging.getLogger("zcode.pool")

if TYPE_CHECKING:
    from .models import Account


class AccountClientPool:
    """管理每个账号专属的 httpx.AsyncClient 实例。"""

    def __init__(self, idle_timeout: float | None = None) -> None:
        # account_id -> (client, last_active_timestamp)
        self._clients: dict[str, tuple[httpx.AsyncClient, float]] = {}
        self._lock = asyncio.Lock()
        self._idle_timeout = (
            idle_timeout if idle_timeout is not None else float(settings.CLIENT_IDLE_TIMEOUT)
        )

    def resolve_proxy(self, account: Account | str | None) -> str | None:
        """解析指定账号应绑定的代理出口。

        优先级：
        1. 单账号专属代理配置（account.proxy）
        2. 全局多代理池按 account_id 进行一致性哈希分配
        3. 优雅降级返回 None（独立直连）
        """
        # 1. 账号自身显式绑定的代理
        if account is not None and not isinstance(account, str):
            if getattr(account, "proxy", None) and str(account.proxy).strip():
                return str(account.proxy).strip()

        account_id = ""
        if account is not None:
            account_id = account.id if not isinstance(account, str) else account

        proxies = settings.get_global_proxies()
        if not proxies:
            return None

        if account_id:
            # MD5 一致性哈希，确保同一账号在代理池不变时始终固定出口
            idx = int(hashlib.md5(account_id.encode("utf-8")).hexdigest(), 16) % len(proxies)
            return proxies[idx]

        return proxies[0]

    async def get_client(
        self,
        account: Account | str | None = None,
        *,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.AsyncClient:
        """获取或创建指定账号专属的 AsyncClient。"""
        account_id = "__anonymous__"
        if account is not None:
            account_id = account.id if not isinstance(account, str) else account

        async with self._lock:
            # 检查现有活跃客户端
            if account_id in self._clients:
                client, _ = self._clients[account_id]
                if not getattr(client, "is_closed", False):
                    self._clients[account_id] = (client, time.time())
                    return client

            # 新建客户端
            proxy = self.resolve_proxy(account)
            req_timeout = (
                timeout
                if timeout is not None
                else httpx.Timeout(connect=30.0, read=None, write=120.0, pool=30.0)
            )
            limits = httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=120.0,
            )

            if proxy:
                _logger.info(f"[ClientPool] 账号 {account_id} 绑定出口代理: {proxy}")
                client = httpx.AsyncClient(proxy=proxy, timeout=req_timeout, limits=limits)
            else:
                _logger.debug(
                    f"[ClientPool] 账号 {account_id} 未配置代理，使用独立直连连接池 (优雅降级)"
                )
                client = httpx.AsyncClient(timeout=req_timeout, limits=limits)

            self._clients[account_id] = (client, time.time())
            return client

    async def prune_idle(self) -> int:
        """清理并关闭超时未活跃的客户端实例。"""
        now = time.time()
        to_close: list[tuple[str, httpx.AsyncClient]] = []

        async with self._lock:
            for acc_id, (client, last_active) in list(self._clients.items()):
                if now - last_active > self._idle_timeout:
                    to_close.append((acc_id, client))
                    self._clients.pop(acc_id, None)

        closed_count = 0
        for acc_id, client in to_close:
            try:
                if not getattr(client, "is_closed", False) and hasattr(client, "aclose"):
                    await client.aclose()
                closed_count += 1
                _logger.debug(f"[ClientPool] 账号 {acc_id} 客户端空闲超时已回收")
            except Exception as e:
                _logger.warning(f"[ClientPool] 回收客户端 {acc_id} 异常: {e}")

        return closed_count

    async def close_account(self, account_id: str) -> None:
        """关闭并注销特定账号的客户端。"""
        async with self._lock:
            pair = self._clients.pop(account_id, None)

        if pair:
            client, _ = pair
            if not getattr(client, "is_closed", False) and hasattr(client, "aclose"):
                await client.aclose()

    async def aclose(self) -> None:
        """关闭所有托管客户端并清空连接池。"""
        async with self._lock:
            pairs = list(self._clients.values())
            self._clients.clear()

        for client, _ in pairs:
            try:
                if not getattr(client, "is_closed", False) and hasattr(client, "aclose"):
                    await client.aclose()
            except Exception:
                pass


# 模块级单例连接池
account_client_pool = AccountClientPool()
