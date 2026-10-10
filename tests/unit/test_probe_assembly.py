"""百位大区端口装配与生命周期引擎单元测试。"""

from scripts.mihomo_auto_probe import (
    REGION_PORT_TIERS,
    allocate_region_ports,
    compute_node_fingerprint,
)


def test_compute_node_fingerprint_stability():
    """验证节点全凭证物理指纹的稳定性与唯一性。"""
    p1 = {"server": "1.2.3.4", "port": 443, "type": "ss", "cipher": "aes-128-gcm", "password": "pass"}
    p2 = {"server": "1.2.3.4", "port": 443, "type": "ss", "cipher": "aes-128-gcm", "password": "pass"}
    p3 = {"server": "1.2.3.4", "port": 8443, "type": "ss", "cipher": "aes-128-gcm", "password": "pass"}

    fp1 = compute_node_fingerprint(p1)
    fp2 = compute_node_fingerprint(p2)
    fp3 = compute_node_fingerprint(p3)

    assert fp1 == fp2
    assert fp1 != fp3


def test_allocate_region_ports_hundreds_isolation():
    """验证各大区节点物理隔离装配在各自百位端口段内。"""
    db = {
        "fp-hk-1": {"name": "HK-1", "region": "HK", "state": "active"},
        "fp-hk-2": {"name": "HK-2", "region": "HK", "state": "active"},
        "fp-tw-1": {"name": "TW-1", "region": "TW", "state": "active"},
        "fp-jp-1": {"name": "JP-1", "region": "JP", "state": "active"},
        "fp-us-1": {"name": "US-1", "region": "US", "state": "active"},
        "fp-dead": {"name": "DEAD", "region": "HK", "state": "tombstone"},
    }

    allocated = allocate_region_ports(db)

    # HK: 21100, 21101
    assert allocated["fp-hk-1"]["port"] == 21100
    assert allocated["fp-hk-2"]["port"] == 21101

    # TW: 21200
    assert allocated["fp-tw-1"]["port"] == 21200

    # JP: 21300
    assert allocated["fp-jp-1"]["port"] == 21300

    # US: 21500
    assert allocated["fp-us-1"]["port"] == 21500

    # tombstone 节点不占端口
    assert "port" not in allocated["fp-dead"]


def test_allocate_region_ports_preserves_existing():
    """验证已分配端口的节点保持端口号不变（幂等稳定）。"""
    db = {
        "fp-hk-1": {"name": "HK-1", "region": "HK", "state": "active", "port": 21105},
        "fp-hk-2": {"name": "HK-2", "region": "HK", "state": "active"},
    }

    allocated = allocate_region_ports(db)
    # fp-hk-1 保留原有 21105
    assert allocated["fp-hk-1"]["port"] == 21105
    # fp-hk-2 获取剩余第一个可用端口 21100
    assert allocated["fp-hk-2"]["port"] == 21100
