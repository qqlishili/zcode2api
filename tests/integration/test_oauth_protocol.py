"""OAuth 官方协议跟进（2026-09，zcode-switch oauth.rs 同形）：

- init 响应 data.poll_token 由服务端下发，后续 poll 必须采用该值
- 服务端不返回 poll_token 时回落自造（旧协议兼容）
- poll 4xx 承载业务码 3004 → expired（会话过期），不再是 failed
- init 响应带非零业务码 → 明确报错
"""

from __future__ import annotations

import asyncio

import pytest


def _oauth_header_names(headers: dict) -> set[str]:
    return {str(k).lower() for k in headers}


async def _drain_followup() -> None:
    from app.routes import admin_api

    pending = list(admin_api._login_followup_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.integration
class TestOAuthProtocol:
    @pytest.fixture(autouse=True)
    async def _reset_mock(self, gateway_client):
        _, mock = gateway_client
        mock.state.oauth_state = "pending"
        mock.state.oauth_poll_status = 200
        mock.state.oauth_poll_body_code = None
        mock.state.oauth_server_poll_token = None
        yield
        mock.state.oauth_poll_body_code = None
        mock.state.oauth_server_poll_token = None
        await _drain_followup()

    def _last_call(self, mock, suffix: str) -> dict:
        headers = next(h for m, p, h, _ in reversed(mock.state.calls) if p.endswith(suffix))
        assert _oauth_header_names(headers) >= {"authorization"}
        return headers

    async def test_init_adopts_server_poll_token(self, gateway_client):
        """官方新协议：init 响应下发 poll_token，poll 必须改用服务端值。"""
        client, mock = gateway_client
        server_token = "s" * 64
        mock.state.oauth_server_poll_token = server_token
        start = (await client.post("/admin/api/login/start",
                                   headers={"Authorization": "Bearer zcode"})).json()
        assert start["flow_id"].startswith("mock-flow-")
        await client.get(f"/admin/api/login/poll/{start['flow_id']}",
                         headers={"Authorization": "Bearer zcode"})
        poll_headers = self._last_call(mock, f"/oauth/cli/poll/{start['flow_id']}")
        assert str(poll_headers.get("authorization")) == f"Bearer {server_token}"

    async def test_init_without_server_token_falls_back_to_self_generated(self, gateway_client):
        """旧协议兼容：init 未下发 poll_token 时，poll 沿用 init 时的自造 token。"""
        client, mock = gateway_client
        start = (await client.post("/admin/api/login/start",
                                   headers={"Authorization": "Bearer zcode"})).json()
        init_headers = self._last_call(mock, "/oauth/cli/init")
        init_auth = str(init_headers.get("authorization") or "")
        assert init_auth.startswith("Bearer ") and len(init_auth) > len("Bearer ")
        await client.get(f"/admin/api/login/poll/{start['flow_id']}",
                         headers={"Authorization": "Bearer zcode"})
        poll_headers = self._last_call(mock, f"/oauth/cli/poll/{start['flow_id']}")
        assert str(poll_headers.get("authorization")) == init_auth

    async def test_poll_3004_maps_to_expired(self, gateway_client):
        """官方 poll 4xx 承载 code=3004 → expired（会话过期），不再是 failed。"""
        client, mock = gateway_client
        mock.state.oauth_poll_status = 403
        mock.state.oauth_poll_body_code = 3004
        start = (await client.post("/admin/api/login/start",
                                   headers={"Authorization": "Bearer zcode"})).json()
        res = await client.get(f"/admin/api/login/poll/{start['flow_id']}",
                               headers={"Authorization": "Bearer zcode"})
        data = res.json()
        assert data["status"] == "expired"
        assert "过期" in (data.get("message") or "")

    async def test_poll_4xx_other_codes_still_failed(self, gateway_client):
        """非 3004 的 poll 4xx 仍为 failed（2.5.7 语义保持）。"""
        client, mock = gateway_client
        mock.state.oauth_poll_status = 401
        mock.state.oauth_poll_body_code = 1001
        start = (await client.post("/admin/api/login/start",
                                   headers={"Authorization": "Bearer zcode"})).json()
        res = await client.get(f"/admin/api/login/poll/{start['flow_id']}",
                               headers={"Authorization": "Bearer zcode"})
        assert res.json()["status"] == "failed"
