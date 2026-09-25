"""请求体变换 —— 对齐官方 ZCode 客户端与 zapi body-transformer.ts 在 anthropic 通道上的变换集。

coding-plan 通道（本服务 JWT 通道）应用三项：
  1. system 身份块：前置 ZCode 官方 system 块（CLI Prefix / Agent Identity /
     Environment Info）+ 动态 currentModel 块。网关做内容审查，请求缺这些
     身份块会被拒为 3012 "method not allowed"（镜像 zapi buildStartPlanSystem）。
  2. cache_control：最后一条非 system 消息的最后一个 content block 追加
     `cache_control: {"type": "ephemeral"}`（镜像 ZCode bundle 的 HLr，
     "finalizeLatestNonSystemCacheControl"）。Anthropic API 对低于缓存门槛的
     请求静默忽略 cache_control，因此无条件追加是安全的。
  3. metadata.user_id：对齐官方 ZCode 客户端 anthropic-request-metadata.ts 的
     JSON 字符串契约 `{"device_id":...,"account_uuid":"","session_id":...}`。
     当提供 device_mid 时生成该 JSON 字符串（保留客户端传入的 session_id 或基于
     device_mid + user_id 派生确定性 UUID，避免空串导致跨会话缓存碰撞）；
     未提供 device_mid 时兼容旧接口直接写入 user_id。

所有变换对畸形输入保持 no-op：解析失败返回原样，坏 body 永远不会被这里放大。
"""

from __future__ import annotations

import base64
import copy
import json
import uuid
from pathlib import Path

# ── ZCode 官方 system 身份块（对齐 zapi zcode_system.json，从官方客户端 bundle 提取）──
_SYSTEM_JSON = Path(__file__).with_name("zcode_system.json")
try:
    _ZCODE_SYSTEM_BLOCKS: list[dict] = json.loads(_SYSTEM_JSON.read_text("utf-8"))
except (OSError, ValueError):
    _ZCODE_SYSTEM_BLOCKS = []


def _is_plain_dict(v: object) -> bool:
    return isinstance(v, dict)


def _normalize_user_system(system: object) -> list[dict]:
    """把客户端原有 system 归一为 text block 列表（镜像 zapi normalizeUserSystem）。"""
    if system is None:
        return []
    if isinstance(system, str):
        text = system.strip()
        return [{"type": "text", "text": system}] if text else []
    if not isinstance(system, list):
        return []
    out: list[dict] = []
    for item in system:
        if isinstance(item, str):
            if item.strip():
                out.append({"type": "text", "text": item})
        elif isinstance(item, dict):
            if item.get("type") == "text" and isinstance(item.get("text"), str) and item["text"].strip():
                block = {"type": "text", "text": item["text"]}
                cc = item.get("cache_control")
                if isinstance(cc, dict):
                    block["cache_control"] = cc
                out.append(block)
    return out


def apply_start_plan_system(body: dict, model: str | None = None) -> bool:
    """前置 ZCode 官方 system 身份块 + 动态 currentModel 块。幂等（已前置则跳过）。

    网关内容审查要求 system 含官方身份块，否则 3012。保留客户端原有 system 于官方块之后。
    """
    if not _ZCODE_SYSTEM_BLOCKS:
        return False
    existing = body.get("system")
    # 幂等：首块已是官方标识则视为已注入
    if isinstance(existing, list) and existing:
        first = existing[0]
        if isinstance(first, dict) and first.get("text") == _ZCODE_SYSTEM_BLOCKS[0].get("text"):
            return False
    official = [copy.deepcopy(b) for b in _ZCODE_SYSTEM_BLOCKS]
    if isinstance(model, str) and model.strip():
        official.append({
            "type": "text",
            "text": f"- You are powered by the model named {model}.",
            "cache_control": {"type": "ephemeral"},
        })
    body["system"] = official + _normalize_user_system(existing)
    return True


def apply_cache_control(body: dict) -> bool:
    """最后一条非 system 消息的最后一个 block 加 ephemeral 缓存标记。幂等。"""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return False

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "system":
            continue

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }]
            return True
        if isinstance(content, list) and content:
            last_block = content[-1]
            if isinstance(last_block, dict) and not last_block.get("cache_control"):
                last_block["cache_control"] = {"type": "ephemeral"}
                return True
        return False
    return False


