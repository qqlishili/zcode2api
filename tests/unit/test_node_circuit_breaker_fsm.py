"""测试节点代理三态断路器状态机（CLOSED -> OPEN -> HALF-OPEN）与探针自愈机制。"""

import time
from unittest.mock import patch

import pytest

from app.client_pool import AccountClientPool
from app.models import Account


@pytest.fixture
def pool(tmp_path):
    reg_file = tmp_path / "egress_registry.json"
    reg_file.write_text('{"regions": {"HK": ["http://127.0.0.1:21100", "http://127.0.0.1:21101"], "TW": ["http://127.0.0.1:21200"]}}', encoding="utf-8")
    return AccountClientPool(idle_timeout=60, registry_path=reg_file)


def test_circuit_breaker_closed_to_open_on_network_errors(pool):
    """测试网络超时错误连续 3 次熔断至 OPEN。"""
    proxy = "http://127.0.0.1:21100"

    # 前 2 次普通网络错误，保持 CLOSED
    pool.record_proxy_result(proxy, success=False, is_waf_block=False)
    assert not pool.is_proxy_quarantined(proxy)
    pool.record_proxy_result(proxy, success=False, is_waf_block=False)
    assert not pool.is_proxy_quarantined(proxy)

    # 第 3 次达到阈值，触发熔断 OPEN
    pool.record_proxy_result(proxy, success=False, is_waf_block=False)
    assert pool.is_proxy_quarantined(proxy)
    st = pool.get_quarantine_status()[proxy]
    assert st["state"] == "OPEN"
    assert st["fails"] == 3
    assert st["remaining_quarantine_sec"] > 0


def test_circuit_breaker_closed_to_open_on_waf_blocks(pool):
    """WAF 封禁（3012/405）**一次即熔断**至 OPEN。

    阈值取 1 的依据（2026-10-15 实测，各 4-6 次连续探测）：
      坏组合 5735@21200 → 405/3012 × 4（4/4 一致，稳定复现）
      好组合 9149@21200 → 200/1005 × 6（6/6 一致，从不出现 3012）
    即 3012 是**确定性的组合判定**、不是抖动信号 —— 好组合不会被误杀，
    坏组合则应尽早在第一轮就被隔离（内存态断路器重启即清零，
    阈值高会留下数轮空窗，期间所有账号都被分到已坏的出口）。
    """
    proxy = "http://127.0.0.1:21100"

    # 第 1 次 WAF 封禁即熔断
    pool.record_proxy_result(proxy, success=False, is_waf_block=True)
    assert pool.is_proxy_quarantined(proxy)
    st = pool.get_quarantine_status()[proxy]
    assert st["state"] == "OPEN"
    assert st["fails"] == 1


def test_circuit_breaker_half_open_probe_success(pool):
    """测试冷却期满后进入 HALF-OPEN，探针成功恢复 CLOSED。"""
    proxy = "http://127.0.0.1:21100"

    # 触发熔断（WAF 阈值 1，一次即 OPEN）
    pool.record_proxy_result(proxy, success=False, is_waf_block=True)
    assert pool.is_proxy_quarantined(proxy)

    # 模拟时间流逝 601 秒，超过 600 秒冷却
    current_mono = time.monotonic()
    with patch("time.monotonic", return_value=current_mono + 601):
        # 第一次检查：放行单次探针（返回 False 表示暂不隔离该次探针）
        assert pool.is_proxy_quarantined(proxy) is False
        assert pool._circuit_breakers[proxy]["state"] == "OPEN"
        client = object()
        pool._client_proxies[client] = proxy
        probe = pool.begin_request(client)

        # 探针请求成功
        pool.end_request(client, probe, success=True)

        # 状态机恢复 CLOSED，清空失败计数
        assert pool._circuit_breakers[proxy]["state"] == "CLOSED"
        assert pool._circuit_breakers[proxy]["fails"] == 0
        assert not pool.is_proxy_quarantined(proxy)


def test_circuit_breaker_half_open_probe_failure(pool):
    """测试 HALF-OPEN 探针失败后重新熔断隔离 600 秒。"""
    proxy = "http://127.0.0.1:21100"

    pool.record_proxy_result(proxy, success=False, is_waf_block=True)
    pool.record_proxy_result(proxy, success=False, is_waf_block=True)

    current_mono = time.monotonic()
    with patch("time.monotonic", return_value=current_mono + 601):
        # 隔离期满进入 HALF-OPEN
        assert pool.is_proxy_quarantined(proxy) is False
        client = object()
        pool._client_proxies[client] = proxy
        probe = pool.begin_request(client)
        assert pool._circuit_breakers[proxy]["state"] == "HALF-OPEN"

        # 探针请求失败
        pool.end_request(client, probe, success=False)

        # 重新转为 OPEN
        assert pool._circuit_breakers[proxy]["state"] == "OPEN"
        assert pool.is_proxy_quarantined(proxy) is True


def test_resolve_proxy_avoids_quarantined_ports(pool):
    """测试 resolve_proxy 自动规避熔断隔离端口。"""
    acc = Account(
        id="acc-1",
        name="test-user",
        provider="zai",
        mode="jwt",
        jwt_token="jwt",
        assigned_region="HK",
    )

    # 初始状态下 HK 有 21100 和 21101
    proxy1 = pool.resolve_proxy(acc)
    assert proxy1 in ("http://127.0.0.1:21100", "http://127.0.0.1:21101")

    # 将 proxy1 熔断
    pool.record_proxy_result(proxy1, success=False, is_waf_block=True)
    pool.record_proxy_result(proxy1, success=False, is_waf_block=True)
    assert pool.is_proxy_quarantined(proxy1)

    # resolve_proxy 避让已熔断的 proxy1，分配另一个健康端口
    proxy2 = pool.resolve_proxy(acc)
    assert proxy2 != proxy1
    assert proxy2 in ("http://127.0.0.1:21100", "http://127.0.0.1:21101")
