"""账号大区发牌单元测试。

对应 openspec/changes/auto-assign-new-account-region
（spec: account-region-assignment）。
"""

from app.constants import REGION_ASSIGN_P0, REGION_ASSIGN_P1, REGION_ASSIGN_P2
from app.region_assign import assignable_regions, pick_region

ACTIVE = {'HK': ['a'] * 4, 'TW': ['a'] * 6, 'JP': ['a'] * 22, 'SG': ['a'] * 17,
          'US': ['a'] * 38, 'KR': ['a'] * 2, 'EU': ['a'] * 7, 'OTHER': ['a'] * 31}


# ── 发牌范围与空区规避 ──────────────────────────────────────────────────

def test_assignable_regions_excludes_p2():
    """P2（KR/EU/OTHER）即便有活跃节点也绝不纳入发牌。"""
    regions = assignable_regions(ACTIVE)
    assert set(regions) == set(REGION_ASSIGN_P0) | set(REGION_ASSIGN_P1)
    assert not set(regions) & set(REGION_ASSIGN_P2)


def test_p2_only_registry_yields_no_region():
    """注册表只有 P2 活跃出口时不得发牌（宁可留空，也不发不可用大区）。"""
    assert pick_region({}, {'OTHER': ['a'] * 31}) is None
    assert pick_region({}, {'KR': ['a'] * 2}) is None
    assert pick_region({}, {'EU': ['a'] * 7}) is None


def test_empty_registry_yields_no_region():
    """注册表全空或不可读时不得发牌。"""
    assert pick_region({}, {}) is None
    assert pick_region({}, None) is None


def test_empty_region_is_skipped():
    """候选区中当前无出口的必须跳过，只发有活跃出口的区。"""
    active = {'HK': [], 'JP': ['a'], 'TW': [], 'SG': [], 'US': [], 'OTHER': ['a'] * 31}
    assert assignable_regions(active) == ['JP']
    assert pick_region({}, active) == 'JP'


# ── 配额比例 ────────────────────────────────────────────────────────────

def test_distribution_follows_p0_p1_tiers():
    """连续发牌须落在 P0/P1，且 P0 内部符合 HK:JP:TW = 5:4:2 的加权意图。"""
    existing: dict[str, int] = {}
    for _ in range(20):
        region = pick_region(existing, ACTIVE)
        existing[region] = existing.get(region, 0) + 1
    assert set(existing) <= set(REGION_ASSIGN_P0) | set(REGION_ASSIGN_P1)
    # P0 应占绝对多数，且 HK 多于 JP 多于 TW。
    p0_total = sum(existing.get(r, 0) for r in REGION_ASSIGN_P0)
    assert p0_total >= 15
    assert existing.get('HK', 0) >= existing.get('JP', 0) >= existing.get('TW', 0)


def test_increment_is_stable_not_reordering():
    """增量发牌不得因总量变化而把新号集中到序列末位区（稳定欠额，非全量重排）。"""
    existing: dict[str, int] = {}
    for _ in range(20):
        region = pick_region(existing, ACTIVE)
        existing[region] = existing.get(region, 0) + 1
    # 全量槽位重排会让新号几乎全部落到 SG/US；稳定算法下 P1 合计应远少于 P0。
    p1_total = sum(existing.get(r, 0) for r in REGION_ASSIGN_P1)
    assert p1_total <= 5


def test_single_p0_region_is_used():
    """只有单一 P0 区可用时仍能正常发牌。"""
    assert pick_region({}, {'US': ['a'] * 38}) == 'US'
    assert pick_region({}, {'HK': ['a'] * 4}) == 'HK'


def test_deterministic_for_same_input():
    """同一分布输入必须得到同一结果（发牌可复现）。"""
    existing = {'HK': 5, 'JP': 4, 'TW': 2, 'SG': 1, 'US': 1}
    first = pick_region(dict(existing), ACTIVE)
    assert all(pick_region(dict(existing), ACTIVE) == first for _ in range(5))
