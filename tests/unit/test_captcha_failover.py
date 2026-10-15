"""测试网关人机校验失败时的账号级容灾避让与平滑换号调度，杜绝全局 500 熔断。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.captcha import CaptchaSolveError
from app.models import Account, Status
from app.routes.gateway import _AccountFailure, _dispatch, _try_account, _Upstream
from app.store import Store


@pytest.fixture
def clean_store(tmp_path, monkeypatch):
    db_file = tmp_path / "accounts_test.db"
    monkeypatch.setattr("app.settings.DB_PATH", db_file)
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)
    isolated_store = Store()
    monkeypatch.setattr("app.routes.gateway.store", isolated_store)
    return isolated_store


@pytest.mark.asyncio
async def test_try_account_captcha_error_triggers_failover(monkeypatch):
    """验证 _try_account 遇到验证码失败时，返回 captcha_error 失败对象而非直接 500。"""
    acc = Account(
        id="acc-failover-1",
        name="user-jp",
        provider="zai",
        mode="jwt",
        jwt_token="fake.jwt.token",
        assigned_region="JP",
        status=Status.ACTIVE,
    )
    monkeypatch.setattr("app.client_pool.account_client_pool._get_client_locked", MagicMock())

    with patch("app.captcha.captcha_manager.get_verify_param", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = CaptchaSolveError("验证码求解失败: connect ECONNREFUSED 127.0.0.1:21302")

        res = await _try_account(
            req_id="req-123",
            account=acc,
            body={"model": "glm-5.3-flash", "messages": [{"role": "user", "content": "hi"}]},
            incoming_headers={},
            port=None,
            needs_captcha=True,
            slot_box=[None],
        )

        # 必须是 _AccountFailure 换号事件，绝对不能是 JSONResponse(500)
        assert isinstance(res, _AccountFailure)
        assert res.code == "captcha_error"
        assert res.stage == "captcha"


@pytest.mark.asyncio
async def test_dispatch_captcha_error_switches_to_next_account(clean_store):
    """验证端到端 dispatch：第一个账号验证码失败时自动切换到第二个健康账号成功完成请求。"""
    acc1 = clean_store.add_account("zai", "acc-1-bad", "tok1.bad.sig")
    acc1.status = Status.ACTIVE
    acc1.assigned_region = "JP"
    acc1.mode = "jwt"
    clean_store.update_account(acc1)

    acc2 = clean_store.add_account("zai", "acc-2-good", "tok2.good.sig")
    acc2.status = Status.ACTIVE
    acc2.assigned_region = "HK"
    acc2.mode = "jwt"
    clean_store.update_account(acc2)

    from unittest.mock import MagicMock

    mock_client = AsyncMock()
    mock_client.stream = MagicMock()
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.aread.return_value = b'{"id":"msg_2","choices":[{"message":{"content":"success from acc2"}}]}'
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_resp
    mock_cm.__aexit__.return_value = None
    mock_client.stream.return_value = mock_cm

    calls = 0
    async def fake_get_verify(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CaptchaSolveError("JP 节点抖动求解失败: connect ECONNREFUSED 127.0.0.1:21302")
        return "verify-param-good", "HK"

    with patch("app.captcha.captcha_manager.get_verify_param", side_effect=fake_get_verify), \
         patch("app.client_pool.account_client_pool._get_client_locked", new_callable=MagicMock, return_value=mock_client), \
         patch("app.client_pool.account_client_pool.get_healthy_regions", return_value=["JP", "HK"]):

        body = {"model": "glm-5.3-flash", "messages": [{"role": "user", "content": "hello"}]}
        result = await _dispatch(
            req_id="req-dispatch-test",
            body=body,
            incoming_headers={},
            port=None,
            provider="zai",
        )

        assert isinstance(result, _Upstream)
        assert result.account_name == acc2.name
        await result.close()


@pytest.mark.asyncio
async def test_dispatch_all_captcha_error_returns_structured_503(clean_store):
    """验证全池账号验证码均失败时，网关统一返回 503 且包含 captcha_error 诊断信息。"""
    acc1 = clean_store.add_account("zai", "acc-bad-1", "tok1.bad.sig")
    acc1.status = Status.ACTIVE
    acc1.assigned_region = "JP"
    acc1.mode = "jwt"
    clean_store.update_account(acc1)

    with patch("app.captcha.captcha_manager.get_verify_param", side_effect=CaptchaSolveError("求解超时")), \
         patch("app.client_pool.account_client_pool._get_client_locked", new_callable=MagicMock), \
         patch("app.client_pool.account_client_pool.get_healthy_regions", return_value=["JP"]):

        body = {"model": "glm-5.3-flash", "messages": [{"role": "user", "content": "hello"}]}
        response = await _dispatch(
            req_id="req-all-bad",
            body=body,
            incoming_headers={},
            port=None,
            provider="zai",
        )

        assert response.status_code == 503
        import json
        data = json.loads(response.body.decode("utf-8"))
        assert data["error"]["code"] == "captcha_error"
        assert data["error"]["retryable"] is True
