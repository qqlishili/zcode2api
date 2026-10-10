"""离线账号大区永久绑定发牌脚本 (seed_account_regions.py)

功能与原则：
1. 离线发牌：在服务冷启动/上线前完成 13 个账号的大区永久固化，杜绝运行时高并发动态竞态。
2. 两阶段硬配额：
   - P0 核心区 (HK/TW/JP) 占 80%+ (例如 11/13 = 84.6%)
   - P1 次级区 (SG/US) 占 ~15% (例如 2/13 = 15.4%)
   - P2 兜底区 (KR/EU/OTHER) 占 0% (杜绝分派高危或极少节点区)
3. 幂等性：已绑定 assigned_region 的账号默认保留，不重复发牌（除非指定 --force）。
4. 归一化与持久化：直接写入 SQLite accounts 表与 data 字段，保证单一事实源。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.constants import REGIONS
from app.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_regions")

# 市场供给驱动的大区梯队规范（单一事实源，对齐 OpenSpec geo-pinned-egress-ports）
P0_TIER = ("HK", "JP", "TW")     # 市场主流供给核心区（发牌占比 >= 80%）
P1_TIER = ("SG", "US")           # 优质次级区（发牌占比 ~15-20%）
P2_TIER = ("KR", "EU", "OTHER")  # 兜底高危区（发牌占比 0%）


def generate_quota_slots(total_count: int) -> list[str]:
    """按市场供给两阶段硬配额动态生成大区发牌槽位列表。

    规则：
    - P0 (HK/JP/TW): 优先保障 80%+ 配额，在 HK(5/11)、JP(4/11)、TW(2/11) 间加权轮转；
    - P1 (SG/US): 分配 ~15-20% 配额，在 SG 与 US 间均分；
    - P2: 严格为 0；
    - 槽位总数严格等于 total_count。
    """
    if total_count <= 0:
        return []
    p1_count = max(1, round(total_count * 0.15)) if total_count >= 5 else 0
    p0_count = total_count - p1_count

    slots: list[str] = []
    p0_weights = [("HK", 5), ("JP", 4), ("TW", 2)]
    weight_sum = sum(w for _, w in p0_weights)

    for r, w in p0_weights:
        cnt = round(p0_count * (w / weight_sum))
        slots.extend([r] * cnt)

    while len(slots) < p0_count:
        slots.append("HK")
    while len(slots) > p0_count:
        slots.pop()

    for i in range(p1_count):
        slots.append(P1_TIER[i % len(P1_TIER)])

    return slots


DEFAULT_SLOTS = generate_quota_slots(13)


def seed_regions(provider: str = "zai", force: bool = False, dry_run: bool = False) -> dict[str, str]:
    store = Store()
    accounts = store.list_accounts(provider)
    if not accounts:
        logger.warning(f"提供商 {provider} 下未找到任何账号！")
        return {}

    logger.info(f"读取到 {len(accounts)} 个 {provider} 账号")

    assignments: dict[str, str] = {}
    unassigned = []

    for acc in accounts:
        if acc.assigned_region and not force:
            logger.info(f"账号 [{acc.name}] ({acc.id}) 已绑定大区: {acc.assigned_region} (保留)")
            assignments[acc.id] = acc.assigned_region
        else:
            unassigned.append(acc)

    if not unassigned:
        logger.info("全部账号均已完成大区绑定，无需发牌。")
        return assignments

    # 根据总账号规模动态生成目标配额槽位
    target_slots = generate_quota_slots(len(accounts))
    remaining_slots = list(target_slots)
    for r in assignments.values():
        if r in remaining_slots:
            remaining_slots.remove(r)

    # 兜底循环补齐
    p0_cycle = list(P0_TIER)
    p0_idx = 0
    while len(remaining_slots) < len(unassigned):
        remaining_slots.append(p0_cycle[p0_idx % len(p0_cycle)])
        p0_idx += 1

    for i, acc in enumerate(unassigned):
        region = remaining_slots[i]
        acc.assigned_region = region
        assignments[acc.id] = region
        logger.info(f"发牌分配: 账号 [{acc.name}] ({acc.id}) -> 大区 [{region}]")
        if not dry_run:
            store.update_account(acc)

    if not dry_run:
        store.save()
        logger.info("所有账号大区分配已持久化落库 SQLite accounts.db")
    else:
        logger.info("[Dry Run] 未实际写入数据库")

    # 打印最终统计
    dist = {}
    for r in assignments.values():
        dist[r] = dist.get(r, 0) + 1
    logger.info(f"最终大区分布统计: {dist}")
    return assignments


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="离线发牌固定账号大区")
    parser.add_argument("--provider", default="zai", help="提供商 (默认: zai)")
    parser.add_argument("--force", action="store_true", help="强制重新发牌覆盖已有绑定")
    parser.add_argument("--dry-run", action="store_true", help="仅预览发牌结果，不写入数据库")
    args = parser.parse_args()

    seed_regions(provider=args.provider, force=args.force, dry_run=args.dry_run)
