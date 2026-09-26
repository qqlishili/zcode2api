"""OpenAI Responses API 兼容层 —— /v1/responses ↔ Anthropic Messages 双向直转。

设计原则（对齐 router-for-me/CLIProxyAPI 与 openai/codex codex-rs 状态机契约）：
- 归一化（Canonical IR）：以 Anthropic Messages 请求体与 SSE 事件流为唯一内部表示，
  不绕道 Chat Completions 两段跳，避免多轮混合块（reasoning + message + function_call）有损降级。
- 入站：Responses input（str | 多态 Item 数组）→ Anthropic messages（同角色相邻块自动归并、
  tool_result 置顶、encrypted_content ↔ signature 透明回显、空 content 清洗二次归并、
  tools parameters → input_schema 映射）。
- 出站：Anthropic 响应（JSON 或 SSE 事件流）→ OpenAI Responses 格式（单调递增 sequence_number、
  未闭合块自动收口、流异常补发 response.failed 终态帧防客户端挂起）。
"""

from __future__ import annotations

import json
import time
import uuid

from app.openai_compat import _apply_reasoning_params, _as_int, _image_block


def _text_from_response_part(content: object) -> str:
    """从 Responses message.content 或 summary 数组中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str) and item:
                parts.append(item)
            elif isinstance(item, dict):
                ptype = item.get("type")
                if ptype in ("text", "input_text", "output_text", "summary_text") and isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return "\n".join(p for p in parts if p)
    return ""


def _blocks_from_message_content(content: object, role: str) -> list[dict]:
    """Responses message item 的 content → Anthropic content blocks。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return []
    blocks: list[dict] = []
    for part in content:
        if isinstance(part, str):
            if part:
                blocks.append({"type": "text", "text": part})
            continue
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype in ("text", "input_text", "output_text") and isinstance(part.get("text"), str):
            if part["text"]:
                blocks.append({"type": "text", "text": part["text"]})
        elif role == "user" and ptype in ("input_image", "image_url"):
            raw_url = part.get("image_url")
            if isinstance(raw_url, dict):
                raw_url = raw_url.get("url") or ""
            block = _image_block(str(raw_url or ""))
            if block:
                blocks.append(block)
    return blocks


