"""出口活性探针：定期确认候选出口是否还能服务，结果写回既有断路器。

为什么需要它（第一性原理）：
    client_pool 的断路器只在**真实请求踩雷后**才隔离节点（被动）。用户可能连续踩到
    多个已失效节点才拿到一次成功响应。探针把「发现失效」提前到请求之前。
    反过来，被隔离的节点也只是「暂时不用」，没有任何机制确认它是否已恢复 ——
    探针同样是唯一的恢复信号来源（断路器本身有 HALF-OPEN 但需流量触发）。

为什么不新建白名单：
    client_pool 已经有三态断路器（CLOSED/OPEN/HALF-OPEN）作为「哪些出口可用」的唯一
    记账处。再建一份白名单就是第二处状态，必然漂移。探针只做一件事：把探测结果交给
    record_proxy_result()，让既有机制决定去向。

判据（回答的是「该出口能否服务对话请求」，不是「链路是否通」）：
      - 归一后 200 → 可达
      - 归一后 402（额度耗尽）/ 403（验证码挑战）→ 可达 —— 出口通了，障碍在账号侧，
        换个账号即可成功
      - 归一后 405 或业务码 3012（unusual activity）→ **不可达** —— 这正是「该出口已被
        上游拉黑」的信号，也是本探针存在的首要理由
      - 5xx / 网络层失败 → 不可达（上游或链路故障）

    注意与 scripts/mihomo_auto_probe.py 的 node_reachable 区分：那个函数回答「链路是否
    打通」，把 405/3012 也算通（对节点发现是对的——节点确实到达了 ESA）。探针要判断节点
    能否用于对话，3012 必须判不可达，否则「127 个被拉黑节点」会被全部误判为可用
    （2026-10-15 实测踩到）。
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx

from . import logs, settings

_PROBE_MODEL = "GLM-5.3-Flash"
_PROBE_BODY = {
    "model": _PROBE_MODEL,
    "max_tokens": 1,
    "stream": False,
    # 探测正文带上标记，便于在上游侧与我们自己的日志里区分探测流量
    "messages": [{"role": "user", "content": "ping"}],
}


# 被上游明确封禁的业务码：这类失败不是"网络抖动"，而是"该出口已被拉黑"。
# 它们应当按 WAF 封禁上报（断路器阈值 2），而不是普通失败（阈值 3）——
# 否则服务重启后内存态清零，需要 3 轮（30 分钟）才熔断，
# 期间所有账号都会被分到已坏的出口（2026-10-15 实测踩到）。
_WAF_CODES = {"3012"}
_WAF_STATUSES = {403, 405}


def _is_waf_block(status: int, code: str) -> bool:
    """该次失败是否属于「出口被上游封禁」而非普通网络/上游故障。"""
    return code in _WAF_CODES or status in _WAF_STATUSES


def _probe_accounts() -> list:
    """可作探测身份的账号：凭证有效且启用即可，不要求可服务。

    额度耗尽（exhausted）的账号凭证依然有效，且 max_tokens=1 的探测不产生额度消耗，
    是最低成本的身份；风控禁用的账号同样能验证出口可达性。只有池内不存在任何有效
    凭证时才无探测前提。
    """
    from .store import store

    return [
        a for a in store.list_accounts("zai")
        if a.enabled and a.mode == "jwt" and (a.jwt_token or "").strip()
    ]


def _candidate_egresses() -> list[str]:
    """候选出口：账号实际使用的大区里、未被隔离的节点（按需探测）。

    为什么不探全局池的全部 127 个：账号出口是按大区绑定的（resolve_proxy 优先取
    account.assigned_region 的端口段，且被隔离时只在**同区**环形避让，不跨区）。
    未使用大区的端口即使探通也没有账号会走 —— 属于无用流量。

    实测（2026-10-15）：有效账号只涉及 HK(4)/JP(22)/TW(6) 三个大区共 32 个端口，
    而全局池有 127 个。按需探测把一轮从 ~19 分钟压到 ~5 分钟，且不损失覆盖面。

    这也就是方案里「只扫活节点白名单」的含义：白名单不是「所有可用节点」，
    而是「账号真正会走到的那些节点」。

    直连（None）不属于托管节点探测目标；是否使用由生效 chat 策略决定。
    """
    from .client_pool import account_client_pool
    from .store import store

    if account_client_pool.effective_mode("chat") == "direct":
        return []
    if account_client_pool.policy.chat_mode == "proxy":
        return [account_client_pool.policy.chat_proxy_url]
    regions = {
        a.assigned_region
        for a in store.list_accounts("zai")
        if a.enabled and a.mode == "jwt" and a.assigned_region
    }
    if not regions:
        # 账号都没绑大区时退回全局池（保持与 resolve_proxy 第 3 条回退路径一致）
        return [
            p for p in settings.ZCODE_PROXIES
            if account_client_pool.proxy_eligible(p) and not account_client_pool.is_proxy_quarantined(p)
        ]

    registry = account_client_pool.load_registry()
    out: list[str] = []
    for region in sorted(regions):
        for p in registry.get(region) or []:
            if account_client_pool.proxy_eligible(p) and not account_client_pool.is_proxy_quarantined(p):
                out.append(p)
    return out


def _verdict(effective: int, code: str | None) -> tuple[bool, str]:
    """把归一化后的响应判成「该出口能否服务对话请求」。"""
    from . import constants

    # 风控：业务码 3012 或 HTTP 405。归一化会把 3012 也归成 405，两条都留以便诊断时
    # 区分「上游给了 3012」与「上游直接 405」。
    if code == "3012":
        return False, "该出口已被上游拉黑（3012）"
    if effective in constants.RISK_CONTROL_HTTP_STATUSES:
        return False, "该出口已被上游拒绝（405）"
    if effective == 200:
        return True, "上游已受理"
    if effective in (402, 403):
        # 额度耗尽 / 验证码挑战：障碍在账号侧，该出口可用
        return True, "出口可用（障碍在账号侧）"
    if effective >= 500:
        return False, "上游或链路故障"
    return False, "该出口下请求未获受理"


async def _probe_one(account, proxy: str) -> tuple[bool, str, dict]:
    """对单个出口发一次探测请求。返回 (可达, 说明, 明细)。"""
    from .agent import build_request
    from .channels.plugin_channel import plugin_host
    from .routes.gateway import _classify_business_error

    payload_obj = dict(_PROBE_BODY)
    try:
        _url, headers, payload = build_request(account, payload_obj, None, {}, None)
    except Exception as err:  # noqa: BLE001
        return False, f"构建请求失败: {err}", {}

    if not plugin_host.is_alive():
        # 无 plugin 通道时不做探测：native 在当前上游规则下必然 405，
        # 用它探测只会把所有出口误判为坏。
        return False, "plugin 通道未就绪", {}

    started = time.time()
    try:
        cm = await plugin_host.fetch(
            account=account, model=_PROBE_MODEL, url=_url, method="POST",
            headers=headers, payload=payload, proxy=proxy,
        )
        resp = await cm.__aenter__()
        try:
            head = plugin_host.head_of(getattr(cm, "_fid", 0)) or {}
            status = int(head.get("status") or resp.status_code)
            raw = await resp.aread()
            text = raw.decode("utf-8", "ignore")
            effective, code, _cc = _classify_business_error(status, resp, text)
            reachable, why = _verdict(effective, code)
            return reachable, f"HTTP {status} → {effective} code={code}（{why}）", {
                "status": status, "effective": effective, "code": code,
                "verdict": why,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        finally:
            await cm.__aexit__(None, None, None)
    except httpx.HTTPError as err:
        return False, f"传输失败: {err}", {"elapsed_ms": int((time.time() - started) * 1000)}
    except Exception as err:  # noqa: BLE001
        return False, f"探测异常: {err}", {"elapsed_ms": int((time.time() - started) * 1000)}


class EgressProbe:
    """出口活性探针后台任务（与 captcha 的补充循环同模式）。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._closed = True
        self._last: dict = {}
        # 已确认可服务的 **(账号, 出口) 组合**（每轮探测后重建）。
        # 空集合表示「尚无确认结果」，此时请求侧应直连兜底而不是拿未验证的端口去撞
        # （见 app/channels/plugin_channel.py）。
        #
        # 为什么是组合而不是出口：2026-10-15 决定性实测——
        #     同出口 21200 对账号 5735 返回 405/3012，对 9149 返回 200/1005。
        # 即「该出口能不能服务」取决于**是哪个账号在用**。门禁要判断的正是
        # 「这个账号走这个出口行不行」，所以必须按组合记录。
        self._confirmed: set[tuple[str, str]] = set()

    def is_confirmed(self, account_id: str, proxy: str | None) -> bool:
        """(该账号, 该出口) 组合是否已被探针确认可服务。"""
        return bool(proxy) and bool(account_id) and (account_id, proxy) in self._confirmed

    def confirmed(self) -> set[tuple[str, str]]:
        return set(self._confirmed)

    def start(self) -> None:
        if settings.EGRESS_PROBE_INTERVAL <= 0:
            logs.ok("egress", "出口探针已关闭（EGRESS_PROBE_INTERVAL=0）")
            return
        self._closed = False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logs.ok("egress", f"出口探针已启动（每 {settings.EGRESS_PROBE_INTERVAL}s 一轮）")

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        # 只等一小段，避开与其他启动任务抢求解槽；断路器是内存态、重启即清零，
        # 探针越晚跑，重启后越多的请求会踩到已失效的绑定端口。
        await self._sleep(settings.EGRESS_PROBE_START_DELAY)
        while not self._closed:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                logs.warn("egress", f"出口探测轮次异常: {err}")
            await self._sleep(settings.EGRESS_PROBE_INTERVAL)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise

    async def run_once(self) -> dict:
        """跑一轮探测。结果写回断路器，明细落 DB meta 供管理页观测。"""
        from .client_pool import RegionOfflineError, account_client_pool

        accounts = _probe_accounts()
        if not accounts:
            logs.warn("egress", "池内无有效凭证，跳过本轮出口探测")
            return {}

        candidates = _candidate_egresses()
        if not candidates:
            logs.warn("egress", "无待探测出口（全部隔离或未配置代理池）")
            return {}

        results: dict[str, dict] = {}
        confirmed: set[tuple[str, str]] = set()

        # 按账号配对：每个账号测**它真正会走**的那个出口（与生产同一算法）。
        #
        # 为什么不 round-robin：早前的实现把「候选出口第 i 个」配给「账号第 i 个」，
        # 而生产用 resolve_proxy() 的一致性哈希选路 —— 两套算法不同，导致探针测的
        # 组合与真实组合错位（实测 13 个账号里 12 个不一致），门禁因此基于错误数据。
        #
        # 注：这里不检查 _closed —— 它是「循环是否继续」的生命周期标志，单轮探测
        # 一旦开始就应跑完，否则管理页手动触发与单测都会拿到半截结果。
        for account in accounts:
            try:
                proxy = account_client_pool.resolve_proxy(account, purpose="chat")
            except Exception as err:  # noqa: BLE001
                logs.warn("egress", f"账号 {account.name} 取出口失败: {err}")
                continue
            if not proxy:
                continue      # 直连账号无出口可探
            if proxy not in candidates:
                continue      # 不在候选范围（如已被隔离），不改走直连
            try:
                async with account_client_pool.operation(account, proxy=proxy, purpose="chat") as operation:
                    probe = account_client_pool.begin_request(operation.client)
                    reachable = None
                    extra = {}
                    try:
                        reachable, detail, extra = await _probe_one(account, operation.proxy)
                    finally:
                        account_client_pool.end_request(
                            operation.client, probe, success=reachable,
                            is_waf_block=_is_waf_block(
                                int(extra.get("status") or 0), str(extra.get("code") or "")))
            except RegionOfflineError:
                continue
            results[proxy] = {"reachable": reachable, "detail": detail,
                              "account": account.name, "account_id": account.id, **extra}
            if reachable:
                confirmed.add((account.id, proxy))

        # 本轮结果整体替换：探针每次覆盖全部候选，集合反映的是「最近一次事实」，
        # 而不是历史并集（并集会让已失效的组合永远留在白名单里）。
        self._confirmed = confirmed
        logs.ok("egress", f"出口探测完成：{len(confirmed)}/{len(results)} 组合可服务")
        self._last = {"at": time.time(), "results": results}
        self._persist(results)
        return results

    def _persist(self, results: dict) -> None:
        """写 DB meta（供管理页读取；失败不影响探测本身）。"""
        try:
            from .store import store

            store.set_setting("egress_probe", json.dumps(
                {"at": time.time(), "results": results}, ensure_ascii=False))
        except Exception as err:  # noqa: BLE001
            logs.warn("egress", f"探测结果落库失败: {err}")

    def last_result(self) -> dict:
        return self._last


egress_probe = EgressProbe()
