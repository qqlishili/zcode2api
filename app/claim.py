"""套餐领取（Z.AI billing/preview + billing/claim）。

链路与 zcode-switch claim.rs 同形：
  1. GET  {BILLING_BASE}/billing/preview?app_version=&platform= → data.plans[]
  2. 领取需阿里云无痕验证码：CaptchaManager 服务端求解 → X-Aliyun-Captcha-Verify-Param
  3. POST {BILLING_BASE}/billing/claim  body {"plan_id":...}（+ 可选 Verify-Region 头）

上游业务码语义（沿用 zcode-switch 映射）：1001 套餐不存在 / 1002 活动结束 /
1003 已领取过 / 1004 不符合条件 / 1005 今日名额用完 / 3001 参数错误 /
3007 验证码失败（清码后单次止损）/ 401 未登录。
"""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx

from . import constants, logs, settings
from .captcha import captcha_manager
from .client_pool import RegionOfflineError, account_client_pool
from .models import Account, Status

_TZ_BEIJING = timezone(timedelta(hours=8))


def calculate_claim_blocked_until(now: float | None = None) -> float:
    """计算 1005 今日名额用完后的避让截止时间戳。

    基准：北京时间（UTC+8）次日凌晨 00:05:00
    抖动：叠加 0 ~ 300 秒（5分钟）随机离散 Jitter，打散集群解封流量，防止上游 WAF 踩踏。
    """
    now_ts = now if now is not None else time.time()
    dt = datetime.fromtimestamp(now_ts, tz=_TZ_BEIJING)
    next_day_0005 = (dt + timedelta(days=1)).replace(
        hour=0, minute=5, second=0, microsecond=0
    )
    jitter = random.uniform(0, 300)
    return next_day_0005.timestamp() + jitter


class ClaimError(Exception):
    """业务失败（含上游 code 语义），message 面向用户。

    next_at 仅 1005（名额用完）携带：上游 data.plan.ends_at（秒 → 毫秒），
    即名额恢复时间（zcode-switch claim.rs claim_error 同形）。
    """

    def __init__(self, message: str, *, code: int = -1, next_at: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.next_at = next_at


class ClaimPacingQueueFullError(ClaimError):
    """领额度错峰排队超时快速失败（防止上游/客户端 499 挂死与同节点并发踩踏）。"""

    def __init__(self, message: str = "当前代理出口领额度排队拥挤，请稍后重试", *, code: int = 1005) -> None:
        super().__init__(message, code=code)


class ClaimPacingGate:
    """按出网代理物理端口隔离的串行错峰门禁（防 1005/3012 WAF 连坐）。

    1. 每个代理端口独立持有 asyncio.Lock；
    2. 尝试获取锁，设置超时时间 max_wait_sec（默认 45s），超时立即抛出 ClaimPacingQueueFullError；
    3. 获取锁后，检查与上一次完成的时间差 elapsed = now - last_claim_time；
       若 elapsed < min_interval_sec（默认 20s），则 asyncio.sleep(min_interval_sec - elapsed)；
    4. 退出门禁时记录最后成功放行完成时间戳，并释放锁。
    """

    def __init__(
        self,
        min_interval_sec: float | None = None,
        max_wait_sec: float | None = None,
    ) -> None:
        self._proxy_locks: dict[str, asyncio.Lock] = {}
        self._proxy_last_claim: dict[str, float] = {}
        self._min_interval = min_interval_sec
        self._max_wait = max_wait_sec

    @property
    def min_interval(self) -> float:
        if self._min_interval is not None:
            return self._min_interval
        return float(getattr(settings, "CLAIM_PROXY_INTERVAL", 20.0))

    @property
    def max_wait(self) -> float:
        if self._max_wait is not None:
            return self._max_wait
        return float(getattr(settings, "CLAIM_PROXY_MAX_WAIT", 45.0))

    def _get_lock(self, key: str) -> asyncio.Lock:
        if key not in self._proxy_locks:
            self._proxy_locks[key] = asyncio.Lock()
        return self._proxy_locks[key]

    async def acquire(self, proxy: str | None) -> None:
        key = proxy or "__default__"
        lock = self._get_lock(key)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=self.max_wait)
        except TimeoutError as err:
            raise ClaimPacingQueueFullError(
                f"代理出口 {key} 领额度排队超时（>{self.max_wait}s），已快速失败避让"
            ) from err

        try:
            min_int = self.min_interval
            if min_int > 0:
                now = time.monotonic()
                last = self._proxy_last_claim.get(key, 0.0)
                elapsed = now - last
                if elapsed < min_int:
                    await asyncio.sleep(min_int - elapsed)
        except Exception:
            if lock.locked():
                lock.release()
            raise

    def release(self, proxy: str | None) -> None:
        key = proxy or "__default__"
        self._proxy_last_claim[key] = time.monotonic()
        lock = self._proxy_locks.get(key)
        if lock and lock.locked():
            lock.release()

    @asynccontextmanager
    async def limit(self, proxy: str | None):
        await self.acquire(proxy)
        try:
            yield
        finally:
            self.release(proxy)


