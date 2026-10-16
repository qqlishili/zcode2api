"""按账号、用途及实际出口身份隔离的 HTTP 客户端池。

用途从启动快照选路；继承代理保持已有稳定绑定，显式代理失败禁止切直连。
客户端、操作及验证码 producer 共用物理身份引用，排空后才关闭旧连接。
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
from urllib.parse import urlsplit

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
from .egress_policy import ClaimBinding, EgressPolicy

if TYPE_CHECKING:
    from .models import Account

_logger = logging.getLogger("zcode.pool")
_UNRESOLVED = object()

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
    purpose: str | None = None
    kind: str = "operation"
    released: bool = False


_current_operation: ContextVar[EgressOperation | None] = ContextVar("egress_operation", default=None)


class AccountClientPool:
    """管理每个账号专属的 httpx.AsyncClient 实例。"""

    def __init__(
        self,
        idle_timeout: float | None = None,
        registry_path: Path | str | None = None,
        policy: EgressPolicy | None = None,
    ) -> None:
        # (account_id, purpose, proxy, identity) -> (client, last_active_timestamp)
        self._clients: dict[tuple, tuple[httpx.AsyncClient, float]] = {}
        self._policy = policy
        self._closed_accounts: set[str] = set()
        self._client_purposes: dict[httpx.AsyncClient, str | None] = {}
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
        # 物理退役共享，业务断路器按用途与节点代次隔离。
        self._circuit_breakers: dict[tuple, dict] = {}

    @property
    def policy(self) -> EgressPolicy:
        if self._policy is None:
            from .store import store
            self._policy = store.active_egress_policy
        return self._policy

    def effective_mode(self, purpose: str | None = None) -> str:
        return self.policy.mode(purpose, settings.EGRESS_MODE)

    def _breaker_key(self, proxy, purpose=None, identity=None) -> tuple:
        return purpose or "chat", proxy, identity if identity is not None else self.proxy_identity(proxy)

    def claim_binding_eligible(self, binding: ClaimBinding) -> bool:
        self.load_registry()
        slot = self._registry.get("slots", {}).get(str(binding.port), {})
        if (slot_identity(binding.proxy, self._registry.get("slots", {})) != binding.identity or
                slot.get("region") != binding.region or slot.get("admission") == "empty" or
                binding.identity in self._closed_identities):
            return False
        try:
            lifecycle = json.loads((self._registry_path.parent / "node_lifecycle.json").read_bytes())
            node = lifecycle["nodes"][binding.node_id]
            if not (isinstance(node, dict) and node.get("port") == binding.port and
                    node.get("epoch") == binding.epoch and node.get("region") == binding.region and
                    isinstance(node.get("sources"), list) and node["sources"] and
                    not node.get("retiring") and not node.get("reclaimed")):
                return False
            # 生命周期与路由文件不是单事务；校验期间路由变化则本次拒绝，不能拼版本。
            digest = hashlib.sha256(self._registry_path.read_bytes()).hexdigest()
            return digest == self._registry_digest
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def record_proxy_result(self, proxy_url: str, success: bool, is_waf_block: bool = False, *,
                            purpose=None, identity=None) -> None:
        """仅记录实际网络结果，驱动三态断路器状态机。"""
        if not proxy_url:
            return
        st = self._circuit_breakers.setdefault(self._breaker_key(proxy_url, purpose, identity), {
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

    def is_proxy_quarantined(self, proxy_url: str, *, purpose=None, identity=None) -> bool:
        """检查代理端口当前是否处于熔断隔离状态。"""
        if not proxy_url:
            return False
        st = self._circuit_breakers.get(self._breaker_key(proxy_url, purpose, identity))
        if not st or st["state"] == "CLOSED":
            return False
        return (st["state"] == "OPEN" and time.monotonic() < st["quarantine_until"]
                or st["state"] == "HALF-OPEN" and st["half_open_active"])

    def client_proxy(self, client: httpx.AsyncClient) -> str | None:
        """返回客户端实际出口，避免验证码重新哈希导致漂移。"""
        return self._client_proxies.get(client)

    def begin_request(self, client: httpx.AsyncClient) -> bool:
        """实际联网前按用途及身份取得唯一半开资格。"""
        proxy = self.client_proxy(client)
        if not proxy:
            return False
        purpose = self._client_purposes.get(client)
        identity = self._client_identities.get(client)
        if not self.proxy_eligible(proxy, identity, purpose=purpose):
            raise RegionOfflineError("出口身份已失效")
        if self.is_proxy_quarantined(proxy, purpose=purpose, identity=identity):
            raise RegionOfflineError("账号出口暂时不可用")
        st = self._circuit_breakers.get(self._breaker_key(proxy, purpose, identity))
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
        purpose = self._client_purposes.get(client)
        st = self._circuit_breakers.get(self._breaker_key(proxy, purpose, identity))
        # 旧 CLOSED 结果不能替另一个探针结算或关闭隔离。
        if st and st["state"] != "CLOSED" and not probe:
            return
        if success is not None:
            self.record_proxy_result(proxy, success, is_waf_block, purpose=purpose, identity=identity)
        elif probe and st and st["state"] == "HALF-OPEN":
            st["half_open_active"] = False

    def get_quarantine_status(self) -> dict[str, dict]:
        """返回所有代理断路器的实时健康与隔离状态（供运维/Admin 诊断观测）。"""
        now = time.monotonic()
        res = {}
        for (purpose, p, identity), st in self._circuit_breakers.items():
            rem = max(0.0, st["quarantine_until"] - now) if st["state"] == "OPEN" else 0.0
            key = p if purpose == "chat" else f"{purpose}:{p}"
            res[key] = {
                "purpose": purpose, "identity": identity,
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
            request_path = self._registry_path.parent / "egress_retirement.json"
            if request_path.is_file():
                request = json.loads(request_path.read_bytes())
                for port, slot in request.get("slots", {}).items():
                    self._closed_identities.add((int(port), slot["node_id"], slot["epoch"]))
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
                    for key in list(self._circuit_breakers):
                        if key[2] == identity:
                            self._circuit_breakers.pop(key)
            return cleaned
        except Exception as err:
            _logger.warning("[ClientPool] 读取出口注册表失败: %s", type(err).__name__)
            raise RegionOfflineError("托管出口注册表无效") from err

    def proxy_identity(self, proxy: str | None) -> tuple | None:
        self.load_registry()
        return slot_identity(proxy, self._registry.get("slots", {}))

    def proxy_eligible(self, proxy: str | None, identity: tuple | None = None, *, purpose=None) -> bool:
        self.load_registry()
        if self._registry.get("slots") and managed_port(proxy) is not None:
            current = self.proxy_identity(proxy)
            slot = self._registry["slots"].get(str(managed_port(proxy)), {})
            if not (current and current not in self._closed_identities and
                    (identity is None or identity == current)):
                return False
            if purpose == "claim" and self.policy.claim_mode == "proxy":
                binding = next((b for b in self.policy.claim_bindings if b.identity == current), None)
                return bool(binding and self.claim_binding_eligible(binding))
            return slot.get("admission") in {"active", "fallback"}
        return identity is None

    def get_healthy_regions(self, *, purpose="chat") -> set[str] | None:
        """调度只看所请求用途；直连和共享 chat 端点不受账号大区过滤。"""
        if self.effective_mode(purpose) == "direct" or (purpose == "chat" and self.policy.chat_mode == "proxy"):
            return None
        if purpose == "claim" and self.policy.claim_mode == "proxy":
            return {b.region for b in self.policy.claim_bindings if self.claim_binding_eligible(b) and
                    not self.is_proxy_quarantined(b.proxy, purpose=purpose, identity=b.identity)}
        if not self._registry_path.is_file():
            return None
        reg = self.load_registry()
        return {r for r, ports in reg.items() if any(
            self.proxy_eligible(p, purpose=purpose) and not self.is_proxy_quarantined(p, purpose=purpose)
            for p in ports)}

    def is_region_healthy(self, region: str | None, *, purpose="chat") -> bool:
        regions = self.get_healthy_regions(purpose=purpose)
        if purpose == "claim" and self.policy.claim_mode == "proxy" and not region:
            return False
        return regions is None or not region or region in regions

    def resolve_proxy(self, account: Account | str | None, *, purpose=None) -> str | None:
        """先应用用途快照，再按账号/同区/全局解析继承代理；不跨区降级。"""
        if self.effective_mode(purpose) == "direct":
            return None
        if purpose == "chat" and self.policy.chat_mode == "proxy":
            return self.policy.chat_proxy_url
        if purpose == "claim" and self.policy.claim_mode == "proxy":
            region = getattr(account, "assigned_region", None)
            binding = self.policy.binding(region)
            if binding is None:
                raise RegionOfflineError(f"claim 未配置 {region or '未分配地区'} 领取出口")
            if (not self.claim_binding_eligible(binding) or
                    self.is_proxy_quarantined(binding.proxy, purpose=purpose, identity=binding.identity)):
                raise RegionOfflineError(f"claim {region} 固定领取出口不可用")
            return binding.proxy
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
            bound = self._bound_port(account_id, region_proxies, purpose=purpose)
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
                   if self.proxy_eligible(p, purpose=purpose) and
                   not self.is_proxy_quarantined(p, purpose=purpose)]
        if self._registry.get("slots"):
            proxies = list(dict.fromkeys(p for urls in self._registry_cache.values() for p in urls
                                        if self.proxy_eligible(p, purpose=purpose) and
                                        not self.is_proxy_quarantined(p, purpose=purpose)))
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

    def _bound_port(self, account_id: str, region_proxies: list[str], *, purpose=None) -> str | None:
        """稳定绑定端口的环形避让求解（无日志，供高频只读路径复用）。"""
        if not region_proxies:
            return None
        base = self._binding_index(account_id, region_proxies)
        for offset in range(len(region_proxies)):
            candidate = region_proxies[(base + offset) % len(region_proxies)]
            if (not self.is_proxy_quarantined(candidate, purpose=purpose) and
                    self.proxy_eligible(candidate, purpose=purpose)):
                return candidate
        return None

    def serviceable_ports(self, accounts) -> list[str | None]:
        """周期补货只取 selectable Plan 的 chat 实际出口，去重保序。"""
        accounts = [a for a in accounts or () if a.uses_plan_channel() and a.is_selectable()]
        if not accounts:
            return []
        if self.effective_mode("chat") == "direct":
            return [None]
        if self.policy.chat_mode == "proxy":
            proxy = self.policy.chat_proxy_url
            return [proxy] if (self.proxy_eligible(proxy, purpose="chat") and
                               not self.is_proxy_quarantined(proxy, purpose="chat")) else []
        registry = self.load_registry()
        ports: list[str | None] = []
        for account in accounts:
            cached = next((key[2] for key in self._clients if key[:2] == (account.id, "chat") and
                           self.proxy_eligible(key[2], key[3], purpose="chat")), None)
            explicit = getattr(account, "proxy", None)
            region = getattr(account, "assigned_region", None)
            proxy = cached or (str(explicit).strip() if explicit and str(explicit).strip() else (
                self._bound_port(account.id, registry.get(region, []), purpose="chat")
                if region else self.resolve_proxy(account, purpose="chat")))
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

    def _get_client_locked(self, account=None, *, timeout=None, proxy=_UNRESOLVED, purpose=None) -> httpx.AsyncClient:
        self.load_registry()
        account_id = self._account_id(account)
        if account_id in self._closed_accounts:
            raise RegionOfflineError("账号已关闭，不能取得新客户端")
        direct = self.effective_mode(purpose) == "direct"
        inherited = purpose is None or getattr(self.policy, f"{purpose}_mode") == "inherit"
        if proxy is _UNRESOLVED and inherited:
            for key, (client, _) in self._clients.items():
                if key[:2] != (account_id, purpose) or getattr(client, "is_closed", False):
                    continue
                actual, identity = key[2:]
                region = getattr(account, "assigned_region", None)
                explicit = getattr(account, "proxy", None)
                matches = actual is None if direct else (
                    actual == explicit if explicit else not region or actual in self._registry_cache.get(region, []))
                if matches and self.proxy_eligible(actual, identity, purpose=purpose):
                    self._clients[key] = (client, time.time())
                    return client
        actual = None if direct else ((self.resolve_proxy(account, purpose=purpose) if purpose is not None
                                       else self.resolve_proxy(account)) if proxy is _UNRESOLVED else proxy)
        if purpose == "claim" and self.policy.claim_mode == "proxy" and account is not None:
            expected = self.resolve_proxy(account, purpose=purpose)
            if proxy is not _UNRESOLVED and proxy != expected:
                raise RegionOfflineError("claim 不能改变固定领取出口")
            actual = expected
        identity = self.proxy_identity(actual)
        if (not self.proxy_eligible(actual, identity, purpose=purpose) or
                (not direct and not actual and
                 (getattr(account, "assigned_region", None) or self._registry.get("slots")))):
            raise RegionOfflineError(f"{purpose or 'legacy'} 无可用健康出口，禁止直连降级")
        key = (account_id, purpose, actual, identity)
        pair = self._clients.get(key)
        if pair and not getattr(pair[0], "is_closed", False):
            self._clients[key] = (pair[0], time.time())
            return pair[0]
        # 路由/身份变化只淘汰同账号同用途缓存，在途引用继续持有旧客户端。
        for old_key, (client, _) in list(self._clients.items()):
            if old_key[:2] == key[:2]:
                self._clients.pop(old_key)
                self._retired_clients.add(client)
        req_timeout = timeout if timeout is not None else httpx.Timeout(
            connect=30.0, read=None, write=120.0, pool=30.0)
        client = httpx.AsyncClient(proxy=actual, timeout=req_timeout, trust_env=False,
                                  limits=httpx.Limits(max_keepalive_connections=10,
                                                      max_connections=20, keepalive_expiry=120.0))
        self._clients[key] = (client, time.time())
        self._client_proxies[client] = actual
        self._client_identities[client] = identity
        self._client_purposes[client] = purpose
        _logger.info("[ClientPool] purpose=%s mode=%s egress=%s port=%s",
                     purpose or "legacy", self.effective_mode(purpose),
                     "proxy" if actual else "direct", urlsplit(actual).port if actual else None)
        return client

    async def get_client(self, account=None, *, timeout=None, purpose=None) -> httpx.AsyncClient:
        """兼容只取句柄；真实外呼必须使用 operation 或 acquire_operation。"""
        async with self._lock:
            return self._get_client_locked(account, timeout=timeout, purpose=purpose)

    async def acquire_operation(self, account=None, *, proxy=_UNRESOLVED, identity=None,
                                parent=None, kind="operation", timeout=None, purpose=None) -> EgressOperation:
        """同锁读取版本、取得客户端并计引用；producer 只派生父用途和实际出口。"""
        async with self._lock:
            self.load_registry()
            if parent is not None:
                if parent.pool is not self or parent.released or parent not in self._operations:
                    raise RegionOfflineError("出口父操作已结束")
                if ((proxy is not _UNRESOLVED and proxy != parent.proxy) or
                        (identity is not None and identity != parent.identity) or
                        (purpose is not None and purpose != parent.purpose) or
                        (account is not None and self._account_id(account) != parent.account_id)):
                    raise RegionOfflineError("派生操作不能切换账号、用途或出口")
                client, account_id, actual_identity = parent.client, parent.account_id, parent.identity
                purpose = parent.purpose
            else:
                if identity is not None and not self.proxy_eligible(proxy, identity, purpose=purpose):
                    raise RegionOfflineError("延后任务出口身份已过期")
                client = self._get_client_locked(account, timeout=timeout, proxy=proxy, purpose=purpose)
                account_id = self._account_id(account)
                actual_identity = self._client_identities.get(client)
                if identity is not None and identity != actual_identity:
                    raise RegionOfflineError("延后任务实际出口身份不一致")
            operation = EgressOperation(self, client, account_id, self.client_proxy(client),
                                        actual_identity, self._registry.get("generation"), purpose, kind)
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
    async def operation(self, account=None, *, proxy=_UNRESOLVED, timeout=None, purpose=None):
        """同账号同用途嵌套透传；异步继承的其它用途不能复用父操作。"""
        parent = _current_operation.get()
        if (parent and parent.pool is self and not parent.released and
                parent.account_id == self._account_id(account) and parent.purpose == purpose and
                (proxy is _UNRESOLVED or parent.proxy == proxy)):
            yield parent
            return
        operation = await self.acquire_operation(account, proxy=proxy, timeout=timeout, purpose=purpose)
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
            self._client_purposes.pop(client, None)
            closed += 1
        return closed

    async def prune_idle(self) -> int:
        async with self._lock:
            self.load_registry()
            referenced = {op.client for op in self._operations}
            for account_id, (client, last_active) in list(self._clients.items()):
                if (not self.proxy_eligible(self.client_proxy(client), self._client_identities.get(client),
                                            purpose=self._client_purposes.get(client)) or
                        (time.time() - last_active > self._idle_timeout and client not in referenced)):
                    self._clients.pop(account_id)
                    self._retired_clients.add(client)
            return await self._close_retired_locked()

    async def close_account(self, account_id: str) -> None:
        async with self._lock:
            self._closed_accounts.add(account_id)
            for key, (client, _) in list(self._clients.items()):
                if key[0] == account_id:
                    self._clients.pop(key)
                    self._retired_clients.add(client)
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
                if not self.proxy_eligible(self.client_proxy(client), self._client_identities.get(client),
                                            purpose=self._client_purposes.get(client)):
                    self._clients.pop(account_id)
                    self._retired_clients.add(client)
            await self._close_retired_locked()
            directory = self._registry_path.parent / "egress_consumers"
            consumer = dict(self._consumer, generation=self._registry.get("generation"),
                            fixed_ports=sorted({p for a in accounts if (p := managed_port(a.proxy)) is not None} |
                                               {b.port for b in self.policy.claim_bindings
                                                if self.policy.claim_mode == "proxy"}))
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
