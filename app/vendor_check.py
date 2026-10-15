"""vendor 层版本监控：定期比对上游是否有新版本，有则告警，**不自动应用**。

监控两样东西（见 openspec design.md §4）：

  `magpie-plugins`（插件）—— 上游仓库有新提交？
  `magpie_host.js`     —— 上游同路径文件的 sha256 变了？

**为什么不自动更新**：
    vendor 层是**服务能否向上游发请求的前提**。插件一旦引入回归，网关会整体不可用；
    host.js 更是协议规格，格式变化会直接打断 RPC。而"更新失败自动回滚"本身也要重启
    宿主 —— 在自动化的名义下把风险放大。故本模块**只报告**，应用由人工触发
    （管理页按钮 → `apply_plugin_update()`）。

检查方式用 `git ls-remote`（只问远端 HEAD，不 fetch、不改工作区），因此可以在线执行
而不影响仓库状态；host.js 的 sha256 需要拉取单文件内容。
"""

from __future__ import annotations

import asyncio
import json
import random
import subprocess
import time

from . import logs, settings

_START_DELAY = 20.0     # 启动后稍等，避开启动风暴
STOP_GRACE_SECONDS = 5.0
_GIT_TIMEOUT = 30.0


def _run_git(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    """同步执行 git（在 executor 里跑，避免阻塞事件循环）。"""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


async def _git(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_git, args, cwd)


class VendorCheck:
    """vendor 层版本检查（与 captcha / sentinel 的后台循环同模式）。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._last: dict = {}

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if settings.VENDOR_CHECK_INTERVAL <= 0:
            logs.ok("vendor", "vendor 版本检查已关闭（VENDOR_CHECK_INTERVAL=0）")
            return
        if getattr(settings, "REQUEST_CHANNEL", "native") != "plugin":
            # native 模式不引入 vendor 层，检查它没有意义
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
            try:
                await self.run_once()
            except Exception as err:  # noqa: BLE001 - 后台任务需吞掉异常继续
                logs.warn("vendor", f"版本检查出错: {err}")
            # ±10% 抖动，避免固定机器时钟特征
            wait = settings.VENDOR_CHECK_INTERVAL * random.uniform(0.9, 1.1)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except TimeoutError:
                continue

    # ── 检查 ───────────────────────────────────────────────────────────────

    async def run_once(self) -> dict:
        """跑一轮检查。返回本次结果（同时落 DB meta）。"""
        plugin = await self.check_plugin()
        hostjs = await self.check_hostjs()
        result = {"at": time.time(), "plugin": plugin, "hostjs": hostjs}
        self._last = result
        self._persist(result)

        changed = [k for k, v in (("插件", plugin), ("host.js", hostjs)) if v.get("update_available")]
        if changed:
            await self._alert(changed, plugin, hostjs)
            logs.warn("vendor", f"检测到上游更新: {', '.join(changed)}（未自动应用）")
        return result

    async def check_plugin(self) -> dict:
        """插件 submodule 是否有新提交。用 ls-remote 问远端 HEAD，不动工作区。"""
        sub = str(settings.VENDOR_PLUGIN_DIR)
        code, cur, err = await _git(["rev-parse", "HEAD"], cwd=sub)
        if code != 0:
            return {"ok": False, "error": f"读取本地 commit 失败: {err[:200]}"}

        root = str(settings.ROOT_DIR)
        code, url, err = await _git(
            ["config", "--file", ".gitmodules", "--get",
             "submodule.vendor/magpie-plugins.url"],
            cwd=root,
        )
        if code != 0 or not url:
            return {"ok": False, "error": f"读取 submodule URL 失败: {err[:200]}"}

        code, out, err = await _git(["ls-remote", url, "HEAD"])
        if code != 0:
            return {"ok": False, "error": f"ls-remote 失败: {err[:200]}"}
        remote = out.split()[0] if out else ""
        return {
            "ok": True,
            "current": cur,
            "remote": remote,
            "update_available": bool(remote and remote != cur),
            "url": url,
        }

    async def check_hostjs(self) -> dict:
        """host.js 与上游是否一致（比对 sha256）。"""
        from .channels.plugin_channel import host_source_sha256

        local = host_source_sha256()
        if not local:
            return {"ok": False, "error": f"读取本地 {settings.PLUGIN_HOST_JS} 失败"}

        remote = await self._fetch_remote_sha256()
        if remote is None:
            return {"ok": False, "local": local, "error": "拉取上游 host.js 失败"}
        return {
            "ok": True,
            "local": local,
            "remote": remote,
            "update_available": remote != local,
        }

    async def _fetch_remote_sha256(self) -> str | None:
        """从上游仓库拉 host.js 并算 sha256（只读，不落盘）。"""
        import hashlib

        import httpx

        url = settings.VENDOR_HOSTJS_UPSTREAM_URL
        if not url:
            return None
        try:
            async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                return hashlib.sha256(resp.content).hexdigest()
        except Exception:  # noqa: BLE001
            return None

    # ── 应用（人工触发）────────────────────────────────────────────────────

    async def apply_plugin_update(self) -> dict:
        """把插件 submodule 更新到远端 HEAD 并重启宿主。**只应由人工调用。**

        顺序：update → 重启宿主 → 健康检查；重启失败则回滚 submodule 再重启一次。
        """
        from .channels.base import start_plugin_host, stop_plugin_host

        sub = str(settings.VENDOR_PLUGIN_DIR)
        code, before, _ = await _git(["rev-parse", "HEAD"], cwd=sub)
        if code != 0:
            return {"ok": False, "error": "读取当前 commit 失败"}

        code, _out, err = await _git(["fetch", "--depth", "1", "origin", "HEAD"], cwd=sub)
        if code != 0:
            return {"ok": False, "error": f"fetch 失败: {err[:200]}"}
        code, _out, err = await _git(["reset", "--hard", "FETCH_HEAD"], cwd=sub)
        if code != 0:
            return {"ok": False, "error": f"reset 失败: {err[:200]}"}
        code, after, _ = await _git(["rev-parse", "HEAD"], cwd=sub)

        await stop_plugin_host()
        started = await start_plugin_host()
        if started is not None:
            logs.ok("vendor", f"插件已更新 {before[:8]} → {after[:8]} 并重启宿主")
            return {"ok": True, "before": before, "after": after}

        logs.warn("vendor", f"更新后宿主启动失败，回滚到 {before[:8]}")
        rollback_code, _, rollback_err = await _git(["reset", "--hard", before], cwd=sub)
        if rollback_code != 0:
            return {"ok": False, "error": f"回滚失败: {rollback_err[:200]}", "before": before}
        await stop_plugin_host()
        await start_plugin_host()
        return {"ok": False, "error": "更新后宿主启动失败，已回滚", "before": before, "after": after}

    # ── 落库与告警 ─────────────────────────────────────────────────────────

    def _persist(self, result: dict) -> None:
        try:
            from .store import store

            store.set_setting("vendor_status", json.dumps(result, ensure_ascii=False))
        except Exception as err:  # noqa: BLE001
            logs.warn("vendor", f"版本检查结果落库失败: {err}")

    async def _alert(self, changed: list[str], plugin: dict, hostjs: dict) -> None:
        try:
            from .notify import send_bark_notification
            from .store import store

            key = store.bark_device_key()
            if not key:
                return
            lines: list[str] = []
            if "插件" in changed:
                lines.append(f"插件: {plugin.get('current', '')[:8]} → {plugin.get('remote', '')[:8]}")
            if "host.js" in changed:
                lines.append(f"host.js: {hostjs.get('local', '')[:8]} → {hostjs.get('remote', '')[:8]}")
            await send_bark_notification(
                device_key=key,
                server_url=store.bark_server_url(),
                title="zcode2api · 上游有更新",
                body="\n".join(lines) + "\n（未自动应用，请人工确认）",
                group="ZCode运维",
            )
        except Exception as err:  # noqa: BLE001
            logs.warn("vendor", f"更新告警发送失败: {err}")

    def last_result(self) -> dict:
        return self._last

    @staticmethod
    def snapshot() -> dict:
        """读回最近一次检查结果（管理页/诊断用）。"""
        from .store import store

        raw = store.get_setting("vendor_status")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}


vendor_check = VendorCheck()