claim_pacing_gate = ClaimPacingGate()


AUTH_EXPIRED_MESSAGE = "凭证失效，请重新授权"

_CLAIM_FAIL = {
    1001: "套餐不存在",
    1002: "活动已结束或套餐暂不可领取",
    1003: "该套餐已经领取过",
    1004: "不符合领取条件",
    1005: "今日领取名额已用完",
    3001: "领取参数错误，请刷新后重试",
    3007: "验证码校验失败，请重试",
    3012: "机房环境被上游风控拦截(3012)，请使用浏览器滑块手动领取",
    401: "请先登录后再领取",
}


def format_claim_blocked_time(blocked_ts: float, now_ts: float | None = None) -> str:
    """格式化 1005 名额避让时间戳（消除跨天与日期歧义）。

    按北京时间（UTC+8）对比当前自然日：
    - 当日：'今日 HH:MM:SS'
    - 次日：'次日 HH:MM:SS'
    - 隔天或更远：'YYYY-MM-DD HH:MM:SS'
    """
    now_dt = datetime.fromtimestamp(now_ts if now_ts is not None else time.time(), tz=_TZ_BEIJING)
    target_dt = datetime.fromtimestamp(blocked_ts, tz=_TZ_BEIJING)
    day_diff = (target_dt.date() - now_dt.date()).days
    time_str = target_dt.strftime("%H:%M:%S")
    if day_diff == 0:
        return f"今日 {time_str}"
    if day_diff == 1:
        return f"次日 {time_str}"
    return f"{target_dt.strftime('%Y-%m-%d')} {time_str}"


def billing_block_reason(account: Account, *, action: str = "领取") -> str | None:
    """JWT 不可打 billing 时的用户文案；可打则返回 None。"""
    if account.is_cooling():
        return f"账号冷却中（风控/限流），已跳过{action}"
    if action == "领取" and account.is_claim_blocked():
        dt_str = format_claim_blocked_time(account.claim_blocked_until or time.time())
        return f"今日领取名额已用完，避让期内跳过领取（名额等待至: {dt_str}，若届时活动下线将自动跳过）"
    if account.mode != "jwt" or not (account.jwt_token or "").strip():
        return f"非 Coding Plan 账号，已跳过{action}"
    if not account.enabled:
        return f"账号已停用，已跳过{action}"
    if account.status == Status.DISABLED:
        return f"账号风控封禁，已跳过{action}"
    if account.status == Status.INVALID or not account.uses_plan_channel():
        return AUTH_EXPIRED_MESSAGE
    return None


def _mark_claim_blocked(account: Account, next_at: int | None = None) -> None:
    """标记 1005 名额用完避让期并持久化（优先采用上游下发 next_at + 离散抖动，未下发回退次日 00:05）。"""
    from .store import store

    live = store.find(account.provider, account.id)
    now_ts = time.time()
    if next_at and (next_at / 1000.0) > now_ts:
        blocked = (next_at / 1000.0) + random.uniform(5, 60)
    else:
        blocked = calculate_claim_blocked_until(now_ts)
    if live is not None:
        live.claim_blocked_until = blocked
        store.update_account(live)
        account.claim_blocked_until = live.claim_blocked_until
    else:
        account.claim_blocked_until = blocked


def _mark_auth_failure(account: Account) -> None:
    from .store import store

    live = store.find(account.provider, account.id)
    if live is None:
        return
    live.status = Status.INVALID
    live.last_error = AUTH_EXPIRED_MESSAGE
    store.update_account(live)
    account.status = live.status
    account.last_error = live.last_error


def _fail_message(code: int, body: dict) -> str:
    base = _CLAIM_FAIL.get(code, "领取失败")
    server = body.get("msg") or body.get("message") or ""
    return f"{base}（{server}）" if server else base


