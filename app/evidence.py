"""请求与验证码求解的落盘证据（跨重启的可用性判定基线）。

设计要点（openspec/changes/pin-captcha-egress-and-refill-domain）：
- 独立 SQLite（``data/evidence.db``，WAL），不与账号库 ``accounts.db`` 混用；
- **白名单字段**：只写标识、出口端口、诊断码与耗时，绝不写 verifyParam、token、
  cookie、上游原文；文本字段一律截断；
- **纯观测**：任何异常只 warning，绝不改变请求、调度或账号状态；
- 保留 ``EVIDENCE_RETENTION_DAYS``（默认 7）天，按写入计数节流做增量裁剪。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from . import logs, settings

__all__ = ["record_request", "record_solve", "summary", "prune", "reset", "enabled", "port_of"]

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_path: Path | None = None
_writes = 0
_PRUNE_EVERY = 200
_TEXT_LIMIT = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    req_id    TEXT,
    ts        REAL NOT NULL,
    endpoint  TEXT,
    model     TEXT,
    stream    INTEGER,
    account   TEXT,
    mode      TEXT,
    ok        INTEGER,
    status    INTEGER,
    error     TEXT,
    t_first   REAL,
    t_total   REAL,
    in_tok    INTEGER,
    out_tok   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE TABLE IF NOT EXISTS solve_events (
    ts            REAL NOT NULL,
    solve_id      TEXT,
    source        TEXT,
    port          TEXT,
    stage         TEXT,
    reason        TEXT,
    stop_reason   TEXT,
    attempts      INTEGER,
    solve_ms      INTEGER,
    queue_wait_ms INTEGER,
    config_ms     INTEGER,
    param_chars   INTEGER,
    exit_code     INTEGER,
    hit           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_solve_ts ON solve_events(ts);
CREATE INDEX IF NOT EXISTS idx_solve_port ON solve_events(port, ts);
"""


def enabled() -> bool:
    return bool(settings.EVIDENCE_ENABLED)


def _db_path() -> Path:
    return Path(settings.DATA_DIR) / "evidence.db"


def _connect() -> sqlite3.Connection | None:
    """惰性建库；data 目录被测试隔离或证据库不可用时返回 None（不抛）。"""
    global _conn, _path
    if not enabled():
        return None
    path = _db_path()
    if _conn is not None and _path == path:
        return _conn
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    except (sqlite3.Error, OSError) as err:
        logs.warn("evidence", f"证据库不可用（{type(err).__name__}），本次不落盘")
        return None
    if _conn is not None:
        try:
            _conn.close()
        except sqlite3.Error:
            pass
    _conn, _path = conn, path
    return _conn


def reset() -> None:
    """关闭句柄并丢弃路径缓存（测试与 data 目录切换使用）。"""
    global _conn, _path, _writes
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        _conn, _path, _writes = None, None, 0


def _text(value: object, limit: int = _TEXT_LIMIT) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def _int_or_none(value: object) -> int | None:
    return value if type(value) is int else None


def _real_or_none(value: object) -> float | None:
    return float(value) if type(value) in (int, float) else None


def port_of(proxy: str | None) -> str | None:
    """``http://127.0.0.1:21301`` → ``21301``（无端口不猜测）。"""
    if not proxy:
        return None
    tail = str(proxy).rsplit(":", 1)[-1].strip()
    return tail if tail.isdigit() else None


def _prune(conn: sqlite3.Connection) -> None:
    cutoff = time.time() - max(1, int(settings.EVIDENCE_RETENTION_DAYS)) * 86400
    for table in ("requests", "solve_events"):
        conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))


def prune() -> int:
    """按保留窗口裁剪历史行，返回删除的条目数（写满 N 条时自动调用）。"""
    conn = _connect()
    if conn is None:
        return 0
    try:
        with _lock:
            before = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("requests", "solve_events"))
            _prune(conn)
            conn.commit()
            after = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        for t in ("requests", "solve_events"))
    except sqlite3.Error as err:
        logs.warn("evidence", f"证据裁剪失败（{type(err).__name__}）")
        return 0
    return max(0, before - after)


def _write(table: str, columns: tuple[str, ...], row: tuple) -> None:
    global _writes
    conn = _connect()
    if conn is None:
        return
    placeholders = ", ".join("?" for _ in columns)
    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    try:
        with _lock:
            conn.execute(statement, row)
            _writes += 1
            if _writes % _PRUNE_EVERY == 0:
                _prune(conn)
            conn.commit()
    except sqlite3.Error as err:
        logs.warn("evidence", f"证据写入失败（{type(err).__name__}）")


