"""账号专属独立客户端连接池（物理隔离 + 多出口哈希 + 优雅直连降级）。

设计原则：
1. 第一性原理：连接复用与隔离的粒度为账号（account_id）。
   杜绝多账号交替复用同一条底层 TCP/TLS 链路，消除上游 WAF 针对同一链路多凭证的关联风控判定。
2. 归一化路由：
   - 优先级 1: 账号显式绑定代理（account.proxy）
   - 优先级 2: 全局多出口代理列表（ZCODE_PROXIES）按 account_id 一致性哈希绑定（单账号固定出口 IP）
   - 优先级 3: 优雅后备降级（未配置代理时自动降级为账号专属独立直连 Client，绝不断网）
3. 资源回收：
   空闲缓存按 TTL 回收；实际操作和 producer 引用归零后才关闭 Client。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from . import settings
from .egress import (
    PROTOCOL_VERSION,
    managed_port,
    process_identity,
    slot_identity,
    validate_registry,
    write_private_json,
)

if TYPE_CHECKING:
    from .models import Account

_logger = logging.getLogger("zcode.pool")

class RegionOfflineError(RuntimeError):
    """账号所属大区离线且无可用健康端口时的熔断异常（严禁降级裸连机房 IP）。"""


@dataclass(eq=False)
class EgressOperation:
    pool: AccountClientPool
    client: httpx.AsyncClient
    account_id: str
    proxy: str | None
    identity: tuple | None
    generation: str | None
    kind: str = "operation"
    released: bool = False


_current_operation: ContextVar[EgressOperation | None] = ContextVar("egress_operation", default=None)


class AccountClientPool:
    """管理每个账号专属的 httpx.AsyncClient 实例。"""

    def __init__(
        self,
        idle_timeout: float | None = None,
        registry_path: Path | str | None = None,
    ) -> None:
        # account_id -> (client, last_active_timestamp)
        self._clients: dict[str, tuple[httpx.AsyncClient, float]] = {}
        self._client_proxies: dict[httpx.AsyncClient, str | None] = {}
        self._lock = asyncio.Lock()
        self._idle_timeout = (
            idle_timeout if idle_timeout is not None else float(settings.CLIENT_IDLE_TIMEOUT)
        )
        self._registry_path = (
            Path(registry_path) if registry_path else (settings.DATA_DIR / "egress_registry.json")
        )
        self._registry_cache: dict[str, list[str]] = {}
        self._registry_mtime: float = 0.0
        self._registry_digest: str | None = None
        self._registry: dict = {}
        self._client_identities: dict[httpx.AsyncClient, tuple | None] = {}
        self._operations: set[EgressOperation] = set()
        self._retired_clients: set[httpx.AsyncClient] = set()
        self._closed_identities: set[tuple] = set()
        self._coordinator_task: asyncio.Task | None = None
        self._consumer: dict | None = None
        # 三态断路器状态机：proxy_url -> {"state": "CLOSED"|"OPEN"|"HALF-OPEN", "fails": int, "quarantine_until": float, "half_open_active": bool}
        self._circuit_breakers: dict[str, dict] = {}

    def record_proxy_result(self, proxy_url: str, success: bool, is_waf_block: bool = False) -> None:
        """仅记录实际网络结果，驱动三态断路器状态机。"""
        if not proxy_url:
            return
        st = self._circuit_breakers.setdefault(proxy_url, {
            "state": "CLOSED",
            "fails": 0,
            "quarantine_until": 0.0,
            "half_open_active": False,
        })
        now = time.monotonic()
        if success:
            if st["state"] != "CLOSED":
                _logger.info(f"[CircuitBreaker] 代理 {proxy_url} 探针成功，恢复 CLOSED 状态")
            st["state"] = "CLOSED"
            st["fails"] = 0
            st["half_open_active"] = False
            st["quarantine_until"] = 0.0
        else:
            if st["state"] == "HALF-OPEN":
                st["state"] = "OPEN"
                st["quarantine_until"] = now + float(settings.CAPTCHA_CIRCUIT_BREAKER_COOLDOWN)
                st["half_open_active"] = False
                _logger.warning(f"[CircuitBreaker] 代理 {proxy_url} 半开探针失败，重新熔断隔离 {settings.CAPTCHA_CIRCUIT_BREAKER_COOLDOWN}s")
            else:
                st["fails"] += 1
                # WAF 封禁（3012/405）是**确定性**信号，不是抖动：
                # 实测坏组合 4/4 稳定复现 3012、好组合 6/6 从不出现。
                # 故阈值取 1 —— 见到即熔断，避免在"已被拉黑"的出口上继续浪费请求。
                # 误杀风险极低，且冷却 600s 后自动 HALF-OPEN 复探。
                threshold = 1 if is_waf_block else 3
                if st["fails"] >= threshold:
                    st["state"] = "OPEN"
                    st["quarantine_until"] = now + float(settings.CAPTCHA_CIRCUIT_BREAKER_COOLDOWN)
                    _logger.warning(
                        f"[CircuitBreaker] 代理 {proxy_url} 连续失败 {st['fails']} 次（waf={is_waf_block}），"
                        f"触发熔断隔离 {settings.CAPTCHA_CIRCUIT_BREAKER_COOLDOWN}s"
                    )

    def is_proxy_quarantined(self, proxy_url: str) -> bool:
        """检查代理端口当前是否处于熔断隔离状态。"""
        if not proxy_url:
            return False
        st = self._circuit_breakers.get(proxy_url)
        if not st or st["state"] == "CLOSED":
            return False
        return (st["state"] == "OPEN" and time.monotonic() < st["quarantine_until"]
                or st["state"] == "HALF-OPEN" and st["half_open_active"])

    def client_proxy(self, client: httpx.AsyncClient) -> str | None:
        """返回客户端实际出口，避免验证码重新哈希导致漂移。"""
        return self._client_proxies.get(client)

    def begin_request(self, client: httpx.AsyncClient) -> bool:
        """在实际联网前准入，返回本次是否拥有唯一半开资格。"""
        proxy = self.client_proxy(client)
        if not proxy:
            return False
        if self.is_proxy_quarantined(proxy):
            raise RegionOfflineError("账号出口暂时不可用")
        st = self._circuit_breakers.get(proxy)
        if st and st["state"] in ("OPEN", "HALF-OPEN"):
            st["state"] = "HALF-OPEN"
            st["half_open_active"] = True
            return True
        return False

    def end_request(self, client: httpx.AsyncClient, probe: bool, *,
                    success: bool | None = None, is_waf_block: bool = False) -> None:
        """成对结算；取消和本地错误只释放资格。"""
        proxy = self.client_proxy(client)
        if not proxy:
            return
        identity = self._client_identities.get(client)
        if identity and identity != self.proxy_identity(proxy):
            return
        st = self._circuit_breakers.get(proxy)
        # 旧 CLOSED 结果不能替另一个探针结算或关闭隔离。
        if st and st["state"] != "CLOSED" and not probe:
            return
        if success is not None:
            self.record_proxy_result(proxy, success, is_waf_block)
        elif probe and st and st["state"] == "HALF-OPEN":
            st["half_open_active"] = False

    def get_quarantine_status(self) -> dict[str, dict]:
        """返回所有代理断路器的实时健康与隔离状态（供运维/Admin 诊断观测）。"""
        now = time.monotonic()
        res = {}
        for p, st in self._circuit_breakers.items():
            rem = max(0.0, st["quarantine_until"] - now) if st["state"] == "OPEN" else 0.0
            res[p] = {
                "state": st["state"],
                "fails": st["fails"],
                "remaining_quarantine_sec": round(rem, 1),
            }
        return res

    def load_registry(self) -> dict[str, list[str]]:
        """从 data/egress_registry.json 加载各大区在线活跃出口映射。"""
        if not self._registry_path.is_file():
            if self._registry.get("slots"):
                raise RegionOfflineError("托管出口注册表缺失")
            return {}
        try:
            content = self._registry_path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest == self._registry_digest:
                return self._registry_cache
            raw_data = json.loads(content)
            validate_registry(raw_data)
            if self._registry.get("slots") and not raw_data.get("slots"):
                raise ValueError("managed_registry_cannot_downgrade")
            regions = raw_data.get("regions", {})
            cleaned = {}
            for reg, plist in regions.items():
                if isinstance(plist, list):
                    cleaned[reg] = [str(p).strip() for p in plist if str(p).strip()]
            self._registry_cache = cleaned
            self._registry = raw_data
            self._registry_digest = digest
            self._registry_mtime = self._registry_path.stat().st_mtime
            request_path = self._registry_path.parent / "egress_retirement.json"
            if request_path.is_file():
                request = json.loads(request_path.read_bytes())
                for port, slot in request.get("slots", {}).items():
                    self._closed_identities.add((int(port), slot["node_id"], slot["epoch"]))
            for client, identity in self._client_identities.items():
                if identity and identity != slot_identity(self.client_proxy(client), raw_data.get("slots", {})):
                    self._closed_identities.add(identity)
                    self._circuit_breakers.pop(self.client_proxy(client), None)
            return cleaned
        except Exception as err:
            _logger.warning("[ClientPool] 读取出口注册表失败: %s", type(err).__name__)
            raise RegionOfflineError("托管出口注册表无效") from err

    def proxy_identity(self, proxy: str | None) -> tuple | None:
        self.load_registry()
        return slot_identity(proxy, self._registry.get("slots", {}))

    def proxy_eligible(self, proxy: str | None, identity: tuple | None = None) -> bool:
        self.load_registry()
        if self._registry.get("slots") and managed_port(proxy) is not None:
            current = self.proxy_identity(proxy)
            slot = self._registry["slots"].get(str(managed_port(proxy)), {})
            return bool(current and current not in self._closed_identities and
                        (identity is None or identity == current) and
                        slot.get("admission") in {"active", "fallback"})
        return identity is None

    def get_healthy_regions(self) -> set[str] | None:
        """返回当前拥有至少一个健康出口的大区集合。若无注册表文件则返回 None（全放行）。"""
        if not self._registry_path.is_file():
            return None
        reg = self.load_registry()
        return {r for r, ports in reg.items() if any(not self.is_proxy_quarantined(p) for p in ports)}

    def is_region_healthy(self, region: str | None) -> bool:
        """检查特定大区是否拥有活跃出口。未指定大区或无注册表文件则视为健康（走全局/直连）。"""
        if not region:
            return True
        if not self._registry_path.is_file():
            return True
        reg = self.load_registry()
        ports = reg.get(region, [])
        if not ports:
            return False
        return any(not self.is_proxy_quarantined(p) for p in ports)

    def resolve_proxy(self, account: Account | str | None) -> str | None:
        """解析指定账号应绑定的代理出口。

        优先级：
        1. 账号自身显式绑定的专属代理（account.proxy）
        2. 账号所属永久大区端口段（account.assigned_region）的**稳定绑定**端口，
           该端口不可用时按注册表顺序环形 failover 到同区其它合格端口
        3. 回退全局多代理池（ZCODE_PROXIES）按 account_id 一致性哈希
        4. 优雅降级返回 None（独立直连）

        绑定索引只由「注册表全量端口列表」决定，不用健康集合做哈希分母：
        否则一个端口熔断就会让账号出口漂移，已预热的 token 全部作废，且上游会
        看到同一账号在出口间跳动（见 openspec design.md D1）。
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

        # 2. 账号永久绑定的大区百位端口段：稳定基准索引 + 环形避让
        if assigned_region:
            reg = self.load_registry()
            region_proxies = reg.get(assigned_region, [])
            bound = self._bound_port(account_id, region_proxies)
            if bound:
                base = self._binding_index(account_id, region_proxies)
                if bound != region_proxies[base]:
                    _logger.warning(
                        f"[ClientPool] 账号 {account_id} 绑定出口 {region_proxies[base]} "
                        f"暂不可用，临时 failover 到 {bound}"
                    )
                return bound
            # 若该大区无可用出口，绝不跨区换节点！返回 None（调度器负责前置避让换账号）
            _logger.warning(f"[ClientPool] 账号 {account_id} 绑定大区 {assigned_region} 当前无可用健康出口")
            return None

        # 3. 回退全局 ZCODE_PROXIES
        proxies = [p for p in settings.get_global_proxies()
                   if self.proxy_eligible(p) and not self.is_proxy_quarantined(p)]
        if self._registry.get("slots"):
            proxies = list(dict.fromkeys(p for urls in self._registry_cache.values() for p in urls
                                        if self.proxy_eligible(p) and not self.is_proxy_quarantined(p)))
        if not proxies:
            return None

        if account_id:
            idx = int(hashlib.md5(account_id.encode("utf-8")).hexdigest(), 16) % len(proxies)
            return proxies[idx]

        return proxies[0]

    @staticmethod
    def _binding_index(account_id: str, region_proxies: list[str]) -> int:
        """稳定绑定索引：分母固定为该大区注册表全量端口数。

        无账号身份时退化为首个端口（保持既有匿名调用方语义）。
        """
        if not account_id or not region_proxies:
            return 0
        return int(hashlib.md5(account_id.encode("utf-8")).hexdigest(), 16) % len(region_proxies)

    def _bound_port(self, account_id: str, region_proxies: list[str]) -> str | None:
        """稳定绑定端口的环形避让求解（无日志，供高频只读路径复用）。"""
        if not region_proxies:
            return None
        base = self._binding_index(account_id, region_proxies)
        for offset in range(len(region_proxies)):
            candidate = region_proxies[(base + offset) % len(region_proxies)]
            if not self.is_proxy_quarantined(candidate) and self.proxy_eligible(candidate):
                return candidate
        return None

    def serviceable_ports(self, accounts) -> list[str]:
        """当前**可服务账号**实际绑定的出口端口集合（去重保序）。

        预热域只能来自这里：给没有可服务账号的大区/端口预热，产出的 token 无人能
        消费，却会挤占唯一求解槽并把真正要用的端口压到 sdk_failed（2026-10-13 实测
        21301 被压后 1/3 成功、同区无人压的 21303 为 3/3）。

        该路径每 3 秒被补货循环调用，故意不产生 failover/离线日志。
        """
        accounts = list(accounts or ())
        if not accounts:
            return []
        registry = self.load_registry()
        ports: list[str] = []
        for account in accounts:
            if not (account.uses_plan_channel() and account.is_selectable()):
                continue
            explicit = getattr(account, "proxy", None)
            if explicit and str(explicit).strip():
                proxy = str(explicit).strip()
            else:
                region = getattr(account, "assigned_region", None)
                proxy = (self._bound_port(account.id, registry.get(region, []))
                         if region else self.resolve_proxy(account))
            if proxy and proxy not in ports:
                ports.append(proxy)
        return ports

    def get_any_healthy_proxy(self) -> str | None:
        """获取任意一个当前健康的大区代理出口（供全局无特定账号上下文时使用，如预解池补货）。"""
        reg = self.load_registry()
        for reg_name in ("HK", "TW", "JP", "SG", "US", "KR", "EU", "OTHER"):
            ports = reg.get(reg_name)
            if ports:
                for p in ports:
                    if not self.is_proxy_quarantined(p):
                        return p
        proxies = [p for p in settings.get_global_proxies()
                   if self.proxy_eligible(p) and not self.is_proxy_quarantined(p)]
        return proxies[0] if proxies else None

    def get_healthy_proxies(self) -> list[str]:
        """获取所有健康大区的首选代理出口列表（按优先级大区收集每个大区的首选出口，排除隔离端口）。"""
        reg = self.load_registry()
        res: list[str] = []
        for reg_name in ("HK", "TW", "JP", "SG", "US", "KR", "EU", "OTHER"):
            ports = reg.get(reg_name)
            if ports:
                for p in ports:
                    if not self.is_proxy_quarantined(p):
                        res.append(p)
                        break
        if not res:
            res = [p for p in settings.get_global_proxies()
                   if self.proxy_eligible(p) and not self.is_proxy_quarantined(p)]
        return res

    @staticmethod
    def _account_id(account) -> str:
        return account if isinstance(account, str) else getattr(account, "id", "__anonymous__")

    def _get_client_locked(self, account=None, *, timeout=None, proxy=None) -> httpx.AsyncClient:
        self.load_registry()
        account_id = self._account_id(account) if proxy is None else "__proxy__:" + proxy
        explicit = getattr(account, "proxy", None)
        pair = self._clients.get(account_id)
        if pair:
            client, _ = pair
            actual = self.client_proxy(client)
            identity = self._client_identities.get(client)
            region = getattr(account, "assigned_region", None)
            region_matches = (not region or actual in self._registry_cache.get(region, []))
            if (not getattr(client, "is_closed", False) and self.proxy_eligible(actual, identity) and
                    (not explicit or explicit == actual) and (explicit or region_matches)):
                self._clients[account_id] = (client, time.time())
                return client
            self._clients.pop(account_id)
            self._retired_clients.add(client)
        actual = proxy if proxy is not None else self.resolve_proxy(account)
        if (not self.proxy_eligible(actual) or
                (not actual and (getattr(account, "assigned_region", None) or self._registry.get("slots")))):
            raise RegionOfflineError("账号大区无可用健康出口，禁止直连降级")
        req_timeout = timeout if timeout is not None else httpx.Timeout(
            connect=30.0, read=None, write=120.0, pool=30.0)
        client = httpx.AsyncClient(proxy=actual, timeout=req_timeout, trust_env=False,
                                  limits=httpx.Limits(max_keepalive_connections=10,
                                                      max_connections=20, keepalive_expiry=120.0))
        self._clients[account_id] = (client, time.time())
        self._client_proxies[client] = actual
        self._client_identities[client] = self.proxy_identity(actual)
        return client

    async def get_client(self, account=None, *, timeout=None) -> httpx.AsyncClient:
        """兼容只取句柄；真实外呼必须使用 operation 或 acquire_operation。"""
        async with self._lock:
            return self._get_client_locked(account, timeout=timeout)

    async def acquire_operation(self, account=None, *, proxy=None, identity=None,
                                parent=None, kind="operation", timeout=None) -> EgressOperation:
        """同锁读取版本、取得客户端并计引用；派生只允许有效父操作同身份。"""
        async with self._lock:
            self.load_registry()
            if parent is not None:
                if parent.pool is not self or parent.released or parent not in self._operations:
                    raise RegionOfflineError("出口父操作已结束")
                if proxy is not None and proxy != parent.proxy:
                    raise RegionOfflineError("派生操作不能切换出口")
                client = parent.client
                account_id = parent.account_id
                actual_identity = parent.identity
            else:
                if identity is not None and not self.proxy_eligible(proxy, identity):
                    raise RegionOfflineError("延后任务出口身份已过期")
                client = self._get_client_locked(account, timeout=timeout, proxy=proxy)
                account_id = self._account_id(account) if proxy is None else "__proxy__:" + proxy
                actual_identity = self._client_identities.get(client)
            operation = EgressOperation(self, client, account_id, self.client_proxy(client),
                                        actual_identity, self._registry.get("generation"), kind)
            self._operations.add(operation)
            return operation

    async def release_operation(self, operation: EgressOperation) -> None:
        async with self._lock:
            if operation.pool is not self or operation.released:
                return
            operation.released = True
            self._operations.discard(operation)
            await self._close_retired_locked()

    @asynccontextmanager
    async def operation(self, account=None, *, proxy=None, timeout=None):
        """嵌套的同账号调用透传已准入身份，任务继承的过期上下文不能复活。"""
        parent = _current_operation.get()
        if (parent and parent.pool is self and not parent.released and
                ((proxy is not None and parent.proxy == proxy) or
                 (proxy is None and parent.account_id == self._account_id(account)))):
            yield parent
            return
        operation = await self.acquire_operation(account, proxy=proxy, timeout=timeout)
        token = _current_operation.set(operation)
        try:
            yield operation
        finally:
            _current_operation.reset(token)
            await self.release_operation(operation)

    @staticmethod
    def current_operation() -> EgressOperation | None:
        operation = _current_operation.get()
        return operation if operation and not operation.released else None

    async def _close_retired_locked(self) -> int:
        closed = 0
        referenced = {op.client for op in self._operations}
        for client in self._retired_clients - referenced:
            if not getattr(client, "is_closed", False):
                await client.aclose()
            self._retired_clients.remove(client)
            self._client_proxies.pop(client, None)
            self._client_identities.pop(client, None)
            closed += 1
        return closed

    async def prune_idle(self) -> int:
        async with self._lock:
            self.load_registry()
            referenced = {op.client for op in self._operations}
            for account_id, (client, last_active) in list(self._clients.items()):
                if (not self.proxy_eligible(self.client_proxy(client), self._client_identities.get(client)) or
                        (time.time() - last_active > self._idle_timeout and client not in referenced)):
                    self._clients.pop(account_id)
                    self._retired_clients.add(client)
            return await self._close_retired_locked()

    async def close_account(self, account_id: str) -> None:
        async with self._lock:
            pair = self._clients.pop(account_id, None)
            if pair:
                self._retired_clients.add(pair[0])
            await self._close_retired_locked()

    async def aclose(self) -> None:
        async with self._lock:
            self._retired_clients.update(client for client, _ in self._clients.values())
            self._clients.clear()
            await self._close_retired_locked()

    async def coordinate_retirement(self, captcha, accounts) -> None:
        """无前台流量时也关闭旧准入、清缓存并发布当前进程确认。"""
        if self._consumer is None:
            return
        async with self._lock:
            self.load_registry()
            captcha.retire_egress(self)
            for account_id, (client, _) in list(self._clients.items()):
                if not self.proxy_eligible(self.client_proxy(client), self._client_identities.get(client)):
                    self._clients.pop(account_id)
                    self._retired_clients.add(client)
            await self._close_retired_locked()
            directory = self._registry_path.parent / "egress_consumers"
            consumer = dict(self._consumer, generation=self._registry.get("generation"),
                            fixed_ports=sorted({p for a in accounts if (p := managed_port(a.proxy)) is not None}))
            write_private_json(directory / (consumer["boot_id"] + ".json"), consumer)
            request_path = self._registry_path.parent / "egress_retirement.json"
            if not request_path.is_file():
                return
            request = json.loads(request_path.read_bytes())
            if (request.get("generation") != consumer["generation"] or
                    request.get("consumers", {}).get(consumer["boot_id"]) != self._consumer):
                return
            counts = {}
            for port, slot in request["slots"].items():
                identity = (int(port), slot["node_id"], slot["epoch"])
                current = self._registry.get("slots", {}).get(port)
                operations = [op for op in self._operations if op.identity == identity]
                caches = sum(v == identity for v in self._client_identities.values())
                admission_closed = (current == slot or current and current.get("admission") == "empty"
                                    and current.get("epoch") == slot["epoch"])
                self._closed_identities.add(identity)
                counts[port] = dict(slot, admission_closed=bool(admission_closed),
                                    operations=sum(op.kind == "operation" for op in operations),
                                    producers=sum(op.kind == "producer" for op in operations),
                                    caches=caches, tokens=captcha.egress_token_count(identity),
                                    fixed=int(int(port) in consumer["fixed_ports"]))
            write_private_json(directory / (consumer["boot_id"] + ".ack.json"),
                               dict(self._consumer, request_id=request["request_id"],
                                    generation=consumer["generation"], slots=counts))

    async def start_coordinator(self, captcha, store) -> None:
        self.load_registry()
        if os.name != "posix":
            return
        self._consumer = {"protocol": PROTOCOL_VERSION, "pid": os.getpid(),
                          "start": process_identity(os.getpid()), "boot_id": secrets.token_hex(16)}
        await self.coordinate_retirement(captcha, store.list_accounts())

        async def run():
            while True:
                try:
                    await self.coordinate_retirement(captcha, store.list_accounts())
                except Exception as err:
                    _logger.warning("[ClientPool] 排空协调失败: %s", type(err).__name__)
                await asyncio.sleep(1)

        self._coordinator_task = asyncio.create_task(run())

    async def stop_coordinator(self) -> None:
        if self._coordinator_task:
            self._coordinator_task.cancel()
            await asyncio.gather(self._coordinator_task, return_exceptions=True)
            self._coordinator_task = None
        if self._consumer:
            directory = self._registry_path.parent / "egress_consumers"
            for suffix in (".json", ".ack.json"):
                (directory / (self._consumer["boot_id"] + suffix)).unlink(missing_ok=True)
            self._consumer = None


# 模块级单例连接池
account_client_pool = AccountClientPool()
