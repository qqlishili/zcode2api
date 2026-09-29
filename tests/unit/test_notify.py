"""app/notify.py 单元测试：URL/Key 归一化解析、格式校验与非阻塞投递。"""

from __future__ import annotations

import httpx
import pytest

from app.notify import is_valid_bark_key, normalize_bark_config, send_bark_notification


def test_normalize_bark_config_raw_key():
    server, key = normalize_bark_config("abcdef123456")
    assert server == "https://api.day.app"
    assert key == "abcdef123456"


def test_normalize_bark_config_official_url():
    server, key = normalize_bark_config("https://api.day.app/abcdef123456/")
    assert server == "https://api.day.app"
    assert key == "abcdef123456"


def test_normalize_bark_config_custom_server():
    server, key = normalize_bark_config("https://bark.myhost.com/abcdef123456")
    assert server == "https://bark.myhost.com"
    assert key == "abcdef123456"


def test_normalize_bark_config_query_params():
    server, key = normalize_bark_config("https://bark.myhost.com/push?device_key=abcdef123456")
    assert server == "https://bark.myhost.com"
    assert key == "abcdef123456"


def test_normalize_bark_config_empty():
    server, key = normalize_bark_config("")
    assert server == "https://api.day.app"
    assert key == ""


def test_is_valid_bark_key():
    assert is_valid_bark_key("abcdef123456") is True
    assert is_valid_bark_key("abc_def-123456") is True
    assert is_valid_bark_key("short") is False  # <10 chars
    assert is_valid_bark_key("inv@lid#key!!!") is False  # 非法符号
    assert is_valid_bark_key("") is False


@pytest.mark.asyncio
async def test_send_bark_notification_invalid_key():
    ok, msg = await send_bark_notification("short")
    assert ok is False
    assert "格式非法" in msg


@pytest.mark.asyncio
async def test_send_bark_notification_success(monkeypatch):
    class MockResponse:
        status_code = 200
        text = '{"code": 200, "message": "success"}'

        def json(self):
            return {"code": 200, "message": "success"}

    async def mock_post(self, endpoint, json=None):
        assert endpoint == "https://api.day.app/push"
        assert json["device_key"] == "abcdef123456"
        assert json["title"] == "测试标题"
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    ok, msg = await send_bark_notification(
        device_key="abcdef123456",
        title="测试标题",
        body="测试内容",
    )
    assert ok is True
    assert "成功" in msg


@pytest.mark.asyncio
async def test_send_bark_notification_http_error(monkeypatch):
    async def mock_post(self, endpoint, json=None):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    ok, msg = await send_bark_notification(
        device_key="abcdef123456",
        title="测试标题",
        body="测试内容",
        retries=0,
    )
    assert ok is False
    assert "网络错误" in msg


@pytest.mark.asyncio
async def test_send_bark_notification_retry_recovers_from_timeout(monkeypatch):
    """验证首次请求超时（如连接 APNs 抖动）时自动退避重试并在第二次成功。"""
    import asyncio

    from app.notify import notify_claim_outcomes
    from app.store import store

    attempts = 0

    class MockResponse:
        status_code = 200
        text = '{"code": 200, "message": "success"}'

        def json(self):
            return {"code": 200, "message": "success"}

    async def mock_post(self, endpoint, json=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.TimeoutException("Read timed out")
        return MockResponse()

    async def _noop(sec):
        return None

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    monkeypatch.setattr(asyncio, "sleep", _noop)

    ok, msg = await send_bark_notification(
        device_key="abcdef123456",
        title="测试重试",
        body="内容",
        retries=2,
    )
    assert ok is True
    assert attempts == 2

    # 同时验证 notify_claim_outcomes 仅推送非 skipped 的成功项
    store.set_setting("bark_device_key", "abcdef123456")
    sent_payloads = []

    async def mock_post_capture(self, endpoint, json=None):
        sent_payloads.append(json)
        return MockResponse()

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_capture)
    ok2, _ = await notify_claim_outcomes(
        [
            {"account_name": "已领号", "ok": True, "skipped": True},
            {
                "account_name": "新领号",
                "ok": True,
                "plan_name": "ZCode Trust Build",
                "grants": [{"name": "GLM-5.3-Flash", "units": 100000000}],
            },
        ],
        source="一键领取",
    )
    assert ok2 is True
    assert len(sent_payloads) == 1
    assert "1项" in sent_payloads[0]["title"]
    assert "新领号" in sent_payloads[0]["body"]
    assert "已领号" not in sent_payloads[0]["body"]

