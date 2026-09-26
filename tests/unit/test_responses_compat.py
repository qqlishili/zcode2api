"""responses_compat 单元测试 —— OpenAI Responses ↔ Anthropic 双向转换。"""

from __future__ import annotations

import json

from app.responses_compat import (
    ResponsesStreamConverter,
    anthropic_to_responses,
    responses_to_anthropic,
)


def _parse_responses_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    cur_evt = ""
    for line in raw.splitlines():
        if line.startswith("event: "):
            cur_evt = line[7:].strip()
        elif line.startswith("data: "):
            events.append((cur_evt, json.loads(line[6:])))
    return events


class TestResponsesToAnthropic:
    def test_string_input_and_instructions(self):
        body, err = responses_to_anthropic({
            "model": "GLM-5.3-Flash",
            "instructions": "你是代码助手",
            "input": "你好",
            "max_output_tokens": 2048,
        })
        assert err is None and body is not None
        assert body["model"] == "GLM-5.3-Flash"
        assert body["system"] == "你是代码助手"
        assert body["max_tokens"] == 2048
        assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "你好"}]}]

    def test_coalesce_assistant_reasoning_text_and_parallel_function_calls(self):
        """测试 P0/P1：同角色混合块合并、tool_result 置顶、encrypted_content 签名还原、tools parameters→input_schema。"""
        body, err = responses_to_anthropic({
            "model": "GLM-5.3-Flash",
            "input": [
                {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "遵守规范"}]},
                {"type": "message", "role": "user", "content": [
                    {"type": "input_text", "text": "查杭州和上海天气"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
                ]},
                # 无签名 reasoning 块应被静默剔除，不产生空 assistant 消息
                {"type": "reasoning", "encrypted_content": "", "summary": [{"type": "summary_text", "text": "无签名思考"}]},
                # 有签名 reasoning 块 + assistant message + 两个并行 function_call 应合并入同一条 assistant 消息
                {"type": "reasoning", "encrypted_content": "sig_abc123", "summary": [{"type": "summary_text", "text": "先并发查两个城市"}]},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "正在查询"}]},
                {"type": "function_call", "call_id": "call_hz", "name": "get_weather", "arguments": '{"city":"杭州"}'},
                {"type": "function_call", "call_id": "call_sh", "name": "get_weather", "arguments": '{"city":"上海"}'},
                # 后续 user 文本 + 两个 function_call_output 应合并入同一条 user 消息，且 tool_result 排在最前
                {"type": "message", "role": "user", "content": "请汇总"},
                {"type": "function_call_output", "call_id": "call_hz", "output": "晴 25°C"},
                {"type": "function_call_output", "call_id": "call_sh", "output": "多云 22°C", "status": "failed"},
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "get_weather",
                    "description": "查天气",
                    "strict": True,
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
                {"type": "web_search_preview"},
            ],
            "reasoning": {"effort": "max"},
            "prompt_cache_key": "codex-sess-001",
        })
        assert err is None and body is not None
        assert body["system"] == "遵守规范"
        assert body["thinking"] == {"type": "enabled"}
        assert body["output_config"] == {"effort": "max"}
        assert body["metadata"]["session_id"] == "codex-sess-001"

        # tools: parameters -> input_schema, 剥离 strict, 忽略 web_search_preview
        assert body["tools"] == [{
            "name": "get_weather",
            "description": "查天气",
            "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        }]

        assert len(body["messages"]) == 3
        u0, a1, u2 = body["messages"]
        assert u0["role"] == "user"
        assert u0["content"][0] == {"type": "text", "text": "查杭州和上海天气"}
        assert u0["content"][1]["type"] == "image"

        assert a1["role"] == "assistant"
        assert [b["type"] for b in a1["content"]] == ["thinking", "text", "tool_use", "tool_use"]
        assert a1["content"][0] == {"type": "thinking", "thinking": "先并发查两个城市", "signature": "sig_abc123"}
        assert a1["content"][2]["id"] == "call_hz" and a1["content"][2]["input"] == {"city": "杭州"}

        assert u2["role"] == "user"
        # tool_result 必须置顶在 user.content 头部
        assert [b["type"] for b in u2["content"]] == ["tool_result", "tool_result", "text"]
        assert u2["content"][0]["tool_use_id"] == "call_hz"
        assert u2["content"][1]["tool_use_id"] == "call_sh" and u2["content"][1]["is_error"] is True
        assert u2["content"][2] == {"type": "text", "text": "请汇总"}

    def test_empty_unsigned_reasoning_does_not_leave_empty_assistant_turn(self):
        """测试 P1：仅含无签名 reasoning 的历史轮次被剔除后，相邻 user 消息自动二次合并，不留空 content。"""
        body, err = responses_to_anthropic({
            "model": "GLM-5.3-Flash",
            "input": [
                {"type": "message", "role": "user", "content": "第一句"},
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "被中断的无签名思考"}]},
                {"type": "message", "role": "user", "content": "第二句"},
            ],
        })
        assert err is None and body is not None
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][0]["content"] == [
            {"type": "text", "text": "第一句"},
            {"type": "text", "text": "第二句"},
        ]

    def test_stateless_previous_response_id_without_input_rejected(self):
        body, err = responses_to_anthropic({
            "model": "GLM-5.3-Flash",
            "previous_response_id": "resp_123",
        })
        assert body is None and err is not None and "无状态" in err


