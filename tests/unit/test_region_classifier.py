"""大区正则提取与真实出口 IP 双重校验单元测试。"""

from scripts.region_classifier import classify_region_by_name, parse_cf_trace, resolve_region


def test_classify_transit_vs_exit():
    """验证正确忽略中转/入口干扰，锁定最终落地大区。"""
    assert classify_region_by_name("沪日专线-01") == "JP"
    assert classify_region_by_name("广港IEPL-02") == "HK"
    assert classify_region_by_name("日本-中转->美国洛杉矶 01") == "US"
    assert classify_region_by_name("HK to TW 专线 05") == "TW"
    assert classify_region_by_name("🇸🇬 新加坡 0.1x") == "SG"
    assert classify_region_by_name("🇭🇰 香港家宽原生 100M") == "HK"
    assert classify_region_by_name("未知节点-999") == "OTHER"


def test_parse_cf_trace():
    """验证从 cdn-cgi/trace 正确提取 loc。"""
    sample = """fl=541f48
h=cloudflare.com
ip=104.28.212.15
ts=1728600000.123
visit_scheme=http
uag=Mozilla/5.0
colo=HKG
sliver=none
http=http/1.1
loc=HK
tls=off
sni=plaintext
warp=off
gateway=off
rbi=off
kex=none
"""
    assert parse_cf_trace(sample) == "HK"
    assert parse_cf_trace("invalid text") is None


def test_resolve_region_ground_truth_override():
    """验证真实 IP 强制校正节点伪地名欺骗。"""
    # 机场名字写着“日本专线”，但实际出口 Trace 返回 loc=US
    name = "沪日优质专线 01"
    assert classify_region_by_name(name) == "JP"

    # 真机校正强行覆盖为 US
    final_region = resolve_region(name, real_country_code="US")
    assert final_region == "US"

    # 无法获取真机 Trace 时回退正则
    fallback_region = resolve_region(name, real_country_code=None)
    assert fallback_region == "JP"
