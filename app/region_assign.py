"""账号大区发牌 —— 运行期即时分配与存量补配的共享实现。

离线脚本 `scripts/seed_account_regions.py` 负责上线前的批量固化；本模块负责
运行期新账号入池与存量账号补配。两者共用 `app/constants.py` 收口的梯队与权重，
保证「同一账号无论从哪个入口进来都拿到稳定大区」。

第一性原理：大区只决定「这个账号走哪个出口端口段」。它不参与任何上游请求
（`assigned_region` 从不进入请求头或请求体），因此发牌时机不影响账号资格，
只影响该账号能否解析到一个可用出口。
"""

from __future__ import annotations

from .constants import (
    REGION_ASSIGN_P0,
    REGION_ASSIGN_P0_WEIGHTS,
    REGION_ASSIGN_P1,
    REGION_ASSIGN_P1_RATIO,
)

__all__ = ["assignable_regions", "pick_region"]


def assignable_regions(active: dict[str, list[str]] | None) -> list[str]:
    """按梯队顺序返回「当前确有活跃出口」的可发牌大区。

    active 为出口注册表的 regions 映射。空区一律剔除：发到空区的账号在
    `AccountClientPool.resolve_proxy` 会解析不到端口，直接变成不可服务账号。
    """
    def _has(region: str) -> bool:
        return bool((active or {}).get(region))

    return [r for r in (*REGION_ASSIGN_P0, *REGION_ASSIGN_P1) if _has(r)]


def pick_region(existing: dict[str, int], active: dict[str, list[str]] | None) -> str | None:
    """为下一个账号挑选大区；无可发牌大区时返回 None（不发空牌）。

    existing 为已发放大区的账号数量分布。算法在候选区中取「实际占比相对目标占比
    欠额最大」者，即把每次发牌视为对当前最大缺口的一次补足。

    不能用离线脚本的 `generate_quota_slots`：那是按总量全量重排的槽位函数，
    总量每变化一次序列就整体漂移，增量场景下会让新号集中落到序列末位所在区
    （实测 20 个新号里 16 个落在 SG/US），已发绑定也随之失去比例意义。
    """
    candidates = assignable_regions(active)
    if not candidates:
        return None

    total = sum(existing.get(r, 0) for r in candidates) + 1
    p1_members = [r for r in REGION_ASSIGN_P1 if r in candidates]
    # 与离线梯队一致：总数不足 5 时不发 P1，全部落在 P0。
    p1_share = REGION_ASSIGN_P1_RATIO if total >= 5 and p1_members else 0.0
    p0_weights = {r: w for r, w in REGION_ASSIGN_P0_WEIGHTS if r in candidates}
    p0_weight_sum = sum(p0_weights.values()) or 1

    def deficit(region: str) -> float:
        """目标占比 - 实际占比；越大越该发。"""
        if region in p1_members:
            target = p1_share / len(p1_members)
        else:
            target = (1.0 - p1_share) * p0_weights.get(region, 0) / p0_weight_sum
        return target - existing.get(region, 0) / total

    return max(candidates, key=deficit)
