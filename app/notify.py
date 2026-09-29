"""Bark 消息推送客户端。

支持官方服务 (https://api.day.app) 与自建 Bark 服务器，
统一采用 POST JSON 规范投递，支持 URL/Key 归一化解析与前置校验防封禁。
"""

from __future__ import annotations

import asyncio
import re
import urllib.parse

import httpx

from . import logs

_VALID_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-]{10,128}$")
# 后台异步推送任务强引用集合，防止事件循环弱引用 GC 导致中途丢单
_notify_tasks: set[asyncio.Task] = set()


def normalize_bark_config(raw_input: str, default_server: str = "https://api.day.app") -> tuple[str, str]:
    """归一化 Bark 配置，将各种输入形式拆解为 (server_url, device_key)。

    支持输入形式：
    1. 裸 Key：'abcd1234efgh'
    2. 官方全链接：'https://api.day.app/abcd1234efgh/'
    3. 自建 Bark 链接：'https://bark.myhost.com/abcd1234efgh'
    4. 带查询参数的链接：'https://bark.myhost.com/push?device_key=abcd1234efgh'
    """
    raw = (raw_input or "").strip()
    default_server = (default_server or "https://api.day.app").strip().rstrip("/")
    if not raw:
        return default_server, ""

    if "://" in raw:
        try:
            parsed = urllib.parse.urlparse(raw)
            # 1. 优先从 query 查询参数提取 device_key
            query_params = urllib.parse.parse_qs(parsed.query)
            if "device_key" in query_params and query_params["device_key"]:
                key = query_params["device_key"][0].strip()
                path = re.sub(r"/push/?$", "", parsed.path, flags=re.IGNORECASE).rstrip("/")
                base = f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")
                return base or default_server, key

            # 2. 从 path 分段提取
            path_segments = [seg.strip() for seg in parsed.path.strip("/").split("/") if seg.strip()]
            if not path_segments:
                return f"{parsed.scheme}://{parsed.netloc}".rstrip("/"), ""

            # 过滤掉常见路径末端关键词如 push
            if path_segments[-1].lower() == "push" and len(path_segments) > 1:
                key = path_segments[-2]
                prefix = "/" + "/".join(path_segments[:-2]) if len(path_segments) > 2 else ""
            else:
                key = path_segments[-1]
                prefix = "/" + "/".join(path_segments[:-1]) if len(path_segments) > 1 else ""

            server_url = f"{parsed.scheme}://{parsed.netloc}{prefix}".rstrip("/")
            return server_url, key
        except Exception:
            return default_server, raw

    return default_server, raw


def is_valid_bark_key(device_key: str) -> bool:
    """前置格式校验，防止空 Key 或非法字符串触发 Bark 官方 400/404 频控导致 IP 被封。"""
    key = (device_key or "").strip()
    return bool(_VALID_KEY_RE.match(key))


