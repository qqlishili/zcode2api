"""测试验证码 Token 的大区亲和性隔离与 Node 子进程代理环境变量注入。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.captcha import CaptchaManager, _Token
from app.models import Account


@pytest.fixture
async def captcha_mgr(isolated_client_pool):
    mgr = CaptchaManager()
    yield mgr
    await mgr.close()


@pytest.mark.asyncio
async def test_captcha_token_proxy_affinity_isolation(captcha_mgr):
    """验证 HK Token 绝不被 TW 账号误领，池中无同代理 Token 时触发 On-Demand 现解且保留原 Token。"""
    # 1. 预先向池中置入 HK 代理生成的 Token
    hk_token = _Token(param="verify-hk-123", region="HK", proxy="http://127.0.0.1:21100")
    captcha_mgr._put(hk_token)
    assert captcha_mgr._pool_size == 1

    # 2. 构造 TW 账号，其代理映射为 21200
    acc_tw = Account(
        id="acc-tw",
        name="tw-user",
        provider="zai",
        mode="jwt",
        jwt_token="fake.jwt.token",
        assigned_region="TW",
    )

    with patch("app.client_pool.account_client_pool.resolve_proxy", return_value="http://127.0.0.1:21200"), \
         patch.object(captcha_mgr, "fetch_config", new_callable=AsyncMock) as mock_config, \
         patch.object(captcha_mgr, "_solve_one_unlocked", new_callable=AsyncMock) as mock_solve, \
         patch.object(captcha_mgr, "_refill_batch", new_callable=AsyncMock):

        mock_config.return_value = {"sceneId": "test-scene", "region": "cn", "prefix": "pref"}
        mock_solve.return_value = _Token(
            param="verify-tw-456",
            region="TW",
            proxy="http://127.0.0.1:21200",
        )

        # 3. 消费端请求 TW 账号的验证码
        param, region = await captcha_mgr.get_verify_param(account=acc_tw)

        # 4. 断言：获取的是 TW 现解出的 Token，而非池中的 HK Token
        assert param == "verify-tw-456"
        assert region == "TW"
        mock_solve.assert_awaited_once_with(mock_config.return_value, proxy="http://127.0.0.1:21200")

        # 5. 断言：原池中的 HK Token 未被消耗，安全放回
        assert captcha_mgr._pool_size == 1
        remaining_token = captcha_mgr._pool.get_nowait()
        assert remaining_token.param == "verify-hk-123"
        assert remaining_token.proxy == "http://127.0.0.1:21100"


@pytest.mark.asyncio
async def test_captcha_token_proxy_match_success(captcha_mgr):
    """验证池中存在同代理 Token 时直接命中，无需 On-Demand 现解。"""
    hk_token = _Token(param="verify-hk-123", region="HK", proxy="http://127.0.0.1:21100")
    tw_token = _Token(param="verify-tw-456", region="TW", proxy="http://127.0.0.1:21200")
    captcha_mgr._put(hk_token)
    captcha_mgr._put(tw_token)
    assert captcha_mgr._pool_size == 2

    acc_tw = Account(
        id="acc-tw",
        name="tw-user",
        provider="zai",
        mode="jwt",
        jwt_token="fake.jwt.token",
        assigned_region="TW",
    )

    with patch("app.client_pool.account_client_pool.resolve_proxy", return_value="http://127.0.0.1:21200"), \
         patch.object(captcha_mgr, "_solve_one_unlocked", new_callable=AsyncMock) as mock_solve, \
         patch.object(captcha_mgr, "_refill_batch", new_callable=AsyncMock):

        param, region = await captcha_mgr.get_verify_param(account=acc_tw)

        assert param == "verify-tw-456"
        assert region == "TW"
        mock_solve.assert_not_called()

        # 剩余池中只剩 HK Token
        assert captcha_mgr._pool_size == 1
        remaining_token = captcha_mgr._pool.get_nowait()
        assert remaining_token.param == "verify-hk-123"


@pytest.mark.asyncio
async def test_run_solver_injects_proxy_environment(captcha_mgr, monkeypatch):
    """验证 _run_solver 在启动 Node 进程时注入 HTTP_PROXY / HTTPS_PROXY 环境变量。"""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"VERIFY_PARAM=test-param-xyz\n", b"")
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
         patch("pathlib.Path.exists", return_value=True):
        mock_exec.return_value = mock_proc

        target_proxy = "http://127.0.0.1:21300"
        param = await captcha_mgr._run_solver("scene", "cn", "prefix", proxy=target_proxy)

        assert param == "test-param-xyz"
        mock_exec.assert_awaited_once()

        # 校验 kwargs 中的 env 参数
        _, kwargs = mock_exec.call_args
        env_passed = kwargs.get("env")
        assert env_passed is not None
        assert env_passed.get("HTTP_PROXY") == target_proxy
        assert env_passed.get("HTTPS_PROXY") == target_proxy


@pytest.mark.asyncio
async def test_run_solver_captures_stderr_on_failure(captcha_mgr):
    """无结构化摘要时只保留安全未知类别，不能透传 stderr 地址。"""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (b"", b"TypeError: fetch failed (connect ECONNREFUSED 127.0.0.1:21302)\n")
    mock_proc.returncode = 1

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec, \
         patch("pathlib.Path.exists", return_value=True):
        mock_exec.return_value = mock_proc

        with pytest.raises(RuntimeError) as exc_info:
            await captcha_mgr._run_solver("scene", "cn", "prefix", proxy="http://127.0.0.1:21302")

        assert str(exc_info.value) == "solver_failed_unknown"
        assert "ECONNREFUSED" not in str(exc_info.value)


def test_pick_proxy_for_refill_balances_regions(captcha_mgr):
    """验证 _pick_proxy_for_refill 会自适应优先选择当前池中存量最少的健康代理。"""
    candidates = ["http://127.0.0.1:21100", "http://127.0.0.1:21200", "http://127.0.0.1:21300"]

    # 1. 空池时，优先选择首选大区 HK
    assert captcha_mgr._pick_proxy_for_refill(candidates) == "http://127.0.0.1:21100"

    # 2. 放入一个 HK Token 后，存量最少的为 TW (21200)
    captcha_mgr._put(_Token("tok1", "HK", proxy="http://127.0.0.1:21100"))
    assert captcha_mgr._pick_proxy_for_refill(candidates) == "http://127.0.0.1:21200"

    # 3. 再放入一个 TW Token 后，存量最少的为 JP (21300)
    captcha_mgr._put(_Token("tok2", "TW", proxy="http://127.0.0.1:21200"))
    assert captcha_mgr._pick_proxy_for_refill(candidates) == "http://127.0.0.1:21300"


def test_client_pool_get_healthy_proxies(tmp_path):
    """验证 AccountClientPool.get_healthy_proxies 按大区优先级正确收集首选代理。"""
    import json

    from app.client_pool import AccountClientPool

    reg_file = tmp_path / "egress_registry.json"
    registry_data = {
        "updated_at": 1728600000.0,
        "regions": {
            "HK": ["http://127.0.0.1:21100", "http://127.0.0.1:21101"],
            "TW": ["http://127.0.0.1:21200"],
            "JP": ["http://127.0.0.1:21300"],
        },
    }
    reg_file.write_text(json.dumps(registry_data), encoding="utf-8")

    pool = AccountClientPool(registry_path=reg_file)
    proxies = pool.get_healthy_proxies()
    assert proxies == [
        "http://127.0.0.1:21100",
        "http://127.0.0.1:21200",
        "http://127.0.0.1:21300",
    ]

