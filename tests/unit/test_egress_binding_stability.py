"""稳定出口绑定：绑定索引只由注册表全量端口决定，熔断仅做同区环形 failover。

对应 openspec/changes/pin-captcha-egress-and-refill-domain
（spec: Stable Egress Binding with Ring Failover）。
"""

import hashlib
import json
from pathlib import Path

from app.client_pool import AccountClientPool
from app.models import Account

HK_PORTS = ["http://127.0.0.1:21100", "http://127.0.0.1:21101", "http://127.0.0.1:21102"]


def _pool(tmp_path: Path, regions: dict | None = None) -> AccountClientPool:
    registry = tmp_path / "egress_registry.json"
    registry.write_text(json.dumps({"updated_at": 1.0, "regions": regions or {"HK": list(HK_PORTS)}}),
                        encoding="utf-8")
    return AccountClientPool(registry_path=registry)


def _account(account_id: str = "acc-1", region: str = "HK") -> Account:
    return Account(id=account_id, name=account_id, provider="zai", mode="jwt",
                   jwt_token="jwt", assigned_region=region)


def _trip(pool: AccountClientPool, proxy: str) -> None:
    """两次 WAF 失败即熔断该端口。"""
    for _ in range(2):
        pool.record_proxy_result(proxy, success=False, is_waf_block=True)


def test_binding_index_uses_full_registered_port_list(tmp_path: Path):
    """绑定索引分母固定为注册表全量端口数，与当前健康子集无关。"""
    pool = _pool(tmp_path)
    acc = _account("acc-stable")
    expected_index = int(hashlib.md5(acc.id.encode("utf-8")).hexdigest(), 16) % len(HK_PORTS)
    assert pool.resolve_proxy(acc) == HK_PORTS[expected_index]


def test_binding_survives_quarantine_and_returns(tmp_path: Path):
    """熔断期间环形 failover，隔离解除后回到原绑定端口（不产生永久漂移）。"""
    pool = _pool(tmp_path)
    acc = _account("acc-drift")

    bound = pool.resolve_proxy(acc)
    _trip(pool, bound)
    assert pool.is_proxy_quarantined(bound)

    failover = pool.resolve_proxy(acc)
    assert failover != bound
    assert failover in HK_PORTS

    pool.record_proxy_result(bound, success=True)
    assert pool.resolve_proxy(acc) == bound


def test_healthy_subset_changes_do_not_move_other_bindings(tmp_path: Path):
    """健康子集变化（某端口熔断）不会改变其它账号的出口。"""
    pool = _pool(tmp_path)
    accounts = [_account(f"acc-{i}") for i in range(8)]
    before = {acc.id: pool.resolve_proxy(acc) for acc in accounts}

    victim = next(p for p in HK_PORTS if any(v == p for v in before.values()))
    _trip(pool, victim)

    for acc in accounts:
        if before[acc.id] != victim:
            assert pool.resolve_proxy(acc) == before[acc.id]


def test_region_without_eligible_port_returns_none(tmp_path: Path):
    """同区端口全部不可用时返回 None，交由既有大区栅栏换号（绝不跨区/直连）。"""
    pool = _pool(tmp_path)
    acc = _account("acc-fenced")
    for proxy in HK_PORTS:
        _trip(pool, proxy)
    assert pool.resolve_proxy(acc) is None


def test_serviceable_ports_filters_unusable_accounts(tmp_path: Path):
    """预热域只接受可服务账号：耗尽/冷却/Key 模式账号不得进域。"""
    pool = _pool(tmp_path, {"JP": ["http://127.0.0.1:21301", "http://127.0.0.1:21302"],
                            "TW": ["http://127.0.0.1:21200"]})

    active_jp = _account("acc-jp", "JP")
    active_tw = _account("acc-tw", "TW")
    exhausted = _account("acc-ex", "TW")
    exhausted.status = "exhausted"
    cooling = _account("acc-cool", "TW")
    cooling.status = "cooling"
    cooling.cooling_until = 9e12
    key_mode = Account(id="acc-key", name="acc-key", provider="zai", mode="apiKey",
                       api_key="sk-x", assigned_region="TW")

    ports = pool.serviceable_ports([active_jp, active_tw, exhausted, cooling, key_mode])

    assert ports == [pool.resolve_proxy(active_jp), pool.resolve_proxy(active_tw)]
    assert len(ports) == len(set(ports))


def test_empty_account_pool_yields_no_ports(tmp_path: Path):
    pool = _pool(tmp_path)
    assert pool.serviceable_ports([]) == []
    assert pool.serviceable_ports(None) == []
