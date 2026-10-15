"""plugin 通道：经官方插件宿主（host.js）组装并发起上游请求。

为什么需要它：上游 zcode.z.ai 只服务「形态与 ZCode 客户端一致」的请求，手写请求一律
405/3012（见 docs/adr/0001-vendor-official-zcode-plugin.md）。请求形态的权威来源是
magpie-plugins 的 packages/zcode，而它的官方调用点是宿主 host.js。

本模块是 host.js 的 Python 客户端：以 stdin/stdout 的 JSON-Lines 协议对话。
协议（源自 host.js 头部注释，实测一致）：

    magpie → host: {id, method, params}
    host → magpie: {id, result} | {id, error}
    fetch 流式:    {id, event: "head", status, headers}
                   {id, event: "chunk", data(base64)}
                   {id, result: null}
    abort:         {method: "abort", params: {id}}
    credit:        {method: "credit", params: {id, n}}
    host → magpie, 无 id: {event: "log"|"toast"|"auth", ...}

只依赖三个 RPC 方法：init / fetch / usage。其余方法不调用，未知 event 宽松忽略。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from typing import Any

import httpx

from .. import logs, settings
from ..models import Account

# 单次 fetch 的字节窗口：远大于单块体积，避免长响应因窗口渐耗而停滞。
# 见 openspec 的 design.md §2.3：本实现只按 payload 回补 credit，不含帧开销。
_FETCH_WINDOW = 8 << 20


def slot_of(account_id: str) -> str:
    """账号 id → 插件槽位名（纯函数，不存映射表）。

    为什么不直接用 account.id：id 形态不统一（UUID / 名字-随机 hex / 含中文）且长度不定。
    短哈希定长、纯 ASCII、可随时重算，增删账号不影响他人。
    为什么不用序号：账号增删会导致序号重排，路由到错误账号。
    """
    digest = hashlib.sha1(account_id.encode("utf-8")).hexdigest()[:10]
    return f"zcode#h{digest}"


def slot_label(account: Account) -> str:
    """日志用标签：槽位 + 账号名。

    槽位是哈希、不体现账号名，因此凡在日志/异常中提及槽位必须同时写出账号名
    （见 CONTEXT.md「账号槽位」与 spec REQ-DUAL-10）。
    """
    return f"{slot_of(account.id)}({account.name})"


def host_source_sha256() -> str:
    """vendor 层 host.js 的 sha256（**不含来源标注**，供上游比对）。

    文件头是我们加的来源标注，比对时必须剥掉，否则永远"有差异"。
    """
    import hashlib
    import pathlib

    path = pathlib.Path(settings.PLUGIN_HOST_JS)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    # 标注以 "// vendored from" 开头，与正文之间隔一个空行
    marker = "// vendored from"
    if text.startswith(marker):
        parts = text.split("\n\n", 1)
        text = parts[1] if len(parts) > 1 else text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PluginHostError(Exception):
    """宿主交互失败（启动失败、RPC 超时、协议错）。"""


class PluginHost:
    """bun vendor/magpie_host.js 的常驻客户端。

    生命周期（见 spec: plugin-channel-runtime）：
      start() → 常驻；is_alive() 每请求探测；aclose() 优雅关闭。
    账号凭证变化时由调用方 restart()（实测：改 auth.json 不重启不生效）。
    """

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: dict[int, list[dict]] = {}
        self._event_ready: dict[int, asyncio.Event] = {}
        self._auth_path: str = ""
        self._ready = False
        self._failures = 0
        self._cooldown_until = 0.0
        self._alerted = False     # 是否已为本次冷却发过告警

    # ── 生命周期 ────────────────────────────────────────────────────────────

    async def start(self, auth_path: str, timeout: float = 5.0) -> dict:
        """启动宿主并完成 init。失败时抛 PluginHostError。"""
        if self._proc is not None and self._proc.returncode is None:
            await self.aclose()

        # Path 与 str 混用时 json.dumps 会抛 TypeError，这里统一成 str 出口
        bun = str(settings.BUN_PATH)
        host_js = str(settings.PLUGIN_HOST_JS)
        plugin_dir = str(settings.PLUGIN_DIR)
        auth_path = str(auth_path)
        if not os.path.exists(bun):
            raise PluginHostError(f"Bun 不可执行: {bun}")
        if not os.path.exists(host_js):
            raise PluginHostError(f"宿主脚本不存在: {host_js}")

        self._auth_path = auth_path
        work = os.path.dirname(auth_path) or "."
        self._proc = await asyncio.create_subprocess_exec(
            bun, host_js,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # 继承：宿主诊断直接进服务日志
            cwd=work,
        )
        self._reader_task = asyncio.create_task(self._read_loop())

        try:
            result = await asyncio.wait_for(
                self._rpc("init", {
                    "authPath": auth_path,
                    "modelsDevPath": "",
                    "piPath": "",
                    "directory": work,
                    "config": {},
                    "prefer": {},
                    "options": {},
                    "plugins": [{
                        "spec": "@magpie-community/opencode-zcode-auth",
                        "target": plugin_dir,
                    }],
                }),
                timeout=timeout,
            )
        except TimeoutError as err:
            await self.aclose()
            raise PluginHostError(f"init 超时（{timeout}s）") from err
        except Exception as err:  # noqa: BLE001
            await self.aclose()
            raise PluginHostError(f"init 失败: {err}") from err

        plugins = (result or {}).get("plugins") or []
        bad = [p for p in plugins if p.get("error")]
        if bad:
            logs.warn("plugin", f"插件加载报错: {json.dumps(bad, ensure_ascii=False)[:200]}")
        provides = [x for p in plugins for x in (p.get("provides") or [])]
        if "zcode" not in provides:
            await self.aclose()
            raise PluginHostError(f"插件未提供 zcode（provides={provides}）")

        self._ready = True
        self._failures = 0
        return result

    async def restart(self) -> dict:
        """账号凭证变化后重启（实测改 auth.json 不重启不生效）。"""
        return await self.start(self._auth_path)

    def is_alive(self) -> bool:
        """常驻进程是否仍存活。每请求调用，零额外成本。"""
        return (
            self._ready
            and self._proc is not None
            and self._proc.returncode is None
            and asyncio.get_event_loop().time() >= self._cooldown_until
        )

    async def drain(self, timeout: float) -> bool:
        """等在飞 fetch 结束（优雅退休）。

        为什么需要：重启宿主会掐断正在进行的流式响应。Go 侧为滚动替换留了
        `retireWait = 10 分钟`；我们的重启由**人工操作**触发（管理页改凭证/更新 vendor），
        频率低且不并发，故用更短的窗口即可 —— 由 ZCODE_PLUGIN_DRAIN_TIMEOUT 决定。

        返回 True 表示归零（可以安全重启），False 表示超时。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if not self._pending:
                return True
            await asyncio.sleep(0.2)
        return not self._pending

    async def aclose(self, *, drain_timeout: float | None = None) -> None:
        """优雅关闭：等在飞请求（可选）→ 关 stdin → 等退出 → 超时强杀。"""
        if drain_timeout and drain_timeout > 0 and self._pending:
            if not await self.drain(drain_timeout):
                logs.warn("plugin", f"宿主有 {len(self._pending)} 个在飞请求未结束"
                                    f"（等待 {drain_timeout}s 超时），强制关闭")
        self._ready = False
        proc, self._proc = self._proc, None
        task, self._reader_task = self._reader_task, None

        if proc is not None and proc.returncode is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()

        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(PluginHostError("宿主已关闭"))
        self._pending.clear()
        self._events.clear()
        self._event_ready.clear()

    # ── 请求 ───────────────────────────────────────────────────────────────

    async def fetch(
        self,
        *,
        account: Account,
        model: str,
        url: str,
        method: str,
        headers: dict[str, str],
        payload: bytes,
        proxy: str | None,
    ) -> PluginResponseContext:
        """发起一次 fetch，返回未进入的响应上下文（与 native 通道语义一致）。"""
        if not self.is_alive():
            raise PluginHostError("宿主不可用")

        fid = self._next_id()
        self._events[fid] = []
        self._event_ready[fid] = asyncio.Event()
        self._pending[fid] = asyncio.get_running_loop().create_future()

        params = {
            "provider": "zcode",
            "account": slot_of(account.id),
            "model": model,
            "npm": "@ai-sdk/anthropic",
            "url": url,
            "method": method,
            # headers 值必须是字符串（宿主侧 H.set(k, v) 只接受 string）
            "headers": {str(k): str(v) for k, v in (headers or {}).items()},
            # body 必须 base64 字符串：宿主用 bodyOf(b64) → Buffer.from(b64,"base64")
            # 解码（Go 侧的 []byte 同样按 base64 走 JSON）。传裸 bytes 会直接抛
            # "Object of type bytes is not JSON serializable"。
            "body": base64.b64encode(payload).decode("ascii"),
            "window": _FETCH_WINDOW,
            # 直连必须显式传 "direct"：省略会让宿主去读自己的环境变量
            "proxy": proxy or "direct",
        }
        await self._write({"id": fid, "method": "fetch", "params": params})
        return PluginResponseContext(self, fid)

    async def usage(self, account: Account) -> dict:
        """读取额度（旁路对照用；不参与任何决策）。"""
        return await self._rpc("usage", {"provider": "zcode", "account": slot_of(account.id)})

    async def abort(self, fetch_id: int) -> None:
        try:
            await self._write({"id": self._next_id(), "method": "abort",
                               "params": {"id": fetch_id}})
        except Exception:  # noqa: BLE001
            pass

    async def credit(self, fetch_id: int, n: int) -> None:
        """归还读取走的字节，让上游继续推送。

        已知偏差：只归还 payload 字节，不含帧开销（官方计费含此部分）。
        window 设 8 MB 以吸收该偏差；若实测长响应停滞，改为按帧精确归还。
        """
        if n <= 0:
            return
        try:
            await self._write({"id": self._next_id(), "method": "credit",
                               "params": {"id": fetch_id, "n": n}})
        except Exception:  # noqa: BLE001
            pass

    async def iter_chunks(self, fetch_id: int) -> Any:
        """按到达顺序产出已解码的字节块；结果到达即结束。"""
        idx = 0
        while True:
            ready = self._event_ready.get(fetch_id)
            if ready is None:
                return
            await ready.wait()
            ready.clear()

            events = self._events.get(fetch_id) or []
            while idx < len(events):
                ev = events[idx]
                idx += 1
                if ev.get("event") == "chunk":
                    data = ev.get("data") or ""
                    if data:
                        yield base64.b64decode(data)
                elif ev.get("event") == "head":
                    continue
            fut = self._pending.get(fetch_id)
            if fut is not None and fut.done():
                # 结果已到且 chunk 已排空 → 结束
                events = self._events.get(fetch_id) or []
                if idx >= len(events) or all(
                    e.get("event") == "head" for e in events[idx:]
                ):
                    return

    def head_of(self, fetch_id: int) -> dict | None:
        """取 head 事件（status + headers）。"""
        for ev in self._events.get(fetch_id) or []:
            if ev.get("event") == "head":
                return ev
        return None

    async def wait_head(self, fetch_id: int, timeout: float) -> dict:
        """等 head 到达（含建连与上游首响应）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            head = self.head_of(fetch_id)
            if head is not None:
                return head
            fut = self._pending.get(fetch_id)
            if fut is not None and fut.done():
                exc = fut.exception()
                if exc is not None:
                    raise PluginHostError(str(exc))
                raise PluginHostError("宿主未返回 head")
            await asyncio.sleep(0.01)
        raise PluginHostError(f"等待上游响应超时（{timeout}s）")

    async def wait_result(self, fetch_id: int, timeout: float) -> None:
        """等 fetch 结果帧（流结束）。"""
        fut = self._pending.get(fetch_id)
        if fut is None:
            raise PluginHostError("fetch 已释放")
        try:
            await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except TimeoutError as err:
            raise PluginHostError(f"等待响应结束超时（{timeout}s）") from err

    def release(self, fetch_id: int) -> None:
        self._pending.pop(fetch_id, None)
        self._events.pop(fetch_id, None)
        self._event_ready.pop(fetch_id, None)

    def note_failure(self) -> None:
        """记录一次失败；连续 N 次进入冷却（spec REQ-PLUG-08），并告警一次。

        告警触发点是「**刚进入冷却**」而不是每次失败：进冷却意味着插件通道已不可用、
        请求全部降级到 native（而 native 在上游会被 405 拒），这是需要人知道的
        运维事件，不能只躺在日志里。`_alerted` 保证一次冷却只打扰一次。
        """
        self._failures += 1
        if self._failures >= settings.PLUGIN_RESTART_LIMIT:
            self._cooldown_until = asyncio.get_event_loop().time() + settings.PLUGIN_COOLDOWN_SECONDS
            logs.warn("plugin", f"宿主连续失败 {self._failures} 次，冷却 "
                                f"{settings.PLUGIN_COOLDOWN_SECONDS}s 后重试")
            if not self._alerted:
                self._alerted = True
                # 不在请求路径上 await：用 create_task 投递，失败只记日志
                try:
                    asyncio.get_running_loop().create_task(self._alert_down())
                except RuntimeError:
                    pass      # 无事件循环（如单元测试同步调用）：跳过告警

    def note_success(self) -> None:
        self._failures = 0
        self._alerted = False

    async def _alert_down(self) -> None:
        """宿主不可用告警（Bark），复用 sentinel / vendor_check 的同一通道。"""
        try:
            from ..notify import send_bark_notification
            from ..store import store

            key = store.bark_device_key()
            if not key:
                return
            await send_bark_notification(
                device_key=key,
                server_url=store.bark_server_url(),
                title="zcode2api · 插件通道不可用",
                body=(f"宿主连续失败 {self._failures} 次，已冷却 "
                      f"{settings.PLUGIN_COOLDOWN_SECONDS}s。\n"
                      f"期间请求降级 native（上游很可能返回 405），请检查 Bun 与 vendor/magpie_host.js。"),
                group="ZCode运维",
            )
        except Exception as err:  # noqa: BLE001 - 告警失败不影响降级路径
            logs.warn("plugin", f"宿主不可用告警发送失败: {err}")

    # ── 内部 ───────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    async def _write(self, msg: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.returncode is not None:
            raise PluginHostError("宿主进程不可用")
        line = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        proc.stdin.write(line + b"\n")
        await proc.stdin.drain()

    async def _rpc(self, method: str, params: dict) -> Any:
        rid = self._next_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write({"id": rid, "method": method, "params": params})
        try:
            return await fut
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
        """读宿主 stdout，按 id 路由响应，无 id 的归日志。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "ignore").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logs.warn("plugin", f"宿主输出非 JSON: {line[:160]}")
                    continue

                mid = msg.get("id")
                if mid is None:
                    # host 主动事件：log / toast / auth → 归日志（未知也宽松忽略）
                    kind = msg.get("event")
                    if kind == "log":
                        logs.warn("plugin", f"宿主日志: {str(msg.get('message'))[:200]}")
                    elif kind in ("toast", "auth"):
                        logs.warn("plugin", f"宿主事件 {kind}: {json.dumps(msg, ensure_ascii=False)[:200]}")
                    continue

                if "event" in msg:
                    self._events.setdefault(mid, []).append(msg)
                    ready = self._event_ready.get(mid)
                    if ready is not None:
                        ready.set()
                    continue

                fut = self._pending.get(mid)
                if fut is not None and not fut.done():
                    if msg.get("error"):
                        fut.set_exception(
                            PluginHostError(str(msg["error"].get("message") or msg["error"]))
                        )
                    else:
                        fut.set_result(msg.get("result"))
                ready = self._event_ready.get(mid)
                if ready is not None:
                    ready.set()

            # 进程退出：唤醒所有等待者
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PluginHostError("宿主进程已退出"))
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            logs.warn("plugin", f"宿主读取循环异常: {err}")
        finally:
            # 读循环一旦结束，宿主就是"哑"的：进程可能还在（returncode 仍为 None），
            # 但没有任何响应能被路由回来。必须把 _ready 置 False，让 is_alive() 说真话，
            # 否则网关会把请求继续交给它，每个都等到 PLUGIN_HEAD_TIMEOUT（300s）且永不恢复。
            #
            # 判据用「_ready 当前是否仍为 True」而不是另设关闭标志：aclose() 会先置
            # _ready = False 再取消本任务，所以正常关闭时这里不会误报。
            if self._ready:
                self._ready = False
                logs.warn("plugin", "宿主读取循环已结束，标记宿主不可用（后续请求将降级 native）")


