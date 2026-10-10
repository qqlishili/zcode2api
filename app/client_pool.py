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
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from . import settings

if TYPE_CHECKING:
    from .models import Account

_logger = logging.getLogger("zcode.pool")

class RegionOfflineError(RuntimeError):
    """账号所属大区离线且无可用健康端口时的熔断异常（严禁降级裸连机房 IP）。"""


class AccountClientPool:
    """管理每个账号专属的 httpx.AsyncClient 实例。"""

    def __init__(
        self,
        idle_timeout: float | None = None,
        registry_path: Path | str | None = None,
    ) -> None:
        # account_id -> (client, last_active_timestamp)
        self._clients: dict[str, tuple[httpx.AsyncClient, float]] = {}
        self._lock = asyncio.Lock()
        self._idle_timeout = (
            idle_timeout if idle_timeout is not None else float(settings.CLIENT_IDLE_TIMEOUT)
        )
        self._registry_path = (
            Path(registry_path) if registry_path else (settings.DATA_DIR / "egress_registry.json")
        )
        self._registry_cache: dict[str, list[str]] = {}
        self._registry_mtime: float = 0.0

    def load_registry(self) -> dict[str, list[str]]:
        """从 data/egress_registry.json 加载各大区在线活跃出口映射。"""
        if not self._registry_path.is_file():
            return {}
        try:
            mtime = self._registry_path.stat().st_mtime
            if mtime == self._registry_mtime and self._registry_cache:
                return self._registry_cache
            raw_data = json.loads(self._registry_path.read_text(encoding="utf-8"))
            regions = raw_data.get("regions", {})
            cleaned = {}
            for reg, plist in regions.items():
                if isinstance(plist, list):
                    cleaned[reg] = [str(p).strip() for p in plist if str(p).strip()]
            self._registry_cache = cleaned
            self._registry_mtime = mtime
            return cleaned
        except Exception as err:
            _logger.warning(f"[ClientPool] 读取出口注册表失败: {err}")
            return self._registry_cache

    def get_healthy_regions(self) -> set[str] | None:
        """返回当前拥有至少一个健康出口的大区集合。若无注册表文件则返回 None（全放行）。"""
        if not self._registry_path.is_file():
            return None
        reg = self.load_registry()
        return {r for r, ports in reg.items() if ports}

    def is_region_healthy(self, region: str | None) -> bool:
        """检查特定大区是否拥有活跃出口。未指定大区或无注册表文件则视为健康（走全局/直连）。"""
        if not region:
            return True
        if not self._registry_path.is_file():
            return True
        reg = self.load_registry()
        return bool(reg.get(region))

    def resolve_proxy(self, account: Account | str | None) -> str | None:
        """解析指定账号应绑定的代理出口。

        优先级：
        1. 账号自身显式绑定的专属代理（account.proxy）
        2. 账号所属永久大区百位段活跃端口（account.assigned_region）一致性哈希
        3. 回退全局多代理池（ZCODE_PROXIES）按 account_id 一致性哈希
        4. 优雅降级返回 None（独立直连）
        """
        # 1. 账号自身显式绑定的代理
        if account is not None and not isinstance(account, str):
            if getattr(account, "proxy", None) and str(account.proxy).strip():
                return str(account.proxy).strip()

        account_id = ""
        assigned_region = None
        if account is not None:
            if isinstance(account, str):
                account_id = account
            else:
                account_id = account.id
                assigned_region = getattr(account, "assigned_region", None)

        # 2. 账号永久绑定的大区百位端口段一致性哈希
        if assigned_region:
            reg = self.load_registry()
            region_proxies = reg.get(assigned_region, [])
            if region_proxies:
                if account_id:
                    idx = int(hashlib.md5(account_id.encode("utf-8")).hexdigest(), 16) % len(region_proxies)
                    return region_proxies[idx]
                return region_proxies[0]
            # 若该大区无健康出口，绝不跨区换节点！返回 None（调度器负责前置避让换账号）
            _logger.warning(f"[ClientPool] 账号 {account_id} 绑定大区 {assigned_region} 当前无可用健康出口")
            return None

        # 3. 回退全局 ZCODE_PROXIES
        proxies = settings.get_global_proxies()
        if not proxies:
            return None

        if account_id:
            # MD5 一致性哈希，确保同一账号在代理池不变时始终固定出口
            idx = int(hashlib.md5(account_id.encode("utf-8")).hexdigest(), 16) % len(proxies)
            return proxies[idx]

        return proxies[0]

    def get_any_healthy_proxy(self) -> str | None:
        """获取任意一个当前健康的大区代理出口（供全局无特定账号上下文时使用，如预解池补货）。"""
        reg = self.load_registry()
        for reg_name in ("HK", "TW", "JP", "SG", "US", "KR", "EU", "OTHER"):
            ports = reg.get(reg_name)
            if ports:
                return ports[0]
        proxies = settings.get_global_proxies()
        return proxies[0] if proxies else None

    async def get_client(
        self,
        account: Account | str | None = None,
        *,
        timeout: float | httpx.Timeout | None = None,
    ) -> httpx.AsyncClient:
        """获取或创建指定账号专属的 AsyncClient。"""
        account_id = "__anonymous__"
        assigned_region = None
        if account is not None:
            if isinstance(account, str):
                account_id = account
            else:
                account_id = account.id
                assigned_region = getattr(account, "assigned_region", None)

        async with self._lock:
            # 检查现有活跃客户端
            if account_id in self._clients:
                client, _ = self._clients[account_id]
                if not getattr(client, "is_closed", False):
                    self._clients[account_id] = (client, time.time())
                    return client

            # 新建客户端
            proxy = self.resolve_proxy(account)
            if assigned_region and not proxy:
                # 栅栏原则：明确绑定大区的账号在缺乏健康出口时绝对禁止降级直连
                raise RegionOfflineError(
                    f"账号 [{account_id}] 绑定大区 [{assigned_region}] 当前无可用健康出口，禁止直连降级"
                )

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
