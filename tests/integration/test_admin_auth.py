"""管理鉴权：登录失败节流 + 设置接口不回明文密钥。"""

from __future__ import annotations

import json

import pytest


@pytest.mark.integration
class TestAdminAuthThrottle:
    @pytest.mark.parametrize("header", ["cf-connecting-ip", "x-real-ip"])
    async def test_spoofed_ip_header_cannot_bypass_lock(self, gateway_client, monkeypatch, header):
        """ADM-001：同一来源换 IP 头不能重置失败计数。"""
        from app import auth_admin

        monkeypatch.setattr(auth_admin, "ADMIN_FAIL_LIMIT", 3)
        auth_admin.reset_failures()
        client, _ = gateway_client

        for i in range(3):
            res = await client.get("/admin/api/verify", headers={
                "Authorization": "Bearer wrong-password",
                header: f"198.51.100.{i + 1}",
            })
            assert res.status_code == 401

        locked = await client.get("/admin/api/verify", headers={
            "Authorization": "Bearer zcode",
            header: "198.51.100.100",
        })
        assert locked.status_code == 429

    @pytest.mark.parametrize("peer_ip", ["127.0.0.1", "203.0.113.10"])
    async def test_forwarded_ip_respects_proxy_trust(self, fresh_app, monkeypatch, peer_ip):
        """ADM-002：转发头仅经可信代理生效，锁定按客户端隔离。"""
        from httpx import ASGITransport, AsyncClient
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        from app import auth_admin
        from app.main import create_app

        monkeypatch.setattr(auth_admin, "ADMIN_FAIL_LIMIT", 3)
        auth_admin.reset_failures()
        app = ProxyHeadersMiddleware(create_app(), trusted_hosts="127.0.0.1")
        transport = ASGITransport(app=app, client=(peer_ip, 12345))
        headers = {
            "Authorization": "Bearer wrong-password",
            "x-forwarded-for": "198.51.100.10",
            "cf-connecting-ip": "203.0.113.20",
            "x-real-ip": "203.0.113.30",
        }
        async with AsyncClient(transport=transport, base_url="http://gateway.test") as client:
            for _ in range(3):
                res = await client.get("/admin/api/verify", headers=headers)
                assert res.status_code == 401

            headers["Authorization"] = "Bearer zcode"
            headers["x-forwarded-for"] = "198.51.100.11"
            other = await client.get("/admin/api/verify", headers=headers)
            assert other.status_code == (200 if peer_ip == "127.0.0.1" else 429)

            headers["x-forwarded-for"] = "198.51.100.10"
            locked = await client.get("/admin/api/verify", headers=headers)
            assert locked.status_code == 429

    async def test_failed_logins_lock_out(self, gateway_client, monkeypatch):
        from app import auth_admin

        monkeypatch.setattr(auth_admin, "ADMIN_FAIL_LIMIT", 3)
        auth_admin.reset_failures()
        client, _ = gateway_client

        for _ in range(3):
            res = await client.get("/admin/api/verify",
                                   headers={"Authorization": "Bearer wrong-password"})
            assert res.status_code == 401

        locked = await client.get("/admin/api/verify",
                                  headers={"Authorization": "Bearer zcode"})
        assert locked.status_code == 429

    async def test_success_clears_failures(self, gateway_client, monkeypatch):
        from app import auth_admin

        monkeypatch.setattr(auth_admin, "ADMIN_FAIL_LIMIT", 3)
        auth_admin.reset_failures()
        client, _ = gateway_client

        await client.get("/admin/api/verify",
                         headers={"Authorization": "Bearer wrong-password"})
        ok = await client.get("/admin/api/verify",
                              headers={"Authorization": "Bearer zcode"})
        assert ok.status_code == 200

        for _ in range(3):
            res = await client.get("/admin/api/verify",
                                   headers={"Authorization": "Bearer wrong-password"})
            assert res.status_code == 401
        locked = await client.get("/admin/api/verify",
                                  headers={"Authorization": "Bearer zcode"})
        assert locked.status_code == 429


@pytest.mark.integration
class TestSettingsSecretMask:
    async def test_get_settings_masks_secrets(self, gateway_client, fresh_app):
        client, _ = gateway_client
        fresh_app.set_setting("admin_key", "super-secret-admin-key")
        fresh_app.set_setting("gateway_key", "sk-gateway-secret-value")

        res = await client.get("/admin/api/settings",
                               headers={"Authorization": "Bearer super-secret-admin-key"})
        assert res.status_code == 200
        data = res.json()
        dumped = json.dumps(data)
        assert "super-secret-admin-key" not in dumped
        assert "sk-gateway-secret-value" not in dumped
        assert data["admin_key_set"] is True
        assert data["gateway_key_set"] is True
        assert data["admin_key_is_default"] is False
        assert "admin_key_masked" in data
        assert "gateway_key_masked" in data

    async def test_default_admin_key_flag(self, gateway_client, fresh_app):
        client, _ = gateway_client
        res = await client.get("/admin/api/settings",
                               headers={"Authorization": "Bearer zcode"})
        assert res.status_code == 200
        data = res.json()
        assert data["admin_key_is_default"] is True
        assert data.get("admin_key") in (None, "")

    async def test_put_masked_admin_key_does_not_overwrite(self, gateway_client, fresh_app):
        client, _ = gateway_client
        fresh_app.set_setting("admin_key", "keep-this-admin-key")
        res = await client.put(
            "/admin/api/settings",
            json={"admin_key": "keep…key", "account_concurrency": 3},
            headers={"Authorization": "Bearer keep-this-admin-key"},
        )
        assert res.status_code == 200
        assert fresh_app.admin_key() == "keep-this-admin-key"
        assert fresh_app.account_concurrency() == 3