class PluginResponseContext:
    """把宿主的事件流包装成 httpx 的 stream 上下文语义。

    gateway 的使用方式与 native 完全一致：
        cm = await channel.send(...)
        resp = await cm.__aenter__()
        ...
        await cm.__aexit__(None, None, None)

    这里 __aenter__ 返回的是一个真的 httpx.Response，其 stream 是 _PluginStream，
    因此 resp.aread() 与 resp.aiter_bytes() 都可用（spec REQ-DUAL-02）。
    """

    def __init__(self, host: PluginHost, fetch_id: int) -> None:
        self._host = host
        self._fid = fetch_id
        self._response: httpx.Response | None = None

    async def __aenter__(self) -> httpx.Response:
        head = await self._host.wait_head(self._fid, timeout=settings.PLUGIN_HEAD_TIMEOUT)
        status = int(head.get("status") or 0)
        headers = {str(k).lower(): str(v) for k, v in (head.get("headers") or {}).items()}
        self._response = httpx.Response(
            status_code=status,
            headers=headers,
            stream=_PluginStream(self._host, self._fid),
            request=httpx.Request("POST", "https://zcode.z.ai/"),
        )
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        # 流自身的 aclose 已发过 abort（httpx 在读完/关闭时调用），这里只释放本地状态。
        self._host.release(self._fid)
        return False


