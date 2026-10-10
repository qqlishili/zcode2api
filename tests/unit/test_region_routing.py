"""两级大区出口解析与故障避让调度单元测试。"""

import json
from pathlib import Path
import pytest

from app.client_pool import AccountClientPool
from app.models import Account
from app.store import Store


def test_client_pool_two_tier_resolution(tmp_path: Path):
    """验证 AccountClientPool 基于 egress_registry.json 的两级解析与大区物理隔离。"""
    reg_file = tmp_path / "egress_registry.json"
    registry_data = {
        "updated_at": 1728600000.0,
        "regions": {
            "HK": ["http://127.0.0.1:21100", "http://127.0.0.1:21101"],
            "TW": ["http://127.0.0.1:21200"],
            "JP": ["http://127.0.0.1:21300", "http://127.0.0.1:21301"],
            "US": ["http://127.0.0.1:21500"],
        },
    }
    reg_file.write_text(json.dumps(registry_data), encoding="utf-8")

    pool = AccountClientPool(registry_path=reg_file)

    # 1. 账号 HK-1 与 HK-2 必须严格映射到 HK 段（21100~21199）
    acc_hk1 = Account.create("zai", "acc-hk-1", "tok.hk1.sig")
    acc_hk1.assigned_region = "HK"
    proxy_hk1 = pool.resolve_proxy(acc_hk1)
    assert proxy_hk1 in ("http://127.0.0.1:21100", "http://127.0.0.1:21101")

    # 验证同一账号解析结果恒定不变（MD5 一致性哈希）
    assert pool.resolve_proxy(acc_hk1) == proxy_hk1

    # 2. 账号 TW-1 映射到 TW 段（21200）
    acc_tw = Account.create("zai", "acc-tw-1", "tok.tw.sig")
    acc_tw.assigned_region = "TW"
    assert pool.resolve_proxy(acc_tw) == "http://127.0.0.1:21200"

    # 3. 账号专属 proxy 具备最高优先级
    acc_custom = Account.create("zai", "acc-custom", "tok.custom.sig")
    acc_custom.assigned_region = "HK"
    acc_custom.proxy = "http://127.0.0.1:9999"
    assert pool.resolve_proxy(acc_custom) == "http://127.0.0.1:9999"


def test_client_pool_empty_region_never_hops(tmp_path: Path):
    """第一性原理与栅栏原则：当某大区无可用出口时，严禁跨区漂移借调！返回 None。"""
    reg_file = tmp_path / "egress_registry.json"
    registry_data = {
        "updated_at": 1728600000.0,
        "regions": {
            "HK": ["http://127.0.0.1:21100"],
            "SG": [],  # SG 区无节点
        },
    }
    reg_file.write_text(json.dumps(registry_data), encoding="utf-8")

    pool = AccountClientPool(registry_path=reg_file)

    acc_sg = Account.create("zai", "acc-sg-1", "tok.sg.sig")
    acc_sg.assigned_region = "SG"
    # 必须返回 None，绝不能跨区分配 HK 端口
    assert pool.resolve_proxy(acc_sg) is None

    # 未配置大区（未分组）的账号才走全局兜底
    acc_unassigned = Account.create("zai", "acc-unassigned", "tok.unassigned.sig")
    acc_unassigned.assigned_region = None
    # 全局未配时降级直连
    assert pool.resolve_proxy(acc_unassigned) is None


def test_store_select_evacuates_offline_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """验证调度器前置感知大区健康状态：自动剔除离线大区账号，无缝指派健康大区账号接单。"""
    db_file = tmp_path / "accounts.db"
    monkeypatch.setattr("app.settings.DB_PATH", db_file)
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)

    store = Store()
    # 创建 1 个 US 账号，2 个 HK 账号
    acc_us = store.add_account("zai", "acc-us", "tok.us.sig")
    acc_us.assigned_region = "US"
    store.update_account(acc_us)

    acc_hk1 = store.add_account("zai", "acc-hk1", "tok.hk1.sig")
    acc_hk1.assigned_region = "HK"
    store.update_account(acc_hk1)

    acc_hk2 = store.add_account("zai", "acc-hk2", "tok.hk2.sig")
    acc_hk2.assigned_region = "HK"
    store.update_account(acc_hk2)

    # 当前只有 HK 大区健康，US 大区全离线
    healthy_regions = {"HK"}
    observations = {}

    selected = store.select("zai", healthy_regions=healthy_regions, observations=observations)
    # 必须选出 HK 账号，绝不选中 US 账号
    assert selected is not None
    assert selected.assigned_region == "HK"

    # US 账号被前置记录排除原因，不耗尽重试预算
    assert acc_us.id in observations
    assert observations[acc_us.id]["code"] == "region_egress_offline"
