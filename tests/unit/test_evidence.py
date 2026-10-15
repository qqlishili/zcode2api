"""落盘证据：持久化、字段白名单、保留窗口与汇总契约。

对应 openspec/changes/pin-captcha-egress-and-refill-domain
（spec: Durable Request and Solve Evidence / Evidence Summaries）。
"""

import sqlite3
import time

import pytest

from app import evidence, settings

PROMPT_SECRET = "SECRET-USER-PROMPT-TEXT"


@pytest.fixture(autouse=True)
def isolated_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "EVIDENCE_ENABLED", 1)
    monkeypatch.setattr(settings, "EVIDENCE_RETENTION_DAYS", 7)
    evidence.reset()
    yield tmp_path
    evidence.reset()


def _request(ok: bool = True, t_first: float = 1.5, ts: float | None = None) -> dict:
    return {
        "req_id": "req-fixture-1", "ts": ts or time.time(), "endpoint": "messages",
        "model": "GLM-5.3-Flash", "stream": True, "account": "acc@example.com", "mode": "jwt",
        "ok": ok, "status": 200 if ok else 499, "error": "" if ok else "客户端断开",
        "t_first": t_first, "t_total": t_first + 1, "input_tokens": 10, "output_tokens": 5,
        "preview": PROMPT_SECRET,
    }


def test_request_and_solve_records_feed_summary():
    now = time.time()
    evidence.record_request(_request(ok=True, t_first=1.5, ts=now))
    evidence.record_request(_request(ok=False, t_first=9.5, ts=now))
    evidence.record_solve({"source": "refill", "port": "21301", "stage": "run",
                           "attempts": 1, "solve_ms": 6900, "hit": False, "ts": now})
    evidence.record_solve({"source": "hit", "port": "21303", "stage": "extract",
                           "hit": True, "param_chars": 280, "ts": now})

    data = evidence.summary(hours=1)

    assert data["available"] is True
    assert data["requests"]["total"] == 2
    assert data["requests"]["ok"] == 1
    assert data["requests"]["success_rate"] == 0.5
    assert data["requests"]["t_first_p50"] in (1.5, 9.5)
    assert data["solves"]["total"] == 2
    assert data["solves"]["hits"] == 1
    assert data["solves"]["hit_rate"] == 0.5
    assert set(data["solves"]["by_port"]) == {"21301", "21303"}


def test_evidence_store_holds_no_prompt_or_parameter_payload():
    evidence.record_request(_request())
    evidence.record_solve({"source": "refill", "port": "21301", "stage": "run",
                           "reason": "sdk_failed", "hit": False})

    blob = b""
    for suffix in ("", "-wal"):
        path = evidence._db_path().with_name("evidence.db" + suffix)
        if path.exists():
            blob += path.read_bytes()
    assert PROMPT_SECRET.encode() not in blob
    assert b"VERIFY_PARAM" not in blob

    conn = sqlite3.connect(evidence._db_path())
    request_columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
    solve_columns = {row[1] for row in conn.execute("PRAGMA table_info(solve_events)")}
    conn.close()
    assert "preview" not in request_columns
    assert not {"param", "verify_param", "jwt", "cookie"} & (request_columns | solve_columns)
    assert {"t_first", "ok", "status"} <= request_columns
    assert {"port", "source", "reason", "hit"} <= solve_columns


def test_prune_drops_rows_outside_retention():
    evidence.record_request(_request())
    conn = sqlite3.connect(evidence._db_path())
    conn.execute("INSERT INTO requests (req_id, ts, ok) VALUES ('old-fixture', ?, 1)",
                 (time.time() - 8 * 86400,))
    conn.commit()
    conn.close()

    assert evidence.prune() == 1
    assert evidence.summary(hours=24 * 30)["requests"]["total"] == 1


def test_empty_window_is_not_an_error():
    data = evidence.summary(hours=1)
    assert data["available"] is True
    assert data["requests"]["total"] == 0
    assert data["requests"]["success_rate"] is None
    assert data["solves"]["by_port"] == {}


def test_disabled_evidence_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EVIDENCE_ENABLED", 0)
    evidence.reset()

    evidence.record_request(_request())
    evidence.record_solve({"source": "hit", "port": "21303", "hit": True})

    assert not (tmp_path / "evidence.db").exists()
    assert evidence.summary(hours=1)["available"] is False


def test_port_of_helper_never_guesses():
    assert evidence.port_of("http://127.0.0.1:21301") == "21301"
    assert evidence.port_of(None) is None
    assert evidence.port_of("http://127.0.0.1") is None
