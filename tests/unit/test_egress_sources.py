"""来源差分、完整连接身份与同区物理槽的行为回归。"""

import json

import pytest
import yaml

from scripts import mihomo_auto_probe as probe


@pytest.fixture
def proxy():
    return {"name": "HK-fixture", "type": "trojan", "server": "fixture.test", "port": 443,
            "password": "fixture-secret", "sni": "fixture.test", "tls": True,
            "ws-opts": {"path": "/a", "headers": {"Host": "fixture.test"}}}


def test_identity_ignores_only_display_name_and_mapping_order(proxy):
    renamed = dict(reversed(list(proxy.items())), name="renamed")
    assert probe.compute_node_fingerprint(proxy) == probe.compute_node_fingerprint(renamed)
    for key, value in (("sni", "other.test"), ("password", "changed"), ("tls", False),
                       ("port", "443"), ("ws-opts", {"path": "/b"}), ("extension", {"x": True})):
        changed = dict(proxy, **{key: value})
        assert probe.compute_node_fingerprint(proxy) != probe.compute_node_fingerprint(changed)


def test_shared_member_is_preserved_when_one_source_deletes_and_other_fails(proxy):
    fp = probe.compute_node_fingerprint(proxy)
    other = dict(proxy, server="new.test")
    first = probe.merge_sources({"nodes": {}, "sources": {}}, {"a": {fp: proxy}, "b": {fp: proxy}})
    second = probe.merge_sources(first, {"a": {probe.compute_node_fingerprint(other): other}, "b": None})
    assert second["nodes"][fp]["sources"] == ["b"]
    assert first["nodes"][fp]["sources"] == ["a", "b"]
    third = probe.merge_sources(second, {"a": None})
    assert third["nodes"][fp]["sources"] == []
    assert third["sources"]["a"] == second["sources"]["a"]


def test_ambiguous_empty_truncated_and_unknown_connection_fields(proxy):
    assert probe.source_snapshot({"proxies": []}) is None
    assert probe.source_snapshot({}) is None
    assert probe.source_snapshot({"proxies": [proxy, {"name": "truncated"}]}) is None
    assert probe.source_snapshot("invalid") is None
    extended = dict(proxy, extension={"option": True})
    assert probe.source_snapshot({"proxies": [extended]})[probe.compute_node_fingerprint(extended)] == extended


def test_legacy_partial_fingerprint_collision_stops_migration(proxy):
    key = probe.legacy_fingerprint(proxy)
    changed = dict(proxy, **{"ws-opts": {"path": "/different"}})
    old = {key: {"proxy": proxy, "port": 21100}}
    baseline = {"proxies": [changed], "listeners": [{"port": 21100, "proxy": changed["name"]}]}
    with pytest.raises(probe.ProbeError, match="ambiguous"):
        probe.migrate_lifecycle(old, baseline)


def test_legacy_duplicate_port_stops_migration(proxy):
    other = dict(proxy, name="retired", server="retired.test")
    old = {probe.legacy_fingerprint(proxy): {"proxy": proxy, "port": 21100},
           probe.legacy_fingerprint(other): {"proxy": other, "port": 21100, "state": "tombstone"}}
    baseline = {"proxies": [proxy], "listeners": [{"port": 21100, "proxy": proxy["name"]}]}
    with pytest.raises(probe.ProbeError, match="port_identity_conflict"):
        probe.migrate_lifecycle(old, baseline)
    assert all(node["port"] == 21100 for node in old.values())


def test_maintenance_duplicate_port_stops_before_last_record_wins(tmp_path, proxy):
    other = dict(proxy, server="retired.test")
    nodes = {probe.compute_node_fingerprint(p): {"proxy": p, "port": 21100}
             for p in (proxy, other)}
    with pytest.raises(probe.ProbeError, match="port_identity_conflict"):
        probe.build_maintenance_structure({}, nodes, tmp_path / "provider.yaml")


def test_maintenance_preserves_unknown_configuration_and_empty_reject(tmp_path, proxy):
    fp = probe.compute_node_fingerprint(proxy)
    baseline = {"secret": "fixture", "dns": {"enable": False}, "rules": ["MATCH,REJECT"],
                "extension": {"keep": True}, "proxies": [proxy],
                "listeners": [{"name": "old", "type": "mixed", "listen": "127.0.0.1",
                               "port": 21100, "proxy": proxy["name"]}]}
    nodes = {fp: {"proxy": proxy, "port": 21100, "state": "active", "region": "HK"}}
    candidate = probe.build_maintenance_structure(baseline, nodes, tmp_path / "provider.yaml")
    assert candidate["extension"] == baseline["extension"] and candidate["dns"] == baseline["dns"]
    assert baseline["listeners"][0]["proxy"] == proxy["name"]
    assert candidate["listeners"][0]["proxy"] == "zcode-slot-21100"
    provider = yaml.safe_load(probe.build_provider(nodes))["proxies"]
    assert len(provider) == 800 and len(candidate["listeners"]) == 800
    assert provider[0]["password"] == proxy["password"]
    assert all(p["type"] == "reject" for p in provider[1:])
    assert candidate["proxy-groups"][0]["filter"] == "^zcode-slot-21100-"


def test_fallback_then_replacement_closes_old_admission(tmp_path, proxy):
    path = tmp_path / "base.yaml"
    path.write_text("{}")
    fp = probe.compute_node_fingerprint(proxy)
    node = {"proxy": proxy, "port": 21100, "state": "active", "region": "HK", "epoch": 1, "sources": []}
    life = {"nodes": {fp: node}, "epochs": {"21100": 1}}
    old = [{"content": None, "mode": 0o600} for _ in range(5)]
    first = probe.build_candidate(life, {}, old)
    registry = json.loads(first[-1]["content"])
    assert registry["slots"]["21100"]["admission"] == "fallback"
    new = dict(proxy, server="replacement.test")
    new_fp = probe.compute_node_fingerprint(new)
    life["nodes"][new_fp] = dict(proxy=new, state="active", region="HK", sources=["a"])
    second = probe.build_candidate(life, {}, first)
    registry = json.loads(second[-1]["content"])
    assert registry["slots"]["21100"]["admission"] == "draining"
    assert registry["regions"]["HK"] == ["http://127.0.0.1:21101"]
    assert yaml.safe_load(first[0]["content"])["proxies"][0] == yaml.safe_load(second[0]["content"])["proxies"][0]


def test_health_timeout_does_not_release_physical_slot(proxy):
    fp = probe.compute_node_fingerprint(proxy)
    nodes = {fp: {"proxy": proxy, "port": 21100, "region": "HK", "state": "stale",
                  "last_healthy": 0, "sources": ["a"]},
             "new": {"state": "active", "region": "HK", "sources": ["a"]}}
    probe.allocate_region_ports(nodes)
    assert nodes["new"]["port"] == 21101 and nodes[fp]["port"] == 21100


def test_provider_conflict_refuses_identity_overwrite(proxy):
    fp = probe.compute_node_fingerprint(proxy)
    with pytest.raises(probe.ProbeError, match="conflict"):
        probe.build_provider({fp: {"proxy": proxy, "port": 21100}, "new": {"proxy": proxy, "port": 21100}})


async def test_remote_yaml_subscription_is_fetched_as_url(monkeypatch, proxy):
    import httpx

    requests = []
    original = httpx.AsyncClient

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(200, text=yaml.safe_dump({"proxies": [proxy]}))

    monkeypatch.setattr(probe.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    data, _ = await probe.fetch_subscription("https://fixture.test/subscription.yaml")
    assert requests == ["https://fixture.test/subscription.yaml"]
    assert probe.source_snapshot(data)
