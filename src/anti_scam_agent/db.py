"""SQLite persistence for analysis runs. Stdlib sqlite3, no ORM, connection-per-call
(safe across the API threadpool + the background worker). One row per run."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_DB_PATH = Path(os.environ.get("ASA_DB_PATH", "anti_scam.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    domain TEXT,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    verdict TEXT,
    is_scam INTEGER,
    scam_type TEXT,
    payment_explicitly_declined INTEGER,
    duration_s REAL,
    cost_usd REAL,
    total_tokens INTEGER,
    report_json TEXT,
    error TEXT
);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Path = DEFAULT_DB_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(_SCHEMA)


def create_job(path: Path, url: str, source: str) -> str:
    jid = uuid.uuid4().hex
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO analyses (id, url, domain, status, source, created_at) "
            "VALUES (?, ?, ?, 'queued', ?, ?)",
            (jid, url, _domain(url), source, _now()),
        )
    return jid


def mark_running(path: Path, jid: str) -> None:
    with _connect(path) as conn:
        conn.execute("UPDATE analyses SET status='running' WHERE id=?", (jid,))


def save_result(path: Path, jid: str, curated: dict) -> None:
    tel = curated.get("telemetry", {})
    with _connect(path) as conn:
        conn.execute(
            "UPDATE analyses SET status='done', finished_at=?, verdict=?, is_scam=?, "
            "scam_type=?, payment_explicitly_declined=?, duration_s=?, cost_usd=?, "
            "total_tokens=?, report_json=? WHERE id=?",
            (
                _now(), curated.get("verdict"), 1 if curated.get("is_scam") else 0,
                curated.get("scam_type"),
                1 if curated.get("payment_explicitly_declined") else 0,
                tel.get("duration_s"), tel.get("cost_usd"), tel.get("total_tokens"),
                json.dumps(curated), jid,
            ),
        )


def mark_error(path: Path, jid: str, message: str) -> None:
    with _connect(path) as conn:
        conn.execute(
            "UPDATE analyses SET status='error', finished_at=?, error=? WHERE id=?",
            (_now(), message, jid),
        )


def get(path: Path, jid: str) -> dict | None:
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM analyses WHERE id=?", (jid,)).fetchone()
    return dict(row) if row else None


def list_analyses(path: Path, limit: int = 50, offset: int = 0, status: str | None = None) -> list[dict]:
    q = "SELECT id, url, domain, status, source, created_at, finished_at, verdict, " \
        "is_scam, scam_type, duration_s FROM analyses"
    params: list = []
    if status:
        q += " WHERE status=?"
        params.append(status)
    q += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with _connect(path) as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def stats(path: Path) -> dict:
    with _connect(path) as conn:
        done = conn.execute("SELECT verdict, is_scam, scam_type, duration_s, cost_usd "
                            "FROM analyses WHERE status='done'").fetchall()
    total = len(done)
    by_verdict: dict[str, int] = {}
    scam_types: dict[str, int] = {}
    scam_count = 0
    durations: list[float] = []
    cost_total = 0.0
    for r in done:
        v = r["verdict"] or "unknown"
        by_verdict[v] = by_verdict.get(v, 0) + 1
        if r["is_scam"]:
            scam_count += 1
            st = r["scam_type"] or "unspecified"
            scam_types[st] = scam_types.get(st, 0) + 1
        if r["duration_s"] is not None:
            durations.append(r["duration_s"])
        if r["cost_usd"] is not None:
            cost_total += r["cost_usd"]
    return {
        "total": total,
        "by_verdict": by_verdict,
        "scam_count": scam_count,
        "legit_count": by_verdict.get("legitimate", 0) + by_verdict.get("likely_legitimate", 0),
        "uncertain_count": by_verdict.get("uncertain", 0),
        "scam_rate": (scam_count / total) if total else 0.0,
        "scam_types": scam_types,
        "avg_duration_s": (sum(durations) / len(durations)) if durations else 0.0,
        "total_cost_usd": cost_total,
    }
