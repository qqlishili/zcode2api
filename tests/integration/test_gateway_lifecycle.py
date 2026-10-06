"""INT-040/041：网关夹具退出时等待后台任务并关闭连接。"""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import gateway_client, seed_account

_REFRESH_JWT = "lifecycle.eyJzdWIiOiJsaWZlY3ljbGUifQ.sig"


@pytest.mark.integration
class TestGatewayLifecycle:
    async def test_teardown_finishes_quota_refresh_and_closes_client(
        self, fresh_app, mock_server, monkeypatch, stub_captcha,
    ):
        from app.routes import gateway

        account = seed_account(fresh_app, _REFRESH_JWT, name="lifecycle")
        started = asyncio.Event()
        release = asyncio.Event()
        original_refresh = gateway._safe_refresh

        async def _held_refresh(account):
            started.set()
            await release.wait()
            await original_refresh(account)

        monkeypatch.setattr(gateway, "_safe_refresh", _held_refresh)
        fixture = gateway_client.__wrapped__(fresh_app, mock_server, monkeypatch, stub_captcha)
        client, _ = await anext(fixture)
        task = None
        try:
            response = await client.post("/v1/messages", json={
                "model": "GLM-5.3-Flash", "max_tokens": 64,
                "messages": [{"role": "user", "content": "lifecycle fixture"}],
            })
            assert response.status_code == 200
            await asyncio.wait_for(started.wait(), timeout=5)
            task, = [t for t in gateway._bg_tasks if t.get_loop() is asyncio.get_running_loop()]
            shared = gateway._get_shared_client()
            assert not task.done() and not shared.is_closed
            release.set()
            await fixture.aclose()
            assert task.done() and not task.cancelled()
            assert fresh_app.find("zai", account.id).last_checked_at is not None
            assert shared.is_closed
            assert asyncio.get_running_loop() not in gateway._SHARED_CLIENTS
        finally:
            release.set()
            if task is not None:
                await task
            await fixture.aclose()
            await gateway.close_shared_client()

    async def test_teardown_reports_background_failure_and_closes_client(
        self, fresh_app, mock_server, monkeypatch, stub_captcha,
    ):
        from app.routes import gateway

        fixture = gateway_client.__wrapped__(fresh_app, mock_server, monkeypatch, stub_captcha)
        await anext(fixture)
        shared = gateway._get_shared_client()

        async def _boom():
            await asyncio.sleep(0)
            raise RuntimeError("fixture background failure")

        gateway._spawn_bg(_boom())
        task, = [t for t in gateway._bg_tasks if t.get_loop() is asyncio.get_running_loop()]
        try:
            with pytest.raises(RuntimeError, match="fixture background failure"):
                await fixture.aclose()
            assert task.done()
            assert shared.is_closed
        finally:
            await asyncio.gather(task, return_exceptions=True)
            await fixture.aclose()
            await gateway.close_shared_client()
