"""ZCode 额度 / 余额 / 用量查询，以及账号状态判定。

在查询基础上提供「额度用完自动标记 exhausted」的监控能力。
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from . import constants, logs, settings
from .models import Account, Status
from .store import store

_TZ_BEIJING = timezone(timedelta(hours=8))
_DEVICE_MID: str | None = None


def device_mid() -> str:
    """本机设备 ID（ZCode 客户端 telemetry 同款语义）。

    billing 全家桶（current/balance/usage/preview/claim）必需 X-Device-Mid，
    缺失时上游返回 code=3001 parameter error。首次生成后持久化到 data 目录。
    """
    global _DEVICE_MID
    if _DEVICE_MID:
        return _DEVICE_MID
    path = settings.DATA_DIR / "device_mid"
    try:
        _DEVICE_MID = path.read_text().strip()
        if _DEVICE_MID:
            return _DEVICE_MID
    except OSError:
        pass
    _DEVICE_MID = str(uuid.uuid4())
    try:
        os.makedirs(settings.DATA_DIR, exist_ok=True)
        with open(path, "x") as f:
            f.write(_DEVICE_MID)
    except OSError as err:  # noqa: BLE001 - 生成失败不阻断查询，仅进程内复用
        logs.warn("quota", f"device_mid 持久化失败（仅进程内生效）: {err}")
    return _DEVICE_MID


def _auth_headers(account: Account) -> dict:
    """billing 族请求头（对齐 zcode-switch zai_billing_headers 实证形态）。

    UA/版本走 BILLING_APP_VERSION（官方桌面端现行版，与 messages 指纹刻意
    分离）；平台/os/语言/时区/设备 ID 按账号指纹档案出值（每账号独立设备，
    见 fingerprint.py）；每请求全新 x-request-id。
    """
    from .fingerprint import profile_for

    profile = profile_for(account)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"ZCode/{constants.BILLING_APP_VERSION}",
        "HTTP-Referer": constants.ZCODE_ORIGIN,
        "X-Title": constants.BILLING_TITLE,
        "X-ZCode-App-Version": constants.BILLING_APP_VERSION,
        "X-Platform": profile.platform_full,
        "X-Release-Channel": constants.BILLING_RELEASE_CHANNEL,
        "X-Client-Language": profile.language,
        "X-Client-Timezone": profile.timezone,
        "X-Os-Category": profile.os_category,
        "X-Os-Version": profile.os_version,
        "X-Device-Mid": profile.device_mid,
        "x-request-id": str(uuid.uuid4()),
    }
    if account.mode == "jwt" and account.jwt_token:
        headers["Authorization"] = f"Bearer {account.jwt_token}"
    elif account.api_key:
        headers["x-api-key"] = account.api_key
    return headers


def _bonus_active(plan: dict, now: float) -> bool:
    """plan 是否带有已生效的一次性赠送授权（balance 不含这类额度）。

    用于额度耗尽判定：日窗口用完但赠送池有效时，账号仍有真实可用额度。
    """
    for e in (plan or {}).get("entitlements") or []:
        if e.get("period") != "one_time":
            continue
        eff = e.get("effective_at") or 0
        ends = e.get("ends_at") or e.get("expires_at") or 0
        if eff and eff <= now and (not ends or now <= ends):
            return True
    return False


def _canonical_model_key(bal: dict) -> str:
    """提取并归一化模型键名。

    优先深度检查 capabilities 数组中的 model: 协议标签；若包含 flash（大小写无关）
    则无条件归一化为 constants.DEFAULT_MODEL（GLM-5.3-Flash）。
    兜底检查 show_name 与 model 字段；非 Flash 模型保留原样。
    """
    for cap in bal.get("capabilities") or []:
        if isinstance(cap, str) and cap.lower().startswith("model:"):
            mid = cap[len("model:"):].strip().lower()
            if "flash" in mid:
                return constants.DEFAULT_MODEL
    raw = str(bal.get("show_name") or bal.get("model") or "").strip()
    if "flash" in raw.lower():
        return constants.DEFAULT_MODEL
    return raw or "model"


def _safe_units(val) -> int:
    try:
        return int(float(val or 0))
    except (ValueError, TypeError):
        return 0


def _extract_expire(obj: dict) -> str | None:
    """对齐 zcode-switch extract_expire：从对象中提取并格式化过期时间字符串 (YYYY-MM-DD HH:MM)。"""
    keys = (
        "nextRenewTime", "expireTime", "expire_time", "endTime", "end_time", "expireAt", "expiredTime",
        "validEndTime", "expires_at", "expiresAt", "expired_at", "period_end", "ends_at",
    )
    for k in keys:
        v = obj.get(k)
        if v is None:
            continue
        try:
            n = float(v)
            if n > 1_000_000_000_000:
                n = n / 1000.0
            if n > 1_000_000_000:
                dt = datetime.fromtimestamp(n, tz=_TZ_BEIJING)
                return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            pass
        if isinstance(v, str):
            s = v.strip()
            if not s:
                continue
            try:
                dt_iso = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt_iso.tzinfo is None:
                    dt_iso = dt_iso.replace(tzinfo=_TZ_BEIJING)
                return dt_iso.astimezone(_TZ_BEIJING).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
            if "T" in s:
                s = s.replace("T", " ")
            if len(s) >= 16:
                return s[:16]
            return s
    return None


def _plan_tier_from_id(plan_id: str, name: str | None = None) -> tuple[str, str]:
    """对齐 zcode-switch plan_tier_from_id：从 plan_id 与 name 计算套餐层级与代码。"""
    hay = (plan_id or "").lower()
    if name:
        hay += " " + name.lower()
    if "max" in hay:
        return ("Max", "max")
    if "pro" in hay:
        return ("Pro", "pro")
    if "lite" in hay:
        return ("Lite", "lite")
    if "start" in hay:
        return ("Start Plan", "start")
    if any(k in hay for k in ("trial", "taste", "experience", "gift", "weekend", "promo", "activity", "trust", "体验", "赠送")):
        return ("体验", "trial")
    return (name or plan_id or "Other", "other")


def _build_plan_slots(balance_data: dict, current_plans: list) -> list[dict]:
    """从 billing/balance 与 billing/current 数据构建结构化多套餐层级槽位（对齐 zcode-switch）。"""
    raw_plans = balance_data.get("plans") or current_plans or []
    slots: list[dict] = []

    # 1. 优先从 active 套餐数组构建槽位
    for pl in raw_plans:
        if not isinstance(pl, dict):
            continue
        status = str(pl.get("status") or "").lower()
        if status and status != "active":
            continue
        pid = str(pl.get("plan_id") or pl.get("planId") or "").strip()
        pname = str(pl.get("name") or pid).strip()
        tier, tier_code = _plan_tier_from_id(pid, pname)
        expire = _extract_expire(pl)
        slots.append({
            "pid": pid,
            "name": pname or pid or "常规套餐",
            "tier": tier,
            "tier_code": tier_code,
            "expire": expire,
            "total": None,
            "used": None,
            "remaining": None,
            "percent_used": None,
            "items": [],
        })

    # 2. 遍历 balances 额度桶，分发至目标套餐
    raw_balances = balance_data.get("balances") or []
    loose_items: list[dict] = []

    for bal in raw_balances:
        if not isinstance(bal, dict):
            continue
        tot = _safe_units(bal.get("total_units"))
        used = _safe_units(bal.get("used_units"))
        rem = _safe_units(bal.get("remaining_units") if bal.get("remaining_units") is not None else bal.get("available_units"))
        model_name = str(bal.get("show_name") or bal.get("name") or bal.get("model") or "Unknown").strip()
        exp_str = _extract_expire(bal)
        pct = round(used / tot * 100.0, 1) if tot > 0 else 0.0

        item = {
            "name": model_name,
            "total": tot,
            "used": used,
            "remaining": rem,
            "percent_used": pct,
            "unit": str(bal.get("unit_type") or "token"),
            "expires_at": exp_str or bal.get("expires_at"),
        }

        bpid = str(bal.get("plan_id") or bal.get("planId") or "").strip()
        target = next((s for s in slots if s["pid"] and s["pid"] == bpid), None) if bpid else None
        if not target and len(slots) == 1 and not bpid:
            target = slots[0]

        if target:
            if not target.get("expire") and exp_str:
                target["expire"] = exp_str
            target["items"].append(item)
        else:
            loose_items.append(item)

    # 3. 孤儿额度桶容错兜底：未匹配到任何套餐的额度，放入默认常规槽位
    if loose_items:
        max_exp = next((it["expires_at"] for it in loose_items if it.get("expires_at")), None)
        slots.append({
            "pid": "default",
            "name": "常规额度",
            "tier": "Standard",
            "tier_code": "free",
            "expire": max_exp,
            "total": None,
            "used": None,
            "remaining": None,
            "percent_used": None,
            "items": loose_items,
        })

    # 4. 计算各个 Slot 的汇总统计值（对齐 zcode-switch 汇总逻辑）
    for s in slots:
        items = s.get("items") or []
        if items:
            t_sum = sum(it.get("total") or 0 for it in items)
            u_sum = sum(it.get("used") or 0 for it in items)
            r_sum = sum(it.get("remaining") or 0 for it in items)
            s["total"] = t_sum
            s["used"] = u_sum
            s["remaining"] = r_sum
            s["percent_used"] = round(u_sum / t_sum * 100.0, 1) if t_sum > 0 else 0.0

    return slots


async def fetch_quota(account: Account, include_claimable: bool = False) -> dict:
    """拉取单个账号的 方案 / 余额 / 用量，写回账号状态并持久化。

    当 include_claimable=True 时，同步探测待领取活动套餐（并发前置日活上报）。
    返回结构: {"billing":..., "balance":..., "usage":..., "claimable":..., "error":...}
    """
    live = store.find(account.provider, account.id)
    if live is None:
        return {"error": "账号已删除"}
    if live is not account:
        account = live
    if not account.allows_billing() and not account.is_cooling():
        # 与 admin/monitor 同一扇门。冷却账号仍允许查询（测试与兜底约定：
        # 拿额度不得提前解除冷却）；废 JWT / 风控 / 手动停用一律不打 billing。
        from .claim import AUTH_EXPIRED_MESSAGE, billing_block_reason

        return {
            "error": account.last_error
            or billing_block_reason(account, action="上游查询")
            or AUTH_EXPIRED_MESSAGE
        }
    headers = _auth_headers(account)
    base = settings.ZCODE_BILLING_BASE
    result: dict = {}

    async with httpx.AsyncClient(timeout=20) as client:
        async def _get(path: str):
            try:
                return await client.get(f"{base}{path}", headers=headers)
            except httpx.HTTPError:
                return None

        billing_res, balance_res, usage_res = await asyncio.gather(
            _get("/billing/current"),
            _get("/billing/balance"),
            _get("/usage"),
        )

    live = store.find(account.provider, account.id)
    if live is None:
        return {"error": "账号已删除"}
    if live is not account:
        account = live

    now = time.time()
    account.last_checked_at = now

    # 鉴权失败 → 标记 invalid
    if billing_res is not None and billing_res.status_code in (401, 403):
        body = (billing_res.text or "").lower()
        if "captcha" not in body and "verify" not in body:
            from .claim import AUTH_EXPIRED_MESSAGE

            account.status = Status.INVALID
            account.last_error = AUTH_EXPIRED_MESSAGE
            store.update_account(account)
            return {"error": account.last_error}

    if billing_res is not None and billing_res.status_code == 200:
        try:
            data = billing_res.json()
            result["billing"] = data
            plans = (data.get("data") or {}).get("plans") or []
            # 全量保留（多套餐时 entitlements 不丢）；plan 兼容保留首个
            account.plans = plans
            account.plan = plans[0] if plans else {}
        except (ValueError, KeyError):
            pass

    quota_map: dict = {}
    balance_parsed_ok = False
    if balance_res is not None and balance_res.status_code == 200:
        try:
            data = balance_res.json()
            if isinstance(data, dict):
                result["balance"] = data
                now_ts = now
                for bal in (data.get("data") or {}).get("balances") or []:
                    exp = bal.get("expires_at")
                    try:
                        exp_val = float(exp) if exp is not None else 0
                        if exp_val and exp_val <= now_ts:
                            continue
                    except (ValueError, TypeError):
                        pass

                    name = _canonical_model_key(bal)
                    window = {
                        "total": _safe_units(bal.get("total_units")),
                        "used": _safe_units(bal.get("used_units")),
                        "remaining": _safe_units(bal.get("remaining_units")),
                        "expires_at": bal.get("expires_at"),
                    }
                    prev = quota_map.get(name)
                    if prev:
                        # 同模型多窗口（如日窗 + 一次性活动赠送池）正常累加合并
                        for k in ("total", "used", "remaining"):
                            prev[k] = (prev.get(k) or 0) + (window.get(k) or 0)
                        prev["expires_at"] = max(prev.get("expires_at") or 0, window.get("expires_at") or 0)
                    else:
                        quota_map[name] = window
                balance_parsed_ok = True
        except (ValueError, KeyError):
            pass

    if usage_res is not None and usage_res.status_code == 200:
        try:
            account.usage = usage_res.json().get("data") or {}
            result["usage"] = account.usage
        except (ValueError, KeyError):
            pass

    # 只要上游 /billing/balance 成功响应 200 并合法解析，物理事实即为基准进行真相同步
    if balance_parsed_ok:
        account.quota = quota_map
        account.plan_slots = _build_plan_slots(data.get("data") or {}, account.plans)
        if quota_map:
            # 额度耗尽判定：优先锚定主免费池模型 DEFAULT_MODEL（GLM-5.3-Flash，大小写无关），
            # 防止新号在 GLM-5.3-Flash 耗尽后因闲置的 300 万 GLM-5.3 余量阻塞 EXHAUSTED 状态或触发误恢复；
            # 若 quota_map 不含主模型窗口（如离线 Mock 环境），回退按全部窗口余量判定。
            target_norm = constants.DEFAULT_MODEL.strip().lower()
            flash_remainings = [
                q.get("remaining")
                for k, q in quota_map.items()
                if isinstance(q, dict)
                and str(k).strip().lower() == target_norm
                and q.get("remaining") is not None
            ]
            target_remainings = flash_remainings if flash_remainings else [
                q.get("remaining")
                for q in quota_map.values()
                if isinstance(q, dict) and q.get("remaining") is not None
            ]
            all_exhausted = bool(target_remainings) and all((r or 0) <= 0 for r in target_remainings)
            has_remaining = bool(target_remainings) and any((r or 0) > 0 for r in target_remainings)
            if all_exhausted:
                account.status = Status.EXHAUSTED
                account.last_error = "额度已用完"
            elif account.status in (Status.EXHAUSTED, Status.COOLING) and has_remaining:
                # 额度恢复（窗口重置 / 新领套餐到账）→ 重新激活。冷却期内不提前解除；
                # 废 JWT / 风控禁用绝不能因额度数字复活 Plan 通道。
                if not account.is_cooling():
                    account.status = Status.ACTIVE
                    account.last_error = None
                    account.cooling_until = None
        else:
            # balance_res 成功响应 200 但 balances 列表为空：
            # 说明上游确认该账号物理上已无任何有效额度窗口（套餐到期或未开通）。
            # 必须清空 account.quota 避免残留过期快照；若账号当前为 ACTIVE，无额度时立即转为 EXHAUSTED。
            if account.status == Status.ACTIVE:
                account.status = Status.EXHAUSTED
                account.last_error = "额度已用完"
            account.plan_slots = []

    # 当 include_claimable=True 时，同步探测待领取活动套餐（并发前置日活上报，带独立短超时与异常隔离）
    if include_claimable and account.mode == "jwt" and account.jwt_token and account.allows_billing():
        try:
            from .claim import preview_plans, report_activation_events

            try:
                # 兼容 5 秒短超时，前置激活事件上报（不阻断 preview）
                await asyncio.wait_for(report_activation_events(account), timeout=5.0)
            except Exception as act_err:
                logs.warn("quota", f"账号 {account.name} 刷新前置日活上报跳过: {act_err}")

            claim_plans = await asyncio.wait_for(preview_plans(account), timeout=8.0)
            account.claimable_plans = claim_plans
            result["claimable"] = claim_plans
        except Exception as claim_err:
            logs.info("quota", f"账号 {account.name} 待领活动探测跳过/异常: {claim_err}")

    store.update_account(account)
    return result or {"error": "无法获取额度数据"}


async def refresh_accounts(accounts: list[Account]) -> dict:
    """并发刷新一批账号，返回汇总。"""
    if not accounts:
        return {"ok": 0, "fail": 0}
    sem = asyncio.Semaphore(8)

    async def _one(acc: Account) -> bool:
        async with sem:
            res = await fetch_quota(acc)
            return "error" not in res

    results = await asyncio.gather(*[_one(a) for a in accounts], return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    return {"ok": ok, "fail": len(accounts) - ok}


class QuotaMonitor:
    """后台周期性刷新可管理账号的额度，实现实时用量监控。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        # 启动后先等几秒，避免与服务启动争抢
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=5)
            return
        except TimeoutError:
            pass

        while not self._stop.is_set():
            interval = store.quota_refresh_interval()  # 实时读取设置，改后即生效
            if interval > 0:
                try:
                    accounts = [
                        a for a in store.list_accounts("zai")
                        if a.mode == "jwt" and a.allows_billing()
                    ]
                    if accounts:
                        await refresh_accounts(accounts)
                except Exception as err:  # noqa: BLE001 - 后台任务需吞掉异常继续运行
                    logs.err("quota", f"后台刷新出错: {err}")
            # interval<=0 视为关闭：仍周期性回看设置，便于随时启用
            wait = interval if interval > 0 else 30
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except TimeoutError:
                continue

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


monitor = QuotaMonitor()