def _stringify_tool_output(output: object) -> str:
    """将 function_call_output.output 归一化为字符串。"""
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    if isinstance(output, list):
        text = _text_from_response_part(output)
        if text:
            return text
    try:
        return json.dumps(output, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(output)


def _coalesce_non_empty_messages(raw_msgs: list[dict]) -> list[dict]:
    """剔除空 content 消息并对相邻同角色消息做二次合并（保证 tool_result 位于 user 块头部）。"""
    cleaned: list[dict] = []
    for msg in raw_msgs:
        role = msg.get("role")
        blocks = [b for b in (msg.get("content") or []) if isinstance(b, dict)]
        if not blocks or role not in ("user", "assistant"):
            continue
        if cleaned and cleaned[-1]["role"] == role:
            if role == "user":
                prev = cleaned[-1]["content"]
                tool_results = [b for b in prev if b.get("type") == "tool_result"] + [
                    b for b in blocks if b.get("type") == "tool_result"
                ]
                others = [b for b in prev if b.get("type") != "tool_result"] + [
                    b for b in blocks if b.get("type") != "tool_result"
                ]
                cleaned[-1]["content"] = tool_results + others
            else:
                cleaned[-1]["content"].extend(blocks)
        else:
            if role == "user":
                tool_results = [b for b in blocks if b.get("type") == "tool_result"]
                others = [b for b in blocks if b.get("type") != "tool_result"]
                blocks = tool_results + others
            cleaned.append({"role": role, "content": blocks})
    return cleaned


def responses_to_anthropic(payload: dict) -> tuple[dict | None, str | None]:
    """OpenAI Responses 请求体 → Anthropic messages 体。非法时返回 (None, 错误信息)。"""
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        return None, "必须提供 model 参数"

    raw_input = payload.get("input")
    if raw_input is None or raw_input == "" or raw_input == []:
        if payload.get("previous_response_id"):
            return None, "网关为无状态模式，不支持仅传 previous_response_id，请提供完整 input"
        return None, "必须提供非空 input 参数"

    if isinstance(raw_input, str):
        items: list[object] = [{"type": "message", "role": "user", "content": raw_input}]
    elif isinstance(raw_input, list):
        items = raw_input
    else:
        return None, "input 必须是字符串或数组"

    system_parts: list[str] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        system_parts.append(instructions.strip())

    raw_msgs: list[dict] = []

    def _append(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if raw_msgs and raw_msgs[-1]["role"] == role:
            raw_msgs[-1]["content"].extend(blocks)
        else:
            raw_msgs.append({"role": role, "content": list(blocks)})

    for item in items:
        if isinstance(item, str):
            if item:
                _append("user", [{"type": "text", "text": item}])
            continue
        if not isinstance(item, dict):
            continue

        itype = str(item.get("type") or "").strip().lower()
        if itype == "message" or (not itype and "role" in item):
            role = str(item.get("role") or "user").strip().lower()
            content = item.get("content")
            if role in ("system", "developer"):
                text = _text_from_response_part(content)
                if text:
                    system_parts.append(text)
            elif role == "assistant":
                _append("assistant", _blocks_from_message_content(content, "assistant"))
            else:
                _append("user", _blocks_from_message_content(content, "user"))

        elif itype == "reasoning":
            # 仅当客户端回传了非空 encrypted_content（上游 signature）时还原 thinking 块；
            # 无签名历史 reasoning 块静默跳过，避免触发 Anthropic 400 签名校验拒绝
            sig = item.get("encrypted_content")
            thinking_text = _text_from_response_part(item.get("summary") or item.get("content"))
            if isinstance(sig, str) and sig.strip() and thinking_text:
                _append("assistant", [{
                    "type": "thinking",
                    "thinking": thinking_text,
                    "signature": sig.strip(),
                }])

        elif itype in ("function_call", "custom_tool_call"):
            call_id = str(item.get("call_id") or item.get("id") or "").strip() or f"call_{uuid.uuid4().hex[:12]}"
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if itype == "custom_tool_call":
                raw_in = item.get("input")
                if isinstance(raw_in, str):
                    try:
                        parsed_in = json.loads(raw_in) if raw_in.strip() else {}
                        args_obj = parsed_in if isinstance(parsed_in, dict) else {"input": raw_in}
                    except ValueError:
                        args_obj = {"input": raw_in}
                elif isinstance(raw_in, dict):
                    args_obj = raw_in
                else:
                    args_obj = {}
            else:
                raw_args = item.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args) if raw_args.strip() else {}
                        args_obj = parsed if isinstance(parsed, dict) else {"_raw": raw_args}
                    except ValueError:
                        args_obj = {"_raw": raw_args}
                elif isinstance(raw_args, dict):
                    args_obj = raw_args
                else:
                    args_obj = {}
            _append("assistant", [{
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": args_obj,
            }])

        elif itype in ("function_call_output", "custom_tool_call_output"):
            call_id = str(item.get("call_id") or item.get("id") or "").strip()
            if not call_id:
                continue
            tr_block: dict = {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": _stringify_tool_output(item.get("output")),
            }
            if item.get("status") in ("failed", "incomplete") or item.get("is_error") is True:
                tr_block["is_error"] = True
            _append("user", [tr_block])

        else:
            # Postel 法则：未知 item 类型尝试提取文本降级为 user 块，否则安静跳过
            fallback_text = _text_from_response_part(item.get("content") or item.get("text"))
            if fallback_text:
                _append("user", [{"type": "text", "text": fallback_text}])

    out_msgs = _coalesce_non_empty_messages(raw_msgs)
    if not out_msgs:
        return None, "input 未包含有效消息内容"

    max_tok = _as_int(
        payload.get("max_output_tokens")
        or payload.get("max_tokens")
        or payload.get("max_completion_tokens")
    ) or 4096

    body: dict = {
        "model": model,
        "messages": out_msgs,
        "max_tokens": max_tok,
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)

    try:
        if payload.get("temperature") is not None:
            body["temperature"] = float(payload["temperature"])
        if payload.get("top_p") is not None:
            body["top_p"] = float(payload["top_p"])
    except (TypeError, ValueError):
        pass

    if payload.get("stream"):
        body["stream"] = True

    _apply_reasoning_params(payload, body)

    # 会话亲和：优先取 prompt_cache_key / conversation / previous_response_id / user
    for sk in ("prompt_cache_key", "conversation", "previous_response_id", "user"):
        sval = payload.get(sk)
        if isinstance(sval, dict):
            sval = sval.get("id")
        if isinstance(sval, str) and sval.strip():
            meta = dict(body.get("metadata")) if isinstance(body.get("metadata"), dict) else {}
            meta["session_id"] = sval.strip()
            body["metadata"] = meta
            break

    # 工具定义映射（兼容 Responses 扁平格式与 Chat Completions 嵌套 function 格式，剥离 strict 等非标字段）
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        mapped: list[dict] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            ttype = str(t.get("type") or "function").strip().lower()
            if ttype != "function":
                continue
            spec = t.get("function") if isinstance(t.get("function"), dict) else t
            name = str(spec.get("name") or "").strip()
            if not name:
                continue
            schema = spec.get("parameters") or spec.get("input_schema")
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            mapped.append({
                "name": name,
                "description": str(spec.get("description") or ""),
                "input_schema": schema,
            })
        if mapped:
            body["tools"] = mapped

    choice = payload.get("tool_choice")
    if choice == "none":
        body.pop("tools", None)
    elif choice == "auto":
        body["tool_choice"] = {"type": "auto"}
    elif choice == "required":
        body["tool_choice"] = {"type": "any"}
    elif isinstance(choice, dict) and choice.get("type") == "function":
        name = str(choice.get("name") or (choice.get("function") or {}).get("name") or "").strip()
        if name:
            body["tool_choice"] = {"type": "tool", "name": name}

    return body, None


def _build_usage(usage: dict | None) -> dict:
    u = usage or {}
    in_tok = _as_int(u.get("input_tokens")) or 0
    out_tok = _as_int(u.get("output_tokens")) or 0
    cached = _as_int(u.get("cache_read_input_tokens")) or 0
    return {
        "input_tokens": in_tok,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": out_tok,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": in_tok + out_tok,
    }


def anthropic_to_responses(data: dict, model: str, effort: str | None = None) -> dict:
    """Anthropic message 响应 → OpenAI Responses 对象（顶层平铺 output items）。"""
    resp_id = str(data.get("id") or f"resp_{uuid.uuid4().hex[:24]}")
    created_at = int(time.time())
    output: list[dict] = []
    text_parts: list[str] = []

    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "thinking":
            t_text = str(block.get("thinking") or "")
            sig = str(block.get("signature") or "")
            output.append({
                "id": f"rs_{resp_id}_{len(output)}",
                "type": "reasoning",
                "status": "completed",
                "encrypted_content": sig,
                "summary": [{"type": "summary_text", "text": t_text}] if t_text else [],
            })
        elif btype == "text" and isinstance(block.get("text"), str):
            text = block["text"]
            text_parts.append(text)
            if output and output[-1].get("type") == "message":
                output[-1]["content"].append({
                    "type": "output_text",
                    "text": text,
                    "annotations": [],
                    "logprobs": [],
                })
            else:
                output.append({
                    "id": f"msg_{resp_id}_{len(output)}",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                        "logprobs": [],
                    }],
                })
        elif btype == "tool_use":
            call_id = str(block.get("id") or f"call_{uuid.uuid4().hex[:12]}")
            output.append({
                "id": f"fc_{call_id}",
                "type": "function_call",
                "call_id": call_id,
                "name": str(block.get("name") or ""),
                "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                "status": "completed",
            })

    stop_reason = data.get("stop_reason")
    is_incomplete = stop_reason == "max_tokens"
    res: dict = {
        "id": resp_id,
        "object": "response",
        "created_at": created_at,
        "status": "incomplete" if is_incomplete else "completed",
        "background": False,
        "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if is_incomplete else None,
        "model": str(data.get("model") or model),
        "output": output,
        "output_text": "".join(text_parts),
        "usage": _build_usage(data.get("usage")),
    }
    if effort:
        res["reasoning"] = {"effort": effort}
    return res


