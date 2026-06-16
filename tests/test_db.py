import json
from pathlib import Path

import pytest

from anti_scam_agent import db


@pytest.fixture
def dbpath(tmp_path) -> Path:
    p = tmp_path / "test.db"
    db.init_db(p)
    return p


def _curated(verdict="scam", is_scam=True):
    return {
        "url": "http://shop.test", "domain": "shop.test",
        "verdict": verdict, "is_scam": is_scam, "scam_type": "phishing",
        "payment_explicitly_declined": False,
        "telemetry": {"duration_s": 30.0, "cost_usd": 0.02, "total_tokens": 1000},
    }


def test_create_job_is_queued(dbpath):
    jid = db.create_job(dbpath, "http://shop.test", "web")
    row = db.get(dbpath, jid)
    assert row["status"] == "queued"
    assert row["url"] == "http://shop.test"
    assert row["source"] == "web"
    assert row["domain"] == "shop.test"


def test_mark_running_then_save_result(dbpath):
    jid = db.create_job(dbpath, "http://shop.test", "web")
    db.mark_running(dbpath, jid)
    assert db.get(dbpath, jid)["status"] == "running"
    db.save_result(dbpath, jid, _curated())
    row = db.get(dbpath, jid)
    assert row["status"] == "done"
    assert row["verdict"] == "scam"
    assert row["is_scam"] == 1
    assert row["cost_usd"] == 0.02
    assert json.loads(row["report_json"])["domain"] == "shop.test"
    assert row["finished_at"] is not None


def test_mark_error(dbpath):
    jid = db.create_job(dbpath, "http://x.test", "extension")
    db.mark_error(dbpath, jid, "boom")
    row = db.get(dbpath, jid)
    assert row["status"] == "error"
    assert row["error"] == "boom"


def test_get_missing_returns_none(dbpath):
    assert db.get(dbpath, "nope") is None


def test_list_newest_first(dbpath):
    a = db.create_job(dbpath, "http://a.test", "web")
    b = db.create_job(dbpath, "http://b.test", "web")
    db.save_result(dbpath, a, _curated())
    db.save_result(dbpath, b, _curated(verdict="legitimate", is_scam=False))
    rows = db.list_analyses(dbpath, limit=10, offset=0)
    assert [r["id"] for r in rows][:2] == [b, a]


def test_stats_aggregates(dbpath):
    for v, scam in [("scam", True), ("scam", True), ("legitimate", False), ("uncertain", False)]:
        jid = db.create_job(dbpath, "http://x.test", "web")
        db.save_result(dbpath, jid, _curated(verdict=v, is_scam=scam))
    s = db.stats(dbpath)
    assert s["total"] == 4
    assert s["scam_count"] == 2
    assert s["by_verdict"]["scam"] == 2
    assert s["by_verdict"]["legitimate"] == 1
    assert s["scam_types"]["phishing"] == 2
    assert round(s["scam_rate"], 2) == 0.5
    assert s["avg_duration_s"] == 30.0
    assert round(s["total_cost_usd"], 2) == 0.08