async def send_bark_notification(
    device_key: str,
    server_url: str = "https://api.day.app",
    title: str = "",
    body: str = "",
    group: str = "ZCode活动",
    url: str | None = None,
    sound: str = "minuet",
    is_archive: int = 1,
    timeout: float = 12.0,
    retries: int = 2,
) -> tuple[bool, str]:
    """通过 POST JSON 方式向 Bark 服务端投递通知（内置瞬态网络退避重试）。

    全量异常捕获，非阻塞超时熔断，绝不向外冒泡异常影响主进程。
    针对跨洋链路连接 Apple APNs 偶发抖动，仅对超时/网络错误/5xx 退避重试，
    对 4xx 业务拒绝立即终止不重试（防触发 Bark 官方恶意请求 IP 封禁）。
    返回: (ok: bool, message: str)
    """
    key = (device_key or "").strip()
    if not is_valid_bark_key(key):
        return False, "Bark 设备 Key 格式非法或未配置（须为 10~128 位字符）"

    server = (server_url or "https://api.day.app").strip().rstrip("/")
    endpoint = f"{server}/push"

    payload: dict = {
        "device_key": key,
        "title": title or "ZCode 监控通知",
        "body": body or "",
        "group": group,
        "sound": sound,
        "isArchive": is_archive,
    }
    if url and url.strip():
        payload["url"] = url.strip()

    max_attempts = max(1, retries + 1)
    last_msg = "推送失败"

    for attempt in range(1, max_attempts + 1):
        resp = None
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(endpoint, json=payload)
        except httpx.TimeoutException:
            last_msg = f"推送请求超时（>{timeout}s）"
            logs.warn("notify", f"Bark 投递超时 ({attempt}/{max_attempts}): {endpoint}")
        except httpx.HTTPError as err:
            last_msg = f"推送网络错误: {err}"
            logs.warn("notify", f"Bark 网络异常 ({attempt}/{max_attempts}): {err}")
        except Exception as err:
            last_msg = f"推送未知异常: {err}"
            logs.err("notify", f"Bark 投递发生意外: {err}")
            return False, last_msg

        if resp is not None:
            if resp.status_code == 200:
                try:
                    res_json = resp.json()
                    code = res_json.get("code")
                    if code == 200 or str(code) == "200":
                        return True, "推送成功"
                    msg = res_json.get("message") or f"未知业务码 {code}"
                    return False, f"Bark 业务失败: {msg}"
                except Exception:
                    # 兼容非标准 JSON 返回但 HTTP 200 的情况
                    return True, "推送成功"

            last_msg = f"Bark 服务端返回 HTTP {resp.status_code}: {resp.text[:100]}"
            logs.warn("notify", last_msg)
            # 4xx 客户端/凭证错误不重试，防止高频失败封禁 IP
            if 400 <= resp.status_code < 500:
                return False, last_msg

        if attempt < max_attempts:
            await asyncio.sleep(1.0 * attempt)

    return False, last_msg


async def notify_claim_outcomes(outcomes: list[dict], source: str = "活动领取") -> tuple[bool, str]:
    """归一化套餐领取成功 Bark 战报推送。

    仅提取实际新领取成功的条目（ok=True 且非 skipped），若无成功项或未配置 Bark Key 则静默跳过。
    """
    from .store import store

    ok_items = [
        o for o in (outcomes or [])
        if isinstance(o, dict) and o.get("ok") and not o.get("skipped")
    ]
    if not ok_items:
        return False, "no_new_claims"

    bark_key = store.bark_device_key()
    if not bark_key:
        return False, "bark_not_configured"
    bark_server = store.bark_server_url()

    lines: list[str] = []
    for item in ok_items[:6]:
        acc_name = item.get("account_name") or item.get("name") or item.get("account_id") or "账号"
        plan_name = item.get("plan_name") or item.get("plan_id") or "活动套餐"
        grants = item.get("grants") or []
        grants_desc = "、".join(
            f"{g.get('name')} {int(g.get('units', 0)):,}"
            for g in grants if isinstance(g, dict) and g.get("name")
        )
        line = f"· {acc_name}: {plan_name}" + (f" ({grants_desc})" if grants_desc else "")
        lines.append(line)

    if len(ok_items) > 6:
        lines.append(f"· 其余 {len(ok_items) - 6} 项已领取完毕")

    title = f"🎁 ZCode {source}成功 ({len(ok_items)}项)"
    body = "\n".join(lines)
    ok, msg = await send_bark_notification(
        device_key=bark_key,
        server_url=bark_server,
        title=title,
        body=body,
        group="ZCode活动",
    )
    if ok:
        logs.ok("notify", f"{source}通知已送达 Bark ({len(ok_items)}项)")
    else:
        logs.warn("notify", f"{source} Bark 推送失败: {msg}")
    return ok, msg


def schedule_claim_notification(outcomes: list[dict], source: str = "活动领取") -> None:
    """后台非阻塞调度领取成功 Bark 通知，不阻塞 Web 接口响应。"""
    ok_items = [
        o for o in (outcomes or [])
        if isinstance(o, dict) and o.get("ok") and not o.get("skipped")
    ]
    if not ok_items:
        return

    async def _run() -> None:
        try:
            await notify_claim_outcomes(ok_items, source=source)
        except Exception as err:  # noqa: BLE001 - 后台推送异常自兜
            logs.warn("notify", f"后台 Bark 推送任务异常: {err}")

    task = asyncio.create_task(_run())
    _notify_tasks.add(task)
    task.add_done_callback(_notify_tasks.discard)

