"""账号入池发牌接入测试：覆盖四类入口与三个恢复场景。

对应 openspec/changes/auto-assign-new-account-region
（spec: account-region-assignment / Account Creation Assigns A Region 等）。

场景对照（用户提出）：
  A. 删了重加    —— 重加即重新发牌
  B. 换指纹后    —— 大区必须保留（发牌幂等）
  C. 导入老备份  —— 尊重备份里的大区
"""

import pytest

ACTIVE = {'HK': ['a'] * 4, 'TW': ['a'] * 6, 'JP': ['a'] * 22, 'SG': ['a'] * 17,
          'US': ['a'] * 38, 'KR': ['a'] * 2, 'EU': ['a'] * 7, 'OTHER': ['a'] * 31}


@pytest.fixture
def registry(monkeypatch):
    """把出口注册表固定为上表，避免读到真实数据目录。"""
    from app.client_pool import AccountClientPool

    pool = AccountClientPool()
    monkeypatch.setattr(pool, 'load_registry', lambda: ACTIVE)
    monkeypatch.setattr('app.client_pool.account_client_pool', pool)
    return pool


# ── 四类入口：全部经 add_account 收敛 ────────────────────────────────────

def test_add_account_assigns_region(fresh_app, registry):
    """后台粘贴 / OAuth 回传 / API Key / 备份导入 的共同底座必须发牌。"""
    acc = fresh_app.add_account('zai', 'oauth-login', 'a.b.c')
    assert acc.assigned_region in ('HK', 'JP', 'TW', 'SG', 'US')


def test_oauth_callback_path_assigns_region(fresh_app, registry):
    """OAuth 授权回传入池（store.add_account）必须拿到大区。"""
    acc = fresh_app.add_account('zai', 'oauth-callback', 'x.y.z')
    assert acc.assigned_region


def test_bulk_add_and_import_assign_region(fresh_app, registry):
    """批量新增与备份导入两条路径同样拿到大区。"""
    bulk = fresh_app.add_account('zai', 'bulk-1', 'a.b.1')
    assert bulk.assigned_region
    count = fresh_app.import_accounts({'providers': {'zai': [{'name': 'imp-1', 'secret': 'a.b.2'}]}})
    assert count == 1
    imported = next(a for a in fresh_app.list_accounts('zai') if a.name == 'imp-1')
    assert imported.assigned_region


def test_apikey_account_also_gets_region(fresh_app, registry):
    """API Key 账号同样需要出口，一并发牌。"""
    acc = fresh_app.add_account('zai', 'key-only', 'sk-not-a-jwt')
    assert acc.mode == 'apiKey'
    assert acc.assigned_region


# ── 场景 A：删了重加 ────────────────────────────────────────────────────

def test_delete_then_recreate_assigns_again(fresh_app, registry):
    """删掉再用同一 token 重加，必须重新发牌（不会留下空大区）。"""
    first = fresh_app.add_account('zai', 're-add', 'a.b.c')
    assert fresh_app.remove_account('zai', first.id) is True
    again = fresh_app.add_account('zai', 're-add', 'a.b.c')
    assert again.id != first.id
    assert again.assigned_region


# ── 场景 B：换指纹后保留 ────────────────────────────────────────────────

def test_region_survives_fingerprint_rotation(fresh_app, registry):
    """换指纹不得改变大区（发牌幂等：已有绑定一律保留）。"""
    acc = fresh_app.add_account('zai', 'rotate-me', 'a.b.c')
    before = acc.assigned_region
    from app.fingerprint import rotate

    rotate(acc)
    assert acc.assigned_region == before


def test_existing_region_is_never_overwritten(fresh_app, registry):
    """已绑定大区的账号再次经过发牌路径必须保持不变。"""
    acc = fresh_app.add_account('zai', 'fixed', 'a.b.c')
    acc.assigned_region = 'TW'
    fresh_app.update_account(acc)
    assert fresh_app._assign_region(acc) is False
    assert acc.assigned_region == 'TW'


# ── 场景 C：备份还原 ────────────────────────────────────────────────────

def test_export_includes_region(fresh_app, registry):
    """导出必须带上大区，否则恢复后绑定丢失。"""
    acc = fresh_app.add_account('zai', 'dump-me', 'a.b.c')
    payload = fresh_app.export()
    item = next(i for i in payload['providers']['zai'] if i['name'] == 'dump-me')
    assert item['assigned_region'] == acc.assigned_region


def test_import_respects_backup_region(fresh_app, registry):
    """备份里带大区的账号在恢复时尊重原绑定，不被重新发牌覆盖。"""
    payload = {'providers': {'zai': [
        {'name': 'restored', 'secret': 'a.b.restored', 'assigned_region': 'TW'},
    ]}}
    assert fresh_app.import_accounts(payload) == 1
    restored = next(a for a in fresh_app.list_accounts('zai') if a.name == 'restored')
    assert restored.assigned_region == 'TW'


def test_export_import_roundtrip_preserves_region(fresh_app, registry):
    """导出→导入往返后大区保持一致。"""
    original = fresh_app.add_account('zai', 'roundtrip', 'a.b.rt')
    original.assigned_region = 'JP'
    fresh_app.update_account(original)
    payload = fresh_app.export()
    fresh_app.remove_account('zai', original.id)
    fresh_app.import_accounts(payload)
    restored = next(a for a in fresh_app.list_accounts('zai') if a.name == 'roundtrip')
    assert restored.assigned_region == 'JP'


# ── 启动补配 ────────────────────────────────────────────────────────────

def test_backfill_fills_gaps_and_preserves_existing(fresh_app, monkeypatch, registry):
    """启动补配只补缺口：已有大区的账号不动，缺的补上。"""
    from app.main import _backfill_regions

    kept = fresh_app.add_account('zai', 'has-region', 'a.b.1')
    kept.assigned_region = 'HK'
    fresh_app.update_account(kept)
    gap = fresh_app.add_account('zai', 'no-region', 'a.b.2')
    gap.assigned_region = None
    fresh_app.update_account(gap)

    monkeypatch.setattr('app.main.store', fresh_app, raising=False)
    monkeypatch.setattr('app.store.store', fresh_app, raising=False)
    count = _backfill_regions()

    assert count == 1
    assert kept.assigned_region == 'HK'
    assert gap.assigned_region in ('HK', 'JP', 'TW', 'SG', 'US')


def test_backfill_skips_credentialless_accounts(fresh_app, monkeypatch, registry):
    """无凭证账号不臆造大区。"""
    from app.main import _backfill_regions

    acc = fresh_app.add_account('zai', 'empty-secret', 'a.b.c')
    acc.assigned_region = None
    acc.jwt_token = None
    acc.api_key = None
    fresh_app.update_account(acc)
    monkeypatch.setattr('app.main.store', fresh_app, raising=False)
    monkeypatch.setattr('app.store.store', fresh_app, raising=False)
    assert _backfill_regions() == 0
    assert acc.assigned_region is None


def test_no_assignable_region_leaves_account_empty(fresh_app, monkeypatch):
    """注册表只有 P2 时留空，等待后续补配，不臆造。"""
    from app.client_pool import AccountClientPool

    pool = AccountClientPool()
    monkeypatch.setattr(pool, 'load_registry', lambda: {'OTHER': ['a'] * 31})
    monkeypatch.setattr('app.client_pool.account_client_pool', pool)
    acc = fresh_app.add_account('zai', 'p2-only', 'a.b.c')
    assert acc.assigned_region is None