def record_request(entry: dict) -> None:
    """记录一次网关请求结果（字段来自 reqlog 的既有结构，白名单落库）。"""
    if not isinstance(entry, dict) or not enabled():
        return
    ts = _real_or_none(entry.get("ts")) or time.time()
    _write(
        "requests",
        ("req_id", "ts", "endpoint", "model", "stream", "account", "mode", "ok",
         "status", "error", "t_first", "t_total", "in_tok", "out_tok"),
        (
            _text(entry.get("req_id"), 64),
            ts,
            _text(entry.get("endpoint"), 32),
            _text(entry.get("model"), 64),
            1 if entry.get("stream") else 0,
            _text(entry.get("account"), 64),
            _text(entry.get("mode"), 16),
            None if entry.get("ok") is None else (1 if entry.get("ok") else 0),
            _int_or_none(entry.get("status")),
            _text(entry.get("error")),
            _real_or_none(entry.get("t_first")),
            _real_or_none(entry.get("t_total")),
            _int_or_none(entry.get("input_tokens")),
            _int_or_none(entry.get("output_tokens")),
        ),
    )


def record_solve(event: dict) -> None:
    """记录一次求解结束或水库命中（端口/来源/诊断码/耗时，含参数长度但不含参数）。"""
    if not isinstance(event, dict) or not enabled():
        return
    _write(
        "solve_events",
        ("ts", "solve_id", "source", "port", "stage", "reason", "stop_reason",
         "attempts", "solve_ms", "queue_wait_ms", "config_ms", "param_chars", "exit_code", "hit"),
        (
            _real_or_none(event.get("ts")) or time.time(),
            _text(event.get("solve_id"), 32),
            _text(event.get("source"), 16),
            _text(event.get("port"), 8),
            _text(event.get("stage"), 16),
            _text(event.get("reason"), 40),
            _text(event.get("stop_reason"), 24),
            _int_or_none(event.get("attempts")),
            _int_or_none(event.get("solve_ms")),
            _int_or_none(event.get("queue_wait_ms")),
            _int_or_none(event.get("config_ms")),
            _int_or_none(event.get("param_chars")),
            _int_or_none(event.get("exit_code")),
            None if event.get("hit") is None else (1 if event.get("hit") else 0),
        ),
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return round(ordered[index], 2)


def summary(hours: int = 24) -> dict:
    """落盘证据汇总：请求成功率/首字分位、求解成功率、池命中率、按端口与原因的分布。

    空窗口返回零值而非报错；证据库不可用时返回 ``available: false``。
    """
    window = max(1, min(int(hours or 24), 24 * 30))
    since = time.time() - window * 3600
    conn = _connect()
    if conn is None:
        return {"available": False, "window_hours": window, "requests": {}, "solves": {}}
    try:
        with _lock:
            req_rows = conn.execute(
                "SELECT ok, t_first, status FROM requests WHERE ts >= ?", (since,)).fetchall()
            solve_rows = conn.execute(
                "SELECT source, port, reason, stop_reason, hit FROM solve_events WHERE ts >= ?",
                (since,)).fetchall()
    except sqlite3.Error as err:
        logs.warn("evidence", f"证据读取失败（{type(err).__name__}）")
        return {"available": False, "window_hours": window, "requests": {}, "solves": {}}

    total = len(req_rows)
    ok = sum(1 for row in req_rows if row[0] == 1)
    first = [float(row[1]) for row in req_rows if row[1] is not None]
    solves = len(solve_rows)
    hits = sum(1 for row in solve_rows if row[4] == 1)
    failed = sum(1 for row in solve_rows if row[4] != 1 and row[2])
    by_port: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for source, port, reason, _stop, hit in solve_rows:
        key = port or "unknown"
        by_port[key] = by_port.get(key, 0) + 1
        label = "hit" if hit == 1 else (reason or source or "unknown")
        by_reason[label] = by_reason.get(label, 0) + 1
    return {
        "available": True,
        "window_hours": window,
        "retention_days": int(settings.EVIDENCE_RETENTION_DAYS),
        "requests": {
            "total": total,
            "ok": ok,
            "error": total - ok,
            "success_rate": round(ok / total, 4) if total else None,
            "t_first_p50": _percentile(first, 0.5),
            "t_first_p90": _percentile(first, 0.9),
        },
        "solves": {
            "total": solves,
            "hits": hits,
            "failed": failed,
            "hit_rate": round(hits / solves, 4) if solves else None,
            "success_rate": round((solves - failed) / solves, 4) if solves else None,
            "by_port": dict(sorted(by_port.items(), key=lambda kv: -kv[1])),
            "by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
        },
    }
