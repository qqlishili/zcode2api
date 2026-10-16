"""出口配置边界与保存/生效快照，SQLite 使用 tmp_path。"""

import json
import sqlite3

import pytest

from app import settings
from app.egress_policy import EgressPolicy, environment_policy, parse_policy
from app.store import Store


def test_defaults_and_partial_bindings():
    assert environment_policy({}) == EgressPolicy()
    policy = environment_policy({
        "ZCODE_CLAIM_EGRESS_MODE": "proxy",
        "ZCODE_CLAIM_EGRESS_BINDINGS": json.dumps({"HK": {"port": 21100, "node_id": "a" * 64, "epoch": 1}}),
    })
    assert policy.binding("HK").identity == (21100, "a" * 64, 1)
    assert policy.binding("TW") is None
    assert policy.mode("chat", "direct") == "direct"
    assert policy.mode("claim", "direct") == "proxy"


@pytest.mark.parametrize("field,value", [
    ("chat_mode", "invalid"), ("claim_mode", None), ("chat_proxy_url", 40000),
    ("chat_proxy_url", "https://127.0.0.1:40000"), ("chat_proxy_url", "http://example.com:40000"),
    ("chat_proxy_url", "http://user:secret@127.0.0.1:40000"),
    ("chat_proxy_url", "http://127.0.0.1:40000/path"), ("claim_bindings", []),
    ("claim_bindings", {"HK": {"port": 21300, "node_id": "a" * 64, "epoch": 1}}),
    ("claim_bindings", {"HK": {"port": 21100, "node_id": "a" * 64, "epoch": True}}),
    ("claim_bindings", {"HK": {"port": 21100, "node_id": "old", "epoch": 1}}),
])
def test_invalid_policy_rejected(field, value):
    raw = EgressPolicy().as_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        parse_policy(raw)


def test_complete_object_and_url_normalization():
    raw = EgressPolicy(chat_mode="proxy", chat_proxy_url=" http://127.0.0.1:40000/ ").as_dict()
    assert parse_policy(raw).chat_proxy_url == "http://127.0.0.1:40000"
    for value in ({}, {**raw, "admin_key": "bad"}, EgressPolicy(chat_mode="proxy").as_dict()):
        with pytest.raises(ValueError):
            parse_policy(value)
    with pytest.raises(ValueError):
        environment_policy({"ZCODE_CLAIM_EGRESS_BINDINGS": "not-json"})


@pytest.fixture
def policy_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "accounts.db")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "EGRESS_POLICY", EgressPolicy(chat_mode="direct"))
    return Store()


def test_save_reset_and_restart(policy_store):
    original = policy_store.active_egress_policy
    policy_store.set_setting("gateway_key", "keep-this")
    raw = EgressPolicy(chat_mode="proxy", chat_proxy_url="http://127.0.0.1:40000").as_dict()
    saved = policy_store.save_egress_policy(raw)
    assert saved["pending_restart"] and saved["saved_source"] == "database"
    assert policy_store.active_egress_policy is original
    restarted = Store()
    assert restarted.active_egress_policy == parse_policy(raw)
    assert not restarted.egress_settings()["pending_restart"]
    reset = restarted.save_egress_policy(None)
    assert reset["pending_restart"] and reset["saved_source"] == "environment"
    assert restarted.active_egress_policy == parse_policy(raw)
    assert Store().active_egress_policy == original
    assert Store().get_setting("gateway_key") == "keep-this"
    assert not Store().egress_settings()["pending_restart"]


def test_equivalent_policy_still_requires_source_reload(policy_store):
    raw = policy_store.active_egress_policy.as_dict()
    assert policy_store.save_egress_policy(raw)["pending_restart"]
    restarted = Store()
    assert not restarted.save_egress_policy(raw)["pending_restart"]


def test_failed_commit_preserves_database_and_cache(policy_store, monkeypatch):
    original = policy_store.egress_settings()
    connect = policy_store._connect

    class FailedCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError("isolated failure")

    def fail_connect():
        return sqlite3.connect(settings.DB_PATH, factory=FailedCommit)

    monkeypatch.setattr(policy_store, "_connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError):
        policy_store.save_egress_policy(EgressPolicy(chat_mode="direct").as_dict())
    assert policy_store.egress_settings() == original
    monkeypatch.setattr(policy_store, "_connect", connect)
    assert not Store().egress_settings()["pending_restart"]
    assert Store().active_egress_source == "environment"


def test_corrupt_saved_policy_stops_startup(policy_store):
    policy_store._set_meta("egress_policy", '{"chat_mode":"direct"}')
    with pytest.raises(ValueError, match="损坏"):
        Store()
