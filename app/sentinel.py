"""官方活动哨兵监控与智能抢领闭环服务。

以极轻量单账号（Sentinel）低频巡检上游套餐列表（无验证码、零成本），
差量比对持久化已见活动，杜绝告警风暴；
发现新活动后以单并发顺序串行 + 离散抖动延时自动为全池账号抢领套餐（保护验证码预解池），
并将活动详情与抢领战报通过 Bark 推送至用户设备。
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from . import logs
from .claim import (
    _TZ_BEIJING,
    _norm_plan_id,
    account_held_plan_ids,
    auto_claim_all_plans,
    is_base_plan_id,
    pool_active_plans,
    preview_plans,
    report_activation_events,
    sync_pool_claimable_plans,
)
from .models import Account, Status
from .notify import notify_claim_outcomes, send_bark_notification
from .store import store

# 补领失败冷却时间（6小时），防止个别账号因 3012 风控或条件不符在每轮巡检重复撞击上游
_CATCHUP_COOLDOWN_SEC = 6 * 3600
# 新活动 Bark 推送连续失败最大保留轮数（超出后兜底落库，防永久错误配置死循环）
_MAX_NOTIFY_FAIL_ROUNDS = 3
# 跨北京时间零点破冰窗口（00:00:15 ~ 00:01:00）与零点后 10 分钟内未见新活动的快速复查间隔（90 ~ 150 秒）
_MIDNIGHT_WAKE_MIN_SEC = 15.0
_MIDNIGHT_WAKE_MAX_SEC = 60.0
_MIDNIGHT_GRACE_MINUTES = 10
_MIDNIGHT_RETRY_MIN_SEC = 90.0
_MIDNIGHT_RETRY_MAX_SEC = 150.0


class Sentinel:
    """活动巡检与智能抢领单例调度器。"""

    STOP_GRACE_SECONDS = 5.0

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running: bool = False
        # 记录 (account_id, plan_id) -> cooldown_until 时间戳
        self._catchup_cooldown: dict[tuple[str, str], float] = {}
        # 记录 plan_id -> Bark 推送连续失败轮数（未达上限前不写入 seen_ids，确保跨轮补推）
        self._notify_fail_counts: dict[str, int] = {}

    def start(self) -> None:
        """启动后台巡检任务。"""
        if self._running or (self._task and not self._task.done()):
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """优雅停止后台巡检任务（带 STOP_GRACE_SECONDS 截止保护，防求解长尾卡住停服）。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=self.STOP_GRACE_SECONDS)
            except (TimeoutError, asyncio.CancelledError, Exception):
                pass
            self._task = None

    def _next_sleep_seconds(
        self,
        interval: int,
        last_result: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> float:
        """计算下一轮巡检睡眠秒数：常规 ±10% 抖动 + 跨北京时间 00:00 零点破冰对齐 + 00:00~00:10 快速复查 + 1005 解封唤醒。"""
        if interval <= 0:
            return 30.0
        now_ts = now if now is not None else time.time()
        wait = float(interval) * random.uniform(0.9, 1.1)

        dt = datetime.fromtimestamp(now_ts, tz=_TZ_BEIJING)
        next_midnight = (dt + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sec_to_midnight = (next_midnight - dt).total_seconds()

        # 1. 跨零点破冰截断：若本轮睡眠将跨越北京时间 00:00:00，截断至 00:00:15 ~ 00:01:00 准时唤醒抢领新日活动
        if 0 < sec_to_midnight < wait:
            wait = sec_to_midnight + random.uniform(_MIDNIGHT_WAKE_MIN_SEC, _MIDNIGHT_WAKE_MAX_SEC)

        # 2. 凌晨 00:00 ~ 00:10 黄金窗口快速兜底：若刚过零点上游稍晚几分钟上架，且尚未探测到今日新活动，每 90~150s 快速复查
        if dt.hour == 0 and dt.minute < _MIDNIGHT_GRACE_MINUTES:
            mmdd = dt.strftime("%m%d")
            has_new = bool((last_result or {}).get("new_plans"))
            seen_today = any(
                _norm_plan_id(pid).endswith(mmdd)
                for pid in store.get_seen_plan_ids()
            )
            if not has_new and not seen_today:
                wait = min(wait, random.uniform(_MIDNIGHT_RETRY_MIN_SEC, _MIDNIGHT_RETRY_MAX_SEC))

        # 3. 1005 名额避让解封对齐：若池内存在即将解封（如 00:05+jitter 或 next_at）的账号，对齐解封时间点唤醒补领
        unblock_deltas = [
            a.claim_blocked_until - now_ts
            for a in store.list_accounts("zai")
            if a.allows_billing()
            and a.claim_blocked_until
            and a.claim_blocked_until > now_ts
        ]
        if unblock_deltas:
            earliest_delta = min(unblock_deltas)
            if earliest_delta < wait:
                wait = min(wait, earliest_delta + random.uniform(5.0, 20.0))

        return max(5.0, wait)

    async def _loop(self) -> None:
        """周期巡检主循环。"""
        logs.info("sentinel", "活动哨兵调度器已启动")
        # 启动后首次探针稍作延时（30秒），避开主服务刚启动时的初始化高负载
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            return

        while self._running:
            interval = store.sentinel_interval()
            if interval <= 0:
                # 巡检间隔设为 0 时暂停巡检，每 30 秒轮询一次设置变更
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    break
                continue

            last_result: dict[str, Any] | None = None
            try:
                last_result = await self.check_once()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logs.err("sentinel", f"活动巡检异常: {err}")

            wait_sec = self._next_sleep_seconds(interval, last_result=last_result)
            try:
                await asyncio.sleep(wait_sec)
            except asyncio.CancelledError:
                break

    def _prune_expired_cooldown(self, now_ts: float) -> None:
        """清理已到期的补领失败冷却记录。"""
        if not self._catchup_cooldown:
            return
        for k in [k for k, ts in self._catchup_cooldown.items() if ts <= now_ts]:
            self._catchup_cooldown.pop(k, None)

    def _record_cooldown(
        self,
        account_id: str,
        plan_ids: Iterable[str | dict],
        now_ts: float | None = None,
    ) -> None:
        """将指定账号与套餐标记进补领冷却窗口（6小时），防止连续撞击上游风控。"""
        until = (now_ts if now_ts is not None else time.time()) + _CATCHUP_COOLDOWN_SEC
        for item in plan_ids:
            pid = _norm_plan_id(item)
            if pid:
                self._catchup_cooldown[(account_id, pid)] = until

    @staticmethod
    def _billable_candidates(*, include_claim_blocked: bool = False) -> list[Account]:
        """筛选可计费 JWT 账号，同优先级随机打散并按 ACTIVE 优先排序。"""
        candidates = [
            a for a in store.list_accounts("zai")
            if a.allows_billing() and (include_claim_blocked or not a.is_claim_blocked())
        ]
        random.shuffle(candidates)
        candidates.sort(key=lambda a: 0 if a.status == Status.ACTIVE else 1)
        return candidates

    async def _probe_upstream_plans(self, candidates: list[Account]) -> list[dict] | None:
        """执行有界单轮探针（最多 3 个账号）：前置日活事件上报 + preview_plans。"""
        max_probe = min(3, len(candidates))
        for candidate in candidates[:max_probe]:
            try:
                try:
                    await asyncio.wait_for(report_activation_events(candidate), timeout=5.0)
                except Exception as act_err:
                    logs.info("sentinel", f"探针账号 {candidate.name} 前置激活上报跳过: {act_err}")
                return await preview_plans(candidate)
            except Exception as err:
                logs.warn("sentinel", f"账号 {candidate.name} 探测失败: {err}，尝试下一个探针")
        logs.err("sentinel", f"连续 {max_probe} 次哨兵探针均失败，终止本轮巡检")
        return None

    async def _run_catchup_claims(
        self,
        candidates: list[Account],
        plans: list[dict],
        now_ts: float,
    ) -> dict[str, Any]:
        """存量未领账号自动补领闭环：检查池内漏领当期有效活动的账号并串行补领。"""
        catchup_reports: list[dict] = []
        catchup_ok_outcomes: list[dict] = []
        if store.sentinel_auto_claim():
            active_pids = {
                _norm_plan_id(p)
                for p in pool_active_plans(now_ts)
                if _norm_plan_id(p) and not is_base_plan_id(p)
            }
            for acc in candidates:
                held = account_held_plan_ids(acc)
                acc_claimable = {
                    _norm_plan_id(p)
                    for p in (getattr(acc, "claimable_plans", None) or [])
                    if isinstance(p, dict) and _norm_plan_id(p) and not is_base_plan_id(p)
                }
                target_pids = (active_pids | acc_claimable) - held
                pending_pids = [
                    pid for pid in target_pids
                    if self._catchup_cooldown.get((acc.id, pid), 0) <= now_ts
                ]
                if not pending_pids:
                    continue
                try:
                    await asyncio.sleep(random.uniform(0.6, 1.5))
                    outcomes = await auto_claim_all_plans(acc)
                    ok_items = [o for o in outcomes if o.get("ok")]
                    if ok_items:
                        catchup_reports.append({"name": acc.name, "result": f"补领成功{len(ok_items)}项"})
                        for item in ok_items:
                            catchup_ok_outcomes.append({"account_name": acc.name, **item})
                    else:
                        self._record_cooldown(acc.id, pending_pids)
                        catchup_reports.append({"name": acc.name, "result": "补领未果(已避让)"})
                except Exception as err:
                    self._record_cooldown(acc.id, pending_pids)
                    catchup_reports.append({"name": acc.name, "result": f"补领失败({err})"})

        if catchup_reports:
            logs.ok("sentinel", f"巡检完成，已为 {len(catchup_reports)} 个漏领账号执行自动补领: {catchup_reports}")
            if catchup_ok_outcomes:
                await notify_claim_outcomes(catchup_ok_outcomes, source="哨兵自动补领")
        else:
            logs.info("sentinel", f"巡检完成，当前共 {len(plans)} 个上游套餐，无新活动")
        return {"ok": True, "new_plans_count": 0, "plans_count": len(plans), "catchup_reports": catchup_reports}

    async def _claim_new_plans_across_pool(self, new_plans: list[dict]) -> list[dict]:
        """发现新活动时：先向全池可计费账号同步待领状态，再按单并发顺序串行 + 抖动执行全池抢领。"""
        all_jwt = self._billable_candidates(include_claim_blocked=True)
        # 先把新活动同步进全池未持有账号的 claimable_plans，确保即便跳过或抢领失败，页面立即可见待领 Icon
        sync_pool_claimable_plans(new_plans, accounts=all_jwt)
        if not store.sentinel_auto_claim():
            return []

        claim_reports: list[dict] = []
        new_pids_lower = {_norm_plan_id(p) for p in new_plans if _norm_plan_id(p)}
        for acc in all_jwt:
            if acc.is_claim_blocked():
                claim_reports.append({"name": acc.name, "result": "1005避让中"})
                continue
            try:
                await asyncio.sleep(random.uniform(0.6, 1.5))
                outcomes = await auto_claim_all_plans(acc)
                ok_count = sum(1 for o in outcomes if o.get("ok"))
                fail_items = [o for o in outcomes if not o.get("ok")]
                if ok_count:
                    res_str = f"成功领{ok_count}项"
                elif new_pids_lower & account_held_plan_ids(acc):
                    res_str = "已持有(成功)"
                elif fail_items:
                    first_msg = str(fail_items[0].get("message") or "领取失败").split("（")[0]
                    res_str = f"失败({first_msg})"
                    self._record_cooldown(acc.id, fail_items)
                else:
                    res_str = "无新配额"
                claim_reports.append({"name": acc.name, "result": res_str})
            except Exception as err:
                claim_reports.append({"name": acc.name, "result": f"失败({err})"})
        return claim_reports

    @staticmethod
    def _format_bark_report(new_plans: list[dict], claim_reports: list[dict]) -> tuple[str, str]:
        """组装新活动发现与全池抢领战报的 Bark 标题与正文。"""
        body_lines = []
        for p in new_plans:
            p_name = p.get("name") or p.get("plan_id")
            grants_desc = "、".join(f"{g.get('name')} {int(g.get('units', 0)):,} Token" for g in p.get("grants", []))
            desc = f"· {p_name}" + (f" ({grants_desc})" if grants_desc else "")
            body_lines.append(desc)

        if claim_reports:
            success_cnt = sum(1 for r in claim_reports if "成功" in r["result"])
            body_lines.append(f"\n📊 全池抢领战报 ({success_cnt}/{len(claim_reports)} 成功):")
            for r in claim_reports[:6]:
                body_lines.append(f"  · {r['name']}: {r['result']}")
            if len(claim_reports) > 6:
                body_lines.append(f"  · 其余 {len(claim_reports) - 6} 个账号已处理完毕")

        return f"🎉 发现 ZCode 新活动 ({len(new_plans)}项)", "\n".join(body_lines)

    async def _deliver_and_commit_new_plans(
        self,
        new_plans: list[dict],
        claim_reports: list[dict],
    ) -> None:
        """时序安全投递 Bark 战报并更新 seen_ids（防单次网络抖动丢通知）。"""
        notify_title, notify_body = self._format_bark_report(new_plans, claim_reports)
        bark_key = store.bark_device_key()
        bark_server = store.bark_server_url()
        commit_ids: list[str] = []
        if bark_key:
            ok, msg = await send_bark_notification(
                device_key=bark_key,
                server_url=bark_server,
                title=notify_title,
                body=notify_body,
                group="ZCode活动",
            )
            if ok:
                logs.ok("sentinel", "新活动通知已成功送达 Bark 客户端")
                for p in new_plans:
                    pid = p["plan_id"]
                    self._notify_fail_counts.pop(pid, None)
                    commit_ids.append(pid)
            else:
                logs.warn("sentinel", f"新活动 Bark 推送失败: {msg}")
                for p in new_plans:
                    pid = p["plan_id"]
                    fails = self._notify_fail_counts.get(pid, 0) + 1
                    self._notify_fail_counts[pid] = fails
                    if fails >= _MAX_NOTIFY_FAIL_ROUNDS:
                        logs.warn("sentinel", f"活动 {pid} 连续 {fails} 轮 Bark 推送失败，兜底标记为已见")
                        self._notify_fail_counts.pop(pid, None)
                        commit_ids.append(pid)
        else:
            commit_ids = [p["plan_id"] for p in new_plans]

        if commit_ids:
            store.add_seen_plan_ids(commit_ids)

    async def check_once(self) -> dict[str, Any]:
        """执行单次活动巡检与差量闭环处理，返回探测与抢领结果摘要。"""
        now_ts = time.time()
        self._prune_expired_cooldown(now_ts)

        candidates = self._billable_candidates(include_claim_blocked=False)
        if not candidates:
            logs.info("sentinel", "未找到可用的 JWT 账号用于活动探测，跳过本次巡检")
            return {"ok": False, "reason": "no_active_sentinel"}

        plans = await self._probe_upstream_plans(candidates)
        if plans is None:
            return {"ok": False, "reason": "probe_exhausted"}

        seen_ids = store.get_seen_plan_ids()
        new_plans = [p for p in plans if p.get("plan_id") and p["plan_id"] not in seen_ids]
        if not new_plans:
            return await self._run_catchup_claims(candidates, plans, now_ts)

        logs.ok("sentinel", f"🎉 发现 {len(new_plans)} 个全新活动套餐: {[p.get('name') or p['plan_id'] for p in new_plans]}")
        claim_reports = await self._claim_new_plans_across_pool(new_plans)
        await self._deliver_and_commit_new_plans(new_plans, claim_reports)
        return {
            "ok": True,
            "new_plans": new_plans,
            "claim_reports": claim_reports,
        }


sentinel = Sentinel()

