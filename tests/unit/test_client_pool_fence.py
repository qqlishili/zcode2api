"""测试 ClientPool 大区离线熔断栅栏与代理导出契约。"""

import json
from pathlib import Path

import pytest

from app.client_pool import AccountClientPool, RegionOfflineError
from app.models import Account


@pytest.fixture
def tmp_registry(tmp_path: Path):
    reg_file = tmp_path / "egress_registry.json"
    data = {
        "updated_at": 123456789.0,
        "regions": {
            "TW": ["http://127.0.0.1:21200", "http://127.0.0.1:21201"],
            "HK": ["http://127.0.0.1:21100"],
            "JP": [],
        },
    }
    reg_file.write_text(json.dumps(data), encoding="utf-8")
    return reg_file


@pytest.mark.asyncio
async def test_client_pool_resolves_healthy_proxy(tmp_registry: Path):
    pool = AccountClientPool(registry_path=tmp_registry)
    acc_tw = Account(id="tw-01", name="tw-01", provider="zai", mode="jwt", assigned_region="TW")
    proxy = pool.resolve_proxy(acc_tw)
    assert proxy in ("http://127.0.0.1:21200", "http://127.0.0.1:21201")

    client = await pool.get_client(acc_tw)
    assert client is not None
    await pool.aclose()


@pytest.mark.asyncio
async def test_client_pool_fences_offline_region(tmp_registry: Path):
    pool = AccountClientPool(registry_path=tmp_registry)
    acc_jp = Account(id="jp-01", name="jp-01", provider="zai", mode="jwt", assigned_region="JP")

    # JP 大区在注册表中为空，必须抛出 RegionOfflineError，绝不允许降级直连
    assert pool.resolve_proxy(acc_jp) is None
    with pytest.raises(RegionOfflineError, match="无可用健康出口"):
        await pool.get_client(acc_jp)
    await pool.aclose()


def test_get_any_healthy_proxy(tmp_registry: Path):
    pool = AccountClientPool(registry_path=tmp_registry)
    any_proxy = pool.get_any_healthy_proxy()
    assert any_proxy in ("http://127.0.0.1:21200", "http://127.0.0.1:21201", "http://127.0.0.1:21100")
