"""上游请求构建。

负责根据账号凭证选择端点、组装请求头、应用 body 变换。
实际发送与流式透传在 routes/gateway.py。
"""

from __future__ import annotations

import copy
import json

from . import body_transform, constants, settings
from .identity import build_identity_headers, build_trace_headers
from .models import Account

# 透传客户端 header 时需要剔除的字段
_DROP_HEADERS = {
    "host",
    "content-length",
    "x-api-key",
    "authorization",
    "user-agent",
    "http-referer",
    "referer",
    "origin",
    "cookie",
    "accept",
    "accept-language",
    "accept-encoding",
    "connection",
    "true-client-ip",
    "x-original-forwarded-for",
    # 身份/追踪头由本服务仿真生成，禁止客户端透传覆盖（指纹一致性）
    "x-device-mid",
    "x-request-id",
    "x-zcode-trace-id",
    "x-zcode-session-type",
    "x-query-id",
    "x-session-id",
    "x-title",
    "x-platform",
    "x-release-channel",
    "x-client-language",
    "x-client-timezone",
    "x-os-category",
    "x-os-version",
}

# 前缀剔除：本服务仿真的头族 + 客户端 SDK 特征头族。
# x-stainless-* 是 Anthropic SDK 自动附加的运行环境指纹（lang/runtime/package 版本），
# 值来自真实调用客户端而非官方 ZCode 桌面端 —— 与 ZCode UA 组成矛盾信号，
# 且 zapi（Node fetch 直发、无 stainless 头）长期被上游正常接受，剔除后同为已验证形状。
# x-forwarded-*/forwarded/x-real-ip/via/cf-*/cdn-loop 是反代与 CDN 隧道注入的
# 基础设施头（2026-09 review）：官方客户端永不携带，透传泄露部署拓扑并构成指纹矛盾。
_DROP_HEADER_PREFIXES = ("x-zcode", "x-stainless", "x-forwarded", "forwarded",
                         "x-real-ip", "via", "cf-", "cdn-loop")


def build_request(
    account: Account,
    body: dict,
    verify_param: str | None,
    incoming_headers: dict | None = None,
    verify_region: str | None = None,
    force_fallback: bool = False,
) -> tuple[str, dict, bytes]:
    """返回 (目标 URL, 请求头, 序列化后的请求体)。

    内部先深拷贝 body，避免跨账号换号或切 API Key 回退时污染调用方原字典。
    force_fallback：强制走 API Key 回退通道（Plan 通道瞬态失败如 429 时，
    账号 status 保持 ACTIVE，不能靠状态推导路由，须显式指定）。
    """
    provider = account.provider
    req_body = copy.deepcopy(body) if isinstance(body, dict) else {}

    if provider == "zai":
        if account.uses_plan_channel() and not force_fallback:
            target_url = settings.UPSTREAM["zai"]
            auth = {"Authorization": f"Bearer {account.jwt_token}"}
        elif account.api_key:
            target_url = settings.UPSTREAM["zai_fallback"]
            auth = {"x-api-key": account.api_key}
        else:
            raise RuntimeError("账号缺少有效凭证")
    elif provider == "bigmodel":
        target_url = settings.UPSTREAM["bigmodel"]
        if not account.api_key:
            raise RuntimeError("BigModel 账号缺少 API Key")
        auth = {"x-api-key": account.api_key}
    else:
        raise RuntimeError(f"未知提供商: {provider}")

    if provider == "zai" and account.uses_plan_channel() and not force_fallback:
        # JWT 通道：全量身份头 + 追踪头（对齐官方客户端 pio + trace 头序 + metadata.user_id JSON 契约）
        id_headers = build_identity_headers(account)
        user_id = body_transform.jwt_user_id(account.jwt_token)
        model = req_body.get("model") if isinstance(req_body.get("model"), str) else None
        req_body = body_transform.transform_body(
            req_body,
            user_id=user_id,
            model=model,
            device_mid=id_headers.get("X-Device-Mid"),
        )
        headers = {
            "content-type": "application/json",
            **auth,
            "anthropic-version": constants.ANTHROPIC_VERSION,
            **id_headers,
            **build_trace_headers(),
        }
    else:
        # API Key 通道（回退 / bigmodel）：清理内部过渡字段 session_id，保持原有最小头集
        if isinstance(req_body.get("metadata"), dict) and "session_id" in req_body["metadata"]:
            req_body["metadata"].pop("session_id", None)
            if not req_body["metadata"]:
                req_body.pop("metadata", None)
        headers = {
            "content-type": "application/json",
            **auth,
            "anthropic-version": constants.ANTHROPIC_VERSION,
            "User-Agent": settings.USER_AGENT,
            "X-ZCode-App-Version": constants.X_ZCODE_APP_VERSION,
            "X-ZCode-Agent": constants.X_ZCODE_AGENT,
            "HTTP-Referer": constants.HTTP_REFERER,
        }
    if verify_param:
        headers[constants.CAPTCHA_HEADER] = verify_param
    if verify_region:
        headers[constants.CAPTCHA_REGION_HEADER] = verify_region

    for key, value in (incoming_headers or {}).items():
        lower = key.lower()
        if lower in _DROP_HEADERS or lower.startswith(_DROP_HEADER_PREFIXES):
            continue
        headers[key] = value

    return target_url, headers, json.dumps(req_body, ensure_ascii=False).encode("utf-8")