def _extract_conversation_seed(body: dict | None) -> str:
    """取首条非 system 消息前 512 字符作会话种子（同对话多轮恒定，跨对话隔离）。"""
    if not _is_plain_dict(body):
        return ""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") == "system":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:512]
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"].strip():
                    parts.append(block["text"].strip())
                elif isinstance(block, str) and block.strip():
                    parts.append(block.strip())
            if parts:
                return " ".join(parts)[:512]
    return ""


def format_metadata_user_id(
    device_mid: str | None,
    user_id: str | None = None,
    existing_metadata: dict | None = None,
    conversation_seed: str | None = None,
) -> str | None:
    """生成官方 ZCode 客户端契约的 metadata.user_id JSON 字符串。

    对齐 ZCode src/main/agent/runtime/anthropic-request-metadata.ts：
    `{"device_id": "<X-Device-Mid>", "account_uuid": "", "session_id": "<sessionId>"}`
    """
    eff_device = (device_mid or "").strip()
    if not eff_device:
        return user_id

    eff_session = ""
    if _is_plain_dict(existing_metadata):
        raw_sid = existing_metadata.get("session_id")
        if isinstance(raw_sid, str) and raw_sid.strip():
            eff_session = raw_sid.strip()
        else:
            raw_uid = existing_metadata.get("user_id")
            if isinstance(raw_uid, str) and raw_uid.strip():
                if raw_uid.strip().startswith("{"):
                    try:
                        parsed = json.loads(raw_uid)
                        if isinstance(parsed, dict) and isinstance(parsed.get("session_id"), str):
                            eff_session = parsed["session_id"].strip()
                    except (ValueError, TypeError):
                        pass
                else:
                    eff_session = raw_uid.strip()

    if not eff_session:
        seed_suffix = f":{conversation_seed.strip()}" if isinstance(conversation_seed, str) and conversation_seed.strip() else ""
        eff_session = str(uuid.uuid5(uuid.NAMESPACE_OID, f"{eff_device}:{user_id or 'anon'}{seed_suffix}"))

    return json.dumps(
        {
            "device_id": eff_device,
            "account_uuid": "",
            "session_id": eff_session,
        },
        separators=(",", ":"),
    )


def apply_user_id(body: dict, user_id: str) -> bool:
    """注入 metadata.user_id（保留已有 metadata 其它合法字段）。幂等。"""
    existing = body.get("metadata")
    if _is_plain_dict(existing) and existing.get("user_id") == user_id and "session_id" not in existing:
        return False
    merged = dict(existing) if _is_plain_dict(existing) else {}
    merged.pop("session_id", None)  # 清理内部过渡字段，避免发往 Anthropic 触发 schema 报错
    merged["user_id"] = user_id
    body["metadata"] = merged
    return True


def jwt_user_id(jwt_token: str | None) -> str | None:
    """从 JWT payload 解 user_id（sub / user_id 字段），失败返回 None。

    JWT 是账号凭证（短期有效），user_id 跟随 token 变化，因此不跨进程缓存。
    """
    if not jwt_token or jwt_token.count(".") != 2:
        return None
    try:
        payload_b64 = jwt_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    user_id = payload.get("user_id") or payload.get("sub")
    return str(user_id) if user_id else None


def transform_body(
    body: dict,
    user_id: str | None = None,
    model: str | None = None,
    device_mid: str | None = None,
) -> dict:
    """按 anthropic 通道变换 body（原地修改并返回）。变换失败静默保持原样。"""
    try:
        apply_start_plan_system(body, model)
        apply_cache_control(body)
        existing_meta = body.get("metadata") if _is_plain_dict(body.get("metadata")) else None
        conv_seed = _extract_conversation_seed(body)
        target_uid = format_metadata_user_id(device_mid, user_id, existing_meta, conv_seed) if device_mid else user_id
        if target_uid:
            apply_user_id(body, target_uid)
    except Exception:  # noqa: BLE001 - 变换永不放大请求失败
        pass
    return body
