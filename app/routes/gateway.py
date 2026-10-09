"""核心网关：/v1/messages（Anthropic 风格）与 /v1/chat/completions（OpenAI 风格）。

共用多账号轮询 + 额度用完自动换号 + 阿里无痕验证自动续期；OpenAI 端点由
openai_compat 做双向格式转换，调度与错误处理策略完全一致。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import secrets
import time
import weakref
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import constants, logs, reqlog, settings
from ..agent import build_request
from ..auth_admin import verify_gateway_key
from ..captcha import captcha_manager
from ..models import Account, Status
from ..openai_compat import StreamConverter, _apply_reasoning_params, anthropic_to_openai, openai_to_anthropic
from ..quota import fetch_quota
from ..responses_compat import ResponsesStreamConverter, anthropic_to_responses, responses_to_anthropic
from ..store import store

_sleep = asyncio.sleep  # 模块级引用：测试可 patch 此名而免污染全局 asyncio

router = APIRouter()
_SHARED_CLIENTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient] = (
    weakref.WeakKeyDictionary()
)


def _get_shared_client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    client = _SHARED_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=30.0, read=None, write=120.0, pool=30.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=120.0),
        )
        _SHARED_CLIENTS[loop] = client
    return client


async def close_shared_client() -> None:
    loop = asyncio.get_running_loop()
    client = _SHARED_CLIENTS.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()


MAX_CAPTCHA_RETRIES = 3
MAX_ACCOUNT_ATTEMPTS = 5

# 常量收口：模型表与被拒信号关键字统一在 app/constants.py
MODEL_NAME_MAP = constants.MODEL_NAME_MAP
AVAILABLE_MODELS = constants.AVAILABLE_MODELS
_EXHAUST_KEYWORDS = constants.EXHAUST_KEYWORDS


def _detect_provider(body: dict, headers) -> str:
    model = body.get("model") or ""
    if model.startswith("bigmodel/") or headers.get("x-provider") == "bigmodel":
        return "bigmodel"
    return "zai"


def _normalize_body(body: dict) -> dict:
    # 纯免费赠送池模式：将任意外部请求模型名统一归一化为 DEFAULT_MODEL（GLM-5.3-Flash），
    # 防止客户端传入 claude-*/gpt-*/glm-5.2 等触发上游 3006 model not allowed 或误耗非免费池窗口
    model = constants.DEFAULT_MODEL
    body["model"] = model

    # 上游对 max_tokens 有硬校验（400 code 1210），钳制到合法区间并记录钳制动作
    raw = body.get("max_tokens")
    if raw is not None and not isinstance(raw, bool):
        try:
            mt = int(float(raw))
        except (TypeError, ValueError):
            mt = None
        if mt is not None:
            clamped = max(1, min(mt, constants.MAX_TOKENS_LIMIT))
            if clamped != mt:
                logs.warn("gateway", f"max_tokens {mt} 超出上游范围 [1,{constants.MAX_TOKENS_LIMIT}]，钳制为 {clamped}")
            body["max_tokens"] = clamped

    messages = body.get("messages")
    if isinstance(messages, list):
        bridged = []
        for msg in messages:
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                bridged.append({**msg, "content": [{"type": "text", "text": msg["content"]}]})
            else:
                bridged.append(msg)
        body["messages"] = bridged
    return body


def _extract_business_code(text: str, data: dict | None = None) -> tuple[str | None, str]:
    """提取上游 JSON 响应中的业务错误码（如 1005/1006/1302/3002/3007/3008/3012）。"""
    parsed = data if isinstance(data, dict) else _safe_json(text)
    if not isinstance(parsed, dict):
        return None, ""
    # 正常 Anthropic message 响应不是业务错误
    if parsed.get("type") == "message":
        return None, ""

    raw_code = parsed.get("code")
    err_obj = parsed.get("error")
    if raw_code is None and isinstance(err_obj, dict):
        raw_code = err_obj.get("code") or err_obj.get("type")

    code_str = str(raw_code).strip() if raw_code is not None else None
    msg_parts: list[str] = []
    for k in ("msg", "message"):
        v = parsed.get(k)
        if isinstance(v, str) and v.strip():
            msg_parts.append(v.strip())
    if isinstance(err_obj, dict):
        for k in ("message", "msg", "type"):
            v = err_obj.get(k)
            if isinstance(v, str) and v.strip():
                msg_parts.append(v.strip())
    return code_str, " ".join(msg_parts)


def _is_captcha_error(text: str) -> bool:
    low = text.lower()
    return "captcha" in low or "verify token" in low or "verify failed" in low


def _detect_captcha_challenge(resp: httpx.Response, text: str | None = None) -> str | None:
    """验证码挑战检测（对齐 zapi handler.ts 与 ZCode failure-provider-business-codes.ts）。

    三种形态：
      1. 响应头 x-aliyun-captcha-verify-param 存在（官方挑战信号）
      2. body 含业务码 3007（无论 HTTP 200/400/403）
      3. HTTP 403 + 文案 captcha/verify（老检测，保留兼容）
    返回挑战标记（非 None 即挑战），否则 None。
    """
    # 1) challenge 响应头
    header_val = resp.headers.get(constants.CAPTCHA_HEADER)
    if header_val and header_val.strip():
        return "header"

    if text is None:
        return None
    low = text.lower()

    # 2) body code 3007（支持 HTTP 200/400/403 任意状态）
    code, _ = _extract_business_code(text)
    if code in constants.CAPTCHA_BUSINESS_CODES:
        return "in-body-3007"
    if resp.status_code in (200, 400, 403) and any(m in text for m in constants.CAPTCHA_BODY_MARKERS):
        return "in-body-3007"

    # 3) 403 + 挑战文案
    if resp.status_code == 403 and _is_captcha_error(low):
        return "text"

    return None


def _is_exhausted(status_code: int, text: str) -> bool:
    # 429 是频控信号，优先于一切 body 关键词：429 body 带额度文案时
    # （api.z.ai 实测形态）必须走频控重试，不得判成额度耗尽踢号。
    if status_code == 429:
        return False
    if status_code in constants.EXHAUST_HTTP_STATUSES:
        return True
    code, _ = _extract_business_code(text)
    if code in constants.EXHAUST_BUSINESS_CODES:
        return True
    low = text.lower()
    return any(k in low for k in _EXHAUST_KEYWORDS)


def _is_risk_control(status_code: int, text: str) -> bool:
    """风控信号判定（3012「unusual activity」/ messages 端点 405）。

    与验证码挑战互斥：调用点已先排除 challenge 形态。命中即账号级风控，
    直接禁用 Plan 通道（或切 API Key 回退），不做自动退避。
    """
    if status_code in constants.RISK_CONTROL_HTTP_STATUSES:
        return True
    code, _ = _extract_business_code(text)
    if code == "3012":
        return True
    low = text.lower()
    return any(m.lower() in low for m in constants.RISK_CONTROL_MARKERS)


def _classify_business_error(status_code: int, resp: httpx.Response, text: str) -> tuple[int, str | None, bool]:
    """把上游业务错误（含 HTTP 200 包装的业务错误 JSON）归一为标准 HTTP 状态码。

    返回 (effective_status_code, business_code, is_concurrency_limit)。
    仅当 JSON 明确为非 message 错误结构时才改写 200 状态码，原样保留非 JSON 响应以兼容既有调用方。
    """
    data = _safe_json(text)
    if not isinstance(data, dict) or data.get("type") == "message":
        return status_code, None, False

    code, _msg = _extract_business_code(text, data)
    is_err_payload = (
        (code is not None and code not in ("0", "200"))
        or data.get("success") is False
        or data.get("type") == "error"
        or isinstance(data.get("error"), dict)
    )
    if not is_err_payload:
        return status_code, None, False

    if code == "3012" or _is_risk_control(status_code, text):
        return 405, code or "3012", False
    if code in constants.CAPTCHA_BUSINESS_CODES or _detect_captcha_challenge(resp, text):
        return (status_code if status_code in (400, 403) else 403), code or "3007", False
    if code in constants.EXHAUST_BUSINESS_CODES:
        return 402, code, False
    if code in constants.AUTH_INVALID_BUSINESS_CODES:
        return 401, code, False
    if code in constants.CONCURRENCY_LIMIT_BUSINESS_CODES:
        return 429, code, True
    if code in constants.RATE_LIMIT_BUSINESS_CODES:
        return 429, code, False
    if code in constants.SERVER_ERROR_BUSINESS_CODES:
        return (status_code if status_code >= 500 else 500), code, False
    if status_code < 400:
        return 400, code, False
    return status_code, code, False


def _is_thinking_signature_rejection(status_code: int, text: str) -> bool:
    """判定是否为 Anthropic/GLM 历史消息 thinking 块签名/格式不兼容导致的 400 拒绝。

    对齐官方 ZCode src/main/agent/runtime/reasoning-history-normalization.ts。
    """
    if status_code != 400 or not text:
        return False
    low = text.lower()
    if "thinking" not in low and "redacted_thinking" not in low:
        return False
    markers = (
        "signature",
        "invalid",
        "unsupported",
        "not supported",
        "malformed",
        "corrupted",
        "cannot be modified",
        "must match",
    )
    return any(m in low for m in markers)


def _strip_thinking_from_body(body: dict) -> dict | None:
    """深拷贝 body 并剥离历史 assistant 消息中的 thinking / redacted_thinking 块。

    若未发现任何可剥离的 thinking 块则返回 None（避免无意义的重复请求）。
    """
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    cloned = copy.deepcopy(body)
    stripped = False
    for msg in cloned.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        kept = [
            b for b in content
            if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))
        ]
        if len(kept) != len(content):
            stripped = True
            msg["content"] = kept or [{"type": "text", "text": ""}]
    return cloned if stripped else None


def _parse_retry_after(value: str | None) -> int | None:
    """解析 Retry-After（仅秒数形态；HTTP-date 形态少见，放弃即用默认重试等待）。

    非正数不采信；超长值封顶采信 —— 尊重上游意图的同时防止把客户端吊死。
    """
    if not value:
        return None
    try:
        secs = int(float(value.strip()))
    except (ValueError, AttributeError):
        return None
    return min(secs, settings.RETRY_429_WAIT_MAX) if secs > 0 else None


def _mark(account: Account, status_value: str, error: str | None = None) -> None:
    account.status = status_value
    account.last_error = error
    if status_value == Status.COOLING:
        account.cooling_until = time.time() + settings.COOLING_SECONDS
    store.update_account(account)


def _last_user_text(body: dict) -> str:
    for msg in reversed(body.get("messages") or []):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    return part.get("text", "")
    return ""


@router.get("/v1/models", dependencies=[Depends(verify_gateway_key)])
async def list_models():
    """列出可用模型（Anthropic /v1/models 风格）。"""
    return {
        "object": "list",
        "data": [
            {"id": i, "type": "model", "display_name": i, "created_at": "2025-01-01T00:00:00Z"}
            for i in AVAILABLE_MODELS
        ],
    }


@router.post("/v1/messages", dependencies=[Depends(verify_gateway_key)])
async def messages(request: Request):
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": {"message": "请求体不是合法 JSON", "type": "invalid_request"}}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": {"message": "请求体必须是 JSON 对象", "type": "invalid_request_error"}},
            status_code=400,
        )

    err = _apply_reasoning_params(body, body)
    if err:
        return JSONResponse({"error": {"message": err, "type": "invalid_request_error"}}, status_code=400)

    incoming_headers = dict(request.headers)
    provider = _detect_provider(body, request.headers)
    body = _normalize_body(body)
    # 验证码页面由本服务托管，端口取实际请求端口（兼容任意启动端口）
    port = request.url.port or settings.PORT

    req_id = secrets.token_hex(8)
    logs.req(req_id, str(body.get("model") or "-"), bool(body.get("stream")), _last_user_text(body))
    reqlog.begin(req_id, "messages", str(body.get("model") or "-"),
                 bool(body.get("stream")), _last_user_text(body))

    try:
        result = await _dispatch(req_id, body, incoming_headers, port, provider)
    except asyncio.CancelledError:
        # 客户端在调度期间断开（429 重试/验证码等待可达数分钟）——CancelError
        # 是 BaseException，不兜底会让监控条目永久滞留「进行中」
        reqlog.finish_error(req_id, "客户端断开", status=499)
        raise
    except Exception as err:  # noqa: BLE001 - 调度层意外异常也要收口监控条目
        reqlog.finish_error(req_id, f"网关内部错误: {err}", status=500)
        return JSONResponse(
            {"error": {"message": "网关内部错误", "type": "internal_error"}},
            status_code=500,
        )
    if isinstance(result, _Upstream):
        # dispatch 返回与流式生成器启动之间的取消窗口：兜底关闭释放并发槽位
        try:
            return result.to_streaming(req_id)
        except asyncio.CancelledError:
            await result.close()
            raise
    return result


@router.post("/v1/chat/completions", dependencies=[Depends(verify_gateway_key)])
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": {"message": "请求体不是合法 JSON", "type": "invalid_request_error"}}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": {"message": "请求体必须是 JSON 对象", "type": "invalid_request_error"}}, status_code=400)

    body, err = openai_to_anthropic(payload)
    if err or body is None:
        return JSONResponse({"error": {"message": err or "请求体不合法", "type": "invalid_request_error"}}, status_code=400)

    incoming_headers = dict(request.headers)
    provider = _detect_provider(body, request.headers)
    body = _normalize_body(body)
    port = request.url.port or settings.PORT

    req_id = secrets.token_hex(8)
    logs.req(req_id, str(body.get("model") or "-"), bool(payload.get("stream")), _last_user_text(body))
    reqlog.begin(req_id, "chat", str(body.get("model") or "-"),
                 bool(payload.get("stream")), _last_user_text(body))

    try:
        result = await _dispatch(req_id, body, incoming_headers, port, provider)
    except asyncio.CancelledError:
        reqlog.finish_error(req_id, "客户端断开", status=499)
        raise
    except Exception as err:  # noqa: BLE001 - 调度层意外异常也要收口监控条目
        reqlog.finish_error(req_id, f"网关内部错误: {err}", status=500)
        return JSONResponse(
            {"error": {"message": "网关内部错误", "type": "internal_error"}},
            status_code=500,
        )
    if not isinstance(result, _Upstream):
        return result

    model = str(body.get("model") or "")
    if payload.get("stream"):
        try:
            return _openai_stream_response(result, model, req_id)
        except asyncio.CancelledError:
            await result.close()
            raise

    try:
        raw = await result.aread()
        logs.req_ok(req_id)
    except asyncio.CancelledError:
        reqlog.finish_error(req_id, "客户端断开", status=499, t_first=result.t_first)
        raise
    except Exception as err:  # noqa: BLE001
        logs.req_err(req_id, f"读取上游响应失败: {err}")
        reqlog.finish_error(req_id, f"读取上游响应失败: {err}", status=502)
        return JSONResponse({"error": {"message": f"读取上游响应失败: {err}", "type": "upstream_error"}}, status_code=502)
    finally:
        await result.close()
    data = _safe_json(raw.decode("utf-8", "ignore"))
    if not isinstance(data, dict) or data.get("type") != "message":
        reqlog.finish_error(req_id, "上游响应格式异常", status=502, t_first=result.t_first)
        return JSONResponse({"error": {"message": "上游响应格式异常", "type": "upstream_error"}}, status_code=502)
    usage = data.get("usage") or {}
    reqlog.finish_ok(req_id, t_first=result.t_first, status=result.resp.status_code,
                     **reqlog.extract_usage(usage))
    return JSONResponse(anthropic_to_openai(data, model))


def _openai_stream_response(up: _Upstream, model: str, req_id: str) -> StreamingResponse:
    """把上游 Anthropic SSE 事件流转换为 OpenAI chunk 流。"""
    conv = StreamConverter(model)
    async def _iter():
        usage = reqlog.extract_usage(None)
        completed = False
        try:
            yield conv.start()
            async for line in up.resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str:
                    continue
                evt = _safe_json(data_str)
                if isinstance(evt, dict):
                    _check_stream_error(evt)
                    completed = completed or evt.get("type") == "message_stop"
                    usage = _extract_event_usage(evt, usage)
                    for out in conv.feed(evt):
                        yield out
            if not completed and "event-stream" in up.resp.headers.get("content-type", "").lower():
                raise _UpstreamStreamIncomplete()
            yield conv.done()
            up.finish_result(True, "HTTP 200 · 流式完成")
            logs.req_ok(req_id)
            reqlog.finish_ok(req_id, t_first=up.t_first, status=up.resp.status_code,
                             **usage)
        except asyncio.CancelledError:
            up.finish_result(False, "客户端断开")
            reqlog.finish_error(req_id, "客户端断开", status=499, t_first=up.t_first)
            raise
        except _UpstreamStreamError as err:
            up.finish_result(False, str(err))
            logs.req_err(req_id, str(err))
            reqlog.finish_error(req_id, str(err), status=up.resp.status_code, t_first=up.t_first)
            yield "data: " + json.dumps({"error": {"type": "upstream_error", "message": str(err)}}) + "\n\n"
        except Exception as err:  # noqa: BLE001
            up.finish_result(False, f"流传输中断: {err}")
            logs.req_err(req_id, f"流传输中断: {err}")
            reqlog.finish_error(req_id, f"流传输中断: {err}", t_first=up.t_first)
        finally:
            await up.close()

    return StreamingResponse(_iter(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.post("/v1/responses", dependencies=[Depends(verify_gateway_key)])
async def responses_endpoint(request: Request):
    """OpenAI Responses API 兼容端点（/v1/responses ↔ Anthropic /v1/messages 直转）。"""
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": {"message": "请求体不是合法 JSON", "type": "invalid_request_error"}}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": {"message": "请求体必须是 JSON 对象", "type": "invalid_request_error"}}, status_code=400)

    body, err = responses_to_anthropic(payload)
    if err or body is None:
        return JSONResponse({"error": {"message": err or "请求体不合法", "type": "invalid_request_error"}}, status_code=400)

    incoming_headers = dict(request.headers)
    provider = _detect_provider(body, request.headers)
    body = _normalize_body(body)
    port = request.url.port or settings.PORT

    effort = None
    out_cfg = body.get("output_config")
    if isinstance(out_cfg, dict) and isinstance(out_cfg.get("effort"), str):
        effort = out_cfg["effort"]

    req_id = secrets.token_hex(8)
    logs.req(req_id, str(body.get("model") or "-"), bool(payload.get("stream")), _last_user_text(body))
    reqlog.begin(req_id, "responses", str(body.get("model") or "-"),
                 bool(payload.get("stream")), _last_user_text(body))

    try:
        result = await _dispatch(req_id, body, incoming_headers, port, provider)
    except asyncio.CancelledError:
        reqlog.finish_error(req_id, "客户端断开", status=499)
        raise
    except Exception as err:  # noqa: BLE001 - 调度层意外异常也要收口监控条目
        reqlog.finish_error(req_id, f"网关内部错误: {err}", status=500)
        return JSONResponse(
            {"error": {"message": "网关内部错误", "type": "internal_error"}},
            status_code=500,
        )
    if not isinstance(result, _Upstream):
        return result

    model = str(body.get("model") or "")
    if payload.get("stream"):
        try:
            return _responses_stream_response(result, model, req_id, effort=effort)
        except asyncio.CancelledError:
            await result.close()
            raise

    try:
        raw = await result.aread()
        logs.req_ok(req_id)
    except asyncio.CancelledError:
        reqlog.finish_error(req_id, "客户端断开", status=499, t_first=result.t_first)
        raise
    except Exception as err:  # noqa: BLE001
        logs.req_err(req_id, f"读取上游响应失败: {err}")
        reqlog.finish_error(req_id, f"读取上游响应失败: {err}", status=502)
        return JSONResponse({"error": {"message": f"读取上游响应失败: {err}", "type": "upstream_error"}}, status_code=502)
    finally:
        await result.close()
    data = _safe_json(raw.decode("utf-8", "ignore"))
    if not isinstance(data, dict) or data.get("type") != "message":
        reqlog.finish_error(req_id, "上游响应格式异常", status=502, t_first=result.t_first)
        return JSONResponse({"error": {"message": "上游响应格式异常", "type": "upstream_error"}}, status_code=502)
    usage = data.get("usage") or {}
    reqlog.finish_ok(req_id, t_first=result.t_first, status=result.resp.status_code,
                     **reqlog.extract_usage(usage))
    return JSONResponse(anthropic_to_responses(data, model, effort=effort))


def _responses_stream_response(
    up: _Upstream,
    model: str,
    req_id: str,
    effort: str | None = None,
) -> StreamingResponse:
    """把上游 Anthropic SSE 事件流转换为 OpenAI Responses SSE 事件流。"""
    conv = ResponsesStreamConverter(model, effort=effort)
    async def _iter():
        usage = reqlog.extract_usage(None)
        try:
            for out in conv.start():
                yield out
            async for line in up.resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                evt = _safe_json(data_str)
                if isinstance(evt, dict):
                    _check_stream_error(evt)
                    usage = _extract_event_usage(evt, usage)
                    for out in conv.feed(evt):
                        yield out
                    if conv.is_finished:
                        break
            if not conv.is_finished and "event-stream" in up.resp.headers.get("content-type", "").lower():
                raise _UpstreamStreamIncomplete()
            for out in conv.done():
                yield out
            up.finish_result(True, "HTTP 200 · 流式完成")
            logs.req_ok(req_id)
            reqlog.finish_ok(req_id, t_first=up.t_first, status=up.resp.status_code,
                             **usage)
        except asyncio.CancelledError:
            if conv.is_finished:
                up.finish_result(True, "HTTP 200 · 流式完成")
                logs.req_ok(req_id)
                reqlog.finish_ok(req_id, t_first=up.t_first, status=up.resp.status_code,
                                 **usage)
            else:
                up.finish_result(False, "客户端断开")
                reqlog.finish_error(req_id, "客户端断开", status=499, t_first=up.t_first)
            raise
        except _UpstreamStreamError as err:
            up.finish_result(False, str(err))
            logs.req_err(req_id, str(err))
            reqlog.finish_error(req_id, str(err), status=up.resp.status_code, t_first=up.t_first)
            for out in conv.fail(str(err)):
                yield out
        except Exception as err:  # noqa: BLE001
            up.finish_result(False, f"流传输中断: {err}")
            logs.req_err(req_id, f"流传输中断: {err}")
            reqlog.finish_error(req_id, f"流传输中断: {err}", t_first=up.t_first)
            for out in conv.fail(f"流传输中断: {err}"):
                yield out
        finally:
            await up.close()

    return StreamingResponse(_iter(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ── 会话亲和路由（保多轮对话缓存与思维链签名连续；固定窗口/满步数/满并发自动轮转）────
_SESSION_AFFINITY_TTL = 600.0
_SESSION_AFFINITY_MAX_REQUESTS = 20
_SESSION_AFFINITY_MAX_SIZE = 2048
_session_affinity: dict[str, tuple] = {}


def _extract_session_affinity_key(
    provider: str,
    body: dict,
    incoming_headers: dict | None = None,
) -> tuple[str | None, bool]:
    """提取会话亲和键 (affinity_key, prefer_sticky)。

    显式 session header / metadata 首轮即粘性；未显式指定时按首条非 system 消息
    哈希派生，首轮走 round-robin 分散落号并记绑定，次轮起在固定窗口内复用同号。
    """
    if isinstance(incoming_headers, dict):
        lower_headers = {str(k).lower(): v for k, v in incoming_headers.items() if isinstance(k, str)}
        for hk in ("x-session-id", "x-conversation-id", "x-claude-code-session-id"):
            val = lower_headers.get(hk)
            if isinstance(val, str) and val.strip():
                return f"{provider}:sid:{val.strip()[:128]}", True

    meta = body.get("metadata") if isinstance(body, dict) else None
    if isinstance(meta, dict):
        raw_sid = meta.get("session_id")
        if isinstance(raw_sid, str) and raw_sid.strip():
            return f"{provider}:sid:{raw_sid.strip()[:128]}", True
        raw_uid = meta.get("user_id")
        if isinstance(raw_uid, str) and raw_uid.strip():
            uid_str = raw_uid.strip()
            if uid_str.startswith("{"):
                try:
                    parsed = json.loads(uid_str)
                    if isinstance(parsed, dict) and isinstance(parsed.get("session_id"), str) and parsed["session_id"].strip():
                        return f"{provider}:sid:{parsed['session_id'].strip()[:128]}", True
                except (ValueError, TypeError):
                    pass
            return f"{provider}:sid:{uid_str[:128]}", True

    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        return None, False
    non_sys = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
    if not non_sys:
        return None, False

    first_content = non_sys[0].get("content")
    first_text = ""
    if isinstance(first_content, str):
        first_text = first_content.strip()
    elif isinstance(first_content, list):
        parts: list[str] = []
        for b in first_content:
            if isinstance(b, dict) and isinstance(b.get("text"), str) and b["text"].strip():
                parts.append(b["text"].strip())
            elif isinstance(b, str) and b.strip():
                parts.append(b.strip())
        first_text = " ".join(parts)

    if not first_text:
        return None, False

    model_str = str(body.get("model") or "")
    digest = hashlib.sha256(
        f"{provider}:{model_str}:{first_text[:512]}".encode("utf-8", "ignore")
    ).hexdigest()[:24]
    return f"{provider}:pfx:{digest}", len(non_sys) >= 2


def _get_sticky_account(
    provider: str,
    affinity_key: str | None,
    skip_ids: set[str],
    limit: int,
    model: str | None = None,
) -> Account | None:
    """查询粘性绑定的账号。

    采用固定生命周期（Fixed-Window TTL，禁止命中时滑动续期）+ 单轮最大请求步数上限：
    一旦超过固定时长、达到步数上限、目标模型无额度、冷却中或并发已满，立即解绑并返回 None，
    使长会话平滑轮转到池内其它健康账号，防止单账号被永久钉死过热触发 3012 风控。
    """
    if not affinity_key:
        return None
    entry = _session_affinity.get(affinity_key)
    if entry is None:
        return None
    acc_id, created_at = entry[0], entry[1]
    req_count = entry[2] if len(entry) > 2 else 0
    now = time.time()
    if now - created_at > _SESSION_AFFINITY_TTL or req_count >= _SESSION_AFFINITY_MAX_REQUESTS:
        _session_affinity.pop(affinity_key, None)
        return None
    if acc_id in skip_ids:
        return None
    acc = store.find(provider, acc_id)
    if acc is None or not acc.is_selectable(now) or not acc.has_model_quota(model):
        _session_affinity.pop(affinity_key, None)
        return None
    if model and not acc.explicitly_supports_model(model):
        # 若会话中途切换到当前账号不具备的模型（如老号无 GLM-5.3），且池内有支持该模型的其它账号，则让出粘性
        if any(
            other.id != acc.id and other.is_selectable(now) and other.explicitly_supports_model(model)
            for other in store.list_accounts(provider)
        ):
            return None
    if limit > 0 and _inflight.get(acc.id, 0) >= limit:
        return None
    return acc


def _bind_sticky_account(affinity_key: str | None, account_id: str) -> None:
    """记录或更新会话粘性绑定的账号 ID（固定创建时间戳 created_at 不滑动，仅累加请求计数）。"""
    if not affinity_key or not account_id:
        return
    now = time.time()
    prev = _session_affinity.get(affinity_key)
    if prev is not None and prev[0] == account_id and (now - prev[1] <= _SESSION_AFFINITY_TTL):
        prev_count = prev[2] if len(prev) > 2 else 0
        _session_affinity[affinity_key] = (account_id, prev[1], prev_count + 1)
    else:
        _session_affinity[affinity_key] = (account_id, now, 1)
    if len(_session_affinity) > _SESSION_AFFINITY_MAX_SIZE:
        expired = [k for k, v in _session_affinity.items() if now - v[1] > _SESSION_AFFINITY_TTL]
        for k in expired:
            _session_affinity.pop(k, None)
        if len(_session_affinity) > _SESSION_AFFINITY_MAX_SIZE:
            oldest_key = min(_session_affinity, key=lambda k: _session_affinity[k][1])
            _session_affinity.pop(oldest_key, None)


async def _dispatch(req_id, body, incoming_headers, port, provider):
    """多账号会话粘性亲和 + 轮询故障转移调度：_Upstream（成功）或 JSONResponse（错误）。

    单账号并发限制：选号后若该账号在飞请求已达上限（store.account_concurrency，
    0 = 不限），跳过换下一个账号——不排队（流式请求可占槽位数分钟，排队会
    放大延迟甚至吊死客户端）。满号跳过不计入 MAX_ACCOUNT_ATTEMPTS（只计真正
    进入 _try_account 的次数）。全满/无号 → 503。
    """
    tried: set[str] = set()
    observations: dict = {}
    failures: dict = {}
    limit = _limit()
    attempts = 0
    req_model = str(body.get("model") or "").strip() if isinstance(body, dict) else ""
    affinity_key, prefer_sticky = _extract_session_affinity_key(provider, body, incoming_headers)
    # 在查询解绑前保留旧号，仅用于本次新窗口的软偏好，不放入 tried。
    entry = _session_affinity.get(affinity_key) if prefer_sticky else None
    avoid_id = None
    if entry and (time.time() - entry[1] > _SESSION_AFFINITY_TTL
                  or (entry[2] if len(entry) > 2 else 0) >= _SESSION_AFFINITY_MAX_REQUESTS):
        avoid_id = entry[0]

    while attempts < MAX_ACCOUNT_ATTEMPTS:
        account = None
        if attempts == 0 and prefer_sticky:
            account = _get_sticky_account(provider, affinity_key, tried, limit, model=req_model)
        if account is None:
            account = store.select(provider, skip_ids=tried, model=req_model, avoid_id=avoid_id,
                                   observations=observations)
        if account is None:
            break
        tried.add(account.id)
        if limit > 0 and _inflight.get(account.id, 0) >= limit:
            failures[account.id] = _AccountFailure("local_concurrency_limit", "local_scheduler")
            logs.warn(req_id, f"账号 {account.name} 并发已满（{_inflight.get(account.id, 0)}/{limit}），切换下一个")
            continue
        attempts += 1
        needs_captcha = provider == "zai" and account.uses_plan_channel()

        slot_box: list[str | None] = [None]
        if limit > 0:
            _inflight[account.id] = _inflight.get(account.id, 0) + 1
            slot_box[0] = account.id
        try:
            result = await _try_account(
                req_id, account, body, incoming_headers, port, needs_captcha, slot_box,
            )
        except BaseException:
            if slot_box[0] is not None:
                _release_slot(slot_box[0])
                slot_box[0] = None
            raise
        if isinstance(result, _AccountFailure):
            failures[account.id] = result
            if slot_box[0] is not None:
                _release_slot(slot_box[0])
                slot_box[0] = None
            continue
        if isinstance(result, _Upstream):
            _bind_sticky_account(affinity_key, account.id)
            held = slot_box[0]
            slot_box[0] = None
            if held is not None:
                result.on_close = _make_slot_releaser(held)
            return result
        if slot_box[0] is not None:
            _release_slot(slot_box[0])
            slot_box[0] = None
        if isinstance(result, JSONResponse) and result.status_code < 400:
            _bind_sticky_account(affinity_key, account.id)
        return result

    logs.req_err(req_id, "无可用账号 / 额度均已耗尽 / 并发已满")
    error, diagnostics = _unavailability(req_id, provider, req_model, observations,
                                        failures, attempts >= MAX_ACCOUNT_ATTEMPTS)
    reqlog.finish_error(req_id, "无可用账号 / 额度均已耗尽 / 并发已满", status=503, diagnostics=diagnostics)
    headers = {"X-Request-Id": req_id}
    if error["retry_after"] is not None:
        headers["Retry-After"] = str(error["retry_after"])
    return JSONResponse({"error": error}, status_code=503, headers=headers)


def _release_slot(account_id: str) -> None:
    n = _inflight.get(account_id, 0) - 1
    if n <= 0:
        _inflight.pop(account_id, None)
    else:
        _inflight[account_id] = n


def _park_slot(slot_box: list[str | None] | None) -> None:
    """等待（429/验证码/5xx）前释放并发槽，避免把账号冻住数分钟。"""
    if slot_box and slot_box[0] is not None:
        _release_slot(slot_box[0])
        slot_box[0] = None


def _reacquire_slot(account: Account, slot_box: list[str | None] | None) -> bool:
    """等待结束后重新占槽；占不到则让调用方换号。"""
    if slot_box is None:
        return True
    limit = _limit()
    if limit <= 0:
        return True
    if _inflight.get(account.id, 0) >= limit:
        return False
    _inflight[account.id] = _inflight.get(account.id, 0) + 1
    slot_box[0] = account.id
    return True


def _make_slot_releaser(account_id: str):
    def _release() -> None:
        _release_slot(account_id)
    return _release


@dataclass
class _AccountFailure:
    """换号证据，不携带账号身份或上游原文。"""

    code: str | None
    origin: str
    stage: str = "dispatch"
    at: float = field(default_factory=time.time)
    raw_http_status: int | None = None
    effective_status: int | None = None
    business_code: str | None = None
    deadline: float | None = None
    state_code: str | None = None
    history: list = field(default_factory=list)
    history_truncated: int = 0


_HARD_UNAVAILABILITY = {"pool_empty", "account_disabled", "credential_invalid", "quota_exhausted",
                        "model_quota_exhausted", "account_risk_blocked"}
_TEMP_UNAVAILABILITY = {"account_cooling", "local_concurrency_limit", "upstream_concurrency_limit",
                        "upstream_rate_limited", "upstream_transport_error", "upstream_server_error"}


def _retry_deadline(headers, now: float) -> float | None:
    """对外等待保留绝对期限，不使用内部等待封顶。"""
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                return None
            reference = headers.get("date")
            if reference:
                reference = parsedate_to_datetime(reference)
                if reference.tzinfo is None:
                    return None
                return now + max(0, target.timestamp() - reference.timestamp())
            return max(now, target.timestamp())
        except (ValueError, TypeError, OverflowError):
            return None
    target = now + seconds
    return target if math.isfinite(seconds) and seconds >= 0 and math.isfinite(target) else None


def _safe_business_code(value) -> str | None:
    if value is None:
        return None
    value = str(value)
    known = (constants.EXHAUST_BUSINESS_CODES + constants.AUTH_INVALID_BUSINESS_CODES
             + constants.CAPTCHA_BUSINESS_CODES + constants.RATE_LIMIT_BUSINESS_CODES
             + constants.SERVER_ERROR_BUSINESS_CODES)
    return value if value in known or (value.isascii() and value.isdigit() and 1 <= len(value) <= 6) else None


def _unavailability(req_id, provider, model, observations, failures, attempt_limit):
    """一次构造公开终因与后台证据；终止快照不推进选号游标。"""
    snapshot = store.selection_snapshot(provider, model)
    now = time.time()
    causes: set[tuple[str, str]] = set()
    evidence = []
    evidence_count = 0
    candidates = []
    unresolved = 0
    ordinals = {account_id: i + 1 for i, account_id in enumerate(snapshot)}

    def observe(account_id, failure):
        nonlocal evidence_count
        evidence_count += 1
        if len(evidence) >= 32:
            return
        evidence.append({"candidate": ordinals.get(account_id), "stage": failure.stage, "at": failure.at,
                         "code": failure.code, "origin": failure.origin,
                         "raw_http_status": failure.raw_http_status,
                         "effective_status": failure.effective_status,
                         "business_code": _safe_business_code(failure.business_code)})

    for account_id, state in snapshot.items():
        blocked = state["code"] or state["model_code"]
        failure = failures.get(account_id)
        evidence_count += failure.history_truncated if failure else 0
        old = observations.get(account_id)
        for historic in (failure.history if failure else []):
            observe(account_id, historic)
        if failure:
            observe(account_id, failure)
        elif old:
            observe(account_id, _AccountFailure(old["code"], "account_state", "selection", old["at"]))
        codes = []
        deadlines = []
        unknown = False
        # 终止时旧状态或满槽已解除，旧证据不能再解释无候选。
        recovered = (failure and failure.state_code and failure.state_code != state["code"]) or (
            failure and failure.code == "local_concurrency_limit"
            and (_limit() <= 0 or _inflight.get(account_id, 0) < _limit()))
        if recovered or (old and not blocked and not failure):
            unknown = True
        elif failure:
            if failure.code:
                codes.append((failure.code, failure.origin))
            else:
                unknown = True
            if failure.deadline is not None:
                deadlines.append(failure.deadline)
            if failure.origin in ("upstream", "transport"):
                deadlines.extend(h.deadline for h in failure.history if h.deadline is not None)
        elif blocked:
            if blocked == "unknown_unavailability":
                unknown = True
            else:
                codes.append((blocked, "account_state"))
                observe(account_id, _AccountFailure(blocked, "account_state", "terminal_selection", state["at"]))
        else:
            unknown = True
        # 冷却/上游临时错误不能掩盖同账号的模型硬障碍。
        if state["model_code"] and not any(c in _HARD_UNAVAILABILITY for c, _ in codes):
            codes.append((state["model_code"], "account_state"))
        # 已观测到的硬状态也会阻止临时错误恢复，不能因状态未变而省略。
        if failure and state["code"] in _HARD_UNAVAILABILITY and (
            state["code"] != failure.state_code or failure.code not in _HARD_UNAVAILABILITY
        ):
            codes.append((state["code"], "account_state"))
        if state["code"] == "account_cooling":
            if math.isfinite(state["cooling_until"]):
                deadlines.append(state["cooling_until"])
        if state["code"] == "unknown_unavailability":
            unknown = True
        causes.update(codes)
        hard = any(c in _HARD_UNAVAILABILITY for c, _ in codes)
        temporary = any(c in _TEMP_UNAVAILABILITY or (c == "upstream_429_unknown" and deadlines)
                        for c, _ in codes)
        uncertain = unknown or any(c in {"captcha_retry_exhausted", "upstream_429_unknown"}
                                  and not (c == "upstream_429_unknown" and deadlines) for c, _ in codes)
        if unknown:
            unresolved += 1
        candidates.append((hard, temporary, uncertain, max(deadlines) if deadlines else None))
    if not snapshot:
        causes.add(("pool_empty", "local_scheduler"))
        candidates.append((True, False, False, None))
    # 删除/新增等请求间变动同样导致覆盖缺口。
    unresolved += len((set(failures) | set(observations)) - set(snapshot))
    complete = unresolved == 0
    distinct = {code for code, _ in causes}
    code = ("dispatch_attempt_limit" if attempt_limit else "unknown_unavailability" if not complete or not distinct
            else next(iter(distinct)) if len(distinct) == 1 else "mixed_unavailability")
    recoverable = [c for c in candidates if not c[0] and c[1] and not c[2]]
    retryable = (None if attempt_limit else True if recoverable else
                 False if complete and all(c[0] for c in candidates) else None)
    retry_after = None
    if retryable is True and all(c[3] is not None for c in recoverable) and not any(
        not c[0] and c[2] for c in candidates
    ):
        retry_after = max(0, math.ceil(min(c[3] for c in recoverable) - now))
    public_causes = [{"code": c, "origin": o} for c, o in sorted(causes)]
    error = {"message": "所有账号均不可用、额度已用完或并发已满，请在后台检查账号状态",
             "type": "no_available_account", "code": code, "causes": public_causes,
             "retryable": retryable, "retry_after": retry_after, "request_id": req_id}
    diagnostics = {"error_code": code, "error_causes": public_causes, "retryable": retryable,
                   "retry_after": retry_after, "stop_reason": "attempt_limit" if attempt_limit else "no_candidate",
                   "coverage_complete": complete and not attempt_limit, "unresolved_count": unresolved,
                   "evidence": evidence, "evidence_truncated": max(0, evidence_count - 32)}
    return error, diagnostics


# fire-and-forget 后台任务强引用：事件循环对 task 只持弱引用，裸 create_task
# 会被 GC 中途静默丢弃（同 main.py 启动安装序已修过的缺陷，2026-09 review）。
_bg_tasks: set[asyncio.Task] = set()


def _spawn_bg(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


# 账号并发限制：account_id → 在飞请求数。asyncio 单线程事件循环下
# check+inc 原子；释放走 _Upstream.close / 失败路径，泄漏面在测试钉住。
_inflight: dict[str, int] = {}


def _limit() -> int:
    """当前并发上限（0 = 不限），实时读取设置（后台改后即生效）。"""
    return store.account_concurrency()


def _extract_usage_from_json_bytes(raw_bytes: bytes) -> dict[str, int | None]:
    data = _safe_json(raw_bytes.decode("utf-8", "ignore"))
    return reqlog.extract_usage(data.get("usage") if isinstance(data, dict) else None)


def _extract_event_usage(evt: object, usage: dict) -> dict[str, int | None]:
    """三协议共用原始事件用量，独立于客户端兼容转换。"""
    if not isinstance(evt, dict):
        return usage
    if evt.get("type") == "message_start":
        message = evt.get("message")
        return reqlog.extract_usage(message.get("usage") if isinstance(message, dict) else None, usage)
    if evt.get("type") == "message_delta":
        return reqlog.extract_usage(evt.get("usage"), usage)
    return usage


def _extract_sse_line_event(line_bytes: bytes) -> object:
    line = line_bytes.decode("utf-8", "ignore").strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    return _safe_json(payload)


class _UpstreamStreamError(RuntimeError):
    """HTTP 200 流内失败，不把上游事件或凭据写入日志。"""


class _UpstreamStreamIncomplete(_UpstreamStreamError):
    """上游静默 EOF，复用流内失败收口。"""

    def __init__(self) -> None:
        super().__init__("上游流式响应未正常结束")


def _check_stream_error(evt: object) -> None:
    if isinstance(evt, dict) and evt.get("type") == "error":
        raise _UpstreamStreamError("上游流式响应报错")


def _make_result_recorder(account: Account, credential: tuple):
    def _record(ok: bool, detail: str) -> None:
        live = store.find(account.provider, account.id)
        if live is not account or (live.mode, live.jwt_token, live.api_key) != credential:
            return
        live.record_result(ok, detail)
        store.update_account(live)
    return _record


class _Upstream:
    """已建立的上游成功流：由调用方消费并负责关闭。"""

    __slots__ = (
        "resp", "cm", "client", "t_first", "account_name", "mode",
        "on_close", "on_result", "preloaded_bytes", "_cm_closed", "_closed",
    )

    def __init__(self, resp: httpx.Response, cm, client: httpx.AsyncClient,
                 t_first: float | None = None, account_name: str = "", mode: str = "",
                 on_close=None, preloaded_bytes: bytes | None = None,
                 cm_closed: bool = False, on_result=None) -> None:
        self.resp = resp
        self.cm = cm
        self.client = client
        self.t_first = t_first
        self.account_name = account_name
        self.mode = mode
        self.on_close = on_close
        self.on_result = on_result
        self.preloaded_bytes = preloaded_bytes
        self._cm_closed = cm_closed
        self._closed = False

    async def aread(self) -> bytes:
        if self.preloaded_bytes is not None:
            return self.preloaded_bytes
        return await self.resp.aread()

    def finish_result(self, ok: bool, detail: str) -> None:
        """账号终态只记录一次，取消或重复关闭不得覆盖已确认结果。"""
        callback = self.on_result
        self.on_result = None
        if callback is not None:
            callback(ok, detail)

    async def close(self) -> None:
        """幂等关闭：释放上游流与并发槽位（on_close），重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        if not self._cm_closed:
            self._cm_closed = True
            await self.cm.__aexit__(None, None, None)
        if self.on_close is not None:
            try:
                self.on_close()
            except Exception:  # noqa: BLE001 - 槽位释放失败不掩盖主流程
                pass

    def to_streaming(self, req_id: str) -> StreamingResponse:
        """原样透传（/v1/messages 直通路径），旁路提取原始输入、输出及缓存用量。"""
        up = self

        async def _body_iter():
            usage = reqlog.extract_usage(None)
            pending_chunk = None
            stream_error = None
            completed = False
            try:
                if up.preloaded_bytes is not None:
                    usage = _extract_usage_from_json_bytes(up.preloaded_bytes)
                    yield up.preloaded_bytes
                else:
                    line_buf = bytearray()
                    async for chunk in up.resp.aiter_bytes():
                        pending_chunk = chunk
                        line_buf.extend(chunk)
                        while b"\n" in line_buf:
                            idx = line_buf.index(b"\n")
                            raw_line = bytes(line_buf[:idx])
                            del line_buf[:idx + 1]
                            try:
                                evt = _extract_sse_line_event(raw_line)
                                _check_stream_error(evt)
                                completed = completed or isinstance(evt, dict) and evt.get("type") == "message_stop"
                                usage = _extract_event_usage(evt, usage)
                            except _UpstreamStreamError as err:
                                stream_error = err
                                up.finish_result(False, str(err))
                                reqlog.finish_error(req_id, str(err), status=up.resp.status_code,
                                                    t_first=up.t_first)
                            # 错误帧的空行也要透传，SSE 客户端收到完整帧才会处理。
                            if stream_error is not None and not raw_line.strip():
                                raise stream_error
                        if len(line_buf) > 65536:
                            line_buf.clear()
                        pending_chunk = None
                        yield chunk
                    if line_buf:
                        evt = _extract_sse_line_event(bytes(line_buf))
                        _check_stream_error(evt)
                        completed = completed or isinstance(evt, dict) and evt.get("type") == "message_stop"
                        usage = _extract_event_usage(evt, usage)
                    if stream_error is not None:
                        raise stream_error
                    if "event-stream" in up.resp.headers.get("content-type", "").lower() and not completed:
                        raise _UpstreamStreamIncomplete()
                up.finish_result(True, "HTTP 200 · 流式完成")
                logs.req_ok(req_id)
                reqlog.finish_ok(req_id, t_first=up.t_first, status=up.resp.status_code,
                                 **usage)
            except asyncio.CancelledError:
                up.finish_result(False, "客户端断开")
                reqlog.finish_error(req_id, "客户端断开", status=499, t_first=up.t_first)
                raise
            except _UpstreamStreamError as err:
                up.finish_result(False, str(err))
                logs.req_err(req_id, str(err))
                reqlog.finish_error(req_id, str(err), status=up.resp.status_code, t_first=up.t_first)
                if pending_chunk is not None:
                    yield pending_chunk
                if isinstance(err, _UpstreamStreamIncomplete):
                    event = {"type": "error", "error": {"type": "upstream_error", "message": str(err)}}
                    yield b"\n\nevent: error\ndata: " + json.dumps(event).encode() + b"\n\n"
            except Exception as err:  # noqa: BLE001
                up.finish_result(False, f"流传输中断: {err}")
                logs.req_err(req_id, f"流传输中断: {err}")
                reqlog.finish_error(req_id, f"流传输中断: {err}", t_first=up.t_first)
            finally:
                await up.close()

        return StreamingResponse(_body_iter(), status_code=up.resp.status_code,
                                 media_type=up.resp.headers.get("content-type", "application/json"),
                                 headers={"Cache-Control": "no-cache"})