def _fail_error(code: int, body: dict) -> ClaimError:
    """业务码 → ClaimError；1005 附带名额恢复时间（data.plan.ends_at 秒 → 毫秒）。"""
    message = _fail_message(code, body)
    next_at = None
    if code == 1005:
        ends = ((body.get("data") or {}).get("plan") or {}).get("ends_at")
        if isinstance(ends, (int, float)) and ends > 0:
            next_at = int(ends * 1000)
    return ClaimError(message, code=code, next_at=next_at)


def _business_code(body: dict) -> int:
    code = body.get("code")
    try:
        return int(code) if code is not None else -1
    except (TypeError, ValueError):
        return -1


def parse_plan(raw: dict) -> dict | None:
    """提取可领取套餐（plan_id/name/描述/优先级 + model_usage token 授权项，支持已解析结构幂等重入）。"""
    plan_id = str(raw.get("plan_id") or raw.get("planId") or "").strip()
    if not plan_id:
        return None
    grants = []
    for ent in raw.get("entitlements") or []:
        if ent.get("meter") != "model_usage" or ent.get("unit_type") != "token":
            continue
        name = str(ent.get("show_name") or ent.get("showName") or "").strip()
        if not name:
            continue
        units = ent.get("grant_units", ent.get("grantUnits")) or 0
        grants.append({
            "name": name,
            "units": float(units),
            "period": ent.get("period") or "one_time",
        })
    if not grants and isinstance(raw.get("grants"), list):
        grants = [
            {
                "name": str(g.get("name") or "").strip(),
                "units": float(g.get("units") or 0),
                "period": g.get("period") or "one_time",
            }
            for g in raw["grants"]
            if isinstance(g, dict) and str(g.get("name") or "").strip()
        ]
    return {
        "plan_id": plan_id,
        "name": str(raw.get("name") or "").strip(),
        "description": str(raw.get("description") or "").strip(),
        "priority": raw.get("priority") or 0,
        "grants": grants,
    }


async def _billing_request(account: Account, method: str, path: str, **kwargs) -> dict:
    headers = dict(kwargs.pop("headers"))
    client = kwargs.pop("client", None)
    # 同用途外层操作可能持有长流客户端，billing 超时必须逐请求生效。
    kwargs.setdefault("timeout", 25.0)
    try:
        async with account_client_pool.operation(account, timeout=25.0, purpose="claim") as operation:
            if client is not None and client is not operation.client:
                raise ClaimError("领取客户端与已准入出口不一致")
            client = operation.client
            probe = account_client_pool.begin_request(client)
            success = None
            waf = False
            try:
                res = await client.request(
                    method, f"{settings.ZCODE_BILLING_BASE}{path}",
                    headers=headers, **kwargs,
                )
                success = True
                try:
                    code = str(res.json().get("code"))
                    waf = (code in constants.CAPTCHA_BUSINESS_CODES or
                           any(marker in res.text.lower() for marker in constants.RISK_CONTROL_MARKERS) or
                           res.status_code in constants.RISK_CONTROL_HTTP_STATUSES)
                    if waf:
                        success = False
                except (ValueError, AttributeError):
                    pass
            except httpx.HTTPError:
                success = False
                raise
            finally:
                account_client_pool.end_request(client, probe, success=success, is_waf_block=waf)
    except RegionOfflineError as err:
        raise ClaimError(f"账号大区出口离线: {err}") from err
    except httpx.HTTPError as err:
        # 连接/超时等网络故障统一转业务错误：路由层只需面对 ClaimError 一种失败
        raise ClaimError(f"上游网络错误: {err}") from err
    if res.status_code in (401, 403):
        text = (res.text or "").lower()
        if "captcha" not in text and "verify" not in text:
            _mark_auth_failure(account)
            raise ClaimError(AUTH_EXPIRED_MESSAGE)
    try:
        body = res.json()
    except ValueError:
        raise ClaimError(f"上游响应非 JSON HTTP {res.status_code}") from None
    return body


def jwt_user_id(account: Account) -> str | None:
    """JWT payload 的 user_id（zcode-switch telemetry_user_id 同源语义）。

    官方客户端事件上报以 user_id 标识用户；hub 不存 user_info，直接从 JWT
    解出（user_id 优先，sub 兜底，两者同为 36 位 uuid）。解析口径收口在
    `Account.identity`：该属性解不出 payload 身份时回退 secret 全文，此处
    据此判断——身份等于全文说明没有可上报的 user_id，返回 None 而不把凭证
    本身当作 user_id 上报。
    """
    ident = account.identity
    if not ident or ident == account.secret:
        return None
    return ident


