"""百位大区端口装配与生命周期引擎单元测试。"""

from scripts.mihomo_auto_probe import (
    allocate_region_ports,
    compute_node_fingerprint,
)


def test_reserved_tombstone_port_is_not_reused():
    db = {'dead': {'state': 'tombstone', 'region': 'HK', 'port': 21100},
          'new': {'state': 'active', 'region': 'HK'}}
    assert allocate_region_ports(db)['new']['port'] == 21101


def test_region_correction_reserves_old_port():
    db = {'corrected': {'state': 'active', 'region': 'JP', 'port': 21100},
          'new': {'state': 'active', 'region': 'HK'}}
    allocate_region_ports(db)
    assert db['corrected']['port'] == 21100
    assert db['corrected']['state'] == 'stale'
    assert db['new']['port'] == 21101


def test_production_ports_are_never_redirected():
    baseline = {'listeners': [{'port': 21100, 'proxy': 'old', 'type': 'mixed'}],
                'proxies': [{'name': 'old', 'type': 'http', 'server': '127.0.0.1', 'port': 31000}]}
    db = {'new': {'state': 'active', 'region': 'HK'}}
    assert allocate_region_ports(db, baseline)['new']['port'] == 21101


def test_exhausted_tier_refuses_new_port():
    baseline = {'listeners': [{'port': p, 'type': 'mixed'} for p in range(21100, 21200)]}
    db = {'new': {'state': 'active', 'region': 'HK'},
          'corrected': {'state': 'active', 'region': 'HK', 'port': 21300}}
    allocate_region_ports(db, baseline)
    assert 'port' not in db['new']
    assert db['corrected']['port'] == 21300
    assert db['corrected']['state'] == 'stale'


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


# ── 判据：3012/405 必须判为**不可达**（2026-10-15 修复）────────────────────


class _Resp:
    """最小 httpx.Response 替身：只带判据链需要的东西。"""

    def __init__(self, status: int) -> None:
        self.status_code = status
        # _classify_business_error 会读 headers（验证码挑战头检测）
        self.headers: dict[str, str] = {}
        self.text = ""


def test_risk_control_3012_is_not_reachable() -> None:
    """业务码 3012（风控拉黑）必须判为不可达。

    旧实现只拒 `effective >= 500`，把归一后的 405 放行，导致一轮探活
    143 个节点里 124 个被判 active —— 而它们对 zcode 全部返回 3012。
    """
    from scripts.mihomo_auto_probe import node_reachable

    body = '{"code":3012,"msg":"request has been blocked due to unusual activity"}'
    reachable, outcome = node_reachable(_Resp(405), body)
    assert reachable is False, "3012 必须判为不可达"
    assert outcome == "3012"


def test_plain_405_is_not_reachable() -> None:
    """裸 405（无业务码）同样不可达。"""
    from scripts.mihomo_auto_probe import node_reachable

    reachable, outcome = node_reachable(_Resp(405), "")
    assert reachable is False
    assert outcome == "405"


def test_business_accepted_statuses_are_reachable() -> None:
    """200 / 402（额度耗尽）/ 403（验证码）== 出口通，障碍在账号侧。"""
    from scripts.mihomo_auto_probe import node_reachable

    assert node_reachable(_Resp(200), '{"type":"message"}')[0] is True
    assert node_reachable(_Resp(402), '{"code":1005,"msg":"exceed quota limit"}')[0] is True
    assert node_reachable(_Resp(403), "")[0] is True


def test_upstream_failure_is_not_reachable() -> None:
    """5xx（上游/链路故障）必须判为不可达。"""
    from scripts.mihomo_auto_probe import node_reachable

    assert node_reachable(_Resp(500), "")[0] is False
    assert node_reachable(_Resp(502), '{"error":"bad gateway"}')[0] is False


def test_business_level_rejection_is_reachable() -> None:
    """业务级拒绝证明链路已打通 → 可达（与 B 类「边缘拒绝」区分）。

    404 plan not found / 429 限流都说明请求到达了业务层，
    只是业务上被拒 —— 这不影响「该出口可用」的结论。
    """
    from scripts.mihomo_auto_probe import node_reachable

    assert node_reachable(_Resp(404), '{"code":1001,"msg":"plan not found"}')[0] is True
    assert node_reachable(_Resp(429), "")[0] is True


def test_risk_control_statuses_come_from_constants() -> None:
    """风控判据必须取自 constants，不得在脚本里硬编码状态码。

    本次 bug 的成因是两处各写一套阈值：网关用 constants（拒 405），
    脚本用 `effective >= 500`（放过 405），同一份响应得出相反结论。
    """
    from app import constants
    from scripts.mihomo_auto_probe import node_reachable

    # constants 里登记的风控状态，脚本必须一律判不可达
    for st in constants.RISK_CONTROL_HTTP_STATUSES:
        assert node_reachable(_Resp(st), "")[0] is False, f"风控状态 {st} 必须不可达"
    # 反例：非风控的 4xx（业务级拒绝）仍可达
    assert node_reachable(_Resp(404), '{"code":1001}')[0] is True
