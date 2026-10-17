"""模型名称回显：保留请求大小写，真实不同时保留上游名，覆盖首帧。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app.routes import gateway as gw


@pytest.mark.parametrize("endpoint", ["chat/completions", "responses", "messages"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("requested, reported, expected", [
    ("GLM-5.3-Flash", "glm-5.3-flash", "GLM-5.3-Flash"),
    ("gLm-5.3-fLaSh", "glm-5.3-flash", "gLm-5.3-fLaSh"),
    ("glm-5.3", "GLM-5.3", "glm-5.3"),
    ("GLM-5.3-Flash", "glm-5.3", "glm-5.3"),
    ("codex-auto-review", "glm-5.3-flash", "glm-5.3-flash"),
    ("gLm-5.3-fLaSh", None, "gLm-5.3-fLaSh"),
])
async def test_gateway_response_model(fresh_app, monkeypatch, endpoint, stream,
                                      requested, reported, expected):
    sent_models = []
    upstream = []

    async def dispatch(req_id, body, *args):
        sent_models.append(body["model"])
        message = {"id": "msg_model", "type": "message", "model": reported,
                   "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn"}
        if stream:
            events = [
                {"type": "ping"},
                {"type": "message_start", "message": {**message, "content": []}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"},
            ]
            raw = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            resp = httpx.Response(200, content=raw, headers={"content-type": "text/event-stream"})
        else:
            resp = httpx.Response(200, json=message)
        result = gw._Upstream(resp, AsyncMock(), None)
        upstream.append(result)
        return result

    monkeypatch.setattr(gw, "_dispatch", dispatch)
    app = FastAPI()
    app.include_router(gw.router)
    payload = {"model": requested, "stream": stream}
    if endpoint == "responses":
        payload["input"] = "hi"
    else:
        payload["messages"] = [{"role": "user", "content": "hi"}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/" + endpoint, json=payload)
    assert response.status_code == 200
    assert sent_models == [gw.MODEL_NAME_MAP[requested.lower()]]
    assert upstream[0]._closed
    if stream:
        events = [json.loads(line[5:]) for line in response.text.splitlines()
                  if line.startswith("data:") and line[5:].strip() != "[DONE]"]
        models = [obj["model"] for event in events
                  if "model" in (obj := event.get("response", event.get("message", event)))]
        assert models and all(model == expected for model in models), models
        if endpoint == "responses":
            assert events[0]["type"] == "response.created"
            assert events[-1]["type"] == "response.completed"
        elif endpoint == "messages":
            assert events[0]["type"] == "ping"
            assert events[-1]["type"] == "message_stop"
            assert next(e["delta"]["text"] for e in events if e["type"] == "content_block_delta") == "OK"
        else:
            assert events[0]["choices"][0]["delta"]["role"] == "assistant"
            assert response.text.rstrip().endswith("data: [DONE]")
    else:
        assert response.json()["model"] == expected


@pytest.mark.parametrize("size", [1, 7, 65536])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
async def test_messages_model_preserves_split_frames(size, newline):
    requested = "GLM-5.3-Flash"
    start = {"type": "message_start", "message": {
        "id": "msg_case", "type": "message", "model": "glm-5.3-flash",
        "content": [], "usage": {"input_tokens": 0, "cache_read_input_tokens": 6}}}
    delta = {"type": "content_block_delta", "delta": {
        "type": "thinking_delta", "thinking": "glm-5.3-flash 中文" * 8000}}
    def wire(event):
        return b"data: " + json.dumps(event, ensure_ascii=False).encode() + newline * 2

    prefix = b": ping" + newline * 2 + b"event: message_start" + newline
    tail = b"event: content_block_delta" + newline + wire(delta) + b'data: {"type":"message_stop"}'
    raw = prefix + wire(start) + tail
    start["message"]["model"] = requested
    expected = prefix + wire(start) + tail

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(raw), size):
                yield raw[index:index + size]

    response = httpx.Response(200, stream=Stream(), headers={"content-type": "text/event-stream"})
    upstream = gw._Upstream(response, AsyncMock(), None)
    actual = b"".join([chunk async for chunk in upstream.to_streaming("model-chunks", requested).body_iterator])
    assert actual == expected
    assert upstream._closed
