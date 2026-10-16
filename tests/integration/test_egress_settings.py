"""真实管理路由的配置契约，节点/SQLite 均为独立夹具。"""

import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from app import auth_admin
from app.egress_policy import EgressPolicy
from tests.unit.test_egress_operations import write_registry

AUTH = {"Authorization": "Bearer zcode"}


@pytest.fixture
def egress_env(gateway_client, isolated_client_pool, fresh_app):
    client, _ = gateway_client
    pool = isolated_client_pool
    auth_admin.reset_failures()
    write_registry(pool._registry_path, [(21100, "a" * 64, 1, "draining"),
                                         (21300, "b" * 64, 1, "active")])
    (pool._registry_path.parent / "node_lifecycle.json").write_text(json.dumps({"nodes": {
        "a" * 64: {"port": 21100, "epoch": 1, "region": "HK", "sources": ["private-subscription"],
                   "name": "HK 候选", "proxy": {"password": "private-password"}},
        "b" * 64: {"port": 21300, "epoch": 1, "region": "JP", "sources": ["private-subscription"],
                   "name": "JP 候选"},
    }}), encoding="utf-8")
    return client, pool, fresh_app


async def selection(client, region="HK"):
    response = await client.get("/admin/api/settings/egress/nodes", headers=AUTH)
    assert response.status_code == 200
    assert "private-password" not in response.text and "private-subscription" not in response.text
    node = next(n for n in response.json()["nodes"] if n["region"] == region)
    assert node["selectable"]
    return node["selection"]


async def test_authenticated_save_reset_and_legacy_filter(egress_env):
    client, pool, store = egress_env
    for endpoint in ("/settings/egress", "/settings/egress/nodes"):
        assert (await client.get("/admin/api" + endpoint)).status_code == 401
    assert (await client.put("/admin/api/settings/egress", json={})).status_code == 401
    original = store.active_egress_policy
    token = await selection(client)
    payload = EgressPolicy(chat_mode="direct", claim_mode="proxy").as_dict()
    payload["claim_bindings"] = {"HK": token}
    response = await client.put("/admin/api/settings/egress", json=payload, headers=AUTH)
    assert response.status_code == 200
    saved = response.json()
    assert saved["pending_restart"] and saved["active_source"] == "environment"
    assert saved["saved_selections"] == {"HK": token}
    assert store.active_egress_policy is original and pool.policy is original
    assert (await client.get("/admin/api/settings/egress", headers=AUTH)).json() == saved
    await client.put("/admin/api/settings", json={"egress_policy": "bad", "account_concurrency": 3}, headers=AUTH)
    assert store.egress_settings() == saved and store.account_concurrency() == 3
    reset = await client.put("/admin/api/settings/egress", json={"restore_environment": True}, headers=AUTH)
    assert reset.status_code == 200 and not reset.json()["pending_restart"]
    assert store.account_concurrency() == 3


@pytest.mark.parametrize("payload", [[], {}, {"restore_environment": False},
    {**EgressPolicy().as_dict(), "admin_key": "bad"},
    {**EgressPolicy().as_dict(), "chat_mode": "wrong"},
    {**EgressPolicy().as_dict(), "chat_mode": "proxy", "chat_proxy_url": "http://example.com:80"},
    {**EgressPolicy().as_dict(), "claim_bindings": {"HK": {"port": 21100}}},
])
async def test_invalid_configuration_is_atomic(egress_env, payload):
    client, _, store = egress_env
    original = store.egress_settings()
    response = await client.put("/admin/api/settings/egress", json=payload, headers=AUTH)
    assert response.status_code == 400
    assert store.egress_settings() == original


async def test_cross_region_and_changed_node_rejected(egress_env):
    client, pool, store = egress_env
    token = await selection(client)
    payload = EgressPolicy(claim_mode="proxy").as_dict()
    payload["claim_bindings"] = {"JP": token}
    assert (await client.put("/admin/api/settings/egress", json=payload, headers=AUTH)).status_code == 400
    write_registry(pool._registry_path, [(21100, "c" * 64, 2, "draining")])
    payload["claim_bindings"] = {"HK": token}
    assert (await client.put("/admin/api/settings/egress", json=payload, headers=AUTH)).status_code == 409
    assert store.active_egress_source == "environment" and not store.egress_settings()["pending_restart"]


async def test_database_failure_preserves_both_views(egress_env, monkeypatch):
    client, _, store = egress_env
    original = store.egress_settings()
    def fail():
        raise sqlite3.OperationalError("isolated write failure")
    monkeypatch.setattr(store, "_connect", fail)
    response = await client.put("/admin/api/settings/egress", json=EgressPolicy().as_dict(), headers=AUTH)
    assert response.status_code == 500 and store.egress_settings() == original


async def test_manual_browser_token_rejected_before_upstream(egress_env, monkeypatch):
    from app.routes import admin_api

    client, _, store = egress_env
    monkeypatch.setattr(store, "active_egress_policy", EgressPolicy(claim_mode="direct"))
    send = AsyncMock()
    monkeypatch.setattr(admin_api, "claim_with_captcha", send)
    config = await client.get("/admin/api/claim/captcha-config", headers=AUTH)
    assert config.json()["verification_mode"] == "server"
    response = await client.post("/admin/api/claim/manual", headers=AUTH,
                                 json={"account_id": "any", "captcha_verify_param": "browser"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "claim_verification_route_mismatch"
    send.assert_not_awaited()

async def test_delete_account_closes_all_purposes_after_refs_release(egress_env):
    from app.client_pool import RegionOfflineError
    from tests.conftest import seed_account

    client, pool, store = egress_env
    account = seed_account(store, "h.eyJzdWIiOiJkZWxldGUifQ.sig", name="delete")
    pool._policy = EgressPolicy(chat_mode="direct", claim_mode="direct")
    chat = await pool.acquire_operation(account, purpose="chat")
    claim = await pool.acquire_operation(account, purpose="claim")
    response = await client.request("DELETE", "/admin/api/accounts", headers=AUTH, json=[account.id])
    assert response.status_code == 200 and response.json()["deleted"] == 1
    assert not chat.client.is_closed and not claim.client.is_closed
    with pytest.raises(RegionOfflineError):
        await pool.acquire_operation(account, purpose="chat")
    await pool.release_operation(chat)
    await pool.release_operation(claim)
    assert chat.client.is_closed and claim.client.is_closed
