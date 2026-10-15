"""预热域收窄与每端口水位：补货只针对可服务账号绑定的出口端口。

对应 openspec/changes/pin-captcha-egress-and-refill-domain
（spec: Proxy-Affinity Regional Captcha Pre-solved Reservoir / Refill Domain Follows Serviceable Accounts）。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app import captcha as cap
from app.captcha import CaptchaManager, _Token

JP = "http://127.0.0.1:21301"
TW = "http://127.0.0.1:21203"


class _Serviceable:
    """满足 _gate_open / serviceable_ports 判据的最小账号替身。"""

    def allows_billing(self) -> bool:
        return True

    def is_selectable(self) -> bool:
        return True


@pytest.fixture
def manager() -> CaptchaManager:
    return CaptchaManager()


async def test_refill_targets_follow_serviceable_ports(manager, monkeypatch):
    """预热域来自 serviceable_ports()，不再使用「每大区首个健康端口」。"""
    monkeypatch.setattr(cap.store, "list_accounts", lambda *a, **k: [_Serviceable()])
    monkeypatch.setattr("app.client_pool.account_client_pool.serviceable_ports",
                        lambda accounts: [JP, TW])
    monkeypatch.setattr("app.client_pool.account_client_pool.get_healthy_proxies",
                        lambda: ["http://127.0.0.1:21100", "http://127.0.0.1:21500"])
    assert manager._refill_targets() == [JP, TW]
    await manager.close()


async def test_refill_loop_never_solves_without_serviceable_port(manager, monkeypatch):
    """可服务账号存在但其大区无合格出口时：只清理过期 token，不发起任何求解。"""
    monkeypatch.setattr(cap.store, "list_accounts", lambda *a, **k: [_Serviceable()])
    monkeypatch.setattr("app.client_pool.account_client_pool.serviceable_ports", lambda accounts: [])

    scheduled: list = []
    monkeypatch.setattr(manager, "_schedule_refill", lambda proxy, **kw: scheduled.append(proxy))

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(cap.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await manager._refill_loop()
    assert scheduled == []


async def test_refill_loop_targets_are_the_returned_ports(manager, monkeypatch):
    """有可服务端口时按端口逐个补货，且不触碰其它大区端口。"""
    monkeypatch.setattr(cap.store, "list_accounts", lambda *a, **k: [_Serviceable()])
    monkeypatch.setattr("app.client_pool.account_client_pool.serviceable_ports", lambda accounts: [JP, TW])

    scheduled: list = []
    monkeypatch.setattr(manager, "_schedule_refill", lambda proxy, **kw: scheduled.append(proxy))

    async def stop(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(cap.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await manager._refill_loop()
    assert scheduled == [JP, TW]


async def test_per_port_watermark_stops_after_one_token(manager, monkeypatch):
    """水位 1：该端口已有 1 枚可用 token 后不再补货。"""
    manager._put(_Token("pooled-fixture", "sgp", proxy=JP))
    solve = AsyncMock()
    monkeypatch.setattr(manager, "_solve_singleflight", solve)

    await manager._refill_single_proxy(JP)

    solve.assert_not_awaited()
    assert len(manager._proxy_pools[JP]) == 1
    await manager.close()


async def test_refill_single_proxy_solves_when_empty(manager, monkeypatch):
    solve = AsyncMock(return_value=None)
    monkeypatch.setattr(manager, "_solve_singleflight", solve)

    await manager._refill_single_proxy(JP)

    assert solve.await_count == 1
    await manager.close()
