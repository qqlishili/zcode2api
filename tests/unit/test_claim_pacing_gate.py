"""测试同一代理出口领额度错峰串行门禁（ClaimPacingGate）与快速失败机制。"""

import asyncio
import time

import pytest

from app.claim import ClaimPacingGate, ClaimPacingQueueFullError


@pytest.mark.asyncio
async def test_claim_pacing_gate_serialization():
    """测试同代理连续领额度强制施加最小间隔（Pacing）。"""
    gate = ClaimPacingGate(min_interval_sec=0.1, max_wait_sec=1.0)
    proxy = "http://127.0.0.1:21100"

    t0 = time.monotonic()
    times = []

    async def worker(idx):
        async with gate.limit(proxy):
            times.append(time.monotonic() - t0)

    # 启动 2 个同代理的任务
    await asyncio.gather(worker(1), worker(2))

    # 第 2 个任务必须比第 1 个任务至少延迟 min_interval_sec (0.1s)
    assert len(times) == 2
    assert times[1] - times[0] >= 0.08  # 考虑浮点微差，至少经过了约 0.1s


@pytest.mark.asyncio
async def test_claim_pacing_gate_different_proxies_parallel():
    """测试不同代理端口互不阻塞，并发执行。"""
    gate = ClaimPacingGate(min_interval_sec=0.2, max_wait_sec=1.0)
    proxy1 = "http://127.0.0.1:21100"
    proxy2 = "http://127.0.0.1:21200"

    t0 = time.monotonic()
    times = {}

    async def worker(p, name):
        async with gate.limit(p):
            await asyncio.sleep(0.05)
            times[name] = time.monotonic() - t0

    # 同时在两个不同代理执行
    await asyncio.gather(worker(proxy1, "p1"), worker(proxy2, "p2"))

    # 两个代理几乎同秒完成（耗时远低于 0.2s 串行时间）
    assert abs(times["p1"] - times["p2"]) < 0.08


@pytest.mark.asyncio
async def test_claim_pacing_gate_queue_timeout_fail_fast():
    """测试排队等待超过 max_wait_sec 立即抛出 ClaimPacingQueueFullError 快速失败。"""
    # 门禁设置较长的间隔和较短的等待超时
    gate = ClaimPacingGate(min_interval_sec=0.5, max_wait_sec=0.1)
    proxy = "http://127.0.0.1:21100"

    async def long_holder():
        async with gate.limit(proxy):
            await asyncio.sleep(0.3)

    async def queued_waiter():
        await asyncio.sleep(0.02)  # 确保在 long_holder 占锁后到达
        async with gate.limit(proxy):
            pass

    t0 = time.monotonic()
    with pytest.raises(ClaimPacingQueueFullError) as exc_info:
        await asyncio.gather(long_holder(), queued_waiter())

    duration = time.monotonic() - t0
    # 快速失败应该在 ~0.1s 时触发，而不是等到 0.3s 后完成
    assert duration < 0.25
    assert "排队超时" in str(exc_info.value)