async def _try_account(req_id, account, body, incoming_headers, port, needs_captcha,
                       slot_box: list | None = None):
    """尝试用单个账号转发，含验证码续期、业务错误码分流、历史思考块剥离重试与可配置重试。"""
    captcha_retries = 0
    retries_429 = 0
    retries_5xx = 0
    force_fallback = False  # 本请求瞬态走 Key 回退（不改账号持久化状态）
    retried_thinking_strip = False
    attempt_body = body
    model_name = str(body.get("model") or "-")
    history = []
    history_truncated = 0
    wait_deadline = None
    raw_status = effective_status = business_code = deadline = None

    def next_account(code, origin="upstream", stage="upstream"):
        return _AccountFailure(code, origin, stage, raw_http_status=raw_status,
                               effective_status=effective_status, business_code=business_code,
                               deadline=max(d for d in (deadline, wait_deadline) if d is not None)
                               if origin in ("upstream", "transport") and (deadline is not None or wait_deadline is not None) else None,
                               state_code=account.selection_exclusion(), history=list(history),
                               history_truncated=history_truncated)

    def remember(code):
        nonlocal history_truncated, wait_deadline
        event = next_account(code)
        event.history = []
        event.history_truncated = 0
        if deadline is not None:
            wait_deadline = max(wait_deadline or deadline, deadline)
        if len(history) < 32:
            history.append(event)
        else:
            history_truncated += 1

    while True:
        raw_status = effective_status = business_code = deadline = None
        attempt_t0 = time.time()
        reqlog.mark_account(req_id, account.name, account.mode)
        verify_param = verify_region = None
        if needs_captcha:
            _park_slot(slot_box)
            try:
                verify_param, verify_region = await captcha_manager.get_verify_param(port)
            except Exception as err:  # noqa: BLE001
                logs.req_err(req_id, f"人机校验失败: {err}")
                reqlog.finish_error(req_id, f"人机校验失败: {err}", status=500)
                return JSONResponse(
                    {"error": {"message": f"无法完成人机校验: {err}", "type": "captcha_error"}},
                    status_code=500,
                )
            if not _reacquire_slot(account, slot_box):
                logs.warn(req_id, f"账号 {account.name} 验证码等待后并发已满，切换下一个")
                return next_account("local_concurrency_limit", "local_scheduler", "captcha_reacquire")

        try:
            credential = (account.mode, account.jwt_token, account.api_key)
            url, headers, payload = build_request(account, attempt_body, verify_param,
                                                  incoming_headers, verify_region,
                                                  force_fallback=force_fallback)
        except RuntimeError as err:
            account.record_result(False, f"凭证无效: {err}")
            _mark(account, Status.INVALID, str(err))
            logs.warn(req_id, f"账号 {account.name} 凭证无效，切换下一个")
            return next_account("credential_invalid", "local_scheduler", "credential")

        client = _get_shared_client()
        cm = client.stream("POST", url, headers=headers, content=payload)
        try:
            resp = await cm.__aenter__()
        except httpx.HTTPError as err:
            account.record_result(False, f"连接失败: {err}")
            # 废 JWT / 风控禁用走 Key 回退失败时不得洗成 cooling，否则冷却结束会重开 Plan
            if account.status in (Status.INVALID, Status.DISABLED):
                store.update_account(account)
            else:
                _mark(account, Status.COOLING, f"连接失败: {err}")
            logs.warn(req_id, f"账号 {account.name} 连接失败，切换下一个")
            return next_account("upstream_transport_error", "transport", "connect")

        status_code = resp.status_code
        received_at = time.time()
        raw_status = status_code
        content_type = (resp.headers.get("content-type") or "").lower()
        preloaded_bytes: bytes | None = None
        cm_closed = False
        text = ""
        is_concurrency_limit = False

        if status_code >= 400 or "event-stream" not in content_type:
            try:
                preloaded_bytes = await resp.aread()
            except httpx.HTTPError as err:
                await cm.__aexit__(None, None, None)
                account.record_result(False, f"读取响应失败: {err}")
                if account.status in (Status.INVALID, Status.DISABLED):
                    store.update_account(account)
                else:
                    _mark(account, Status.COOLING, f"读取响应失败: {err}")
                logs.warn(req_id, f"账号 {account.name} 读取响应失败，切换下一个")
                return next_account("upstream_transport_error", "transport", "read")
            await cm.__aexit__(None, None, None)
            cm_closed = True
            text = preloaded_bytes.decode("utf-8", "ignore")
            status_code, _biz_code, is_concurrency_limit = _classify_business_error(
                status_code, resp, text,
            )
            business_code = _biz_code
        effective_status = status_code
        deadline = _retry_deadline(resp.headers, received_at)

        if status_code >= 400:
            # 验证码挑战：三形态任一命中即清池重试（不改账号状态）
            challenge = _detect_captcha_challenge(resp, text) if needs_captcha else None
            if challenge:
                captcha_manager.invalidate()
                captcha_retries += 1
                if captcha_retries >= MAX_CAPTCHA_RETRIES:
                    account.record_result(False, "验证码挑战连续失败")
                    logs.warn(req_id, f"账号 {account.name} 验证码连续失败，切换下一个")
                    return next_account("captcha_retry_exhausted")
                logs.warn(req_id, f"账号 {account.name} 验证码挑战（{challenge}），刷新重试")
                continue  # 同账号重建请求重试

            # 风控（3012「unusual activity」/ 405）：真封禁 → 禁用账号，人工恢复。
            # 必须先于 exhausted/其它错误判定，且不再重试（避免对封禁账号持续施压）。
            if _is_risk_control(status_code, text):
                account.record_result(False, f"风控封禁 HTTP {status_code}（3012/unusual activity）")
                account.ban_for_risk()
                account.last_error = (
                    f"风控封禁 (3012/unusual activity) HTTP {status_code}，"
                    f"确认恢复后请在后台手动启用（第 {account.risk_strikes} 次）"
                )
                store.update_account(account)
                if needs_captcha and account.has_apikey_fallback():
                    logs.warn(
                        req_id,
                        f"账号 {account.name} 命中风控 HTTP {status_code}，已禁用 Plan 通道"
                        f"（累计第 {account.risk_strikes} 次），切 API Key 回退",
                    )
                    needs_captcha = False
                    force_fallback = True
                    continue
                logs.warn(
                    req_id,
                    f"账号 {account.name} 命中风控 HTTP {status_code}，已禁用"
                    f"（累计第 {account.risk_strikes} 次），切换下一个",
                )
                return next_account("account_risk_blocked")

            if _is_exhausted(status_code, text):
                account.record_result(False, f"额度用完 HTTP {status_code}")
                if account.status in (Status.INVALID, Status.DISABLED):
                    store.update_account(account)
                else:
                    _mark(account, Status.EXHAUSTED, "额度已用完")
                    _spawn_bg(_safe_refresh(account))
                logs.warn(req_id, f"账号 {account.name} 额度用完，切换下一个")
                return next_account("quota_exhausted")

            if status_code == 401:
                account.record_result(False, "鉴权失败 HTTP 401")
                _mark(account, Status.INVALID, "鉴权失败 HTTP 401")
                if needs_captcha and account.has_apikey_fallback():
                    logs.warn(req_id, f"账号 {account.name} 鉴权失败 401，切 API Key 回退")
                    needs_captcha = False
                    force_fallback = True
                    continue
                logs.warn(req_id, f"账号 {account.name} 鉴权失败 401，切换下一个")
                return next_account("credential_invalid")

            if status_code == 403:
                # 403 已排除挑战形态（上方 challenge 分支），此处为真实鉴权拒绝
                account.record_result(False, "鉴权失败 HTTP 403")
                _mark(account, Status.INVALID, "鉴权失败 HTTP 403")
                if needs_captcha and account.has_apikey_fallback():
                    logs.warn(req_id, f"账号 {account.name} 鉴权失败 403，切 API Key 回退")
                    needs_captcha = False
                    force_fallback = True
                    continue
                logs.warn(req_id, f"账号 {account.name} 鉴权失败 403，切换下一个")
                return next_account("credential_invalid")

            if status_code == 429:
                reason_code = ("upstream_concurrency_limit" if is_concurrency_limit else
                               "upstream_rate_limited" if business_code in constants.RATE_LIMIT_BUSINESS_CODES
                               else "upstream_429_unknown")
                # 并发上限类错误码（3008/3009/3010）：立即走 API Key 回退或换下一个账号，不在原地干等
                if not is_concurrency_limit and retries_429 < settings.RETRY_429_TIMES:
                    retries_429 += 1
                    wait = _parse_retry_after(resp.headers.get("retry-after")) or settings.RETRY_429_WAIT
                    logs.warn(
                        req_id,
                        f"账号 {account.name} 被限流 429，{wait}s 后重试"
                        f"（{retries_429}/{settings.RETRY_429_TIMES}）",
                    )
                    _park_slot(slot_box)
                    remember(reason_code)
                    await _sleep(wait)
                    if not _reacquire_slot(account, slot_box):
                        logs.warn(req_id, f"账号 {account.name} 429 等待后并发已满，切换下一个")
                        return next_account("local_concurrency_limit", "local_scheduler", "retry_reacquire")
                    continue
                if needs_captcha and account.has_apikey_fallback():
                    account.record_result(False, "Plan 通道 429 耗尽，切 API Key 回退")
                    logs.warn(req_id, f"账号 {account.name} Plan 通道 429 耗尽，切 API Key 回退")
                    needs_captcha = False
                    force_fallback = True
                    retries_429 = 0
                    continue
                reason_msg = "并发上限 429，立即切换下一个" if is_concurrency_limit else (
                    f"429 重试 {settings.RETRY_429_TIMES} 次耗尽"
                )
                account.record_result(False, reason_msg)
                store.update_account(account)
                logs.warn(
                    req_id,
                    f"账号 {account.name} {reason_msg}，切换下一个（账号保持可用）",
                )
                return next_account(reason_code)

            if status_code >= 500:
                # 一般性上游错误：重试，耗尽才冷却账号并换号
                if retries_5xx < settings.RETRY_5XX_TIMES:
                    retries_5xx += 1
                    logs.warn(
                        req_id,
                        f"账号 {account.name} 上游 HTTP {status_code}，"
                        f"{settings.RETRY_5XX_WAIT}s 后重试（{retries_5xx}/{settings.RETRY_5XX_TIMES}）",
                    )
                    _park_slot(slot_box)
                    remember("upstream_server_error")
                    await _sleep(settings.RETRY_5XX_WAIT)
                    if not _reacquire_slot(account, slot_box):
                        logs.warn(req_id, f"账号 {account.name} 5xx 等待后并发已满，切换下一个")
                        return next_account("local_concurrency_limit", "local_scheduler", "retry_reacquire")
                    continue
                account.record_result(False, f"HTTP {status_code} 重试 {settings.RETRY_5XX_TIMES} 次耗尽，冷却")
                if account.status in (Status.INVALID, Status.DISABLED):
                    # Key 回退 5xx 不得覆盖废 JWT / 风控禁用，否则冷却结束会重开 Plan
                    store.update_account(account)
                    logs.warn(req_id, f"账号 {account.name} 上游 {status_code} 重试耗尽，Plan 已停用，切换下一个")
                else:
                    cool = settings.COOLING_SECONDS
                    account.status = Status.COOLING
                    account.cooling_until = time.time() + cool
                    account.last_error = f"上游 HTTP {status_code} 重试 {settings.RETRY_5XX_TIMES} 次耗尽，冷却"
                    store.update_account(account)
                    logs.warn(req_id, f"账号 {account.name} 上游 {status_code} 重试耗尽，冷却 {cool}s，切换下一个")
                return next_account("upstream_server_error")

            # 历史 assistant 消息 thinking 签名/格式不兼容（400）：自动剥离历史 thinking 块重试一次
            if not retried_thinking_strip and _is_thinking_signature_rejection(status_code, text):
                repaired = _strip_thinking_from_body(attempt_body)
                if repaired is not None:
                    retried_thinking_strip = True
                    attempt_body = repaired
                    logs.warn(req_id, f"账号 {account.name} 上游拒绝历史 thinking 签名，剥离历史 thinking 块后原地重试")
                    continue

            # 其它 4xx：直接回传客户端；响应体全量落日志供排查
            # （错误 JSON 通常很小；防御性上限 4KB，超长按 HTML 类 WAF 页处理只留头部）
            account.fail_count += 1
            account.record_result(False, f"HTTP {status_code}: {text[:120]}".replace("\n", " "))
            store.update_account(account)
            logs.req_err(req_id, f"上游错误 HTTP {status_code}（账号 {account.name}）")
            body_log = text if len(text) <= 4000 else text[:4000] + f"...(共 {len(text)} 字节，疑似 WAF 页)"
            logs.warn(req_id, f"上游 {status_code} 完整响应体: {body_log}")
            reqlog.finish_error(req_id, f"HTTP {status_code}: {text[:120]}".replace("\n", " "),
                                status=status_code, t_first=time.time() - attempt_t0)
            return JSONResponse(
                _safe_json(text) or {"error": {"message": text[:500], "type": "upstream_error"}},
                status_code=status_code,
            )

        # 成功：记录用量并把打开的上游流交给调用方
        account.use_count += 1
        account.last_used_at = time.time()
        is_stream = bool(body.get("stream")) and "event-stream" in content_type
        if not is_stream:
            account.record_result(True, f"HTTP 200 · {model_name} · {time.time() - attempt_t0:.1f}s")
        # API Key 回退成功不得把废 JWT / 风控禁用洗成 active，也不得清风控计数
        if account.status not in (Status.INVALID, Status.DISABLED):
            account.risk_strikes = 0
            account.last_error = None
            account.cooling_until = None
            if account.status in (Status.COOLING, Status.EXHAUSTED):
                account.status = Status.ACTIVE
        store.update_account(account)
        _spawn_bg(_safe_refresh(account))

        return _Upstream(resp, cm, client, t_first=time.time() - attempt_t0,
                          account_name=account.name, mode=account.mode,
                          preloaded_bytes=preloaded_bytes, cm_closed=cm_closed,
                          on_result=_make_result_recorder(account, credential) if is_stream else None)


def _safe_json(text: str):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


async def _safe_refresh(account: Account) -> None:
    try:
        live = store.find(account.provider, account.id)
        if live is None:
            return
        if live.provider == "zai" and live.allows_billing():
            # 去抖：每条消息都刷 billing 是流量放大器（会加剧风控），与 monitor 共享
            # last_checked_at，最小间隔内的刷新直接跳过
            last = live.last_checked_at
            if last and time.time() - last < settings.BILLING_REFRESH_MIN_INTERVAL:
                return
            await fetch_quota(live)
    except Exception:  # noqa: BLE001
        pass
