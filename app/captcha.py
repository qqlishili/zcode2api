"""验证码求解 + 代理亲和预解水库 + Singleflight 单飞防惊群。

通过 Node 子进程在 happy-dom 模拟浏览器环境中运行阿里云无痕 SDK，
求得 verifyParam（X-Aliyun-Captcha-Verify-Param）。

架构特性（pre-solved-regional-captcha-pool 规范）：
- 代理亲和物理隔离：按 (region, proxy_url) 精确隔离预解队列，保证出网 IP 强一致，彻底杜绝阿里 WAF Remote IP 不匹配判定（F001/3007）。
- 靶向清池（Scoped Invalidation）：单端口 3007 仅清除目标端口缓存，严禁跨大区株连。
- Singleflight 防惊群：空仓并发合并为单次现解，结果一次性广播并异步补仓。
- 自适应 CPU 保护：单核 VPS 强制并发度为 1，保护 SSE 长流稳定。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import secrets
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from . import constants, evidence, logs, settings
from .store import store

if TYPE_CHECKING:
    from .models import Account

_sleep = asyncio.sleep

# 每端口水位：见 settings.py 注释与 openspec/changes/pin-captcha-egress-and-refill-domain design.md D3
TOKEN_TTL_MS = settings.CAPTCHA_TOKEN_TTL  # 单枚 token 的最大可用时长（ms）


class CaptchaSolveError(Exception):
    """验证码求解最终失败（重试耗尽/求解器不可用）。

    独立于 ClaimError 体系（验证码先于领域层使用），路由层按兜底回执处理。
    """

    def __init__(self, message: str, diagnostics: dict | None = None) -> None:
        super().__init__(message)
        self.diagnostics = safe_diagnostics(diagnostics)


_SOLVER_CODES = {
    "param_empty": (4, "extract"), "param_too_short": (6, "extract"),
    "param_missing_security_token": (6, "extract"), "param_not_base64_json": (6, "extract"),
    "solver_timeout": (2, "run"), "solver_stalled": (2, "run"),
    "sdk_init_failed": (3, "init"), "sdk_failed": (4, "run"),
    "sdk_on_error": (5, "run"), "solver_unclassified": (4, "run"),
}
_LOCAL_CODES = {"param_invalid_unknown", "solver_failed_unknown", "solver_no_output",
                "solver_unavailable", "solver_queue_timeout", "solver_budget_exhausted",
                "solver_port_backoff"}
_STAGES = {"init", "run", "extract", "config", "queue", "retry", "cleanup"}
_TIME_FIELDS = {"queue_wait_ms", "solve_ms", "config_ms", "retry_ms", "cleanup_ms", "elapsed_ms"}


def safe_diagnostics(value: object) -> dict:
    """管理证据仍是信任边界，只接受固定类别和有限数字。"""
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("reason", "last_solver_code"):
        if isinstance(value.get(key), str) and value[key] in _SOLVER_CODES.keys() | _LOCAL_CODES:
            result[key] = value[key]
    if isinstance(value.get("stage"), str) and value["stage"] in _STAGES:
        result["stage"] = value["stage"]
    if isinstance(value.get("stop_reason"), str) and value["stop_reason"] in {"attempts_exhausted", "queue_timeout", "budget_exhausted", "unavailable"}:
        result["stop_reason"] = value["stop_reason"]
    solve_id = value.get("solve_id")
    if isinstance(solve_id, str) and re.fullmatch(r"solve-[0-9a-f]{16}", solve_id):
        result["solve_id"] = solve_id
    for key in _TIME_FIELDS | {"attempts", "param_chars", "exit_code"}:
        number = value.get(key)
        if type(number) in (int, float) and 0 <= number <= 2**53 - 1 and math.isfinite(number):
            result[key] = number
        elif key in value and number is None:
            result[key] = None
    return result


class _SolverError(RuntimeError):
    def __init__(self, code: str, stage: str = "run", **details) -> None:
        super().__init__(code)
        self.diagnostics = safe_diagnostics({"reason": code, "stage": stage, **details})


@dataclass
class _Solve:
    deadline: float
    task: asyncio.Task | None = None
    foreground: int = 0
    refill: int = 0
    details: dict = field(default_factory=dict)
    operation: object = None


_current_solve: ContextVar[_Solve | None] = ContextVar("captcha_solve", default=None)


def _solver_summary(stderr: bytes, returncode: int) -> dict:
    fallback = {"reason": "param_invalid_unknown" if returncode == 6 else
                "solver_no_output" if returncode == 0 else "solver_failed_unknown",
                "stage": "extract" if returncode == 6 else "run", "exit_code": returncode if returncode >= 0 else None}
    for line in stderr.splitlines():
        if not line.startswith(b"SOLVER_DIAG=") or len(line) > 2048:
            continue
        try:
            diag = json.loads(line[len(b"SOLVER_DIAG="):])
            if not isinstance(diag, dict) or set(diag) != {"v", "code", "stage", "exit_code", "param_chars", "elapsed_ms"}:
                continue
            code = diag["code"]
            if (type(diag["v"]) is not int or diag["v"] != 1 or not isinstance(code, str)
                    or code not in _SOLVER_CODES or type(diag["exit_code"]) is not int
                    or (diag["exit_code"], diag["stage"]) != _SOLVER_CODES[code]
                    or diag["exit_code"] != returncode):
                continue
            if any(n is not None and (type(n) is not int or n < 0) for n in (diag["param_chars"], diag["elapsed_ms"])):
                continue
            return {"reason": code, "stage": diag["stage"], "exit_code": returncode,
                    "param_chars": diag["param_chars"]}
        except (ValueError, TypeError):
            continue
    return fallback


class _Token:
    __slots__ = ("param", "region", "proxy", "born_at", "certify_id", "identity")

    def __init__(self, param: str, region: str | None, proxy: str | None = None,
                 certify_id: str | None = None, identity: tuple | None = None) -> None:
        self.param = param
        self.region = region
        self.proxy = proxy
        self.born_at = time.monotonic()
        self.certify_id = certify_id or self._extract_certify_id(param)
        self.identity = identity

    @staticmethod
    def _extract_certify_id(param: str) -> str | None:
        try:
            import base64
            import json
            decoded = base64.b64decode(param + "==").decode("utf-8", "ignore")
            data = json.loads(decoded)
            if isinstance(data, dict):
                return data.get("certifyId") or data.get("certify_id")
        except Exception:
            pass
        return None

    def expired(self) -> bool:
        return (time.monotonic() - self.born_at) * 1000 >= TOKEN_TTL_MS


class _LegacyPoolShim:
    """向后兼容历史测试直接访问 _pool 队列。"""
    def __init__(self, manager: CaptchaManager):
        self._mgr = manager

    def get_nowait(self) -> _Token:
        for q in self._mgr._proxy_pools.values():
            while q:
                t = q.popleft()
                if not t.expired():
                    return t
        raise asyncio.QueueEmpty()

    def put_nowait(self, token: _Token) -> None:
        self._mgr._put(token)


class CaptchaManager:
    def __init__(self) -> None:
        # proxy_url -> deque[_Token] 物理代理亲和水库（按生成时间保序）
        self._proxy_pools: dict[str, deque[_Token]] = defaultdict(deque)
        # 向后兼容测试访问 _pool
        self._pool = _LegacyPoolShim(self)
        # producer 独立持有预算与进程；等待者不拥有它的取消权。
        self._in_flight_solves: dict[str, _Solve] = {}
        # certifyId 去重注册表：certify_id -> timestamp (120s TTL)
        self._seen_certify_ids: dict[str, float] = {}

        self._refill_task: asyncio.Task | None = None
        self._refilling = False
        self._solve_lock = asyncio.Lock()
        self._config_lock = asyncio.Lock()
        self._config_cache: dict | None = None
        self._config_cache_at: float = 0.0
        self._last_error: str | None = None
        # 热路径触发的补货任务强引用（事件循环只持弱引用，裸 create_task 会被 GC）
        self._bg_tasks: set[asyncio.Task] = set()
        self._refill_pending: dict[str, asyncio.Task] = {}
        self._foreground_demand = 0
        self._closed = False
        # 端口级求解退避（proxy -> 连续失败计数 / 冷却截止单调时钟）
        self._port_failures: dict[str, int] = {}
        self._port_cooldown: dict[str, float] = {}

        # CPU 自适应并发信号量：单核 VPS 强制钳制为 1，多核为 max(1, cpu_count - 1)
        cores = os.cpu_count() or 1
        concurrency = 1 if cores <= 1 else max(1, cores - 1)
        self._cpu_semaphore = asyncio.Semaphore(concurrency)

    @property
    def _pool_size(self) -> int:
        return sum(len(q) for q in self._proxy_pools.values())

    @_pool_size.setter
    def _pool_size(self, val: int) -> None:
        pass

    def _put(self, token: _Token) -> None:
        key = token.proxy or "__default__"
        q = self._proxy_pools[key]
        if len(q) < settings.CAPTCHA_REGIONAL_POOL_MAX:
            q.append(token)
            if token.certify_id:
                self._seen_certify_ids[token.certify_id] = time.monotonic()

    # ── 端口级失败退避 ───────────────────────────────────────────────────────
    def _note_port_success(self, proxy: str | None) -> None:
        key = proxy or "__default__"
        self._port_failures.pop(key, None)
        self._port_cooldown.pop(key, None)

    def _note_port_failure(self, proxy: str | None) -> None:
        """同端口连续求解失败达阈值即退避（随机窗口避免多端口同步恢复）。

        只作用于求解调度：不动出口断路器、不改账号状态（那些只由真实网络/WAF证据推进）。
        """
        key = proxy or "__default__"
        count = self._port_failures.get(key, 0) + 1
        if count < max(1, settings.CAPTCHA_PORT_FAIL_THRESHOLD):
            self._port_failures[key] = count
            return
        window = max(1.0, float(settings.CAPTCHA_PORT_COOLDOWN)) * random.uniform(0.75, 1.5)
        self._port_cooldown[key] = time.monotonic() + window
        self._port_failures[key] = 0
        logs.warn("captcha", f"端口 {key} 连续 {count} 次求解失败，退避 {window:.0f}s")

    def port_backoff_remaining(self, proxy: str | None) -> float:
        key = proxy or "__default__"
        until = self._port_cooldown.get(key)
        if not until:
            return 0.0
        remaining = until - time.monotonic()
        if remaining <= 0:
            self._port_cooldown.pop(key, None)
            return 0.0
        return remaining

    # ── 配置 ─────────────────────────────────────────────────────────────────
    async def fetch_config(self, *, operation=None) -> dict:
        now = time.time() * 1000
        if self._config_cache and now - self._config_cache_at < settings.CAPTCHA_CONFIG_CACHE_TTL:
            return self._config_cache
        async with self._config_lock:
            # 双检：等锁期间可能已被其他请求填充
            if self._config_cache and time.time() * 1000 - self._config_cache_at < settings.CAPTCHA_CONFIG_CACHE_TTL:
                return self._config_cache
            try:
                from .client_pool import account_client_pool
                operation = operation or account_client_pool.current_operation()
                if operation is not None:
                    res = await operation.client.get(
                        f"{constants.CLIENT_CONFIGS_URL}?{constants.CLIENT_CONFIGS_QUERY}", timeout=15)
                else:
                    async with account_client_pool.operation(timeout=15) as admitted:
                        res = await admitted.client.get(
                            f"{constants.CLIENT_CONFIGS_URL}?{constants.CLIENT_CONFIGS_QUERY}")
                res.raise_for_status()
                captcha = ((res.json().get("data") or {}).get("configs") or {}).get("captcha")
                if captcha:
                    self._config_cache = captcha
                    self._config_cache_at = time.time() * 1000
                    return captcha
            except (httpx.HTTPError, ValueError):
                logs.warn("captcha", "获取配置失败，使用默认")
            return dict(constants.CAPTCHA_DEFAULTS)

    # ── 预解池生命周期 ───────────────────────────────────────────────────────
    def start(self) -> None:
        """启动后台补充循环（main.py lifespan 调用）。"""
        self._closed = False
        if self._refill_task is None or self._refill_task.done():
            self._refill_task = asyncio.create_task(self._refill_loop())

    async def close(self) -> None:
        self._closed = True
        solves = list(self._in_flight_solves.values())
        tasks = self._bg_tasks | {s.task for s in solves if s.task}
        if self._refill_task:
            tasks.add(self._refill_task)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # 协程开始前取消不会执行 producer 的 finally，回收其已取得引用。
        for solve in solves:
            if not solve.operation.released:
                await solve.operation.pool.release_operation(solve.operation)
        self._refill_task = None
        self._bg_tasks.clear()
        self._refill_pending.clear()
        self._in_flight_solves.clear()

    def _schedule_refill(self, proxy: str | None, *, identity=None) -> None:
        from .client_pool import account_client_pool

        if not account_client_pool.proxy_eligible(proxy, identity):
            return
        identity = identity if identity is not None else account_client_pool.proxy_identity(proxy)
        key = proxy or "__default__"
        if self._closed or key in self._refill_pending:
            return
        task = asyncio.create_task(self._refill_single_proxy(proxy, identity=identity))
        self._bg_tasks.add(task)
        self._refill_pending[key] = task

        def done(finished):
            self._bg_tasks.discard(finished)
            self._refill_pending.pop(key, None)
            if not finished.cancelled():
                finished.exception()

        task.add_done_callback(done)

    def _gate_open(self) -> bool:
        """是否允许预热：存在可走 Plan 对话的 jwt 账号才预热。"""
        return any(
            a.allows_billing() and a.is_selectable()
            for a in store.list_accounts("zai")
        )

    def _refill_targets(self) -> list[str]:
        """预热域 = 当前可服务账号**实际绑定**的出口端口集合。

        不再使用「每个大区首个健康端口」：那样会给没有可服务账号的大区白造 token，
        并把这些首端口压到 sdk_failed（2026-10-13 实测 21301 仅 1/3 成功，
        同大区无人压的 21303 为 3/3、单次 6.9s）。大区本身离线时返回空集合，
        由调度器按栅栏原则换号，而不是预热一个账号根本用不到的端口。
        """
        from .client_pool import account_client_pool

        return account_client_pool.serviceable_ports(store.list_accounts("zai"))

    async def _refill_loop(self) -> None:
        while True:
            try:
                if not self._gate_open():
                    self._evict_expired()
                    await asyncio.sleep(3)
                    continue
                self._evict_expired()
                for p in self._refill_targets():
                    key = p or "__default__"
                    if len(self._proxy_pools[key]) < settings.CAPTCHA_REGIONAL_POOL_MIN:
                        self._schedule_refill(p)
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 后台循环永不退出
                logs.warn("captcha", "补充循环异常")
                await asyncio.sleep(5)

    async def _refill_batch(self, need: int = 1) -> None:
        """兼容性批量补货接口：按需补充，并在连续求解之间错峰 0.4~1.0s。"""
        if self._refilling:
            return
        self._refilling = True
        try:
            from .client_pool import account_client_pool
            candidate_proxies = self._refill_targets()
            if not candidate_proxies:
                cand = account_client_pool.get_any_healthy_proxy()
                candidate_proxies = [cand] if cand else [None]

            identities = {p: account_client_pool.proxy_identity(p) for p in candidate_proxies}
            for i in range(need):
                if i > 0:
                    await _sleep(random.uniform(0.4, 1.0))
                target_proxy = self._pick_proxy_for_refill(candidate_proxies)
                await self._solve_singleflight(None, proxy=target_proxy, background=True, identity=identities[target_proxy])
        finally:
            self._refilling = False

    async def _refill_single_proxy(self, proxy: str | None, *, identity=None) -> None:
        key = proxy or "__default__"
        q = self._proxy_pools[key]
        if len(q) >= settings.CAPTCHA_REGIONAL_POOL_MIN:
            return
        if self.port_backoff_remaining(proxy) > 0:
            return
        try:
            await self._solve_singleflight(proxy=proxy, background=True, identity=identity)
        except Exception:  # noqa: BLE001
            logs.warn("captcha", "单代理补货未完成")

    def _evict_expired(self) -> float:
        now = time.monotonic()
        oldest_age_ms = 0.0
        for q in self._proxy_pools.values():
            while q and (now - q[0].born_at) * 1000 >= TOKEN_TTL_MS:
                q.popleft()
            if q:
                age_ms = (now - q[0].born_at) * 1000
                if age_ms > oldest_age_ms:
                    oldest_age_ms = age_ms
        self._prune_seen_ids()
        return oldest_age_ms

    def _prune_seen_ids(self) -> None:
        now = time.monotonic()
        cutoff = now - 120.0
        expired = [cid for cid, at in self._seen_certify_ids.items() if at < cutoff]
        for cid in expired:
            self._seen_certify_ids.pop(cid, None)

    def _pick_proxy_for_refill(self, candidate_proxies: list[str | None]) -> str | None:
        """为预解池补充选择目标代理：优先补充当前池中存量最少的健康代理。"""
        if not candidate_proxies:
            return None
        if len(candidate_proxies) == 1:
            return candidate_proxies[0]
        counts: dict[str | None, int] = {}
        for p in candidate_proxies:
            key = p or "__default__"
            q = self._proxy_pools.get(key)
            if q:
                counts[p] = sum(1 for t in q if not t.expired())
            else:
                counts[p] = 0
        return min(candidate_proxies, key=lambda p: counts.get(p, 0))

    # ── Token 获取 ────────────────────────────────────────────────────────────
    async def get_verify_param(
        self,
        port: int | None = None,
        account: Account | None = None,
        proxy: str | None = None,
        *,
        failures: dict[str, CaptchaSolveError] | None = None,
        operation=None,
    ) -> tuple[str, str | None]:
        """按账号代理出口物理亲和获取验证码 Token：
        - 严格命中 (region, proxy) 专属水库，保证出网 IP 强一致；
        - 未命中或水库空仓时走 Singleflight 现解，避免惊群并发；
        - 本地求解不能作为代理模型通道的网络证据。
        """
        from .client_pool import account_client_pool

        operation = operation or account_client_pool.current_operation()
        expected_proxy = operation.proxy if operation is not None else proxy
        if expected_proxy is None and account is not None:
            expected_proxy = account_client_pool.resolve_proxy(account)
        if account is None and expected_proxy is None and port is not None:
            expected_proxy = f"http://127.0.0.1:{port}"

        identity = operation.identity if operation is not None else account_client_pool.proxy_identity(expected_proxy)
        if operation is None and not account_client_pool.proxy_eligible(expected_proxy, identity):
            raise CaptchaSolveError("验证码出口已停止新准入")
        key = expected_proxy or "__default__"
        q = self._proxy_pools[key]
        now = time.monotonic()
        while q and ((now - q[0].born_at) * 1000 >= TOKEN_TTL_MS or q[0].identity != identity):
            q.popleft()

        # 1) 亲和水库命中：直接弹出
        if q:
            token = q.popleft()
            self._note_port_success(expected_proxy)
            evidence.record_solve({
                "source": "hit", "port": evidence.port_of(expected_proxy),
                "stage": "extract", "hit": True, "param_chars": len(token.param or ""),
            })
            self._schedule_refill(expected_proxy)
            return token.param, token.region

        # 2) 水库空仓：通过 Singleflight 现解
        if failures is not None and key in failures:
            raise failures[key]
        # 端口刚连续失败过：立即失败，把唯一的求解槽让给健康端口（不新建 Node）
        remaining = self.port_backoff_remaining(expected_proxy)
        if remaining > 0:
            raise CaptchaSolveError("验证码出口暂不可用（求解退避）", {
                "reason": "solver_port_backoff", "stage": "queue", "stop_reason": "unavailable",
            })
        try:
            token = await self._solve_singleflight(proxy=expected_proxy, operation=operation, identity=identity)
        except CaptchaSolveError as err:
            if failures is not None:
                failures[key] = err
            raise
        if token is None:
            raise CaptchaSolveError("验证码求解失败")
        self._schedule_refill(expected_proxy)
        return token.param, token.region

    # ── Singleflight 与实际求解 ──────────────────────────────────────────────
    async def _solve_singleflight(self, config: dict | None = None, proxy: str | None = None,
                                  *, background: bool = False, operation=None, identity=None) -> _Token | None:
        from .client_pool import account_client_pool

        identity = operation.identity if operation is not None else (
            identity if identity is not None else account_client_pool.proxy_identity(proxy))
        key = (proxy or "__default__", identity) if identity else proxy or "__default__"
        if self._closed:
            raise CaptchaSolveError("验证码求解器已关闭")
        async with self._solve_lock:
            solve = self._in_flight_solves.get(key)
            if solve is None:
                if background and (self._foreground_demand or self._cpu_semaphore.locked()):
                    return None
                # producer 的引用由自身持有，等待者取消不能提前报告排空。
                admitted = await account_client_pool.acquire_operation(
                    proxy=proxy, identity=identity, parent=operation, kind="producer")
                solve = _Solve(asyncio.get_running_loop().time() + settings.CAPTCHA_SOLVE_TIMEOUT,
                               operation=admitted)
                self._in_flight_solves[key] = solve
                solve.task = asyncio.create_task(self._produce(solve, config, admitted.proxy))

                def done(task):
                    if self._in_flight_solves.get(key) is solve:
                        self._in_flight_solves.pop(key, None)
                    if not task.cancelled():
                        task.exception()

                solve.task.add_done_callback(done)
        if background:
            solve.refill += 1
        else:
            solve.foreground += 1
            self._foreground_demand += 1
        try:
            return await asyncio.shield(solve.task)
        finally:
            if background:
                solve.refill -= 1
            else:
                solve.foreground -= 1
                self._foreground_demand -= 1
            if not solve.foreground and not solve.refill and not solve.task.done() and not solve.task.cancelling():
                solve.task.cancel()
                await asyncio.gather(solve.task, return_exceptions=True)
            if solve.task.done() and not solve.operation.released:
                await solve.operation.pool.release_operation(solve.operation)

    async def _produce(self, solve: _Solve, config: dict | None, proxy: str | None) -> _Token | None:
        started = time.monotonic()
        details = solve.details
        details.update(solve_id="solve-" + secrets.token_hex(8), attempts=0, queue_wait_ms=0,
                       solve_ms=0, config_ms=0, retry_ms=0, cleanup_ms=0, stage="config")
        failure = None
        succeeded = False
        context = _current_solve.set(solve)
        try:
            async with asyncio.timeout_at(solve.deadline):
                config_at = time.monotonic()
                try:
                    config = config if config is not None else await self.fetch_config(operation=solve.operation)
                finally:
                    details["config_ms"] = round((time.monotonic() - config_at) * 1000)
                token = await self._solve_one_unlocked(config, proxy=proxy)
                if token is not None:
                    token.identity = solve.operation.identity
                    succeeded = True
                if (token is not None and not solve.foreground and solve.refill and
                        solve.operation.pool.proxy_eligible(proxy, token.identity)):
                    self._put(token)
                return token
        except TimeoutError:
            queue = details["stage"] == "queue"
            details.update(reason="solver_queue_timeout" if queue else "solver_budget_exhausted",
                           stop_reason="queue_timeout" if queue else "budget_exhausted")
            failure = CaptchaSolveError("验证码求解预算耗尽", details)
            raise failure from None
        except CaptchaSolveError as err:
            failure = err
            raise
        except Exception:
            details.update(reason="solver_failed_unknown", stop_reason="attempts_exhausted")
            failure = CaptchaSolveError("验证码求解失败", details)
            raise failure from None
        finally:
            _current_solve.reset(context)
            await solve.operation.pool.release_operation(solve.operation)
            details["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            if failure:
                failure.diagnostics = safe_diagnostics(details)
            # 端口级退避只认「实际求解失败」：让位（返回 None）既不算成功也不算失败
            if succeeded:
                self._note_port_success(proxy)
            elif failure is not None:
                self._note_port_failure(proxy)
            summary = {"source": "foreground" if solve.foreground else "refill",
                       **safe_diagnostics(details)}
            logs.info("captcha", json.dumps(summary, ensure_ascii=False))
            evidence.record_solve({**summary, "port": evidence.port_of(proxy), "hit": False})

    async def _solve_one(self, config: dict | None, proxy: str | None = None,
                         *, background: bool = False) -> _Token | None:
        return await self._solve_singleflight(config, proxy=proxy, background=background)

    async def _solve_one_unlocked(self, config: dict, proxy: str | None = None) -> _Token | None:
        scene = config.get("sceneId") or constants.CAPTCHA_DEFAULTS["sceneId"]
        region = config.get("region") or constants.CAPTCHA_DEFAULTS["region"]
        prefix = config.get("prefix") or constants.CAPTCHA_DEFAULTS["prefix"]

        solve = _current_solve.get()
        details = solve.details
        for attempt in range(1, settings.CAPTCHA_SOLVE_RETRIES + 1):
            if attempt > 1:
                details["stage"] = "retry"
                at = time.monotonic()
                try:
                    await _sleep(0.3 * (attempt - 1) + random.uniform(0.1, 0.3))
                finally:
                    details["retry_ms"] += round((time.monotonic() - at) * 1000)
            if asyncio.get_running_loop().time() >= solve.deadline:
                raise TimeoutError
            details["stage"] = "queue"
            at = time.monotonic()
            if not solve.foreground and (self._foreground_demand or self._cpu_semaphore.locked()):
                return None
            try:
                await self._cpu_semaphore.acquire()
            finally:
                details["queue_wait_ms"] += round((time.monotonic() - at) * 1000)
            try:
                details["stage"] = "run"
                details["attempts"] = attempt
                at = time.monotonic()
                try:
                    param = await self._run_solver(scene, region, prefix, proxy=proxy)
                    return _Token(param, region, proxy=proxy)
                except Exception as err:  # noqa: BLE001
                    summary = err.diagnostics if isinstance(err, _SolverError) else {"reason": "solver_failed_unknown", "stage": "run"}
                    details.update(summary)
                    details["last_solver_code"] = summary["reason"]
                    self._last_error = summary["reason"]
                    logs.warn("captcha", f"{details['solve_id']} 第 {attempt}/{settings.CAPTCHA_SOLVE_RETRIES} 次未果 ({summary['reason']})")
                finally:
                    details["solve_ms"] += round((time.monotonic() - at) * 1000)
            finally:
                self._cpu_semaphore.release()
        details["stop_reason"] = "attempts_exhausted"
        raise CaptchaSolveError("验证码求解失败", details)

    async def _run_solver(self, scene: str, region: str, prefix: str, proxy: str | None = None) -> str:
        solver = settings.CAPTCHA_SOLVER_JS
        if not solver.exists():
            raise _SolverError("solver_unavailable", "init")
        env = dict(os.environ)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)
        if proxy:
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy

        try:
            proc = await asyncio.create_subprocess_exec(
                settings.NODE_PATH,
                "--dns-result-order=ipv4first",
                "--no-network-family-autoselection",
                str(solver), scene, region, prefix,
                cwd=str(settings.CAPTCHA_SOLVER_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError:
            raise _SolverError("solver_unavailable", "init") from None
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=settings.CAPTCHA_SOLVE_TIMEOUT
            )
        except TimeoutError:
            raise _SolverError("solver_timeout", "run") from None
        finally:
            if proc.returncode is None:
                cleanup_at = time.monotonic()
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                solve = _current_solve.get()
                if solve:
                    solve.details["cleanup_ms"] += round((time.monotonic() - cleanup_at) * 1000)

        param = None
        for line in stdout_bytes.decode("utf-8", "ignore").splitlines():
            if line.startswith("VERIFY_PARAM="):
                param = line[len("VERIFY_PARAM="):].strip()

        if param and proc.returncode == 0:
            return param

        summary = _solver_summary(stderr_bytes, proc.returncode)
        raise _SolverError(summary.pop("reason"), **summary)

    # ── 靶向精准失效 ─────────────────────────────────────────────────────────
    def retire_egress(self, pool) -> None:
        for proxy, q in self._proxy_pools.items():
            identity = pool.proxy_identity(None if proxy == "__default__" else proxy)
            eligible = pool.proxy_eligible(None if proxy == "__default__" else proxy, identity)
            retained = [t for t in q if eligible and t.identity == identity and not t.expired()]
            q.clear()
            q.extend(retained)

    def egress_token_count(self, identity: tuple) -> int:
        return sum(t.identity == identity for q in self._proxy_pools.values() for t in q)

    def invalidate(self, region: str | None = None, proxy: str | None = None) -> None:
        """靶向清空失效 Token，严禁跨大区株连。"""
        drained = 0
        if proxy:
            q = self._proxy_pools.get(proxy)
            if q:
                drained += len(q)
                q.clear()
        elif region:
            from .client_pool import account_client_pool
            reg_map = account_client_pool.load_registry()
            ports = reg_map.get(region, [])
            for port in ports:
                p_url = f"http://127.0.0.1:{port}"
                q = self._proxy_pools.get(p_url)
                if q:
                    drained += len(q)
                    q.clear()
        else:
            for q in self._proxy_pools.values():
                drained += len(q)
                q.clear()
        if drained:
            logs.warn("captcha", f"验证码失效（region={region}, proxy={proxy}），清空 Token {drained} 枚")


captcha_manager = CaptchaManager()
