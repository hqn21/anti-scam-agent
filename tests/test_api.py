import time

import pytest
from fastapi.testclient import TestClient

import anti_scam_agent.api as api_mod
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict
from anti_scam_agent.reporting import LLMCallMetrics, RunReport, StageReport
from anti_scam_agent.signals import StaticSignals
from anti_scam_agent.web_report import build_curated_report


def _fake_pipeline_factory(verdict: Verdict):
    async def fake_run_pipeline(url, verbose=False):
        assessment = ScamAssessment(verdict=verdict, scam_type="phishing" if verdict == Verdict.scam else None,
                                    reasoning="r", risk_factors=[])
        stage = StageReport.build(name="browsing", model="gpt-4.1", duration_s=1.0, steps=[],
                                  other_metrics=LLMCallMetrics(total_tokens=10, cost_usd=0.001))
        report = RunReport.build(target_domain="shop.test", url=url,
                                 started_at="2026-06-16T10:00:00+08:00", duration_s=2.0,
                                 stages=[stage], verdict=verdict.value, is_scam=assessment.is_scam,
                                 scam_type=assessment.scam_type)
        observation = BrowsingResult(
            website_summary="s", outgoing_links=[], login_attempted=False,
            login_outcome=Outcome.not_attempted, credit_card_submitted=False,
            payment_outcome=Outcome.not_attempted, payment_explicitly_declined=False,
            form_fields_requested=[], unexpected_events=[], visit_completed=True)
        report.curated = build_curated_report(assessment, report, StaticSignals(target_host="shop.test"), observation)
        return assessment, report
    return fake_run_pipeline


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "DB_PATH", tmp_path / "api.db")
    monkeypatch.setattr(api_mod, "run_pipeline", _fake_pipeline_factory(Verdict.scam))
    with TestClient(api_mod.app) as c:  # triggers lifespan (init_db + worker)
        yield c


def _wait_done(client, jid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/analyze/{jid}").json()
        if r["status"] in ("done", "error"):
            return r
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_analyze_creates_job_and_completes(client):
    r = client.post("/api/analyze", json={"url": "shop.test", "source": "web"})
    assert r.status_code == 202
    jid = r.json()["id"]
    assert r.json()["status"] == "queued"
    done = _wait_done(client, jid)
    assert done["status"] == "done"
    assert done["curated"]["verdict"] == "scam"


def test_analyze_unknown_id_404(client):
    assert client.get("/api/analyze/nope").status_code == 404


def test_history_and_detail(client):
    jid = client.post("/api/analyze", json={"url": "shop.test", "source": "web"}).json()["id"]
    _wait_done(client, jid)
    lst = client.get("/api/analyses").json()
    assert any(row["id"] == jid for row in lst)
    detail = client.get(f"/api/analyses/{jid}").json()
    assert detail["verdict"] == "scam"


def test_stats(client):
    jid = client.post("/api/analyze", json={"url": "shop.test", "source": "web"}).json()["id"]
    _wait_done(client, jid)
    s = client.get("/api/stats").json()
    assert s["total"] >= 1
    assert s["scam_count"] >= 1


def test_analyze_worker_error_path(tmp_path, monkeypatch):
    async def boom(url, verbose=False):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_mod, "DB_PATH", tmp_path / "err.db")
    monkeypatch.setattr(api_mod, "run_pipeline", boom)
    with TestClient(api_mod.app) as c:
        jid = c.post("/api/analyze", json={"url": "shop.test", "source": "web"}).json()["id"]
        done = _wait_done(c, jid)
    assert done["status"] == "error"
    assert "kaboom" in (done["error"] or "")


@pytest.mark.skipif(not api_mod._WEB_DIST.is_dir(), reason="web app not built (no web/dist)")
def test_spa_deep_link_falls_back_to_index(client):
    # A client-side route hit directly must serve index.html (not a 404), so links
    # like /report/<id> opened from the extension work.
    r = client.get("/report/some-id")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert '<div id="root"' in r.text
    # API paths must still 404 rather than fall back to the SPA.
    assert client.get("/api/analyze/nope").status_code == 404
