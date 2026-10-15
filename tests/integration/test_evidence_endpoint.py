"""落盘证据管理端点：/admin/api/evidence/summary（跨重启的可用性判定入口）。"""

from __future__ import annotations

import pytest

from app import evidence, settings


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    """本文件的用例要断言落盘行为：显式开启并指向 tmp_path（全局默认关闭）。"""
    monkeypatch.setattr(settings, "EVIDENCE_ENABLED", 1)
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    evidence.reset()
    yield
    evidence.reset()


async def test_evidence_summary_endpoint(gateway_client, fresh_app):
    client, _ = gateway_client

    res = await client.get("/admin/api/evidence/summary?hours=1",
                           headers={"Authorization": "Bearer zcode"})

    assert res.status_code == 200
    body = res.json()
    assert body["window_hours"] == 1
    assert "available" in body
    assert {"total", "ok", "success_rate", "t_first_p50"} <= set(body["requests"])
    assert {"total", "hits", "hit_rate", "by_port", "by_reason"} <= set(body["solves"])


async def test_evidence_summary_requires_admin_key(gateway_client):
    client, _ = gateway_client

    res = await client.get("/admin/api/evidence/summary")

    assert res.status_code == 401


async def test_evidence_summary_reflects_persisted_request(gateway_client, fresh_app):
    client, _ = gateway_client
    evidence.record_request({
        "req_id": "fixture-req", "endpoint": "messages", "model": "GLM-5.3-Flash",
        "stream": True, "account": "acc@example.com", "mode": "jwt", "ok": False,
        "status": 499, "error": "客户端断开", "t_first": 12.0, "t_total": 12.4,
    })

    body = (await client.get("/admin/api/evidence/summary?hours=1",
                             headers={"Authorization": "Bearer zcode"})).json()

    assert body["available"] is True
    assert body["requests"]["total"] >= 1
    assert body["requests"]["t_first_p50"] == 12.0
