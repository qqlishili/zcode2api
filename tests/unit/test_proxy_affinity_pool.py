"""测试代理亲和预解水库、O(1) 弹栈与 Singleflight 单飞防惊群并发合并。"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.captcha import CaptchaManager, _Token
from app.models import Account


@pytest.fixture
def captcha_mgr():
    return CaptchaManager()


@pytest.mark.asyncio
async def test_proxy_affinity_reservoir_storage_and_o1_pop(captcha_mgr):
    """测试基于 deque 的代理亲和存储与 O(1) 弹栈。"""
    proxy_a = "http://127.0.0.1:21100"
    proxy_b = "http://127.0.0.1:21200"

    t1 = _Token(param="token-a-1", region="HK", proxy=proxy_a)
    t2 = _Token(param="token-a-2", region="HK", proxy=proxy_a)
    t3 = _Token(param="token-b-1", region="TW", proxy=proxy_b)

    captcha_mgr._put(t1)
    captcha_mgr._put(t2)
    captcha_mgr._put(t3)

    assert len(captcha_mgr._proxy_pools[proxy_a]) == 2
    assert len(captcha_mgr._proxy_pools[proxy_b]) == 1

    with patch.object(captcha_mgr, "_solve_singleflight", new_callable=AsyncMock) as mock_solve, \
         patch.object(captcha_mgr, "_refill_single_proxy", new_callable=AsyncMock):

        # 从 proxy_a 取 Token，O(1) 弹栈，按 FIFO 取出 t1
        param1, reg1 = await captcha_mgr.get_verify_param(proxy=proxy_a)
        assert param1 == "token-a-1"
        assert reg1 == "HK"
        assert len(captcha_mgr._proxy_pools[proxy_a]) == 1
        mock_solve.assert_not_called()

        # 再次从 proxy_a 取出 t2
        param2, reg2 = await captcha_mgr.get_verify_param(proxy=proxy_a)
        assert param2 == "token-a-2"
        assert reg2 == "HK"
        assert len(captcha_mgr._proxy_pools[proxy_a]) == 0
        mock_solve.assert_not_called()

        # proxy_b 中 token 完好无损
        assert len(captcha_mgr._proxy_pools[proxy_b]) == 1


@pytest.mark.asyncio
async def test_singleflight_concurrent_empty_take_consolidation(captcha_mgr):
    """测试空仓并发请求下的 Singleflight 合并：多个请求同秒到达只触发一次实际求解。"""
    proxy = "http://127.0.0.1:21100"
    solve_call_count = 0

    async def fake_solve_unlocked(config, proxy=None):
        nonlocal solve_call_count
        solve_call_count += 1
        await asyncio.sleep(0.05)  # 模拟耗时
        return _Token(param="shared-token-xyz", region="HK", proxy=proxy)

    with patch.object(captcha_mgr, "fetch_config", new_callable=AsyncMock) as mock_cfg, \
         patch.object(captcha_mgr, "_solve_one_unlocked", side_effect=fake_solve_unlocked), \
         patch.object(captcha_mgr, "_refill_single_proxy", new_callable=AsyncMock):

        mock_cfg.return_value = {"sceneId": "sc", "region": "HK", "prefix": "pre"}

        # 3 个并发请求同时拉取 proxy 的 Token
        results = await asyncio.gather(
            captcha_mgr.get_verify_param(proxy=proxy),
            captcha_mgr.get_verify_param(proxy=proxy),
            captcha_mgr.get_verify_param(proxy=proxy),
        )

        # 3 个请求全部获得相同的求解结果
        for param, region in results:
            assert param == "shared-token-xyz"
            assert region == "HK"

        # 实际求解仅执行了一次（Singleflight 防惊群生效）
        assert solve_call_count == 1


@pytest.mark.asyncio
async def test_account_proxy_affinity_isolation(captcha_mgr):
    """测试账号根据 assigned_region / 映射代理获取对应专属 Token，杜绝跨端口混用。"""
    proxy_hk = "http://127.0.0.1:21100"
    proxy_tw = "http://127.0.0.1:21200"

    captcha_mgr._put(_Token(param="token-tw", region="TW", proxy=proxy_tw))

    acc_hk = Account(
        id="acc-hk",
        name="user-hk",
        provider="zai",
        mode="jwt",
        jwt_token="jwt.hk",
        assigned_region="HK",
    )

    with patch("app.client_pool.account_client_pool.resolve_proxy", return_value=proxy_hk), \
         patch.object(captcha_mgr, "fetch_config", new_callable=AsyncMock) as mock_cfg, \
         patch.object(captcha_mgr, "_solve_one_unlocked", new_callable=AsyncMock) as mock_solve, \
         patch.object(captcha_mgr, "_refill_single_proxy", new_callable=AsyncMock):

        mock_cfg.return_value = {"sceneId": "sc", "region": "HK", "prefix": "pre"}
        mock_solve.return_value = _Token(param="token-hk-fresh", region="HK", proxy=proxy_hk)

        # HK 账号发起获取，因 HK 池为空，触发 HK 现解，绝不会错拿 TW 的 token
        param, region = await captcha_mgr.get_verify_param(account=acc_hk)
        assert param == "token-hk-fresh"
        assert region == "HK"
        mock_solve.assert_awaited_once_with(mock_cfg.return_value, proxy=proxy_hk)

        # TW 水库内的 token 依旧完好
        assert len(captcha_mgr._proxy_pools[proxy_tw]) == 1
