"""测试 telemetry 与 install 模块的代理与 Client 透传契约。"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.fingerprint import DeviceProfile
from app.install import _fetch_client_configs, run_install_sequence_for_account
from app.models import Account
from app.telemetry import post_activation_event


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


@pytest.mark.asyncio
async def test_post_activation_event_reuses_injected_client(mock_profile):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"code": 0, "msg": "ok"}
    mock_client.post.return_value = mock_response

    # 传入 mock_client 时，必须直接调用该 client.post，不得创建临时 Client
    await post_activation_event(mock_profile, "user-123", "app_launch", client=mock_client)
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.await_args
    assert "event/report" in call_args[0][0] or "event" in str(call_args)


@pytest.mark.asyncio
async def test_fetch_client_configs_reuses_injected_client():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"code": 0, "data": {"configs": {}}}
    mock_client.get.return_value = mock_response

    res = await _fetch_client_configs(client=mock_client)
    mock_client.get.assert_awaited_once()
    assert isinstance(res, dict)


@pytest.mark.asyncio
async def test_run_install_sequence_for_account_passes_pool_client(mock_profile):
    acc = Account(id="acc-test", name="test", provider="zai", mode="jwt", jwt_token="a.b.c", assigned_region="TW")

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_resp_config = MagicMock(status_code=200)
    mock_resp_config.json.return_value = {"code": 0, "data": {}}
    mock_resp_event = MagicMock(status_code=200)
    mock_resp_event.json.return_value = {"code": 0, "data": {}}
    mock_client.get.return_value = mock_resp_config
    mock_client.post.return_value = mock_resp_event

    with patch("app.client_pool.account_client_pool.get_client", AsyncMock(return_value=mock_client)) as mock_get_client, \
         patch("app.store.store.find", return_value=acc), \
         patch("app.store.store.update_account"):

        result = await run_install_sequence_for_account(acc)
        mock_get_client.assert_awaited_once_with(acc)
        # client 必须被用来拉取 config 和上报事件
        assert mock_client.get.await_count >= 1
        assert mock_client.post.await_count >= 1
        assert result["installed"] is True