class TestAnthropicToResponses:
    def test_non_streaming_flat_output_items(self):
        out = anthropic_to_responses({
            "id": "msg_01",
            "type": "message",
            "model": "GLM-5.3-Flash",
            "content": [
                {"type": "thinking", "thinking": "先思考", "signature": "sig_xyz"},
                {"type": "text", "text": "结果如下"},
                {"type": "tool_use", "id": "call_1", "name": "bash", "input": {"cmd": "ls"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 100, "output_tokens": 25, "cache_read_input_tokens": 60},
        }, "GLM-5.3-Flash", effort="max")
        assert out["object"] == "response"
        assert out["status"] == "completed"
        assert out["reasoning"] == {"effort": "max"}
        assert out["output_text"] == "结果如下"
        assert [item["type"] for item in out["output"]] == ["reasoning", "message", "function_call"]
        assert out["output"][0]["encrypted_content"] == "sig_xyz"
        assert out["output"][2]["call_id"] == "call_1"
        assert out["output"][2]["arguments"] == '{"cmd": "ls"}'
        assert out["usage"]["input_tokens"] == 100
        assert out["usage"]["input_tokens_details"]["cached_tokens"] == 60
        assert out["usage"]["total_tokens"] == 125


class TestResponsesStreamConverter:
    def test_full_reasoning_text_and_tool_stream(self):
        conv = ResponsesStreamConverter("GLM-5.3-Flash", effort="high")
        raw_chunks: list[str] = []
        anthropic_events = [
            {"type": "message_start", "message": {"id": "msg_s1", "usage": {"input_tokens": 50, "cache_read_input_tokens": 20}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "思考中"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig_stream_1"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "好的"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "call_9", "name": "run"}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"x":1}'}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 15}},
            {"type": "message_stop"},
        ]
        for evt in anthropic_events:
            raw_chunks.extend(conv.feed(evt))
        raw_chunks.extend(conv.done())  # 幂等，不会重复发 response.completed

        parsed = _parse_responses_sse("".join(raw_chunks))
        event_names = [name for name, _ in parsed]
        seq_nums = [payload["sequence_number"] for _, payload in parsed]
        # sequence_number 严格从 1 单调递增无跳号
        assert seq_nums == list(range(1, len(parsed) + 1))
        assert event_names[0] == "response.created"
        assert event_names[1] == "response.in_progress"
        assert "response.reasoning_summary_text.delta" in event_names
        assert "response.output_text.delta" in event_names
        assert "response.function_call_arguments.delta" in event_names
        assert event_names[-1] == "response.completed"
        assert event_names.count("response.completed") == 1

        final_resp = parsed[-1][1]["response"]
        assert final_resp["id"] == "msg_s1"
        assert [i["type"] for i in final_resp["output"]] == ["reasoning", "message", "function_call"]
        assert final_resp["output"][0]["encrypted_content"] == "sig_stream_1"
        assert final_resp["output"][2]["arguments"] == '{"x":1}'
        assert final_resp["usage"]["total_tokens"] == 65

    def test_stream_failure_emits_response_failed_terminal_event(self):
        """测试 P1：流式传输异常中断时闭合未完成块并补发 response.failed 终态帧。"""
        conv = ResponsesStreamConverter("GLM-5.3-Flash")
        chunks = []
        chunks.extend(conv.feed({"type": "message_start", "message": {"id": "msg_err", "usage": {"input_tokens": 10}}}))
        chunks.extend(conv.feed({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}))
        chunks.extend(conv.feed({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "半截"}}))
        chunks.extend(conv.fail("上游连接重置"))

        parsed = _parse_responses_sse("".join(chunks))
        assert parsed[-1][0] == "response.failed"
        assert parsed[-1][1]["response"]["status"] == "failed"
        assert parsed[-1][1]["response"]["error"]["message"] == "上游连接重置"

    def test_early_handshake_start_and_idempotence(self):
        """测试握手提前：conv.start() 立即产出 sequence 1,2 握手帧，且后续 feed 不产生重复创建事件。"""
        conv = ResponsesStreamConverter("GLM-5.3-Flash")
        early_chunks = conv.start()
        parsed_early = _parse_responses_sse("".join(early_chunks))
        assert len(parsed_early) == 2
        assert parsed_early[0][0] == "response.created"
        assert parsed_early[0][1]["sequence_number"] == 1
        assert parsed_early[1][0] == "response.in_progress"
        assert parsed_early[1][1]["sequence_number"] == 2

        # 随后到达上游事件，验证幂等守卫
        feed_chunks = []
        feed_chunks.extend(conv.feed({"type": "message_start", "message": {"id": "msg_idem", "usage": {"input_tokens": 5}}}))
        feed_chunks.extend(conv.feed({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}))
        feed_chunks.extend(conv.feed({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}}))
        feed_chunks.extend(conv.done())

        parsed_all = _parse_responses_sse("".join(early_chunks + feed_chunks))
        names = [n for n, _ in parsed_all]
        assert names.count("response.created") == 1
        assert names.count("response.in_progress") == 1
        # sequence_number 仍然严格从 1 单调递增无空洞
        assert [p["sequence_number"] for _, p in parsed_all] == list(range(1, len(parsed_all) + 1))

    def test_tool_choice_auto_and_custom_tool_call_json_unpacking(self):
        """测试 tool_choice=auto 显式对齐与 custom_tool_call JSON 字符串解包。"""
        body, err = responses_to_anthropic({
            "model": "GLM-5.3-Flash",
            "input": [
                {"type": "custom_tool_call", "call_id": "c_custom", "name": "review_diff", "input": '{"path":"main.py"}'},
            ],
            "tools": [
                {"type": "function", "name": "review_diff", "parameters": {"type": "object"}},
            ],
            "tool_choice": "auto",
        })
        assert err is None and body is not None
        assert body["tool_choice"] == {"type": "auto"}
        assert body["messages"][0]["role"] == "assistant"
        assert body["messages"][0]["content"][0]["input"] == {"path": "main.py"}
