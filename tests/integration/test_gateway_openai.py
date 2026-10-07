"""GW-011 /v1/chat/completions（OpenAI 风格）端点集成测试。

复用 /v1/messages 同一套 mock 上游（返回 Anthropic 格式），验证双向转换：
非流式 JSON 与流式 SSE chunk。
"""

from __future__ import annotations

import json

import pytest

_GOOD_JWT = "hO.eyJzdWIiOiJvIn0.sig"

_THINKING_ENDPOINTS = ["/v1/messages", "/v1/chat/completions", "/v1/responses"]


def _thinking_payload(endpoint, effort=None):
    payload = {"model": "GLM-5.3-Flash", "max_tokens": 64}
    if endpoint == "/v1/responses":
        payload["input"] = "hi"
        if effort is not None:
            payload["reasoning"] = {"effort": effort}
    else:
        payload["messages"] = [{"role": "user", "content": "hi"}]
        if effort is not None:
            if endpoint == "/v1/messages":
                payload["output_config"] = {"effort": effort}
            else:
                payload["reasoning_effort"] = effort
    return payload


@pytest.mark.integration
class TestChatCompletions:
    async def test_nonstream_basic(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT, name="oai")
        res = await client.post(
            "/v1/chat/completions",
            json={"model": "glm-5.3-flash",
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == "GLM-5.3-Flash"
        assert data["choices"][0]["message"]["content"] == "Hello from mock upstream"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert data["usage"]["prompt_tokens"] == 10
        # 最近请求明细：{ok, at, detail} 形态，悬停 tooltip 数据源
        acc = next(a for a in fresh_app.list_accounts("zai") if a.name == "oai")
        last = acc.recent_results[-1]
        assert isinstance(last, dict) and last["ok"] is True
        assert "HTTP 200" in last["detail"] and last["at"] > 0

    async def test_system_message_reaches_upstream_as_system(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT, name="oai-sys")
        res = await client.post(
            "/v1/chat/completions",
            json={"model": "GLM-5.3",
                  "messages": [{"role": "system", "content": "守则"},
                               {"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200
        payload = json.loads(mock.state.calls[-1][3])
        assert payload["system"][-1]["text"] == "守则"  # 用户 system 位于官方身份块之后

    async def test_max_tokens_clamped_to_upstream_limit(self, gateway_client, fresh_app):
        """上游 400 code 1210：max_tokens 上限 131072，网关钳制（GW-012）。"""
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT, name="oai-mt")
        res = await client.post(
            "/v1/messages",
            json={"model": "GLM-5.3", "max_tokens": 999999,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200
        payload = json.loads(mock.state.calls[-1][3])
        assert payload["max_tokens"] == 131072

    async def test_max_tokens_string_form_clamped(self, gateway_client, fresh_app):
        """字符串型 max_tokens 也要钳制，防止绕过类型检查打到上游 1210。"""
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT, name="oai-mt-str")
        res = await client.post(
            "/v1/messages",
            json={"model": "GLM-5.3", "max_tokens": "999999",
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200
        payload = json.loads(mock.state.calls[-1][3])
        assert payload["max_tokens"] == 131072

    async def test_max_tokens_floor_and_passthrough(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT, name="oai-mt2")
        res = await client.post(
            "/v1/messages",
            json={"model": "GLM-5.3", "max_tokens": 1024,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200
        payload = json.loads(mock.state.calls[-1][3])
        assert payload["max_tokens"] == 1024  # 合法值原样透传

    async def test_stream_returns_openai_chunks(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT, name="oai-stream")
        res = await client.post(
            "/v1/chat/completions",
            json={"model": "glm-5.3-flash", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in res.text.splitlines() if ln.startswith("data: ")]
        assert lines[-1] == "data: [DONE]"
        chunks = [json.loads(ln[6:]) for ln in lines[:-1]]
        assert all(c["object"] == "chat.completion.chunk" for c in chunks)
        assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
        text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
        assert "chunk-0" in text
        finishes = [c["choices"][0]["finish_reason"] for c in chunks if c["choices"][0]["finish_reason"]]
        assert finishes == ["stop"]

    async def test_invalid_payload_rejected(self, gateway_client, fresh_app):
        client, _ = gateway_client
        res = await client.post("/v1/chat/completions",
                                json={"messages": [{"role": "user", "content": "hi"}]})
        assert res.status_code == 400
        assert res.json()["error"]["type"] == "invalid_request_error"

    async def test_no_account_returns_503(self, fresh_app):
        from httpx import ASGITransport, AsyncClient

        from app.main import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post("/v1/chat/completions",
                                    json={"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]})
        assert res.status_code == 503
        assert res.json()["error"]["type"] == "no_available_account"

    async def test_gateway_key_required(self, gateway_client, fresh_app):
        client, _ = gateway_client
        fresh_app.set_setting("gateway_key", "sk-gw-test")
        try:
            res = await client.post("/v1/chat/completions",
                                    json={"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]})
            assert res.status_code == 401
            res = await client.post("/v1/chat/completions",
                                    headers={"x-api-key": "sk-gw-test"},
                                    json={"model": "glm-5.3", "messages": [{"role": "user", "content": "hi"}]})
            assert res.status_code == 503  # 鉴权通过，进入调度（无账号）
        finally:
            fresh_app.set_setting("gateway_key", "")


@pytest.mark.integration
class TestThinkingLevels:
    @pytest.mark.parametrize("endpoint", _THINKING_ENDPOINTS)
    @pytest.mark.parametrize("effort", ["low", "high", "max"])
    @pytest.mark.parametrize("stream", [False, True])
    async def test_effort_reaches_upstream_unchanged(self, gateway_client, fresh_app, endpoint, effort, stream):
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT)
        res = await client.post(endpoint, json={**_thinking_payload(endpoint, effort), "stream": stream})
        assert res.status_code == 200
        upstream = json.loads(mock.state.calls[-1][3])
        assert upstream["output_config"] == {"effort": effort}
        assert upstream["thinking"] == {"type": "enabled"}
        if stream:
            events = [json.loads(line[6:]) for line in res.text.splitlines()
                      if line.startswith("data: ") and line != "data: [DONE]"]
            if endpoint == "/v1/responses":
                completed = next(evt for evt in events if evt.get("type") == "response.completed")
                assert completed["response"]["reasoning"] == {"effort": effort}
            elif endpoint == "/v1/chat/completions":
                assert "data: [DONE]" in res.text
            else:
                assert any(evt.get("type") == "message_stop" for evt in events)
        elif endpoint == "/v1/responses":
            assert res.json()["reasoning"] == {"effort": effort}

    @pytest.mark.parametrize("endpoint", _THINKING_ENDPOINTS)
    @pytest.mark.parametrize("params", [{}, {"thinking": {"type": "enabled"}},
                                        {"thinking": {"type": "adaptive"}}, {"enable_thinking": True}])
    async def test_omitted_effort_uses_upstream_default(self, gateway_client, fresh_app, endpoint, params):
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT)
        res = await client.post(endpoint, json={**_thinking_payload(endpoint), **params})
        assert res.status_code == 200
        upstream = json.loads(mock.state.calls[-1][3])
        assert "effort" not in upstream.get("output_config", {})
        if not params:
            assert "thinking" not in upstream
        else:
            assert upstream["thinking"] == {"type": "enabled"}

    @pytest.mark.parametrize("endpoint", _THINKING_ENDPOINTS)
    @pytest.mark.parametrize("effort", ["minimal", "medium", "xhigh", "none", "off", "disabled",
                                        "unknown", "", "HIGH", " low ", None, False, 1, [], {}])
    async def test_invalid_effort_rejected_before_selection(self, gateway_client, fresh_app, monkeypatch,
                                                           endpoint, effort):
        params = {"output_config": {"effort": effort}}
        if endpoint == "/v1/chat/completions":
            params = {"reasoning_effort": effort}
        elif endpoint == "/v1/responses":
            params = {"reasoning": {"effort": effort}}
        await self._assert_rejected(gateway_client, fresh_app, monkeypatch, endpoint, params)

    @pytest.mark.parametrize("endpoint", _THINKING_ENDPOINTS)
    @pytest.mark.parametrize("params", [
        {"enable_thinking": False}, {"thinking": {"type": "disabled"}},
        {"thinking": {"type": "enabled", "budget_tokens": 8192}},
        {"reasoning_effort": "high", "output_config": {"effort": "low"}},
        {"reasoning_effort": "high", "reasoning": {"effort": "invalid"}},
        {"reasoning_effort": "high", "thinking": {"type": "disabled"}},
        {"reasoning_effort": "high", "enable_thinking": False},
        {"reasoning_effort": "high", "output_config": {"effort": None}},
        {"thinking": None}, {"thinking": {"type": "auto"}}, {"thinking": {}},
        {"reasoning": []}, {"output_config": "high"}, {"enable_thinking": "true"},
    ])
    async def test_unsupported_or_conflicting_params_rejected(self, gateway_client, fresh_app, monkeypatch,
                                                            endpoint, params):
        await self._assert_rejected(gateway_client, fresh_app, monkeypatch, endpoint, params)

    async def _assert_rejected(self, gateway_client, fresh_app, monkeypatch, endpoint, params):
        client, mock = gateway_client
        selected = []
        original_select = fresh_app.select

        def select(*args, **kwargs):
            selected.append(True)
            return original_select(*args, **kwargs)

        monkeypatch.setattr(fresh_app, "select", select)
        calls_before = len(mock.state.calls)
        res = await client.post(endpoint, json={**_thinking_payload(endpoint), **params})
        assert res.status_code == 400
        error = res.json()["error"]
        assert error["type"] == "invalid_request_error"
        assert all(level in error["message"] for level in ("low", "high", "max"))
        assert selected == []
        assert len(mock.state.calls) == calls_before

    @pytest.mark.parametrize("endpoint", _THINKING_ENDPOINTS)
    async def test_same_efforts_with_budget_keep_explicit_level(self, gateway_client, fresh_app, endpoint):
        client, mock = gateway_client
        from tests.conftest import seed_account

        seed_account(fresh_app, _GOOD_JWT)
        res = await client.post(endpoint, json={
            **_thinking_payload(endpoint, "max"), "reasoning_effort": "max",
            "reasoning": {"effort": "max"}, "output_config": {"effort": "max"},
            "thinking": {"type": "adaptive", "budget_tokens": 1024},
        })
        assert res.status_code == 200
        upstream = json.loads(mock.state.calls[-1][3])
        assert upstream["output_config"] == {"effort": "max"}
        assert upstream["thinking"] == {"type": "enabled"}