class ResponsesStreamConverter:
    """Anthropic SSE 事件流 → OpenAI Responses SSE 事件流（有状态转换器）。

    严格遵循 codex-rs/CLIProxyAPI 状态机契约：
    - 所有事件通过 _emit 统一分配单调递增的 sequence_number（从 1 起）
    - 每个 content_block 严格配对 output_item.added → delta → done → output_item.done
    - 终态必定输出 response.completed / response.incomplete 或异常时的 response.failed
    """

    def __init__(self, model: str, effort: str | None = None) -> None:
        self.model = model
        self.effort = effort
        self.response_id = f"resp_{uuid.uuid4().hex[:24]}"
        self.created_at = int(time.time())
        self.stop_reason: str | None = None
        self.usage: dict[str, int | None] = {
            "input_tokens": None,
            "output_tokens": None,
            "cached_tokens": 0,
        }
        self._seq = 0
        self._started = False
        self._finished = False
        self._output_items: list[dict] = []
        self._active_block: dict | None = None

    def _emit(self, event_type: str, payload: dict) -> str:
        self._seq += 1
        data = {"type": event_type, "sequence_number": self._seq, **payload}
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def start(self) -> list[str]:
        """流式启动握手（幂等）：立即发送 response.created 与 response.in_progress，
        促使 FastAPI 立即向客户端提交 HTTP 200 Headers，消除首字节静默真空。
        """
        return self._ensure_started()

    def _ensure_started(self) -> list[str]:
        if self._started:
            return []
        self._started = True
        base_resp = {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": "in_progress",
            "background": False,
            "error": None,
            "output": [],
            "model": self.model,
        }
        return [
            self._emit("response.created", {"response": base_resp}),
            self._emit("response.in_progress", {"response": {
                "id": self.response_id,
                "object": "response",
                "created_at": self.created_at,
                "status": "in_progress",
                "output": [],
                "model": self.model,
            }}),
        ]

    def _close_active_block(self) -> list[str]:
        block = self._active_block
        if block is None:
            return []
        self._active_block = None
        outs: list[str] = []
        btype = block["type"]
        out_idx = block["output_index"]
        item_id = block["item_id"]

        if btype == "thinking":
            text = "".join(block["buffer"])
            sig = block["signature"]
            outs.append(self._emit("response.reasoning_summary_text.done", {
                "item_id": item_id,
                "output_index": out_idx,
                "summary_index": 0,
                "text": text,
            }))
            outs.append(self._emit("response.reasoning_summary_part.done", {
                "item_id": item_id,
                "output_index": out_idx,
                "summary_index": 0,
                "part": {"type": "summary_text", "text": text},
            }))
            done_item = {
                "id": item_id,
                "type": "reasoning",
                "status": "completed",
                "encrypted_content": sig,
                "summary": [{"type": "summary_text", "text": text}],
            }
            outs.append(self._emit("response.output_item.done", {
                "output_index": out_idx,
                "item": done_item,
            }))
            self._output_items.append(done_item)

        elif btype == "text":
            text = "".join(block["buffer"])
            outs.append(self._emit("response.output_text.done", {
                "item_id": item_id,
                "output_index": out_idx,
                "content_index": 0,
                "text": text,
                "logprobs": [],
            }))
            part = {"type": "output_text", "annotations": [], "logprobs": [], "text": text}
            outs.append(self._emit("response.content_part.done", {
                "item_id": item_id,
                "output_index": out_idx,
                "content_index": 0,
                "part": part,
            }))
            done_item = {
                "id": item_id,
                "type": "message",
                "status": "completed",
                "content": [part],
                "role": "assistant",
            }
            outs.append(self._emit("response.output_item.done", {
                "output_index": out_idx,
                "item": done_item,
            }))
            self._output_items.append(done_item)

        elif btype == "tool_use":
            args_str = "".join(block["buffer"])
            call_id = block["call_id"]
            name = block["name"]
            outs.append(self._emit("response.function_call_arguments.done", {
                "item_id": item_id,
                "output_index": out_idx,
                "call_id": call_id,
                "name": name,
                "arguments": args_str,
            }))
            done_item = {
                "id": item_id,
                "type": "function_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
                "arguments": args_str,
            }
            outs.append(self._emit("response.output_item.done", {
                "output_index": out_idx,
                "item": done_item,
            }))
            self._output_items.append(done_item)

        return outs

    def feed(self, evt: dict) -> list[str]:
        etype = evt.get("type")
        outs: list[str] = []

        if etype == "message_start":
            msg = evt.get("message") or {}
            if msg.get("id") and not self._started:
                self.response_id = str(msg["id"])
            u = msg.get("usage") or {}
            in_tok = _as_int(u.get("input_tokens"))
            if in_tok is not None:
                self.usage["input_tokens"] = in_tok
            cached = _as_int(u.get("cache_read_input_tokens"))
            if cached is not None:
                self.usage["cached_tokens"] = cached
            return self._ensure_started()

        if etype == "content_block_start":
            outs.extend(self._ensure_started())
            outs.extend(self._close_active_block())
            cb = evt.get("content_block") or {}
            btype = cb.get("type")
            out_idx = len(self._output_items)

            if btype == "thinking":
                item_id = f"rs_{self.response_id}_{out_idx}"
                sig = str(cb.get("signature") or "")
                self._active_block = {
                    "type": "thinking",
                    "output_index": out_idx,
                    "item_id": item_id,
                    "signature": sig,
                    "buffer": [],
                }
                outs.append(self._emit("response.output_item.added", {
                    "output_index": out_idx,
                    "item": {
                        "id": item_id,
                        "type": "reasoning",
                        "status": "in_progress",
                        "encrypted_content": sig,
                        "summary": [],
                    },
                }))
                outs.append(self._emit("response.reasoning_summary_part.added", {
                    "item_id": item_id,
                    "output_index": out_idx,
                    "summary_index": 0,
                    "part": {"type": "summary_text", "text": ""},
                }))

            elif btype == "text":
                item_id = f"msg_{self.response_id}_{out_idx}"
                self._active_block = {
                    "type": "text",
                    "output_index": out_idx,
                    "item_id": item_id,
                    "buffer": [],
                }
                outs.append(self._emit("response.output_item.added", {
                    "output_index": out_idx,
                    "item": {
                        "id": item_id,
                        "type": "message",
                        "status": "in_progress",
                        "content": [],
                        "role": "assistant",
                    },
                }))
                outs.append(self._emit("response.content_part.added", {
                    "item_id": item_id,
                    "output_index": out_idx,
                    "content_index": 0,
                    "part": {"type": "output_text", "annotations": [], "logprobs": [], "text": ""},
                }))

            elif btype == "tool_use":
                call_id = str(cb.get("id") or f"call_{uuid.uuid4().hex[:12]}")
                name = str(cb.get("name") or "")
                item_id = f"fc_{call_id}"
                self._active_block = {
                    "type": "tool_use",
                    "output_index": out_idx,
                    "item_id": item_id,
                    "call_id": call_id,
                    "name": name,
                    "buffer": [],
                }
                outs.append(self._emit("response.output_item.added", {
                    "output_index": out_idx,
                    "item": {
                        "id": item_id,
                        "type": "function_call",
                        "status": "in_progress",
                        "call_id": call_id,
                        "name": name,
                        "arguments": "",
                    },
                }))
            return outs

        if etype == "content_block_delta":
            outs.extend(self._ensure_started())
            delta = evt.get("delta") or {}
            dtype = delta.get("type")

            if dtype == "thinking_delta" and isinstance(delta.get("thinking"), str):
                if self._active_block is None or self._active_block["type"] != "thinking":
                    outs.extend(self.feed({"type": "content_block_start", "content_block": {"type": "thinking"}}))
                chunk = delta["thinking"]
                self._active_block["buffer"].append(chunk)
                outs.append(self._emit("response.reasoning_summary_text.delta", {
                    "item_id": self._active_block["item_id"],
                    "output_index": self._active_block["output_index"],
                    "summary_index": 0,
                    "delta": chunk,
                }))
            elif dtype == "signature_delta" and isinstance(delta.get("signature"), str):
                if self._active_block and self._active_block["type"] == "thinking":
                    self._active_block["signature"] += delta["signature"]
            elif dtype == "text_delta" and isinstance(delta.get("text"), str):
                if self._active_block is None or self._active_block["type"] != "text":
                    outs.extend(self.feed({"type": "content_block_start", "content_block": {"type": "text"}}))
                chunk = delta["text"]
                self._active_block["buffer"].append(chunk)
                outs.append(self._emit("response.output_text.delta", {
                    "item_id": self._active_block["item_id"],
                    "output_index": self._active_block["output_index"],
                    "content_index": 0,
                    "delta": chunk,
                    "logprobs": [],
                }))
            elif dtype == "input_json_delta" and isinstance(delta.get("partial_json"), str):
                if self._active_block and self._active_block["type"] == "tool_use":
                    chunk = delta["partial_json"]
                    self._active_block["buffer"].append(chunk)
                    outs.append(self._emit("response.function_call_arguments.delta", {
                        "item_id": self._active_block["item_id"],
                        "output_index": self._active_block["output_index"],
                        "call_id": self._active_block["call_id"],
                        "delta": chunk,
                    }))
            return outs

        if etype == "content_block_stop":
            return self._close_active_block()

        if etype == "message_delta":
            delta = evt.get("delta") or {}
            if delta.get("stop_reason"):
                self.stop_reason = str(delta["stop_reason"])
            out_tok = _as_int((evt.get("usage") or {}).get("output_tokens"))
            if out_tok is not None:
                self.usage["output_tokens"] = out_tok
            return []

        if etype == "message_stop":
            return self.done()

        return []

    def done(self) -> list[str]:
        """正常收口流（幂等）：闭合残余 block 并发送 response.completed / response.incomplete。"""
        if self._finished:
            return []
        outs = self._ensure_started()
        outs.extend(self._close_active_block())
        self._finished = True
        in_tok = self.usage["input_tokens"] or 0
        out_tok = self.usage["output_tokens"] or 0
        cached = self.usage["cached_tokens"] or 0
        is_incomplete = self.stop_reason == "max_tokens"
        terminal_type = "response.incomplete" if is_incomplete else "response.completed"
        final_resp: dict = {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": "incomplete" if is_incomplete else "completed",
            "background": False,
            "error": None,
            "incomplete_details": {"reason": "max_output_tokens"} if is_incomplete else None,
            "model": self.model,
            "output": self._output_items,
            "usage": {
                "input_tokens": in_tok,
                "input_tokens_details": {"cached_tokens": cached},
                "output_tokens": out_tok,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": in_tok + out_tok,
            },
        }
        if self.effort:
            final_resp["reasoning"] = {"effort": self.effort}
        outs.append(self._emit(terminal_type, {"response": final_resp}))
        return outs

    def fail(self, message: str, code: str = "upstream_error") -> list[str]:
        """异常收口流：补发 response.failed 终态帧，防止 codex-rs 客户端死锁挂起。"""
        if self._finished:
            return []
        outs = self._ensure_started()
        outs.extend(self._close_active_block())
        self._finished = True
        failed_resp = {
            "id": self.response_id,
            "object": "response",
            "created_at": self.created_at,
            "status": "failed",
            "background": False,
            "error": {"code": code, "message": message},
            "model": self.model,
            "output": self._output_items,
        }
        outs.append(self._emit("response.failed", {"response": failed_resp}))
        return outs

    @property
    def is_finished(self) -> bool:
        """流是否已生成终态事件（completed / incomplete / failed）。"""
        return self._finished
