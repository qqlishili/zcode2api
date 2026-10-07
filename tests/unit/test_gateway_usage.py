"""USG-001/002 上游用量原值：未知不归零，累计帧不相加。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app import reqlog
from app.routes import gateway as gw


@pytest.fixture
def error_stream():
    """合成流，验证实际生成器的分块、取消与关闭。"""
    reqlog.clear()

    def _build(protocol, size=65536, on_result=None, raw=None,
               content_type="text/event-stream", preloaded_bytes=None):
        if raw is None:
            raw = b'data: {"type":"error","error":{"message":"fixture"}}\n\n'
        closed = []

        class Response:
            status_code = 200
            headers = {"content-type": content_type}

            async def aiter_bytes(self):
                for i in range(0, len(raw), size):
                    yield raw[i:i + size]

            async def aiter_lines(self):
                for line in raw.decode().splitlines():
                    yield line

        class Context:
            async def __aexit__(self, *args):
                closed.append("context")

        reqlog.begin("error", protocol, "GLM-5.3-Flash", True)
        up = gw._Upstream(Response(), Context(), None, on_close=lambda: closed.append("slot"),
                          on_result=on_result, preloaded_bytes=preloaded_bytes)
        response = (up.to_streaming("error") if protocol == "messages" else
                    gw._openai_stream_response(up, "GLM-5.3-Flash", "error") if protocol == "chat" else
                    gw._responses_stream_response(up, "GLM-5.3-Flash", "error"))
        return up, response.body_iterator, closed, raw

    yield _build
    reqlog.clear()


@pytest.mark.parametrize("size", [1, 7, 65536])
async def test_error_frame_cross_chunks_is_preserved(error_stream, size):
    up, iterator, closed, raw = error_stream("messages", size=size)
    assert b"".join([chunk async for chunk in iterator]) == raw
    await up.close()
    assert reqlog.snapshot()[0]["ok"] is False
    assert closed == ["context", "slot"]


@pytest.mark.parametrize("protocol", ["messages", "chat", "responses"])
async def test_cancel_at_error_frame_preserves_failure(error_stream, protocol):
    results = []
    up, iterator, closed, _ = error_stream(protocol, on_result=lambda ok, detail: results.append(ok))
    try:
        async for chunk in iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            if ("event: response.failed" in text if protocol == "responses" else '"error"' in text):
                with pytest.raises(asyncio.CancelledError):
                    await iterator.athrow(asyncio.CancelledError())
                break
        entry = reqlog.snapshot()[0]
        assert entry["ok"] is False and entry["status"] == 200
        await up.close()
        assert results == [False] and closed == ["context", "slot"]
    finally:
        await iterator.aclose()


@pytest.mark.parametrize("size", [1, 7, 65536])
@pytest.mark.parametrize("raw", [b"", b'data: {"type":"message_stop"',
                                  b'data: {"type":"content_block_delta","delta":{"text":"message_stop"}}'])
async def test_eof_bytes_are_preserved_before_error_frame(error_stream, size, raw):
    results = []
    up, iterator, closed, _ = error_stream("messages", size=size, raw=raw,
                                          on_result=lambda ok, detail: results.append(ok))
    response = b"".join([chunk async for chunk in iterator])
    assert response.startswith(raw + b"\n\nevent: error\ndata: ")
    event = json.loads(response[len(raw):].split(b"data: ", 1)[1].strip())
    assert event["type"] == "error" and event["error"]["type"] == "upstream_error"
    assert "未正常结束" in event["error"]["message"]
    assert reqlog.snapshot()[0]["ok"] is False
    await up.close()
    assert results == [False] and closed == ["context", "slot"]


@pytest.mark.parametrize("protocol", ["messages", "chat", "responses"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
@pytest.mark.parametrize("trailing_newline", [False, True])
async def test_complete_terminal_tail_is_not_eof_failure(error_stream, protocol, newline, trailing_newline):
    raw = b'data: {"type":"message_start","message":{"content":[]}}' + newline * 2
    raw += b'data: {"type":"message_stop"}' + (newline * 2 if trailing_newline else b"")
    results = []
    up, iterator, closed, _ = error_stream(protocol, size=7, raw=raw,
                                          on_result=lambda ok, detail: results.append(ok))
    chunks = [chunk async for chunk in iterator]
    response = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    assert "upstream_error" not in response and "response.failed" not in response
    assert (response.encode() == raw if protocol == "messages" else
            "[DONE]" in response if protocol == "chat" else
            response.count("event: response.completed\n") == 1)
    assert reqlog.snapshot()[0]["ok"] is True
    await up.close()
    assert results == [True] and closed == ["context", "slot"]


@pytest.mark.parametrize("protocol", ["messages", "chat", "responses"])
@pytest.mark.parametrize("content_type", ["text/event-stream", "TEXT/EVENT-STREAM"])
async def test_cancel_at_eof_error_preserves_failure(error_stream, protocol, content_type):
    results = []
    up, iterator, closed, _ = error_stream(protocol, raw=b"", content_type=content_type,
                                          on_result=lambda ok, detail: results.append(ok))
    try:
        async for chunk in iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            if ("event: response.failed" in text if protocol == "responses" else '"error"' in text):
                with pytest.raises(asyncio.CancelledError):
                    await iterator.athrow(asyncio.CancelledError())
                break
        entry = reqlog.snapshot()[0]
        assert entry["ok"] is False and entry["status"] == 200
        assert "未正常结束" in entry["error"]
        await up.close()
        assert results == [False] and closed == ["context", "slot"]
    finally:
        await iterator.aclose()


@pytest.mark.parametrize("preloaded", [False, True])
@pytest.mark.parametrize("protocol", ["messages", "chat", "responses"])
async def test_non_sse_json_does_not_require_terminal(error_stream, preloaded, protocol):
    raw = b'{"type":"message","usage":{"input_tokens":2,"output_tokens":1}}'
    up, iterator, closed, _ = error_stream(protocol, raw=raw, content_type="application/json",
                                          preloaded_bytes=raw if preloaded else None)
    chunks = [chunk async for chunk in iterator]
    response = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    assert (response.encode() == raw if protocol == "messages" else
            "[DONE]" in response if protocol == "chat" else
            response.count("event: response.completed\n") == 1)
    assert "upstream_error" not in response and "response.failed" not in response
    assert reqlog.snapshot()[0]["ok"] is True
    await up.close()
    assert closed == ["context", "slot"]


@pytest.mark.parametrize("change", ["credential", "removed", "disabled"])
def test_stream_result_respects_current_account(fresh_app, change):
    from app.models import Status
    from tests.conftest import seed_account

    account = seed_account(fresh_app, "hRecorder.eyJzdWIiOiJmaXh0dXJlIn0.sig", name="recorder")
    credential = (account.mode, account.jwt_token, account.api_key)
    record = gw._make_result_recorder(account, credential)
    if change == "credential":
        account.jwt_token = "hNew.eyJzdWIiOiJuZXcifQ.sig"
    elif change == "removed":
        fresh_app.remove_account("zai", account.id)
    else:
        fresh_app.set_enabled("zai", account.id, False)
    record(False, "上游流式响应报错")
    if change in ("credential", "removed"):
        assert not account.recent_results
    else:
        assert account.status == Status.DISABLED
        assert account.recent_results[-1]["ok"] is False


@pytest.mark.parametrize("value, expected", [(0, 0), (17, 17), (None, None),
                                                (True, None), (-1, None), ("17", None), (1.5, None)])
def test_usage_values_preserve_zero_and_unknown(value, expected):
    usage = gw._extract_usage_from_json_bytes(json.dumps({"usage": {
        "input_tokens": value, "cache_read_input_tokens": 60,
        "cache_creation_input_tokens": 8, "output_tokens": 5,
    }}).encode())
    assert usage == {"input_tokens": expected, "output_tokens": 5,
                     "cache_read_input_tokens": 60, "cache_creation_input_tokens": 8}


def test_sse_partial_updates_are_cumulative():
    usage = gw._extract_usage_from_json_bytes(b'{}')
    for event in [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0,
         "cache_read_input_tokens": 60, "cache_creation_input_tokens": 8}}},
        {"type": "message_delta", "usage": {"output_tokens": 3}},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_delta", "usage": {"output_tokens": None}},
        {"type": "message_start", "message": "malformed"},
    ]:
        evt = gw._extract_sse_line_event(b'data: ' + json.dumps(event).encode())
        usage = gw._extract_event_usage(evt, usage)
    assert usage == {"input_tokens": 0, "output_tokens": 5,
                     "cache_read_input_tokens": 60, "cache_creation_input_tokens": 8}
    assert gw._extract_event_usage(gw._extract_sse_line_event(b'data: [DONE]'), usage) == usage


def test_reqlog_caches_remain_nullable_and_validated():
    reqlog.clear()
    try:
        reqlog.begin("usage", "messages", "m", False)
        assert reqlog.snapshot()[0]["cache_read_input_tokens"] is None
        reqlog.finish_ok("usage", input_tokens=0, output_tokens=True,
                         cache_read_input_tokens=60, cache_creation_input_tokens=8)
        entry = reqlog.snapshot()[0]
        assert entry["input_tokens"] == 0 and entry["output_tokens"] is None
        assert entry["cache_read_input_tokens"] == 60
        assert entry["cache_creation_input_tokens"] == 8
    finally:
        reqlog.clear()


@pytest.mark.parametrize("protocol", ["messages", "chat", "responses"])
async def test_cancelled_stream_closes_monitoring_and_slot(protocol):
    """USG-004：三条流的取消不丢失关闭回调，重复 close 不重复释放。"""
    class Response:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_bytes(self):
            yield b'data: {"type":"message_start","message":{"usage":{"input_tokens":0}}}\n\n'
            raise asyncio.CancelledError

        async def aiter_lines(self):
            yield 'data: {"type":"message_start","message":{"usage":{"input_tokens":0}}}'
            raise asyncio.CancelledError

    closed = []

    class Context:
        async def __aexit__(self, *args):
            closed.append("context")

    reqlog.clear()
    reqlog.begin("cancel", protocol, "m", True)
    up = gw._Upstream(Response(), Context(), None, on_close=lambda: closed.append("slot"))
    response = (up.to_streaming("cancel") if protocol == "messages" else
                gw._openai_stream_response(up, "m", "cancel") if protocol == "chat" else
                gw._responses_stream_response(up, "m", "cancel"))
    try:
        with pytest.raises(asyncio.CancelledError):
            async for _ in response.body_iterator:
                pass
        await up.close()
        assert closed == ["context", "slot"]
        assert reqlog.snapshot()[0]["status"] == 499
    finally:
        reqlog.clear()


async def test_responses_cancel_after_completion_preserves_raw_usage():
    """USG-004：完成事件输出时取消仍保留原始缓存用量。"""
    events = [
        {"type": "message_start", "message": {"id": "m", "content": [], "usage": {
            "input_tokens": 0, "cache_read_input_tokens": 60, "cache_creation_input_tokens": 8}}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]

    class Response:
        status_code = 200

        async def aiter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)

    closed = []

    class Context:
        async def __aexit__(self, *args):
            closed.append("context")

    reqlog.clear()
    reqlog.begin("done", "responses", "m", True)
    up = gw._Upstream(Response(), Context(), None, on_close=lambda: closed.append("slot"))
    iterator = gw._responses_stream_response(up, "m", "done").body_iterator
    try:
        async for chunk in iterator:
            if "response.completed" in chunk:
                with pytest.raises(asyncio.CancelledError):
                    await iterator.athrow(asyncio.CancelledError())
                break
        entry = reqlog.snapshot()[0]
        assert entry["ok"] is True and entry["input_tokens"] == 0
        assert entry["cache_read_input_tokens"] == 60 and entry["cache_creation_input_tokens"] == 8
        assert closed == ["context", "slot"]
    finally:
        await iterator.aclose()
        reqlog.clear()


@pytest.mark.parametrize("size", [1, 7, 65536])
async def test_cached_sse_cross_chunks_and_final_line_are_unchanged(size):
    """USG-002：实际透传路径跨分块提取缓存，尾行没有换行也不丢。"""
    raw = (
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":0,'
        b'"cache_read_input_tokens":60,"cache_creation_input_tokens":8}}}\n\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":5}}\n\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":5}}\n\n'
        b'data: {"type":"message_stop"}'
    )

    class Response:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_bytes(self):
            for i in range(0, len(raw), size):
                yield raw[i:i + size]

    class Context:
        async def __aexit__(self, *args):
            pass

    reqlog.clear()
    reqlog.begin("chunks", "messages", "m", True)
    response = gw._Upstream(Response(), Context(), None).to_streaming("chunks")
    try:
        assert b"".join([chunk async for chunk in response.body_iterator]) == raw
        entry = reqlog.snapshot()[0]
        assert entry["ok"] is True
        assert entry["input_tokens"] == 0 and entry["output_tokens"] == 5
        assert entry["cache_read_input_tokens"] == 60 and entry["cache_creation_input_tokens"] == 8
    finally:
        reqlog.clear()