async def report_activation_events(account: Account) -> str | None:
    """上报官方客户端激活事件（app_launch + app_daily_active + app_login_success）。

    zcode-switch claim_refresh 同形：preview 前模拟桌面端当日活跃（疑似活动
    套餐投放资格信号）。事件体/端点/业务码判定收敛在 telemetry 单一事实源；
    登录态经完整公共头 + Authorization 投递（对齐官方 event-report 投递合同）。
    app_login_success 仅当账号尚未安装（installed_at 为空）时补发一次——登录
    成功是「此设备完成 OAuth 登录」的一次性事件，随高频刷新重复上报会被上游
    读作「账号反复登录」，故以 installed_at 做天然去重。任何失败仅返回文案，
    不阻断 preview，首个失败即中止（日活键在上游按 device_mid+日期去重）。
    """
    from .client_pool import account_client_pool
    from .fingerprint import profile_for
    from .identity import build_identity_headers
    from .telemetry import post_activation_event, resolve_login_provider

    profile = profile_for(account)
    user_id = jwt_user_id(account)
    if not user_id:
        return "JWT 无 user_id，跳过激活上报"
    async with account_client_pool.operation(account, purpose="claim"):
        try:
            client = account_client_pool.current_operation().client
        except Exception as err:
            return f"获取账号大区代理客户端失败: {err}"
        try:
            probe = account_client_pool.begin_request(client)
        except RegionOfflineError:
            return "账号出口暂时不可用，跳过激活上报"
        success = None
        try:
            # 登录态公共头 + Authorization（官方 event-report 投递合同）
            event_headers = {
                **build_identity_headers(account),
                "Authorization": f"Bearer {account.jwt_token}",
            }
            for element in constants.ACTIVATION_ELEMENTS:
                try:
                    await post_activation_event(
                        profile, user_id, element, client=client, headers=event_headers,
                    )
                except (httpx.HTTPError, RuntimeError) as err:
                    success = False if isinstance(err, httpx.HTTPError) else None
                    import re
                    status = re.search(r"HTTP ([1-5][0-9]{2})\b", str(err))
                    detail = f"HTTP {status[1]}" if status else type(err).__name__
                    return f"激活事件 {element} 上报失败: {detail}"
            # 登录成功事件：仅未安装时发一次（installed_at 去重）
            if account.installed_at is None:
                try:
                    await post_activation_event(
                        profile,
                        user_id,
                        constants.LOGIN_EVENT["element"],
                        client=client,
                        headers=event_headers,
                        event_region=constants.LOGIN_EVENT["event_region"],
                        event_type=constants.LOGIN_EVENT["event_type"],
                        event_extra_detail={
                            "login_provider": resolve_login_provider(account.provider),
                        },
                    )
                except (httpx.HTTPError, RuntimeError) as err:
                    success = False if isinstance(err, httpx.HTTPError) else None
                    import re
                    status = re.search(r"HTTP ([1-5][0-9]{2})\b", str(err))
                    detail = f"HTTP {status[1]}" if status else type(err).__name__
                    return f"激活事件 {constants.LOGIN_EVENT['element']} 上报失败: {detail}"
            success = True
        finally:
            account_client_pool.end_request(client, probe, success=success)
        return None


def _norm_plan_id(plan_or_id: dict | str | None) -> str:
    """归一化提取小写去空格的套餐 ID（兼容 dict 的 plan_id / planId / pid 及字符串）。"""
    if isinstance(plan_or_id, dict):
        raw = plan_or_id.get("plan_id") or plan_or_id.get("planId") or plan_or_id.get("pid") or ""
    else:
        raw = plan_or_id or ""
    return str(raw).strip().lower()


def is_base_plan_id(plan_id: str | dict | None) -> bool:
    """是否为基础/体验套餐（非限时大促活动）。"""
    return _norm_plan_id(plan_id) in ("zcode-v3-start-plan", "zcode-v3-start-plan-0817", "default")


def account_held_plan_ids(account: Account) -> set[str]:
    """提取账号当前已持有/生效的套餐 plan_id 集合（小写去空格）。"""
    held = set()
    for pl in getattr(account, "plans", []) or []:
        pid = _norm_plan_id(pl)
        if pid:
            held.add(pid)
    for slot in getattr(account, "plan_slots", []) or []:
        pid = _norm_plan_id(slot)
        if pid and pid != "default":
            held.add(pid)
    return held


