"""账号专属独立客户端连接池（AccountClientPool）单元测试。"""

from __future__ import annotations

import asyncio
import pytest

from app.client_pool import AccountClientPool
from app.models import Account


@pytest.fixture
def test_pool():
    return AccountClientPool(idle_timeout=0.1)


async def test_account_isolated_clients(test_pool: AccountClientPool):
    """验证不同账号分配完全独立的 httpx.AsyncClient 实例（物理隔离）。"""
    acc1 = Account.create("zai", "acc1", "tok.1.sig")
    acc2 = Account.create("zai", "acc2", "tok.2.sig")

    client1 = await test_pool.get_client(acc1)
    client2 = await test_pool.get_client(acc2)

    assert client1 is not client2
    assert not client1.is_closed
    assert not client2.is_closed

    await test_pool.aclose()
    assert client1.is_closed
    assert client2.is_closed


async def test_same_account_reuses_client(test_pool: AccountClientPool):
    """验证同一账号活跃期内复用已建立的客户端实例。"""
    acc1 = Account.create("zai", "acc1", "tok.1.sig")

    client_a = await test_pool.get_client(acc1)
    client_b = await test_pool.get_client(acc1)

    assert client_a is client_b

    await test_pool.aclose()


async def test_proxy_priority_account_over_global(test_pool: AccountClientPool, monkeypatch: pytest.MonkeyPatch):
    """验证账号专属代理优先级高于全局代理池。"""
    monkeypatch.setenv("ZCODE_PROXIES", "http://127.0.0.1:21081,http://127.0.0.1:21082")

    acc = Account.create("zai", "acc-custom", "tok.custom.sig")
    acc.proxy = "http://127.0.0.1:9999"

    resolved = test_pool.resolve_proxy(acc)
    assert resolved == "http://127.0.0.1:9999"

    await test_pool.aclose()


async def test_consistent_hash_proxy_distribution(test_pool: AccountClientPool, monkeypatch: pytest.MonkeyPatch):
    """验证全局代理池按账号 ID MD5 一致性哈希稳定映射。"""
    proxies = "http://127.0.0.1:21081,http://127.0.0.1:21082,http://127.0.0.1:21083"
    monkeypatch.setenv("ZCODE_PROXIES", proxies)

    acc_a = Account.create("zai", "acc-a", "tok.a.sig")
    acc_b = Account.create("zai", "acc-b", "tok.b.sig")

    proxy_a_1 = test_pool.resolve_proxy(acc_a)
    proxy_a_2 = test_pool.resolve_proxy(acc_a)
    assert proxy_a_1 == proxy_a_2
    assert proxy_a_1 in proxies.split(",")

    proxy_b = test_pool.resolve_proxy(acc_b)
    assert proxy_b in proxies.split(",")

    await test_pool.aclose()


async def test_graceful_fallback_no_proxy(test_pool: AccountClientPool, monkeypatch: pytest.MonkeyPatch):
    """验证未配置代理时的优雅后备降级（直连出网，不抛异常）。"""
    monkeypatch.setenv("ZCODE_PROXIES", "")

    acc = Account.create("zai", "acc-direct", "tok.direct.sig")
    resolved = test_pool.resolve_proxy(acc)
    assert resolved is None

    client = await test_pool.get_client(acc)
    assert client is not None
    assert not client.is_closed

    await test_pool.aclose()


async def test_idle_prune(test_pool: AccountClientPool):
    """验证空闲超时客户端能被 prune_idle 正常回收释放。"""
    acc = Account.create("zai", "acc-idle", "tok.idle.sig")
    client = await test_pool.get_client(acc)

    # 等待超过 0.1s idle_timeout
    await asyncio.sleep(0.15)

    closed_count = await test_pool.prune_idle()
    assert closed_count == 1
    assert client.is_closed
