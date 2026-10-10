"""账号大区永久粘性字段（assigned_region）单元测试。"""

import json
import sqlite3
import pytest
from app.models import Account, Status, REGIONS
from app.store import Store


def test_account_assigned_region_field():
    """验证 Account 数据模型默认 assigned_region 为 None，赋值正常。"""
    acc = Account.create("zai", "test-acc", "tok.1.sig")
    assert acc.assigned_region is None

    acc.assigned_region = "HK"
    assert acc.assigned_region == "HK"

    data = acc.to_dict()
    assert data["assigned_region"] == "HK"

    # 验证 from_dict 反序列化
    restored = Account.from_dict(data)
    assert restored.assigned_region == "HK"

    # 验证向后兼容：旧字典无 assigned_region 时默认为 None
    del data["assigned_region"]
    legacy_restored = Account.from_dict(data)
    assert legacy_restored.assigned_region is None


def test_account_public_view_contains_assigned_region():
    """验证 public_view 包含 assigned_region 字段。"""
    acc = Account.create("zai", "test-acc", "tok.1.sig")
    acc.assigned_region = "JP"
    view = acc.public_view()
    assert "assigned_region" in view
    assert view["assigned_region"] == "JP"


def test_store_persistence_and_queries(tmp_path, monkeypatch):
    """验证 SQLite 迁移、落库保存与大区统计查询。"""
    db_file = tmp_path / "accounts.db"
    monkeypatch.setattr("app.settings.DB_PATH", db_file)
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)

    store = Store()
    acc1 = store.add_account("zai", "acc-hk-1", "tok.hk1.sig")
    acc1.assigned_region = "HK"
    store.update_account(acc1)

    acc2 = store.add_account("zai", "acc-hk-2", "tok.hk2.sig")
    acc2.assigned_region = "HK"
    store.update_account(acc2)

    acc3 = store.add_account("zai", "acc-jp-1", "tok.jp1.sig")
    acc3.assigned_region = "JP"
    store.update_account(acc3)

    # 重启 Store 验证从 SQLite 重新加载
    reloaded_store = Store()
    hk_accounts = reloaded_store.get_accounts_by_region("zai", "HK")
    assert len(hk_accounts) == 2
    assert {a.id for a in hk_accounts} == {acc1.id, acc2.id}

    jp_accounts = reloaded_store.get_accounts_by_region("zai", "JP")
    assert len(jp_accounts) == 1
    assert jp_accounts[0].id == acc3.id

    dist = reloaded_store.get_region_distribution("zai")
    assert dist == {"HK": 2, "JP": 1}