class _PluginStream(httpx.AsyncByteStream):
    """host.js 的 chunk 事件流 → httpx 可消费的字节流。

    用 httpx 的公开基类 AsyncByteStream，配 Response(stream=...)（httpx 官方用法；
    magpie 的 Go 侧对同一问题也是自定义一个 Reader）。
    """

    def __init__(self, host: PluginHost, fetch_id: int) -> None:
        self._host = host
        self._fid = fetch_id

    async def __aiter__(self) -> Any:
        async for chunk in self._host.iter_chunks(self._fid):
            yield chunk
            await self._host.credit(self._fid, len(chunk))

    async def aclose(self) -> None:
        await self._host.abort(self._fid)


class PluginChannel:
    """plugin 通道：把请求交给插件宿主组装并发出。"""

    name = "plugin"

    def __init__(self, host: PluginHost) -> None:
        self.host = host

    async def send(
        self,
        account: Account,
        url: str,
        headers: dict[str, str],
        payload: bytes,
        client: httpx.AsyncClient | None,
    ) -> PluginResponseContext:
        model = ""
        try:
            model = str(json.loads(payload.decode("utf-8")).get("model") or "")
        except Exception:  # noqa: BLE001
            pass
        if not model:
            raise PluginHostError("请求体缺少 model")

        # 出口门禁（通道无关的公共层给候选，这里决定是否采用）：
        #   EGRESS_MODE=direct → 强制直连（诊断用）
        #   EGRESS_MODE=proxy  → 只走「探针已确认可服务」的端口，未确认则直连兜底
        # 为什么不能直接用绑定端口：断路器只记录「用过之后」的失败，进程内为空时它会
        # 信任所有端口，于是重启后的首批请求会挨个撞上已被拉黑的绑定端口并把账号打成
        # 风控禁用（2026-10-15 实测）。直连是安全的（实测 200），代价只是暴露真实 IP，
        # 故作为「不确定时的兜底」而非默认：有干净代理用代理，没有就直连。
        proxy = None
        if settings.EGRESS_MODE != "direct" and client is not None:
            try:
                from ..client_pool import account_client_pool
                from ..egress_probe import egress_probe

                candidate = account_client_pool.client_proxy(client)
                # 按 (账号, 出口) 组合判定，不是按出口 —— 实测同出口对不同账号
                # 结果不同（2026-10-15：21200 对 5735 是 3012，对 9149 是 200）。
                if egress_probe.is_confirmed(account.id, candidate):
                    proxy = candidate
            except Exception:  # noqa: BLE001
                proxy = None

        return await self.host.fetch(
            account=account, model=model, url=url, method="POST",
            headers=headers, payload=payload, proxy=proxy,
        )

    async def aclose(self) -> None:
        await self.host.aclose()


# 常驻单例（Phase 4 在 lifespan 中启停）
plugin_host = PluginHost()
plugin_channel = PluginChannel(plugin_host)