def filter_unclaimed_plans(
    account: Account,
    plans: list[dict] | None,
    *,
    extra_held_ids: set[str] | None = None,
) -> list[dict]:
    """过滤出账号当前尚未持有的有效活动套餐列表（保序去重，同名套餐优先保留含 grants 的完整项）。"""
    if not plans:
        return []
    held = account_held_plan_ids(account)
    if extra_held_ids:
        held |= {_norm_plan_id(pid) for pid in extra_held_ids if _norm_plan_id(pid)}
    by_pid: dict[str, dict] = {}
    for p in plans:
        if not isinstance(p, dict):
            continue
        pid = _norm_plan_id(p)
        if not pid or pid in held:
            continue
        prev = by_pid.get(pid)
        if prev is None or (not prev.get("grants") and p.get("grants")):
            by_pid[pid] = p
    return list(by_pid.values())


def sync_account_claimable_plans(
    account: Account,
    plans: list[dict] | None = None,
    *,
    extra_held_ids: set[str] | None = None,
    persist: bool = True,
) -> list[dict]:
    """归一化收口：将候选活动剔除已持有项后同步写入 account.claimable_plans 并按需落库。"""
    from .store import store

    live = store.find(account.provider, account.id) if persist else None
    combined_held = account_held_plan_ids(account)
    if live is not None and live is not account:
        combined_held |= account_held_plan_ids(live)
    if extra_held_ids:
        combined_held |= {_norm_plan_id(pid) for pid in extra_held_ids if _norm_plan_id(pid)}

    source = (
        plans
        if plans is not None
        else [
            *(getattr(account, "claimable_plans", None) or []),
            *(getattr(live, "claimable_plans", None) or [] if live is not None and live is not account else []),
        ]
    )
    unclaimed = filter_unclaimed_plans(account, source, extra_held_ids=combined_held)
    changed = (getattr(account, "claimable_plans", None) != unclaimed) or (
        live is not None and getattr(live, "claimable_plans", None) != unclaimed
    )
    account.claimable_plans = unclaimed
    if live is not None:
        live.claimable_plans = unclaimed

    # 状态自愈归一：若账号已无任何未持有活动套餐，名额避让锁失去标的物，自动解除陈旧避让戳
    if not unclaimed and (getattr(account, "claim_blocked_until", None) or (live is not None and getattr(live, "claim_blocked_until", None))):
        account.claim_blocked_until = None
        if live is not None:
            live.claim_blocked_until = None
        changed = True

    if live is not None and persist and changed:
        store.update_account(live)
    return unclaimed


