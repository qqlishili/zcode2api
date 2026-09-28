"""Bark 消息推送客户端。

支持官方服务 (https://api.day.app) 与自建 Bark 服务器，
统一采用 POST JSON 规范投递，支持 URL/Key 归一化解析与前置校验防封禁。
"""

from __future__ import annotations

import re
import urllib.parse
import httpx

from . import logs

_VALID_KEY_RE = re.compile(r"^[a-zA-Z0-9_\-]{10,128}$")


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
    timeout: float = 8.0,
) -> tuple[bool, str]:
    """通过 POST JSON 方式向 Bark 服务端投递通知。

    全量异常捕获，非阻塞超时熔断，绝不向外冒泡异常影响主进程。
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

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload)
    except httpx.TimeoutException:
        msg = f"推送请求超时（>{timeout}s）"
        logs.warn("notify", f"Bark 投递超时: {endpoint}")
        return False, msg
    except httpx.HTTPError as err:
        msg = f"推送网络错误: {err}"
        logs.warn("notify", f"Bark 网络异常: {err}")
        return False, msg
    except Exception as err:
        msg = f"推送未知异常: {err}"
        logs.err("notify", f"Bark 投递发生意外: {err}")
        return False, msg

    if resp.status_code != 200:
        msg = f"Bark 服务端返回 HTTP {resp.status_code}: {resp.text[:100]}"
        logs.warn("notify", msg)
        return False, msg

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
