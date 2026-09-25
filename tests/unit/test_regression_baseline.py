"""回归基线：zcode2api 原行为锁定（M0 出口标准）。

这些用例描述的是**底座既有行为**，不是新设计 —— 改动底座时它们必须保持绿色，
若有意变更行为，先改用例并在 PR 里说明（docs/development/06 §测试纪律）。
"""

from __future__ import annotations

import json
import time

import pytest

from app.models import Account, Status
from app.routes.gateway import (
    _detect_provider,
    _is_captcha_error,
    _is_exhausted,
    _normalize_body,
)


# ── 模型归一化 ────────────────────────────────────────────────────────────────
class TestNormalizeBody:
    def test_lowercase_alias_mapped(self):
        body = {"model": "glm-5.2"}
        assert _normalize_body(body)["model"] == "GLM-5.2"

    def test_provider_prefix_stripped(self):
        body = {"model": "bigmodel/GLM-5.2"}
        assert _normalize_body(body)["model"] == "bigmodel/GLM-5.2".split("/")[-1]

    def test_unknown_model_passed_through(self):
        body = {"model": "glm-unknown"}
        assert _normalize_body(body)["model"] == "glm-unknown"

    def test_string_content_bridged_to_blocks(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        assert _normalize_body(body)["messages"][0]["content"] == [{"type": "text", "text": "hi"}]

    def test_block_content_untouched(self):
        blocks = [{"type": "text", "text": "hi"}]
        body = {"messages": [{"role": "user", "content": blocks}]}
        assert _normalize_body(body)["messages"][0]["content"] is blocks


# ── provider 判定 ─────────────────────────────────────────────────────────────
class TestDetectProvider:
    def test_default_zai(self):
        assert _detect_provider({}, {}) == "zai"

    def test_model_prefix(self):
        assert _detect_provider({"model": "bigmodel/GLM-5.2"}, {}) == "bigmodel"

    def test_header(self):
        assert _detect_provider({}, {"x-provider": "bigmodel"}) == "bigmodel"


# ── 被拒信号判定（值以 constants 为唯一来源）───────────────────────────────────
class TestRejectionSignals:
    def test_402_is_exhausted(self):
        assert _is_exhausted(402, "")

    @pytest.mark.parametrize("text", ["insufficient balance", "quota exceeded", "余额不足", "额度不足"])
    def test_keyword_exhausted(self, text):
        assert _is_exhausted(400, text)

    def test_plain_400_not_exhausted(self):
        assert not _is_exhausted(400, "bad request")

    @pytest.mark.parametrize("text", ["captcha required", "verify token invalid", "Verify Failed"])
    def test_captcha_error(self, text):
        assert _is_captcha_error(text)

    def test_auth_401_not_captcha(self):
        assert not _is_captcha_error("invalid credentials")


# ── Account 状态机（models.py 原语义）─────────────────────────────────────────
class TestAccountStateMachine:
    def _acc(self, **kw) -> Account:
        return Account.create("zai", "t", "a.b.c", **kw)

    def test_jwt_detection(self):
        acc = self._acc()
        assert acc.mode == "jwt"

    def test_apikey_detection(self):
        acc = Account.create("zai", "t", "not-a-jwt")
        assert acc.mode == "apiKey"

    def test_selectable_default(self):
        assert self._acc().is_selectable()

    def test_exhausted_not_selectable(self):
        acc = self._acc()
        acc.status = Status.EXHAUSTED
        assert not acc.is_selectable()

    def test_cooling_until_expiry(self):
        acc = self._acc()
        acc.status = Status.COOLING
        acc.cooling_until = time.time() - 1
        assert acc.is_selectable()

    def test_cooling_within_window(self):
        acc = self._acc()
        acc.status = Status.COOLING
        acc.cooling_until = time.time() + 100
        assert not acc.is_selectable()

    def test_invalid_jwt_not_selectable_without_apikey(self):
        acc = self._acc()
        acc.status = Status.INVALID
        assert not acc.is_selectable()
        assert not acc.allows_billing()
        assert acc.uses_plan_channel() is False

    def test_invalid_jwt_selectable_via_apikey_fallback(self):
        acc = self._acc()
        acc.api_key = "sk-fallback"
        acc.status = Status.INVALID
        assert acc.is_selectable()
        assert not acc.allows_billing()
        assert acc.uses_plan_channel() is False

    def test_risk_disabled_jwt_selectable_via_apikey(self):
        acc = self._acc()
        acc.api_key = "sk-fallback"
        acc.ban_for_risk()
        assert acc.status == Status.DISABLED
        assert acc.enabled is True
        assert acc.is_selectable()
        assert not acc.allows_billing()

    def test_manual_disable_never_selectable(self):
        acc = self._acc()
        acc.api_key = "sk-fallback"
        acc.enabled = False
        acc.status = Status.DISABLED
        assert not acc.is_selectable()
        assert not acc.allows_billing()

    def test_exhausted_jwt_not_selectable_but_plan_alive(self):
        acc = self._acc()
        acc.status = Status.EXHAUSTED
        assert not acc.is_selectable()
        assert acc.uses_plan_channel() is True
        assert acc.allows_billing() is True

    def test_effective_status_after_cooldown(self):
        acc = self._acc()
        acc.status = Status.COOLING
        acc.cooling_until = time.time() - 1
        assert acc.effective_status() == Status.ACTIVE

    def test_public_view_masks_token(self):
        acc = Account.create("zai", "t", "x" * 100)
        view = acc.public_view()
        assert "x" * 100 not in json.dumps(view)
        assert view["token_masked"].startswith("x")


# ── Store 轮询（store.py 原语义）──────────────────────────────────────────────
class TestStoreRotation:
    def test_round_robin_distribution(self, fresh_app):
        for i in range(3):
            fresh_app.add_account("zai", f"a{i}", f"jwt.token.{i}")
        picks = [fresh_app.select("zai").id for _ in range(9)]
        assert len(set(picks)) == 3
        assert all(picks.count(p) == 3 for p in set(picks))

    def test_skip_ids(self, fresh_app):
        a = fresh_app.add_account("zai", "a", "jwt.token.a")
        b = fresh_app.add_account("zai", "b", "jwt.token.b")
        pick = fresh_app.select("zai", skip_ids={a.id})
        assert pick.id == b.id

    def test_duplicate_secret_dedup(self, fresh_app):
        fresh_app.add_account("zai", "a", "jwt.token.same")
        again = fresh_app.add_account("zai", "b", "jwt.token.same")
        assert len(fresh_app.list_accounts("zai")) == 1
        assert again.name == "a"

    def test_disabled_skipped(self, fresh_app):
        a = fresh_app.add_account("zai", "a", "jwt.token.a")
        fresh_app.set_enabled("zai", a.id, False)
        assert fresh_app.select("zai") is None

    def test_persistence_roundtrip(self, fresh_app, tmp_path):
        fresh_app.add_account("zai", "a", "jwt.token.a")
        from app.store import Store
        reloaded = Store()
        assert [x.name for x in reloaded.list_accounts("zai")] == ["a"]

    def test_export_import(self, fresh_app):
        fresh_app.add_account("zai", "a", "jwt.token.a")
        data = fresh_app.export()
        fresh_app.remove_account("zai", "a")
        assert fresh_app.import_accounts(data) == 1


# ── 请求构建（agent.py 原语义）────────────────────────────────────────────────
class TestBuildRequest:
    def test_jwt_routes_to_plan_channel(self):
        from app.agent import build_request
        acc = Account.create("zai", "t", "a.b.c")
        url, headers, _payload = build_request(acc, {}, None)
        assert url == "https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages"
        assert headers["Authorization"] == "Bearer a.b.c"
        assert headers["anthropic-version"] == "2023-06-01"
        # JWT 通道带全量身份头 + 追踪头；桌面端账号走 electron/stable
        assert headers["X-Title"] == "Z Code@electron"
        assert headers["X-Release-Channel"] == "stable"
        assert "X-Device-Mid" in headers
        assert "x-request-id" in headers and "x-zcode-trace-id" in headers

    def test_apikey_routes_to_fallback(self):
        from app.agent import build_request
        acc = Account.create("zai", "t", "plain-key")
        url, headers, _payload = build_request(acc, {}, None)
        assert url == "https://api.z.ai/api/anthropic/v1/messages"
        assert headers["x-api-key"] == "plain-key"

    def test_invalid_jwt_with_apikey_routes_to_fallback(self):
        from app.agent import build_request
        acc = Account.create("zai", "t", "a.b.c")
        acc.api_key = "sk-fallback"
        acc.status = Status.INVALID
        url, headers, _payload = build_request(acc, {}, None)
        assert url == "https://api.z.ai/api/anthropic/v1/messages"
        assert headers["x-api-key"] == "sk-fallback"
        assert "Authorization" not in headers

    def test_captcha_header_injected(self):
        from app.agent import build_request
        acc = Account.create("zai", "t", "a.b.c")
        _, headers, _payload = build_request(acc, {}, "vp-123")
        assert headers["X-Aliyun-Captcha-Verify-Param"] == "vp-123"

    def test_captcha_region_header_injected(self):
        from app.agent import build_request
        acc = Account.create("zai", "t", "a.b.c")
        _, headers, _payload = build_request(acc, {}, "vp-123", None, "cn")
        assert headers["X-Aliyun-Captcha-Verify-Region"] == "cn"

    def test_client_auth_headers_dropped(self):
        from app.agent import build_request
        acc = Account.create("zai", "t", "a.b.c")
        _, headers, _payload = build_request(acc, {}, None, {
            "authorization": "Bearer client", "x-api-key": "leak", "user-agent": "UA/1",
            "x-zcode-foo": "strip", "x-custom": "keep",
        })
        # 客户端的鉴权/身份头被丢弃，authorization 保留的是账号自身凭证
        assert headers["Authorization"] == "Bearer a.b.c"
        assert "x-api-key" not in {h.lower() for h in headers}
        assert headers.get("x-zcode-foo") is None
        assert headers["User-Agent"] != "UA/1"
        assert headers.get("x-custom") == "keep"

    def test_missing_credential_raises(self):
        from app.agent import build_request
        acc = Account.create("zai", "t", "a.b.c")
        acc.jwt_token = None
        with pytest.raises(RuntimeError):
            build_request(acc, {}, None)


# ── billing 阻断文案 ──────────────────────────────────────────────────────────
class TestBillingBlockReason:
    def test_cooling(self):
        from app.claim import billing_block_reason
        acc = Account.create("zai", "t", "a.b.c")
        acc.status = Status.COOLING
        acc.cooling_until = time.time() + 100
        assert "冷却" in (billing_block_reason(acc) or "")

    def test_invalid_jwt(self):
        from app.claim import AUTH_EXPIRED_MESSAGE, billing_block_reason
        acc = Account.create("zai", "t", "a.b.c")
        acc.status = Status.INVALID
        assert billing_block_reason(acc) == AUTH_EXPIRED_MESSAGE

    def test_manual_disable(self):
        from app.claim import billing_block_reason
        acc = Account.create("zai", "t", "a.b.c")
        acc.enabled = False
        msg = billing_block_reason(acc, action="上游刷新") or ""
        assert "停用" in msg
        assert "重新授权" not in msg

    def test_risk_disabled(self):
        from app.claim import billing_block_reason
        acc = Account.create("zai", "t", "a.b.c")
        acc.ban_for_risk()
        msg = billing_block_reason(acc) or ""
        assert "风控" in msg
        assert "重新授权" not in msg


# ── 网关主流程（HTTP 层，走 Mock 上游；夹具见 conftest.py）────────────────────
@pytest.mark.integration
class TestGatewayHTTP:
    async def test_models_endpoint(self, gateway_client):
        client, _ = gateway_client
        res = await client.get("/v1/models")
        assert res.status_code == 200
        ids = [m["id"] for m in res.json()["data"]]
        assert ids == ["GLM-5.3-Flash", "GLM-5.3"]

    async def test_messages_ok(self, gateway_client):
        client, upstream = gateway_client
        fresh_account("h1.eyJzdWIiOiJhIn0.sig")
        res = await client.post("/v1/messages", json={
            "model": "glm-5.2", "messages": [{"role": "user", "content": "hi"}],
        })
        assert res.status_code == 200
        assert res.json()["content"][0]["text"] == "Hello from mock upstream"
        # 上游收到归一化后的模型名与鉴权头
        method, path, headers, _ = upstream.state.calls[-1]
        assert path == "/api/v1/zcode-plan/anthropic/v1/messages"

    async def test_no_account_503(self, gateway_client):
        client, _ = gateway_client
        res = await client.post("/v1/messages", json={"model": "glm-5.2", "messages": []})
        assert res.status_code == 503
        assert res.json()["error"]["type"] == "no_available_account"


def fresh_account(secret: str) -> Account:
    """向当前 store 注入账号（gateway_client 用 fresh_app 的 store 单例）。"""
    from app.store import store
    return store.add_account("zai", "t", secret)


# ── 思考档位与业务错误码归一（3.14.3 对齐）──────────────────────────────────
class TestThinkingNormalization:
    def test_glm53_coerces_medium_and_minimal_effort(self):
        b1 = _normalize_body({
            "model": "glm-5.3-flash",
            "output_config": {"effort": "medium"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b1["model"] == "GLM-5.3-Flash"
        assert b1["output_config"] == {"effort": "high"}
        assert b1["thinking"] == {"type": "enabled"}

        b2 = _normalize_body({
            "model": "GLM-5.3",
            "output_config": {"effort": "minimal"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b2["output_config"] == {"effort": "low"}
        assert b2["thinking"] == {"type": "enabled"}

        b3 = _normalize_body({
            "model": "GLM-5.3",
            "output_config": {"effort": "xhigh"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b3["output_config"] == {"effort": "max"}

    def test_glm53_disabled_effort_removes_output_config(self):
        b = _normalize_body({
            "model": "GLM-5.3-Flash",
            "output_config": {"effort": "disabled"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b["thinking"] == {"type": "disabled"}
        assert "output_config" not in b

    def test_glm52_coerces_low_to_high_and_keeps_disabled(self):
        b1 = _normalize_body({
            "model": "glm-5.2",
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b1["output_config"] == {"effort": "high"}
        assert b1["thinking"] == {"type": "enabled"}

        b2 = _normalize_body({
            "model": "glm-5.2",
            "output_config": {"effort": "disabled"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b2["output_config"] == {"effort": "disabled"}
        assert b2["thinking"] == {"type": "disabled"}

    def test_glm5_turbo_strips_effort_and_uses_enable_mode(self):
        b = _normalize_body({
            "model": "glm-5-turbo",
            "output_config": {"effort": "high"},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b["model"] == "GLM-5-Turbo"
        assert "output_config" not in b
        assert b["thinking"] == {"type": "enabled"}

    def test_budget_tokens_converted_to_effort(self):
        b_low = _normalize_body({
            "model": "GLM-5.3-Flash",
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert "budget_tokens" not in b_low["thinking"]
        assert b_low["output_config"] == {"effort": "low"}

        b_high = _normalize_body({
            "model": "GLM-5.3",
            "thinking": {"type": "enabled", "budget_tokens": 16384},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b_high["output_config"] == {"effort": "high"}

        b_max = _normalize_body({
            "model": "GLM-5.3",
            "thinking": {"type": "enabled", "budget_tokens": 32768},
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert b_max["output_config"] == {"effort": "max"}


class TestBusinessErrorClassification:
    def _dummy_resp(self, status_code: int = 200):
        import httpx
        return httpx.Response(status_code)

    def test_http_200_exhaust_business_codes_mapped_to_402(self):
        from app.routes.gateway import _classify_business_error
        for code in ("1005", "1304", "1308", "2056", "20097", "insufficient_quota"):
            text = json.dumps({"code": code, "msg": "package quota limit"})
            eff_status, eff_code, is_conc = _classify_business_error(200, self._dummy_resp(200), text)
            assert eff_status == 402
            assert eff_code == code
            assert is_conc is False

    def test_http_200_auth_invalid_1006_mapped_to_401(self):
        from app.routes.gateway import _classify_business_error
        text = json.dumps({"code": 1006, "msg": "invalid token"})
        eff_status, eff_code, _ = _classify_business_error(200, self._dummy_resp(200), text)
        assert eff_status == 401 and eff_code == "1006"

    def test_http_200_captcha_3007_mapped_to_403(self):
        from app.routes.gateway import _classify_business_error
        text = json.dumps({"code": 3007, "msg": "verify required"})
        eff_status, eff_code, _ = _classify_business_error(200, self._dummy_resp(200), text)
        assert eff_status == 403 and eff_code == "3007"

    def test_concurrency_limit_codes_flagged_for_immediate_failover(self):
        from app.routes.gateway import _classify_business_error
        for code in ("3008", "3009", "3010"):
            text = json.dumps({"code": int(code), "msg": "concurrency limit exceeded"})
            eff_status, eff_code, is_conc = _classify_business_error(200, self._dummy_resp(200), text)
            assert eff_status == 429
            assert eff_code == code
            assert is_conc is True

    def test_normal_message_and_non_json_preserved(self):
        from app.routes.gateway import _classify_business_error
        msg_json = json.dumps({"type": "message", "content": [{"type": "text", "text": "ok"}]})
        assert _classify_business_error(200, self._dummy_resp(200), msg_json) == (200, None, False)
        assert _classify_business_error(200, self._dummy_resp(200), "<html>not json</html>") == (200, None, False)


class TestThinkingHistoryStripAndAffinity:
    def test_detects_signature_rejection_and_strips_without_mutating_caller(self):
        from app.routes.gateway import _is_thinking_signature_rejection, _strip_thinking_from_body

        err_text = '{"error":{"type":"invalid_request_error","message":"messages.1.content.0.thinking.signature: Invalid signature in thinking block"}}'
        assert _is_thinking_signature_rejection(400, err_text) is True

        original = {
            "model": "GLM-5.3",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "q1"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "step 1", "signature": "bad-sig"},
                        {"type": "redacted_thinking", "data": "enc"},
                        {"type": "text", "text": "a1"},
                    ],
                },
            ],
        }
        repaired = _strip_thinking_from_body(original)
        assert repaired is not None
        assert repaired["messages"][1]["content"] == [{"type": "text", "text": "a1"}]
        assert len(original["messages"][1]["content"]) == 3

    def test_session_affinity_key_and_sticky_failover(self):
        from app.routes.gateway import (
            _bind_sticky_account,
            _extract_session_affinity_key,
            _get_sticky_account,
            _session_affinity,
        )
        from app.store import store

        _session_affinity.clear()

        b_turn1 = {
            "model": "GLM-5.3",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello session cache"}]}],
        }
        key1, sticky1 = _extract_session_affinity_key("zai", b_turn1, {})
        assert key1 is not None and key1.startswith("zai:pfx:")
        assert sticky1 is False

        b_turn2 = {
            "model": "GLM-5.3",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Hello session cache"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Hi!"}]},
                {"role": "user", "content": [{"type": "text", "text": "Follow up question"}]},
            ],
        }
        key2, sticky2 = _extract_session_affinity_key("zai", b_turn2, {})
        assert key2 == key1
        assert sticky2 is True

        key_hdr, sticky_hdr = _extract_session_affinity_key("zai", b_turn1, {"X-Session-ID": "sess-xyz"})
        assert key_hdr == "zai:sid:sess-xyz"
        assert sticky_hdr is True

        acc1 = store.add_account("zai", "sticky-acc-1", "sk-sticky-test-1")
        acc2 = store.add_account("zai", "sticky-acc-2", "sk-sticky-test-2")
        try:
            _bind_sticky_account(key1, acc1.id)
            chosen = _get_sticky_account("zai", key1, set(), limit=0)
            assert chosen is not None and chosen.id == acc1.id

            assert _get_sticky_account("zai", key1, {acc1.id}, limit=0) is None
            acc1.status = Status.DISABLED
            assert _get_sticky_account("zai", key1, set(), limit=0) is None
        finally:
            store.remove_account("zai", acc1.id)
            store.remove_account("zai", acc2.id)
            _session_affinity.clear()


@pytest.mark.integration
class TestGatewayCrossChunkAnd200BusinessErrors:
    async def test_messages_nonstream_records_tokens(self, gateway_client, fresh_app):
        client, _mock = gateway_client
        from app import reqlog
        from tests.conftest import seed_account

        reqlog.clear()
        seed_account(fresh_app, "hTok.eyJzdWIiOiJ0b2sifQ.sig", name="tok-nonstream")
        res = await client.post("/v1/messages", json={
            "model": "GLM-5.3-Flash",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        })
        assert res.status_code == 200
        snap = reqlog.snapshot()
        assert snap[0]["input_tokens"] == 10
        assert snap[0]["output_tokens"] == 5

    async def test_upstream_to_streaming_extracts_tokens_across_tiny_chunks(self):
        from app import reqlog
        from app.routes.gateway import _Upstream

        reqlog.clear()
        req_id = "chunktest1"
        reqlog.begin(req_id, "messages", "GLM-5.3-Flash", True, "hi")

        sse_bytes = (
            b'event: message_start\n'
            b'data: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":42}}}\n\n'
            b'event: content_block_delta\n'
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n'
            b'event: message_delta\n'
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":19}}\n\n'
        )

        class _ChunkedResp:
            status_code = 200
            headers = {"content-type": "text/event-stream"}

            async def aiter_bytes(self):
                for i in range(0, len(sse_bytes), 7):
                    yield sse_bytes[i:i + 7]

        class _DummyCM:
            async def __aexit__(self, *args):
                return False

        up = _Upstream(_ChunkedResp(), _DummyCM(), None, t_first=0.01)
        streaming_resp = up.to_streaming(req_id)
        collected = b"".join([chunk async for chunk in streaming_resp.body_iterator])
        assert collected == sse_bytes

        snap = reqlog.snapshot()
        assert snap[0]["ok"] is True
        assert snap[0]["input_tokens"] == 42
        assert snap[0]["output_tokens"] == 19

    async def test_try_account_intercepts_http_200_exhaust_and_concurrency_and_thinking_retry(
        self, fresh_app, monkeypatch
    ):
        from app.routes import gateway as gateway_module
        from app.routes.gateway import _NEXT_ACCOUNT, _Upstream, _try_account

        acc1 = fresh_app.add_account("zai", "acc-200-exhaust", "h1.eyJzdWIiOiIxIn0.sig")
        acc2 = fresh_app.add_account("zai", "acc-3008-conc", "h2.eyJzdWIiOiIyIn0.sig")
        acc3 = fresh_app.add_account("zai", "acc-think-retry", "h3.eyJzdWIiOiIzIn0.sig")

        sleep_calls: list[float] = []

        async def _no_sleep(secs: float):
            sleep_calls.append(secs)

        monkeypatch.setattr(gateway_module, "_sleep", _no_sleep)
        monkeypatch.setattr(gateway_module, "_safe_refresh", lambda acc: _no_sleep(0))

        sent_payloads: list[dict] = []
        responses: list[tuple[int, dict]] = []

        class _FakeResp:
            def __init__(self, status_code: int, payload_dict: dict):
                self.status_code = status_code
                self.headers = {"content-type": "application/json"}
                self._raw = json.dumps(payload_dict).encode("utf-8")

            async def aread(self) -> bytes:
                return self._raw

        class _FakeCM:
            def __init__(self, resp: _FakeResp):
                self.resp = resp
                self.exits = 0

            async def __aenter__(self):
                return self.resp

            async def __aexit__(self, *args):
                self.exits += 1
                return False

        class _FakeClient:
            def stream(self, method: str, url: str, headers: dict, content: bytes):
                sent_payloads.append(json.loads(content))
                st, body_dict = responses.pop(0)
                return _FakeCM(_FakeResp(st, body_dict))

        fake_client = _FakeClient()
        monkeypatch.setattr(gateway_module, "_get_shared_client", lambda: fake_client)

        responses.append((200, {"code": 1005, "msg": "package expired"}))
        r1 = await _try_account("r1", acc1, {"model": "GLM-5.3", "messages": []}, {}, 3000, False)
        assert r1 is _NEXT_ACCOUNT
        assert acc1.status == Status.EXHAUSTED

        sleep_calls.clear()
        responses.append((200, {"code": 3008, "msg": "concurrency limit"}))
        r2 = await _try_account("r2", acc2, {"model": "GLM-5.3", "messages": []}, {}, 3000, False)
        assert r2 is _NEXT_ACCOUNT
        assert sleep_calls == []
        assert acc2.status == Status.ACTIVE

        responses.append((400, {"error": {"message": "Invalid signature in thinking block"}}))
        responses.append((200, {
            "type": "message",
            "content": [{"type": "text", "text": "repaired ok"}],
            "usage": {"input_tokens": 8, "output_tokens": 4},
        }))
        body_with_thinking = {
            "model": "GLM-5.3",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "old", "signature": "sig-bad"},
                        {"type": "text", "text": "prev"},
                    ],
                },
            ],
        }
        sent_payloads.clear()
        r3 = await _try_account("r3", acc3, body_with_thinking, {}, 3000, False)
        assert isinstance(r3, _Upstream)
        assert len(sent_payloads) == 2
        assert sent_payloads[0]["messages"][1]["content"][0]["type"] == "thinking"
        assert sent_payloads[1]["messages"][1]["content"] == [{"type": "text", "text": "prev", "cache_control": {"type": "ephemeral"}}]
        await r3.close()