async def auto_claim_all_plans(account: Account, *, skip_plan_ids: set[str] | None = None) -> list[dict]:
    """新账号入池或哨兵自动领取：激活上报 + 逐个领取全部可领套餐。

    入池链路的 fire-and-forget 收尾：任何失败只记日志/返回 outcome，绝不抛出
    （入池流程不受影响）。重复执行安全（上游 1003 已领取过幂等）。
    skip_plan_ids：本轮显式跳过领取的套餐 id；preview 仍照常执行。
    """
    if not (account.mode == "jwt" and account.jwt_token):
        return []
    if account.is_claim_blocked():
        logs.info("claim", f"账号 {account.name} 处于名额满避让期，跳过自动领取")
        return []
    try:
        async with account_client_pool.operation(account, purpose="claim"):
            outcomes: list[dict] = []

            try:
                err = await report_activation_events(account)
                if err:
                    logs.warn("claim", f"账号 {account.name} 激活上报失败: {err}")
            except Exception as err:  # noqa: BLE001 - 激活失败不阻断领取
                logs.warn("claim", f"账号 {account.name} 激活上报异常: {err}")

            try:
                plans = await preview_plans(account)
            except ClaimError as err:
                logs.info("claim", f"账号 {account.name} 无可领套餐（{err}）")
                return outcomes
            except Exception as err:  # noqa: BLE001
                logs.warn("claim", f"账号 {account.name} preview 异常: {err}")
                return outcomes

            if not plans:
                logs.info("claim", f"账号 {account.name} 上游无投放套餐，跳过领取")
                return outcomes

            # 探查完成立即将未持有套餐落库，确保后续即便全部领取失败（如 3012），页面也能显示待领 Icon
            unclaimed = sync_account_claimable_plans(account, plans)
            if not unclaimed:
                logs.info("claim", f"账号 {account.name} 已持有当前全部活动套餐，跳过自动领取")
                return outcomes

            skip = skip_plan_ids or set()
            skipped = 0
            need_refresh = False
            for plan in unclaimed:
                if plan["plan_id"] in skip:
                    skipped += 1
                    continue
                try:
                    result = await claim(account, plan["plan_id"], report_activation=False)
                    outcomes.append({"account_id": account.id, "account_name": account.name,
                                     "ok": True, **result})
                    need_refresh = True
                    logs.ok("claim", f"账号 {account.name} 自动领取成功: "
                                     f"{result.get('plan_name') or plan['plan_id']}")
                except ClaimError as err:
                    msg = str(err)
                    outcome = {"account_id": account.id, "account_name": account.name,
                               "ok": False, "plan_id": plan["plan_id"], "message": msg}
                    if err.code != -1:
                        outcome["code"] = err.code
                    if err.next_at:
                        outcome["next_at"] = err.next_at
                    outcomes.append(outcome)
                    if "已经领取过" in msg:
                        need_refresh = True
                    logs.warn("claim", f"账号 {account.name} 自动领取 {plan['plan_id']} 失败: {err}")
                except Exception as err:  # noqa: BLE001
                    logs.warn("claim", f"账号 {account.name} 自动领取异常: {err}")
                    outcomes.append({"account_id": account.id, "account_name": account.name,
                                     "ok": False, "plan_id": plan["plan_id"], "message": str(err)})

            if skipped:
                logs.info("claim", f"账号 {account.name} {skipped} 个套餐名额等待期，本轮跳过领取")

            if need_refresh:
                try:
                    from .quota import fetch_quota
                    await fetch_quota(account)
                except Exception as q_err:  # noqa: BLE001
                    logs.warn("claim", f"账号 {account.name} 自动领取后刷新额度跳过: {q_err}")

            claimed_ok_ids = {
                _norm_plan_id(o)
                for o in outcomes
                if o.get("ok") or "已经领取过" in str(o.get("message") or "")
            }
            sync_account_claimable_plans(account, unclaimed, extra_held_ids=claimed_ok_ids)
            return outcomes
    except RegionOfflineError as err:
        logs.warn("claim", f"账号 {account.name} 领取出口不可用: {err}")
        return []


async def preview_plans(account: Account) -> list[dict]:
    """拉取账号当前可领取套餐，按优先级降序，并同步持久化待领状态。

    只信本号上游 /billing/preview；返回空时不从池内其他账号补齐候选。
    探查完成后仅同步当前账号的 claimable_plans。
    """
    blocked = billing_block_reason(account, action="上游查询")
    if blocked:
        raise ClaimError(blocked)
    from .fingerprint import profile_for
    from .quota import _auth_headers

    upstream_plans: list[dict] = []
    try:
        body = await _billing_request(
            account, "GET", "/billing/preview",
            headers=_auth_headers(account),
            # platform 跟账号档案走（官方 TH() = process.platform-arch）；
            # 实测 client/configs 才拒 platform 参数，preview 宽容。
            params={"app_version": constants.BILLING_APP_VERSION,
                    "platform": profile_for(account).platform_full},
        )
        code = _business_code(body)
        if code == 0:
            raw_plans = (body.get("data") or {}).get("plans") or []
            upstream_plans = [parsed for parsed in (parse_plan(p) for p in raw_plans) if parsed]
        elif code > 0:
            raise _fail_error(code, body)
    except ClaimError:
        raise
    except Exception as err:
        logs.info("claim", f"账号 {account.name} 上游 preview 请求未果: {err}")

    # 只信上游 preview：不合并池内活动，避免把「池内某账号已持有/邀请制」的活动误判为「人人可领」
    merged_map: dict[str, dict] = {}
    for p in upstream_plans:
        pid = _norm_plan_id(p)
        if not pid:
            continue
        prev = merged_map.get(pid)
        if prev is None or (not prev.get("grants") and p.get("grants")):
            merged_map[pid] = p

    plans = list(merged_map.values())
    plans.sort(key=lambda p: (-p["priority"], p["plan_id"]))
    # 探查即落库：仅同步当前账号的待领活动（不跨账号广播）
    sync_account_claimable_plans(account, plans)
    return plans


