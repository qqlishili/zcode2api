"""离线发牌脚本单元测试。"""

from app.models import Account
from app.store import Store
from scripts.seed_account_regions import seed_regions


def test_seed_regions_deterministic(tmp_path, monkeypatch):
    db_file = tmp_path / "accounts.db"
    monkeypatch.setattr("app.settings.DB_PATH", db_file)
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)

    store = Store()
    # 模拟创建 13 个账号
    created_ids = []
    for i in range(13):
        acc = store.add_account("zai", f"test-acc-{i+1}", f"tok.{i+1}.sig")
        created_ids.append(acc.id)

    # 首次执行发牌
    assignments = seed_regions(provider="zai", force=False, dry_run=False)
    assert len(assignments) == 13

    # 统计 P0 与 P1 比例
    p0_count = sum(1 for r in assignments.values() if r in ("HK", "JP", "TW"))
    p1_count = sum(1 for r in assignments.values() if r in ("SG", "US"))
    p2_count = sum(1 for r in assignments.values() if r in ("KR", "EU", "OTHER"))

    assert p0_count == 11  # 84.6%
    assert p1_count == 2   # 15.4%
    assert p2_count == 0   # 0%

    # 验证落库持久化重读
    reloaded_store = Store()
    dist = reloaded_store.get_region_distribution("zai")
    assert dist == {"HK": 5, "JP": 4, "TW": 2, "SG": 1, "US": 1}

    # 再次执行发牌（幂等性，不改变原有分配）
    second_assignments = seed_regions(provider="zai", force=False, dry_run=False)
    assert second_assignments == assignments


def test_generate_quota_slots_scaling():
    from scripts.seed_account_regions import generate_quota_slots

    slots_20 = generate_quota_slots(20)
    assert len(slots_20) == 20
    p0 = sum(1 for r in slots_20 if r in ("HK", "JP", "TW"))
    p1 = sum(1 for r in slots_20 if r in ("SG", "US"))
    assert p0 >= 16  # >= 80%
    assert p1 == 3   # 15%

