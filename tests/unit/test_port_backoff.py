"""端口级求解退避：连续失败让出唯一求解槽，成功/命中清零，且不动出口断路器。

对应 openspec/changes/pin-captcha-egress-and-refill-domain
（spec: Port-Level Solver Backoff）。
"""

from unittest.mock import AsyncMock

import pytest

from app import settings
from app.captcha import CaptchaManager, CaptchaSolveError, _Token, safe_diagnostics
from app.client_pool import AccountClientPool

PORT_A = "http://127.0.0.1:21301"
PORT_B = "http://127.0.0.1:21303"


@pytest.fixture
async def manager():
    mgr = CaptchaManager()
    mgr.fetch_config = AsyncMock(return_value={"region": "sgp"})
    yield mgr
    await mgr.close()


def _failing_solver(mgr: CaptchaManager, monkeypatch, calls: list | None = None) -> list:
    calls = calls if calls is not None else []

    async def run(*args, proxy=None):
        calls.append(proxy)
        raise RuntimeError("fixture solver failure")

    monkeypatch.setattr(mgr, "_run_solver", run)
    return calls


async def test_two_failures_trigger_backoff_and_third_call_fails_fast(manager, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    calls = _failing_solver(manager, monkeypatch)

    for _ in range(2):
        with pytest.raises(CaptchaSolveError):
            await manager.get_verify_param(proxy=PORT_A)

    remaining = manager.port_backoff_remaining(PORT_A)
    assert remaining > 0
    assert remaining <= 90  # 默认 60s × 1.5 抖动上界

    before = list(calls)
    with pytest.raises(CaptchaSolveError) as err:
        await manager.get_verify_param(proxy=PORT_A)
    assert err.value.diagnostics["reason"] == "solver_port_backoff"
    assert err.value.diagnostics["stop_reason"] == "unavailable"
    assert calls == before  # 退避窗口内不新建 Node


async def test_backoff_does_not_leak_to_other_ports(manager, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)

    async def run(*args, proxy=None):
        if proxy == PORT_A:
            raise RuntimeError("fixture solver failure")
        return "fixture-param"

    monkeypatch.setattr(manager, "_run_solver", run)
    for _ in range(2):
        with pytest.raises(CaptchaSolveError):
            await manager.get_verify_param(proxy=PORT_A)

    assert manager.port_backoff_remaining(PORT_A) > 0
    assert (await manager.get_verify_param(proxy=PORT_B))[0] == "fixture-param"
    assert manager.port_backoff_remaining(PORT_B) == 0


async def test_reservoir_hit_clears_failure_counter(manager, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    _failing_solver(manager, monkeypatch)

    with pytest.raises(CaptchaSolveError):
        await manager.get_verify_param(proxy=PORT_A)
    assert manager._port_failures

    manager._put(_Token("pooled-fixture", "sgp", proxy=PORT_A))
    assert (await manager.get_verify_param(proxy=PORT_A))[0] == "pooled-fixture"
    assert manager._port_failures == {}
    assert manager.port_backoff_remaining(PORT_A) == 0


async def test_refill_skips_port_in_backoff(manager, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    _failing_solver(manager, monkeypatch)
    for _ in range(2):
        with pytest.raises(CaptchaSolveError):
            await manager.get_verify_param(proxy=PORT_A)

    solve = AsyncMock()
    monkeypatch.setattr(manager, "_solve_singleflight", solve)
    await manager._refill_single_proxy(PORT_A)
    solve.assert_not_awaited()


async def test_backoff_never_touches_circuit_breaker(manager, monkeypatch, tmp_path):
    pool = AccountClientPool(registry_path=tmp_path / "registry.json")
    monkeypatch.setattr("app.client_pool.account_client_pool", pool)
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    _failing_solver(manager, monkeypatch)

    for _ in range(3):
        with pytest.raises(CaptchaSolveError):
            await manager.get_verify_param(proxy=PORT_A)

    assert pool._circuit_breakers == {}


def test_port_backoff_diagnostic_survives_whitelist():
    kept = safe_diagnostics({"reason": "solver_port_backoff", "stage": "queue",
                             "stop_reason": "unavailable", "param_chars": None})
    assert kept["reason"] == "solver_port_backoff"
    assert kept["stage"] == "queue"
    assert kept["stop_reason"] == "unavailable"
