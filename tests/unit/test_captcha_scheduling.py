"""验证码调度回归：只隔离配置/Node 边界，保留真实任务与容量竞争。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import captcha, client_pool, settings
from app.captcha import CaptchaManager, CaptchaSolveError, _Token
from app.client_pool import AccountClientPool


@pytest.fixture(autouse=True)
async def isolated_clients(monkeypatch):
    original_client = client_pool.httpx.AsyncClient
    monkeypatch.setattr(client_pool.httpx, "AsyncClient", lambda **kw: original_client(**dict(kw, verify=False)))
    yield
    await client_pool.account_client_pool.aclose()


@pytest.fixture
async def manager(monkeypatch):
    monkeypatch.setattr(captcha.os, "cpu_count", lambda: 2)
    mgr = CaptchaManager()
    monkeypatch.setattr(mgr, "fetch_config", AsyncMock(return_value={"region": "fixture"}))
    monkeypatch.setattr(mgr, "_refill_single_proxy", AsyncMock())
    yield mgr
    await mgr.close()


async def test_retry_releases_slot_for_other_foreground(manager, monkeypatch):
    backoff, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def run(*args, proxy=None):
        calls.append(proxy)
        if calls == ["http://a.test"]:
            raise RuntimeError("fixture failure")
        return "fixture-token"

    async def sleep(_):
        backoff.set()
        await release.wait()

    monkeypatch.setattr(manager, "_run_solver", run)
    monkeypatch.setattr(captcha, "_sleep", sleep)
    first = asyncio.create_task(manager.get_verify_param(proxy="http://a.test"))
    await asyncio.wait_for(backoff.wait(), 1)
    try:
        second = await asyncio.wait_for(manager.get_verify_param(proxy="http://b.test"), 0.2)
        assert second[0] == "fixture-token"
        assert calls == ["http://a.test", "http://b.test"]
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


async def test_cancelling_one_waiter_keeps_singleflight(manager, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def run(*args, proxy=None):
        calls.append(proxy)
        entered.set()
        await release.wait()
        return "shared-fixture"

    monkeypatch.setattr(manager, "_run_solver", run)
    waiters = [asyncio.create_task(manager.get_verify_param(proxy="http://a.test")) for _ in range(3)]
    await entered.wait()
    await asyncio.sleep(0)
    waiters[0].cancel()
    await asyncio.gather(waiters[0], return_exceptions=True)
    solve, = manager._in_flight_solves.values()
    assert not solve.operation.released
    release.set()
    results = await asyncio.wait_for(asyncio.gather(*waiters[1:], return_exceptions=True), 1)
    assert results == [("shared-fixture", "fixture")] * 2
    assert calls == ["http://a.test"]
    assert not manager._in_flight_solves


async def test_eleven_second_attempt_has_no_ten_second_queue_slice(manager, monkeypatch):
    entered = asyncio.Event()

    async def run(*args, proxy=None):
        if proxy == "http://a.test":
            entered.set()
            await asyncio.sleep(11)
        return "fixture-token"

    monkeypatch.setattr(manager, "_run_solver", run)
    first = asyncio.create_task(manager.get_verify_param(proxy="http://a.test"))
    await entered.wait()
    try:
        assert (await manager.get_verify_param(proxy="http://b.test"))[0] == "fixture-token"
    finally:
        await asyncio.gather(first, return_exceptions=True)


async def test_local_failure_does_not_penalize_proxy(manager, monkeypatch, tmp_path):
    pool = AccountClientPool(registry_path=tmp_path / "registry.json")
    monkeypatch.setattr("app.client_pool.account_client_pool", pool)
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    monkeypatch.setattr(manager, "_run_solver", AsyncMock(side_effect=RuntimeError("fixture local error")))
    for _ in range(3):
        with pytest.raises(CaptchaSolveError):
            await manager.get_verify_param(proxy="http://a.test")
    assert pool._circuit_breakers == {}


async def test_request_memo_reuses_failure_but_cache_can_recover(manager, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_RETRIES", 1)
    run = AsyncMock(side_effect=RuntimeError("fixture local error"))
    monkeypatch.setattr(manager, "_run_solver", run)
    failures = {}
    for _ in range(2):
        with pytest.raises(CaptchaSolveError):
            await manager.get_verify_param(proxy="http://a.test", failures=failures)
    assert run.await_count == 1
    manager._put(_Token("recovered-fixture", "fixture", proxy="http://a.test"))
    assert (await manager.get_verify_param(proxy="http://a.test", failures=failures))[0] == "recovered-fixture"
    with pytest.raises(CaptchaSolveError):
        await manager.get_verify_param(proxy="http://a.test", failures={})
    assert run.await_count == 2


def test_health_query_does_not_reserve_half_open(tmp_path):
    pool = AccountClientPool(registry_path=tmp_path / "registry.json")
    pool._circuit_breakers["http://a.test"] = {"state": "OPEN", "fails": 3,
                                    "quarantine_until": 0, "half_open_active": False}
    assert not pool.is_proxy_quarantined("http://a.test")
    assert not pool.is_proxy_quarantined("http://a.test")
    assert pool._circuit_breakers["http://a.test"]["state"] == "OPEN"
    assert not pool._circuit_breakers["http://a.test"]["half_open_active"]


async def test_independent_budget_and_no_attempt_after_expiry(manager, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_TIMEOUT", 0.08)
    run_calls = []

    async def run(*args, proxy=None):
        run_calls.append(proxy)
        if proxy == "http://a.test":
            await asyncio.sleep(1)
        return "healthy-fixture"

    monkeypatch.setattr(manager, "_run_solver", run)
    with pytest.raises(CaptchaSolveError) as error:
        await manager.get_verify_param(proxy="http://a.test")
    assert error.value.diagnostics["reason"] == "solver_budget_exhausted"
    assert error.value.diagnostics["attempts"] == 1
    assert (await manager.get_verify_param(proxy="http://b.test"))[0] == "healthy-fixture"
    assert run_calls == ["http://a.test", "http://b.test"]


async def test_config_and_queue_are_in_budget(manager, monkeypatch):
    monkeypatch.setattr(settings, "CAPTCHA_SOLVE_TIMEOUT", 0.04)
    run = AsyncMock(return_value="fixture")
    monkeypatch.setattr(manager, "_run_solver", run)

    async def config(**kwargs):
        await asyncio.sleep(1)
        return {}

    monkeypatch.setattr(manager, "fetch_config", config)
    with pytest.raises(CaptchaSolveError) as error:
        await manager.get_verify_param(proxy="http://a.test")
    assert error.value.diagnostics["stage"] == "config"
    assert error.value.diagnostics["reason"] == "solver_budget_exhausted"
    run.assert_not_awaited()
    monkeypatch.setattr(manager, "fetch_config", AsyncMock(return_value={}))
    await manager._cpu_semaphore.acquire()
    try:
        with pytest.raises(CaptchaSolveError) as error:
            await manager.get_verify_param(proxy="http://b.test")
        assert error.value.diagnostics["reason"] == "solver_queue_timeout"
        assert error.value.diagnostics["queue_wait_ms"] > 0
        run.assert_not_awaited()
    finally:
        manager._cpu_semaphore.release()


async def test_refill_yields_during_foreground_backoff(manager, monkeypatch):
    backoff, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def run(*args, proxy=None):
        calls.append(proxy)
        if calls == ["http://a.test"]:
            raise RuntimeError("fixture")
        return "fixture"

    async def sleep(_):
        backoff.set()
        await release.wait()

    monkeypatch.setattr(manager, "_run_solver", run)
    monkeypatch.setattr(captcha, "_sleep", sleep)
    foreground = asyncio.create_task(manager.get_verify_param(proxy="http://a.test"))
    await backoff.wait()
    assert await manager._solve_singleflight(proxy="http://b.test", background=True) is None
    assert calls == ["http://a.test"]
    release.set()
    await foreground
    await manager._solve_singleflight(proxy="http://b.test", background=True)
    assert manager._proxy_pools["http://b.test"][0].param == "fixture"
    assert calls == ["http://a.test", "http://a.test", "http://b.test"]


async def test_foreground_joins_refill_without_banking_same_token(manager, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def run(*args, proxy=None):
        calls.append(proxy)
        entered.set()
        await release.wait()
        return "shared-fixture"

    monkeypatch.setattr(manager, "_run_solver", run)
    refill = asyncio.create_task(manager._solve_singleflight(proxy="http://a.test", background=True))
    await entered.wait()
    consumers = [asyncio.create_task(manager.get_verify_param(proxy="http://a.test")) for _ in range(3)]
    await asyncio.sleep(0)
    release.set()
    assert await asyncio.gather(*consumers) == [("shared-fixture", "fixture")] * 3
    await refill
    assert not manager._proxy_pools["http://a.test"]
    assert calls == ["http://a.test"]


@pytest.mark.parametrize("cores", [1, 2, 4])
async def test_cpu_limit_shared_by_front_and_back(monkeypatch, cores):
    monkeypatch.setattr(captcha.os, "cpu_count", lambda: cores)
    manager = CaptchaManager()
    active = maximum = 0
    monkeypatch.setattr(manager, "fetch_config", AsyncMock(return_value={}))
    monkeypatch.setattr(manager, "_refill_single_proxy", AsyncMock())

    async def run(*args, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "fixture"

    monkeypatch.setattr(manager, "_run_solver", run)
    await asyncio.gather(*(manager.get_verify_param(proxy=f"http://proxy{i}.test") for i in range(7)),
                         manager._solve_singleflight(proxy="background", background=True))
    assert maximum == max(1, cores - 1)
    assert active == 0
    await manager.close()


async def test_solver_pipe_error_reaps_real_child(manager, monkeypatch, tmp_path):
    solver = tmp_path / "pipe-fixture.js"
    solver.write_text("setInterval(()=>{},1000);", encoding="utf-8")
    monkeypatch.setattr(settings, "CAPTCHA_SOLVER_DIR", tmp_path)
    monkeypatch.setattr(settings, "CAPTCHA_SOLVER_JS", solver)
    create = asyncio.create_subprocess_exec
    child = None

    async def start(*args, **kwargs):
        nonlocal child
        child = await create(*args, **kwargs)

        async def broken_pipe():
            raise OSError("fixture pipe failure")

        monkeypatch.setattr(child, "communicate", broken_pipe)
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    try:
        with pytest.raises(OSError, match="fixture pipe failure"):
            await manager._run_solver("fixture", "fixture", "fixture")
        assert child is not None and child.returncode is not None
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()


@pytest.mark.parametrize("stop", ["close", "cancel"])
async def test_stop_before_producer_starts_releases_operation(manager, isolated_client_pool, stop):
    waiter = asyncio.create_task(manager.get_verify_param(proxy="http://a.test"))
    await asyncio.sleep(0)
    solve, = manager._in_flight_solves.values()
    assert not solve.task.done() and not solve.operation.released
    if stop == "close":
        await manager.close()
    else:
        solve.task.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    assert solve.operation.released and not isolated_client_pool._operations


async def test_close_kills_real_child_and_releases_queue(manager, monkeypatch, tmp_path):
    import json
    import os
    import time

    pid_file = tmp_path / "pid.txt"
    solver = tmp_path / "fixture.js"
    solver.write_text("require('fs').writeFileSync(" + json.dumps(str(pid_file)) +
                      ",String(process.pid)); setInterval(()=>{},1000);", encoding="utf-8")
    monkeypatch.setattr(settings, "CAPTCHA_SOLVER_DIR", tmp_path)
    monkeypatch.setattr(settings, "CAPTCHA_SOLVER_JS", solver)
    first = asyncio.create_task(manager.get_verify_param(proxy="http://a.test"))
    deadline = time.monotonic() + 3
    while not pid_file.exists() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert pid_file.exists()
    second = asyncio.create_task(manager.get_verify_param(proxy="http://b.test"))
    await asyncio.sleep(0)
    await manager.close()
    await asyncio.gather(first, second, return_exceptions=True)
    assert not manager._in_flight_solves and not manager._bg_tasks
    assert not manager._cpu_semaphore.locked()
    pid = int(pid_file.read_text())
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    else:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            assert exit_code.value != 259
    await manager.close()
