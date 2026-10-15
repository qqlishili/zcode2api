"""额度旁路对照：定期读插件的 usage，只落库供比对，不参与任何决策。

为什么是「旁路对照」而不是「替换」：
    现有 quota.py 的额度判定（套餐识别、桶剩余量、耗尽判定、时间与状态文案归一）
    是逐步调出来的既有资产。插件的 usage 返回的是 magpie 卡片格式，语义与我们的
    口径不完全一致（例如它把普通与 -Trial 消耗计入同一个窗口）。直接替换等于
    把已调好的口径换成未经比对的口径。

    所以先并行读、只比对：确认两边数字长期一致后，再考虑替换。本模块**不改**
    quota.py 的任何判定路径。

节奏：复用 store.quota_refresh_interval()（与后台额度轮询同一间隔），不新增配置项。
    失败隔离：任何异常只记 warn，绝不影响账号状态、选号或请求路径。
"""

from __future__ import annotations

import asyncio
import json
import random
import time

from . import logs, settings

_START_DELAY = 8.0   # 启动后稍等，避开与其他启动任务争抢宿主
STOP_GRACE_SECONDS = 5.0


class UsageShadow:
    """把插件读到的额度原样存进 DB meta，供比对与观测。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动后台对照循环（main.py lifespan 调用）。

        仅在 plugin 通道启用时才有意义 —— native 模式下宿主没起来，
        每次调用都会失败并刷 warn 日志。故此处直接不启动。
        """
        if getattr(settings, "REQUEST_CHANNEL", "native") != "plugin":
            return
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=STOP_GRACE_SECONDS)
        except (TimeoutError, Exception):  # noqa: BLE001
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _loop(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=_START_DELAY)
            return
        except TimeoutError:
            pass

        while not self._stop.is_set():
            interval = _refresh_interval()
            if interval > 0:
                try:
                    await self.run_once()
                except Exception as err:  # noqa: BLE001 - 后台任务需吞掉异常继续
                    logs.warn("usage", f"额度对照出错: {err}")
            # 与 quota 后台轮询同节奏：interval<=0 时也周期回看设置，便于随时启用
            wait = interval * random.uniform(0.9, 1.1) if interval > 0 else 30.0
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except TimeoutError:
                continue

    # ── 单轮 ────────────────────────────────────────────────────────────────

    async def run_once(self) -> dict:
        """对每个可用于 Plan 通道的账号读一次 usage，原样落库。"""
        from .channels.plugin_channel import plugin_host
        from .store import store

        if not plugin_host.is_alive():
            return {}

        accounts = [
            a for a in store.list_accounts("zai")
            if a.enabled and a.mode == "jwt" and (a.jwt_token or "").strip()
        ]
        if not accounts:
            return {}

        snapshot: dict[str, dict] = {}
        for account in accounts:
            try:
                data = await plugin_host.usage(account)
            except Exception as err:  # noqa: BLE001 - 单个账号失败不影响其他与主流程
                snapshot[account.name] = {"error": str(err)[:200]}
                continue
            # 原样保存，不做格式转换 —— 转换会引入两边口径不一致的风险
            snapshot[account.name] = data

        payload = {"at": time.time(), "accounts": snapshot}
        try:
            store.set_setting("plugin_usage_shadow", json.dumps(payload, ensure_ascii=False))
        except Exception as err:  # noqa: BLE001
            logs.warn("usage", f"对照结果落库失败: {err}")

        # 判据：只有在调用抛异常（值为 {"error": ...} 且非空）或宿主返回非空 error 时才算失败。
        # 正常返回里 error 是空字符串，不能按「键是否存在」判断（曾因此把 4 个成功账号记成失败）。
        def _ok(v: dict) -> bool:
            if not isinstance(v, dict):
                return False
            err = v.get("error")
            return not (isinstance(err, str) and err.strip())

        ok = sum(1 for v in snapshot.values() if _ok(v))
        logs.ok("usage", f"额度对照完成：{ok}/{len(snapshot)} 账号读取成功")
        return snapshot

    @staticmethod
    def snapshot() -> dict:
        """读回最近一次对照结果（管理页/诊断用）。"""
        from .store import store

        raw = store.get_setting("plugin_usage_shadow")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}


def available_models() -> list[str]:
    """当前可服务的模型，返回官方写法（如 GLM-5.3-Flash）。

    定义（第一性原理）：可用模型 = **所有启用且 JWT 有效的账号**在 usage 里报出的
    windows[].models 的并集。取并集而不是取某一个账号，是因为实测同一 plan 下
    不同账号的桶不同（2026-10-15：同为 ZCode Global Build，一个账号只有
    GLM-5.3-Flash 桶，另一个还有 GLM-5.3 桶）。

    两处归一：
      1. **剥离 `-Trial`** —— 它是同一桶的另一个名字而非独立模型
         （magpie-plugins 的 giftID 注释：「the picker reads GLM-5.3-Trial while
         the loader strips it back to the raw name」）。
      2. **转成官方写法** —— usage 报的是小写（glm-5.3-flash），客户端认的是
         官方名（GLM-5.3-Flash，实测 evidence 里 108 次都用后者）。归一交给既有的
         MODEL_NAME_MAP，不另建表。

    读数缺失时回退 constants.AVAILABLE_MODELS：/v1/models 是客户端启动握手用的
    接口，不能因为后台旁路还没跑完第一轮就返回空列表把客户端指纹成"无可用模型"。
    """
    from . import constants

    data = UsageShadow.snapshot()
    accounts = data.get("accounts") or {}

    names: list[str] = []
    read_failed = 0
    for usage in accounts.values():
        if not isinstance(usage, dict):
            read_failed += 1
            continue
        err = usage.get("error")
        if isinstance(err, str) and err.strip():
            read_failed += 1              # 该账号读失败，不贡献模型
            continue
        for window in usage.get("windows") or []:
            if not isinstance(window, dict):
                continue
            # 必须是 list：字符串同样可迭代，写成 "glm-5.3" 会被逐字符当成模型名
            models = window.get("models")
            if not isinstance(models, list):
                continue
            for model in models:
                if not isinstance(model, str) or not model.strip():
                    continue
                if model.lower().endswith("-trial"):
                    continue              # 同桶别名，不是独立模型
                names.append(model.strip())

    if not names:
        # 两种"空"必须区分开，否则会掩盖真实故障：
        #   (a) 旁路还没跑出第一轮（accounts 为空）→ 正常冷启动，静默回退
        #   (b) 跑了但所有账号都读失败（read_failed > 0）→ 宿主或上游出问题了，
        #       必须留下痕迹，而不是假装"可用模型就是那两个硬编码"
        if read_failed:
            logs.warn("usage", f"{read_failed} 个账号的额度读取全部失败，"
                               f"/v1/models 回退硬编码列表（请检查插件宿主与上游）")
        return list(constants.AVAILABLE_MODELS)

    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        official = constants.MODEL_NAME_MAP.get(name.lower(), name)
        key = official.lower()
        if key not in seen:
            seen.add(key)
            out.append(official)
    return out


def _refresh_interval() -> int:
    """与后台额度轮询同一节奏（实时读取设置，改后即生效）。"""
    try:
        from .store import store

        return int(store.quota_refresh_interval())
    except Exception:  # noqa: BLE001
        return int(getattr(settings, "QUOTA_REFRESH_INTERVAL", 60))


usage_shadow = UsageShadow()
