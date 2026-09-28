"""官方活动哨兵监控与智能抢领闭环服务。

以极轻量单账号（Sentinel）低频巡检上游套餐列表（无验证码、零成本），
差量比对持久化已见活动，杜绝告警风暴；
发现新活动后以单并发顺序串行 + 离散抖动延时自动为全池账号抢领套餐（保护验证码预解池），
并将活动详情与抢领战报通过 Bark 推送至用户设备。
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from . import logs
from .claim import auto_claim_all_plans, preview_plans
from .models import Status
from .notify import send_bark_notification
from .store import store


class Sentinel:
    """活动巡检与智能抢领单例调度器。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running: bool = False

    def start(self) -> None:
        """启动后台巡检任务。"""
        if self._running or (self._task and not self._task.done()):
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """优雅停止后台巡检任务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

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

            try:
                await self.check_once()
            except asyncio.CancelledError:
                break
            except Exception as err:
                logs.err("sentinel", f"活动巡检异常: {err}")

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def check_once(self) -> dict[str, Any]:
        """执行单次活动巡检与差量闭环处理，返回探测与抢领结果摘要。"""
        # 1. 筛选候选健康 JWT 账号
        candidates = [
            a for a in store.list_accounts("zai")
            if a.mode == "jwt" and a.jwt_token and a.status == Status.ACTIVE and not a.is_claim_blocked()
        ]
        if not candidates:
            logs.info("sentinel", "未找到健康的活跃 JWT 账号用于活动探测，跳过本次巡检")
            return {"ok": False, "reason": "no_active_sentinel"}

        # 2. 单轮探针（上限 min(3, len(candidates)) 次，遇 401 标记失效并轮换下一位，防死锁）
        plans = None
        sentinel_acc = None
        max_probe = min(3, len(candidates))
        for candidate in candidates[:max_probe]:
            try:
                plans = await preview_plans(candidate)
                sentinel_acc = candidate
                break
            except Exception as err:
                logs.warn("sentinel", f"账号 {candidate.name} 探测失败: {err}，尝试下一个探针")

        if sentinel_acc is None or plans is None:
            logs.err("sentinel", f"连续 {max_probe} 次哨兵探针均失败，终止本轮巡检")
            return {"ok": False, "reason": "probe_exhausted"}

        # 3. 差量对比已见活动（SQLite meta 表原子去重）
        seen_ids = store.get_seen_plan_ids()
        new_plans = [p for p in plans if p.get("plan_id") and p["plan_id"] not in seen_ids]

        if not new_plans:
            logs.info("sentinel", f"巡检完成，当前共 {len(plans)} 个上游套餐，无新活动")
            return {"ok": True, "new_plans_count": 0, "plans_count": len(plans)}

        logs.ok("sentinel", f"🎉 发现 {len(new_plans)} 个全新活动套餐: {[p.get('name') or p['plan_id'] for p in new_plans]}")

        # 4. 单并发顺序串行 + 抖动执行全池自动抢领（严格保护验证码预解池与 Node Solver）
        claim_reports: list[dict] = []
        if store.sentinel_auto_claim():
            all_jwt = [
                a for a in store.list_accounts("zai")
                if a.mode == "jwt" and a.jwt_token and a.status == Status.ACTIVE
            ]
            for acc in all_jwt:
                if acc.is_claim_blocked():
                    claim_reports.append({"name": acc.name, "result": "1005避让中"})
                    continue
                try:
                    # 离散随机抖动延时，消除群聚效应
                    await asyncio.sleep(random.uniform(0.6, 1.5))
                    outcomes = await auto_claim_all_plans(acc)
                    ok_count = sum(1 for o in outcomes if o.get("ok"))
                    res_str = f"成功领{ok_count}项" if ok_count else "无新配额"
                    claim_reports.append({"name": acc.name, "result": res_str})
                except Exception as err:
                    claim_reports.append({"name": acc.name, "result": f"失败({err})"})

        # 5. 格式化构建 Bark 通知文本
        body_lines = []
        for p in new_plans:
            p_name = p.get("name") or p.get("plan_id")
            grants_desc = "、".join(f"{g.get('name')} {int(g.get('units', 0)):,} Token" for g in p.get("grants", []))
            desc = f"· {p_name}" + (f" ({grants_desc})" if grants_desc else "")
            body_lines.append(desc)

        if claim_reports:
            success_cnt = sum(1 for r in claim_reports if "成功" in r["result"])
            body_lines.append(f"\n📊 全池抢领战报 ({success_cnt}/{len(claim_reports)} 成功):")
            for r in claim_reports[:5]:  # 最多展示前 5 个账号明细防超长
                body_lines.append(f"  · {r['name']}: {r['result']}")
            if len(claim_reports) > 5:
                body_lines.append(f"  · 其余 {len(claim_reports) - 5} 个账号已处理完毕")

        notify_title = f"🎉 发现 ZCode 新活动 ({len(new_plans)}项)"
        notify_body = "\n".join(body_lines)

        # 6. 时序安全投递与状态落库（投递完成后再将 plan_id 提交持久化落库）
        bark_key = store.bark_device_key()
        bark_server = store.bark_server_url()
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
            else:
                logs.warn("sentinel", f"新活动 Bark 推送失败: {msg}")

        # 提交持久化已见集合
        store.add_seen_plan_ids([p["plan_id"] for p in new_plans])

        return {
            "ok": True,
            "new_plans": new_plans,
            "claim_reports": claim_reports,
        }


sentinel = Sentinel()