async def _auto_pick_plan(account: Account, plan_id: str | None) -> tuple[str, str, list]:
    """plan_id 为空时 preview 自动选优先级最高的未领套餐，并在探查后立即同步 claimable_plans。"""
    if plan_id:
        pid_clean = plan_id.strip()
        pid_norm = _norm_plan_id(pid_clean)
        if pid_norm in account_held_plan_ids(account):
            raise ClaimError("该套餐已经领取过", code=1003)
        for p in (getattr(account, "claimable_plans", None) or []):
            if isinstance(p, dict) and _norm_plan_id(p) == pid_norm:
                merged = [p, *(getattr(account, "claimable_plans", None) or [])]
                sync_account_claimable_plans(account, merged)
                return pid_clean, p.get("name") or pid_clean, p.get("grants") or []
        return pid_clean, pid_clean, []

    plans = await preview_plans(account)
    unclaimed = sync_account_claimable_plans(account, plans)
    if not unclaimed:
        if plans:
            raise ClaimError("该账号已领取过当前所有可用活动套餐", code=1003)
        raise ClaimError("当前暂无可领取的活动套餐")
    best = unclaimed[0]
    return best["plan_id"], best["name"] or best["plan_id"], best["grants"]


def _ensure_claimable_account(account: Account, plan_id: str | None = None) -> None:
    """领取前置栅栏：校验账号为可计费 JWT 账号且未处于冷却/1005 避让期。

    若指定了 plan_id 且账号已持有，放行给 _auto_pick_plan 抛出 1003；
    若账号已无任何待领活动，优先提示无可领/已领过，而非报 1005。
    """
    if not (account.mode == "jwt" and account.jwt_token):
        raise ClaimError("仅 Coding Plan (JWT) 账号支持领取")
    if plan_id and _norm_plan_id(plan_id) in account_held_plan_ids(account):
        return
    blocked = billing_block_reason(account)
    if blocked:
        raise ClaimError(blocked)


def _claim_headers(account: Account, verify_param: str, region: str | None) -> dict:
    """billing/claim 客户端请求头形态（asar claimManualPlan）。

    实测缺版本/平台头时即使验证码有效也 3007；X-Device-Mid 由 _auth_headers 提供。
    """
    from .quota import _auth_headers

    headers = _auth_headers(account)
    headers[constants.CAPTCHA_HEADER] = verify_param
    if region and region.strip():
        headers["X-Aliyun-Captcha-Verify-Region"] = region.strip()
    # 实测缺版本/平台头时即使验证码有效也 3007（_auth_headers 已带，此处显式
    # 兜底防止基座头漂移）。平台必须跟账号档案走，禁止再盖成全局 darwin-arm64。
    headers["X-ZCode-App-Version"] = constants.BILLING_APP_VERSION
    return headers


async def _post_claim(account: Account, headers: dict, plan_id: str, *, client=None) -> dict:
    """提交单次 billing/claim 并统一处理成功/1003 已领剔除与 1005 避让标记。"""
    body = await _billing_request(
        account, "POST", "/billing/claim",
        headers=headers, json={"plan_id": plan_id}, client=client,
    )
    code = _business_code(body)
    if code == 0:
        sync_account_claimable_plans(account, extra_held_ids={plan_id})
        return body
    err = _fail_error(code, body)
    if code == 1003:
        sync_account_claimable_plans(account, extra_held_ids={plan_id})
    elif code == 1001:
        # 上游否认该套餐存在（活动已下线）：剔除候选，否则待领项永久残留，
        # 后台常亮待领图标且哨兵每轮空跑一次验证码求解。
        drop_stale_claimable(account, plan_id)
    elif code == 1005:
        _mark_claim_blocked(account, err.next_at)
        blocked_ts = account.claim_blocked_until or time.time()
        dt_str = format_claim_blocked_time(blocked_ts)
        raise ClaimError(f"今日领取名额已用完，已自动避让至 {dt_str}（若届时活动下线将自动跳过）", code=1005, next_at=err.next_at)
    raise err


def drop_stale_claimable(account: Account, plan_id: str) -> None:
    """剔除上游已否认存在的待领候选（活动下线自愈）。

    `_post_claim` 收到 1001「套餐不存在」时调用：从账号与落库记录中移除该 plan_id，
    使待领列表不再残留永久无法领取的僵尸项。保留其余候选与既有避让状态。
    """
    from .store import store

    live = store.find(account.provider, account.id)
    target = _norm_plan_id(plan_id)
    for holder in (account, live):
        if holder is None:
            continue
        holder.claimable_plans = [
            p for p in (getattr(holder, "claimable_plans", None) or [])
            if _norm_plan_id(p) != target
        ]
    if live is not None:
        store.update_account(live)
    logs.info("claim", f"账号 {account.name} 活动已下线，剔除待领项 {plan_id}")


