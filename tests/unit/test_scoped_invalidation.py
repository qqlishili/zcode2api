"""测试 3007 挑战靶向定向清池（Scoped Invalidation），消除跨大区/跨代理株连。"""

from unittest.mock import patch

import pytest

from app.captcha import CaptchaManager, _Token


@pytest.fixture
def captcha_mgr():
    return CaptchaManager()


def test_scoped_invalidation_by_proxy(captcha_mgr):
    """测试指定 proxy 清池：仅清除该代理队列，其余代理完全不受影响。"""
    proxy_hk1 = "http://127.0.0.1:21100"
    proxy_hk2 = "http://127.0.0.1:21101"
    proxy_tw = "http://127.0.0.1:21200"

    captcha_mgr._put(_Token("hk1-token", "HK", proxy=proxy_hk1))
    captcha_mgr._put(_Token("hk2-token", "HK", proxy=proxy_hk2))
    captcha_mgr._put(_Token("tw-token", "TW", proxy=proxy_tw))

    assert len(captcha_mgr._proxy_pools[proxy_hk1]) == 1
    assert len(captcha_mgr._proxy_pools[proxy_hk2]) == 1
    assert len(captcha_mgr._proxy_pools[proxy_tw]) == 1

    # 靶向清空 proxy_hk1
    captcha_mgr.invalidate(proxy=proxy_hk1)

    assert len(captcha_mgr._proxy_pools[proxy_hk1]) == 0
    assert len(captcha_mgr._proxy_pools[proxy_hk2]) == 1
    assert len(captcha_mgr._proxy_pools[proxy_tw]) == 1


def test_scoped_invalidation_by_region(captcha_mgr):
    """测试指定 region 清池：仅清空目标大区所属全部端口，其他大区保持完好。"""
    proxy_hk1 = "http://127.0.0.1:21100"
    proxy_hk2 = "http://127.0.0.1:21101"
    proxy_tw = "http://127.0.0.1:21200"
    proxy_jp = "http://127.0.0.1:21300"

    captcha_mgr._put(_Token("hk1-t", "HK", proxy=proxy_hk1))
    captcha_mgr._put(_Token("hk2-t", "HK", proxy=proxy_hk2))
    captcha_mgr._put(_Token("tw-t", "TW", proxy=proxy_tw))
    captcha_mgr._put(_Token("jp-t", "JP", proxy=proxy_jp))

    registry_mock = {
        "HK": [21100, 21101],
        "TW": [21200],
        "JP": [21300],
    }

    with patch("app.client_pool.account_client_pool.load_registry", return_value=registry_mock):
        captcha_mgr.invalidate(region="HK")

    assert len(captcha_mgr._proxy_pools[proxy_hk1]) == 0
    assert len(captcha_mgr._proxy_pools[proxy_hk2]) == 0
    assert len(captcha_mgr._proxy_pools[proxy_tw]) == 1
    assert len(captcha_mgr._proxy_pools[proxy_jp]) == 1


def test_global_invalidation_fallback(captcha_mgr):
    """测试无参数清池：全局配置重置时安全清空全部队列。"""
    proxy_hk = "http://127.0.0.1:21100"
    proxy_tw = "http://127.0.0.1:21200"

    captcha_mgr._put(_Token("hk-t", "HK", proxy=proxy_hk))
    captcha_mgr._put(_Token("tw-t", "TW", proxy=proxy_tw))

    captcha_mgr.invalidate()

    assert len(captcha_mgr._proxy_pools[proxy_hk]) == 0
    assert len(captcha_mgr._proxy_pools[proxy_tw]) == 0
