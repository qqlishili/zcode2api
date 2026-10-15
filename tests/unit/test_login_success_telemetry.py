"""测试 app_login_success 事件体与登录态投递契约（组 1 + 组 2）。"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.fingerprint import DeviceProfile
from app.models import Account
from app.telemetry import (
    build_activation_event_body,
    post_activation_event,
    resolve_login_provider,
)


@pytest.fixture
def mock_profile():
    return DeviceProfile(
        platform="darwin",
        arch="arm64",
        os_version="14.5",
        language="zh-CN",
        timezone="Asia/Shanghai",
        screen="1920x1080",
        device_mid="mid-test-123",
    )


def test_resolve_login_provider():
    assert resolve_login_provider("zai") == "z.ai"
    assert resolve_login_provider("bigmodel") == "bigmodel"
    # 未知 provider 原样返回（不臆造）
    assert resolve_login_provider("unknown") == "unknown"


def test_build_event_body_default_region_is_app(mock_profile):
    body = build_activation_event_body("app_launch", mock_profile, "user-123")
    assert body["element_name"] == "app_launch"
    assert body["event_region"] == "app"
    assert body["event_type"] == "view"
    assert body["event_extra_detail"] == {}


def test_build_event_body_login_success_contract(mock_profile):
    body = build_activation_event_body(
        "app_login_success",
        mock_profile,
        "user-123",
        event_region="app_profile",
        event_type="view",
        event_extra_detail={"login_provider": "z.ai"},
    )
    assert body["element_name"] == "app_login_success"
    assert body["event_region"] == "app_profile"
    assert body["event_type"] == "view"
    assert body["event_extra_detail"] == {"login_provider": "z.ai"}
    # 字段集仍为官方 16 字段，不含 talk_id/message_id（会话类事件专属）
    assert "talk_id" not in body
    assert "message_id" not in body


@pytest.mark.asyncio
async def test_post_event_passes_authorization_headers(mock_profile):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"code": 0, "msg": "ok"}
    mock_client.post.return_value = mock_response

    headers = {"Content-Type": "application/json", "Authorization": "Bearer jwt"}
    await post_activation_event(
        mock_profile,
        "user-123",
        "app_login_success",
        client=mock_client,
        headers=headers,
        event_region="app_profile",
        event_extra_detail={"login_provider": "z.ai"},
    )
    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.await_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer jwt"


@pytest.mark.asyncio
async def test_post_event_default_headers_minimal(mock_profile):
    """未传 headers 时保持最小 Content-Type（匿名首启兼容）。"""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"code": 0, "msg": "ok"}
    mock_client.post.return_value = mock_response

    await post_activation_event(mock_profile, "user-123", "app_launch", client=mock_client)
    call_kwargs = mock_client.post.await_args.kwargs
    assert "Authorization" not in call_kwargs["headers"]
    assert call_kwargs["headers"] == {"Content-Type": "application/json"}


# ── 组 2：report_activation_events 的 app_login_success 补发与去重 ──────

def _jwt(uid: str) -> str:
    """构造 payload 含 user_id 的最小 JWT（仅用于身份解析）。"""
    import base64
    import json

    def seg(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg({'user_id': uid})}.sig"


def _account(installed: bool) -> Account:
    acc = Account(id="acc", name="t", provider="zai", mode="jwt", jwt_token=_jwt("uid-1"))
    if installed:
        acc.installed_at = 1234567890.0
    return acc


@pytest.mark.asyncio
async def test_report_activation_events_emits_login_once_when_not_installed():
    """未安装账号补发 app_login_success，事件域为 app_profile、provider 为 z.ai。"""
    from app import telemetry

    acc = _account(installed=False)
    posted: list[tuple] = []

    async def fake_post(profile, user_id, element, client=None, timeout=10, *,
                        headers=None, event_region="app", event_type="view",
                        event_extra_detail=None):
        posted.append((element, event_region, event_type, event_extra_detail))

    with patch("app.client_pool.account_client_pool") as pool, \
         patch.object(telemetry, "post_activation_event", side_effect=fake_post), \
         patch("app.identity.build_identity_headers", return_value={"X-Device-Mid": "mid"}):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        pool.operation.return_value.__aenter__.return_value = None
        pool.operation.return_value.__aexit__.return_value = None
        pool.current_operation.return_value.client = mock_client
        pool.begin_request.return_value = True
        pool.end_request.return_value = None

        from app import claim
        result = await claim.report_activation_events(acc)

    assert result is None
    elements = [e for e, *_ in posted]
    assert elements == ["app_launch", "app_daily_active", "app_login_success"]
    login = posted[-1]
    assert login[1] == "app_profile"
    assert login[2] == "view"
    assert login[3] == {"login_provider": "z.ai"}


@pytest.mark.asyncio
async def test_report_activation_events_skips_login_when_installed():
    """已安装账号不再补发 app_login_success（installed_at 去重）。"""
    from app import telemetry

    acc = _account(installed=True)
    posted: list[str] = []

    async def fake_post(profile, user_id, element, client=None, timeout=10, *,
                        headers=None, event_region="app", event_type="view",
                        event_extra_detail=None):
        posted.append(element)

    with patch("app.client_pool.account_client_pool") as pool, \
         patch.object(telemetry, "post_activation_event", side_effect=fake_post), \
         patch("app.identity.build_identity_headers", return_value={"X-Device-Mid": "mid"}):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        pool.operation.return_value.__aenter__.return_value = None
        pool.operation.return_value.__aexit__.return_value = None
        pool.current_operation.return_value.client = mock_client
        pool.begin_request.return_value = True
        pool.end_request.return_value = None

        from app import claim
        await claim.report_activation_events(acc)

    assert "app_login_success" not in posted
    assert posted == ["app_launch", "app_daily_active"]