def _claim_outcome(body: dict, plan_id: str, plan_name: str, grants: list) -> dict:
    """领取成功返回集；server_time/starts_at/ends_at 为上游秒值 → 毫秒
    （zcode-switch 3.11.2 领取语义：服务端时钟随成功载荷下发，供前端
    区分本机时钟漂移）。缺失字段保持 None，不造数。"""
    data = body.get("data") or {}
    plan = data.get("plan") or {}

    def _ms(key: str) -> int | None:
        val = plan.get(key)
        return int(val * 1000) if isinstance(val, (int, float)) and val > 0 else None

    server_time = data.get("server_time")
    return {
        "plan_id": plan_id,
        "plan_name": plan_name,
        "grants": grants,
        "starts_at": _ms("starts_at"),
        "ends_at": _ms("ends_at"),
        "server_time": int(server_time * 1000) if isinstance(server_time, (int, float)) and server_time > 0 else None,
    }


async def claim_with_captcha(
    account: Account,
    verify_param: str,
    region: str | None,
    plan_id: str | None = None,
) -> dict:
    """手动领取：verify_param 由用户浏览器内阿里 SDK 滑块产生，本端只做转发。

    plan_id 缺省时先 preview 自动选优先级最高套餐（无需验证码）。
    """
    if account_client_pool.policy.claim_mode != "inherit":
        raise ClaimError("claim_verification_route_mismatch: 请使用服务端领取")
    async with account_client_pool.operation(account, purpose="claim"):
        _ensure_claimable_account(account, plan_id=plan_id)
        if not (verify_param or "").strip():
            raise ClaimError("缺少验证码参数，请先完成人机验证")

        plan_id, plan_name, grants = await _auto_pick_plan(account, plan_id or None)
        try:
            await report_activation_events(account)
        except Exception as act_err:
            logs.warn("claim", f"账号 {account.name} 领取前激活上报跳过: {act_err}")
        headers = _claim_headers(account, verify_param.strip(), region)
        client = account_client_pool.current_operation().client
        proxy = account_client_pool.client_proxy(client)
        async with claim_pacing_gate.limit(proxy):
            try:
                body = await _post_claim(account, headers, plan_id, client=client)
            except ClaimError as err:
                if err.code == 3007:
                    captcha_manager.invalidate(proxy=proxy, identity=account_client_pool.current_operation().identity)
                raise
            return _claim_outcome(body, plan_id, plan_name, grants)


async def claim(account: Account, plan_id: str | None = None, *, report_activation: bool = True) -> dict:
    """领取套餐。plan_id 缺省时自动选优先级最高的可领套餐。

    返回 {"plan_id", "plan_name", "grants", "starts_at", "ends_at", "server_time"}；
    3007（验证码失败）定向清空代理亲和水库后直接返回失败，不在本次领取中换码连打。
    """
    async with account_client_pool.operation(account, purpose="claim"):
        _ensure_claimable_account(account, plan_id=plan_id)
        plan_id, plan_name, grants = await _auto_pick_plan(account, plan_id)
        if report_activation:
            try:
                await report_activation_events(account)
            except Exception as act_err:
                logs.warn("claim", f"账号 {account.name} 领取前激活上报跳过: {act_err}")

        client = account_client_pool.current_operation().client
        proxy = account_client_pool.client_proxy(client)
        async with claim_pacing_gate.limit(proxy):
            verify_param, verify_region = await captcha_manager.get_verify_param(proxy=proxy, operation=account_client_pool.current_operation())
            config = await captcha_manager.fetch_config(operation=account_client_pool.current_operation())
            headers = _claim_headers(account, verify_param, verify_region or config.get("region"))
            try:
                body = await _post_claim(account, headers, plan_id, client=client)
            except ClaimError as err:
                if err.code == 3007:
                    logs.warn("claim", f"账号 {account.name} 验证码被拒，定向清空预解池并停止本次领取")
                    captcha_manager.invalidate(proxy=proxy, identity=account_client_pool.current_operation().identity)
                raise
            return _claim_outcome(body, plan_id, plan_name, grants)
