# Web API + Dashboard + Chrome Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the CLI-only Anti-Scam Agent with a FastAPI HTTP API, SQLite persistence, a React dashboard/history/query web app, and a native Chrome extension — all running locally, with the CLI preserved.

**Architecture:** A FastAPI app wraps the existing async `run_pipeline`, runs analyses through a single serialized background worker (job-id + polling), and persists each run to SQLite. A React (Vite) app and a native MV3 Chrome extension both talk to that API; extension runs appear in History because they go through the same API.

**Tech Stack:** Python 3.12 + `uv`, FastAPI + uvicorn, stdlib `sqlite3`, pytest (backend); React + Vite + TypeScript + Tailwind + Recharts + React Router (web); native MV3 service worker + Shadow-DOM content script (extension).

---

## Reference: existing shapes this plan builds on

- `run_pipeline(url: str, verbose: bool=False)` in `src/anti_scam_agent/pipeline.py` — currently returns `ScamAssessment`; builds a `RunReport` internally (lines ~55-65) and writes `logs/`.
- `ScamAssessment` (`models.py`): `verdict: Verdict`, `scam_type: str|None`, `reasoning: str`, `risk_factors: list[str]`, `.is_scam` property.
- `BrowsingResult` (`models.py`): `website_summary`, `outgoing_links`, `login_attempted`, `login_outcome: Outcome`, `credit_card_submitted`, `payment_outcome: Outcome`, `payment_explicitly_declined: bool`, `form_fields_requested`, `unexpected_events`, `visit_completed`.
- `RunReport` (`reporting.py`): `target_domain`, `url`, `started_at`, `duration_s`, `stages: list[StageReport]`, `grand_total: LLMCallMetrics`, `verdict`, `is_scam`, `scam_type`. `StageReport` has `name`, `duration_s`, `totals: LLMCallMetrics`. `LLMCallMetrics` has `cost_usd: float|None`, `total_tokens: int`.
- `StaticSignals` (`signals.py`): `target_host`, `domain_info: DomainInfo|None`, `tls: TlsInfo|None`, `dns: DnsInfo|None`. `DomainInfo`: `domain`, `days_since_creation`, `days_until_expiration`, `registrar`, `registrant_country`, `privacy_protected`. `TlsInfo`: `issuer_org`, `age_days`, `san_count`, `is_free_dv`. `DnsInfo`: `has_mx`, `nameservers`.
- **Blind-browser invariant:** none of the code in this plan feeds into the Browsing Agent's prompt/schema/tools. All new code is downstream of browsing. Do not touch `browsing.py` task prompts, `BrowsingResult` field descriptions, or the `read_email_inbox` description.

## File structure created/modified by this plan

```
src/anti_scam_agent/
  pipeline.py        (modify: return tuple; expose static_signals on RunReport path)
  __main__.py        (modify: unpack tuple)
  db.py              (create: sqlite persistence)
  web_report.py      (create: curated report mapping, LLM-free)
  api.py             (create: FastAPI app + serialized worker)
  server.py          (create: uvicorn entrypoint)
pyproject.toml       (modify: deps + console script)
.gitignore           (modify: anti_scam.db, web/node_modules, web/dist)
tests/
  test_db.py         (create)
  test_web_report.py (create)
  test_api.py        (create)
  test_pipeline.py   (modify: new return tuple)
web/                 (create: Vite React app)
extension/           (create: MV3 extension)
README.md            (rewrite)
```

---

# PHASE 1 — Backend (Python, TDD)

### Task 1: `run_pipeline` returns `(ScamAssessment, RunReport)`

The API needs the rich `RunReport` (and the static signals inside it) — `run_pipeline`
currently throws it away. Make it return both; keep the CLI output identical.

**Files:**
- Modify: `src/anti_scam_agent/pipeline.py`
- Modify: `src/anti_scam_agent/__main__.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Update the failing test expectations**

In `tests/test_pipeline.py`, the helper calls `pipeline.run_pipeline(...)` and ignores
the return. Add a test asserting the new tuple shape. Append:

```python
from anti_scam_agent.reporting import RunReport


def test_pipeline_returns_assessment_and_report(monkeypatch):
    _patch(monkeypatch, [Outcome.unclear])
    out = asyncio.run(pipeline.run_pipeline("http://shop.test"))
    assert isinstance(out, tuple) and len(out) == 2
    assessment, report = out
    assert isinstance(assessment, ScamAssessment)
    assert isinstance(report, RunReport)
    assert report.url == "http://shop.test"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_pipeline.py::test_pipeline_returns_assessment_and_report -v`
Expected: FAIL (currently returns a bare `ScamAssessment`, not a tuple).

- [ ] **Step 3: Change the return type**

In `src/anti_scam_agent/pipeline.py`:
- Change the signature/return annotation:
  `async def run_pipeline(url: str, verbose: bool = False) -> tuple[ScamAssessment, RunReport]:`
- At the end of the function, change `return assessment` to `return assessment, report`.
  (The `report` local already exists from `RunReport.build(...)`.)

- [ ] **Step 4: Keep the CLI output identical**

In `src/anti_scam_agent/__main__.py`, change:

```python
    assessment = asyncio.run(run_pipeline(url, verbose=args.verbose))
    print(assessment.model_dump_json(indent=2))
```

to:

```python
    assessment, _report = asyncio.run(run_pipeline(url, verbose=args.verbose))
    print(assessment.model_dump_json(indent=2))
```

- [ ] **Step 5: Run the pipeline tests**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS (all, including the existing ones — they ignore the return value).

- [ ] **Step 6: Commit**

```bash
git add src/anti_scam_agent/pipeline.py src/anti_scam_agent/__main__.py tests/test_pipeline.py
git commit -m "refactor: run_pipeline returns (ScamAssessment, RunReport)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Curated report mapping (`web_report.py`)

A pure, LLM-free helper that maps `(ScamAssessment, RunReport, StaticSignals, BrowsingResult)`
into a JSON-serializable `dict` for the web app/extension. Excludes the browsing
transcript. Built first because both the DB and the API depend on its shape.

**Decision:** `run_pipeline` has `BrowsingResult` (`result`) and `StaticSignals`
(`static_signals`) as locals but does not expose them. Rather than widen the return
tuple further, derive the curated dict *inside* `run_pipeline` and stash it on a new
attribute. Cleanest: have `run_pipeline` return `(ScamAssessment, RunReport)` and the
**API worker** rebuild curated data from the `RunReport` plus a re-used `BrowsingResult`.
To avoid losing `BrowsingResult`/`StaticSignals`, extend the curated builder to accept
them directly and have `run_pipeline` attach a `curated` dict to the report path.

Simplest clean approach actually implemented here: add the curated builder as a pure
function and call it from `pipeline.py` (which has all four objects), storing the result
in a new field on `RunReport`. To keep `reporting.py` free of model coupling, store it
as a plain `dict` field `curated: dict | None = None` on `RunReport`.

**Files:**
- Create: `src/anti_scam_agent/web_report.py`
- Modify: `src/anti_scam_agent/reporting.py` (add `curated: dict | None = None` to `RunReport`)
- Test: `tests/test_web_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_web_report.py
from anti_scam_agent.models import BrowsingResult, Outcome, ScamAssessment, Verdict
from anti_scam_agent.reporting import LLMCallMetrics, RunReport, StageReport
from anti_scam_agent.signals import DnsInfo, StaticSignals, TlsInfo
from anti_scam_agent.tools.handler import DomainInfo
from anti_scam_agent.web_report import build_curated_report


def _browsing(scammy: bool) -> BrowsingResult:
    return BrowsingResult(
        website_summary="An online shop.",
        outgoing_links=["pay.example.net"] if scammy else [],
        login_attempted=True,
        login_outcome=Outcome.succeeded,
        credit_card_submitted=True,
        payment_outcome=Outcome.succeeded if scammy else Outcome.failed,
        payment_explicitly_declined=not scammy,
        form_fields_requested=["full name", "credit card"],
        unexpected_events=["payment confirmed instantly"] if scammy else [],
        visit_completed=True,
    )


def _report() -> RunReport:
    stage = StageReport.build(
        name="browsing", model="gpt-4.1", duration_s=12.0, steps=[],
        other_metrics=LLMCallMetrics(total_tokens=1000, cost_usd=0.02),
    )
    return RunReport.build(
        target_domain="shop.test", url="http://shop.test",
        started_at="2026-06-16T10:00:00+08:00", duration_s=30.0, stages=[stage],
        verdict="scam", is_scam=True, scam_type="phishing",
    )


def _signals() -> StaticSignals:
    return StaticSignals(
        target_host="shop.test",
        domain_info=DomainInfo(domain="shop.test", days_since_creation=5,
                               days_until_expiration=360, registrar="NameCheap",
                               registrant_country="US", privacy_protected=True),
        tls=TlsInfo(issuer_org="Let's Encrypt", age_days=3, san_count=1, is_free_dv=True),
        dns=DnsInfo(has_mx=False, nameservers=["ns1.example.com"]),
    )


def test_curated_has_headline_and_signal():
    a = ScamAssessment(verdict=Verdict.scam, scam_type="phishing",
                       reasoning="No real card decline.", risk_factors=["instant confirm"])
    c = build_curated_report(a, _report(), _signals(), _browsing(scammy=True))
    assert c["verdict"] == "scam"
    assert c["is_scam"] is True
    assert c["payment_explicitly_declined"] is False
    assert c["reasoning"] == "No real card decline."
    assert c["risk_factors"] == ["instant confirm"]
    assert c["url"] == "http://shop.test"
    assert c["domain"] == "shop.test"


def test_curated_includes_observation_and_signals():
    a = ScamAssessment(verdict=Verdict.scam, scam_type="phishing", reasoning="r", risk_factors=[])
    c = build_curated_report(a, _report(), _signals(), _browsing(scammy=True))
    assert c["observation"]["website_summary"] == "An online shop."
    assert "credit card" in c["observation"]["form_fields_requested"]
    assert c["observation"]["outgoing_links"] == ["pay.example.net"]
    assert c["signals"]["domain_age_days"] == 5
    assert c["signals"]["tls_issuer"] == "Let's Encrypt"
    assert c["signals"]["tls_is_free_dv"] is True
    assert c["signals"]["dns_has_mx"] is False
    assert c["telemetry"]["duration_s"] == 30.0
    assert c["telemetry"]["total_tokens"] == 1000


def test_curated_is_json_serializable():
    import json
    a = ScamAssessment(verdict=Verdict.legitimate, scam_type=None, reasoning="r", risk_factors=[])
    c = build_curated_report(a, _report(), StaticSignals(target_host="x"), _browsing(scammy=False))
    json.dumps(c)  # must not raise
    assert c["signals"]["domain_age_days"] is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_web_report.py -v`
Expected: FAIL (`No module named 'anti_scam_agent.web_report'`).

- [ ] **Step 3: Add `curated` field to `RunReport`**

In `src/anti_scam_agent/reporting.py`, in `class RunReport`, add a field after `scam_type`:

```python
    scam_type: str | None = None
    curated: dict | None = None
```

(`RunReport.build` does not set it; it stays `None` by default. The pipeline assigns it.)

- [ ] **Step 4: Write `web_report.py`**

```python
# src/anti_scam_agent/web_report.py
"""Pure (LLM-free) mapping from pipeline outputs to a curated, JSON-serializable report
for the web app and extension. Excludes the per-step browsing transcript."""

from __future__ import annotations

from anti_scam_agent.models import BrowsingResult, ScamAssessment
from anti_scam_agent.reporting import RunReport
from anti_scam_agent.signals import StaticSignals


def build_curated_report(
    assessment: ScamAssessment,
    report: RunReport,
    signals: StaticSignals,
    observation: BrowsingResult,
) -> dict:
    di = signals.domain_info
    tls = signals.tls
    dns = signals.dns
    return {
        "url": report.url,
        "domain": report.target_domain,
        "started_at": report.started_at,
        "verdict": assessment.verdict.value,
        "is_scam": assessment.is_scam,
        "scam_type": assessment.scam_type,
        "payment_explicitly_declined": observation.payment_explicitly_declined,
        "reasoning": assessment.reasoning,
        "risk_factors": list(assessment.risk_factors),
        "observation": {
            "website_summary": observation.website_summary,
            "form_fields_requested": list(observation.form_fields_requested),
            "unexpected_events": list(observation.unexpected_events),
            "login_attempted": observation.login_attempted,
            "login_outcome": observation.login_outcome.value,
            "credit_card_submitted": observation.credit_card_submitted,
            "payment_outcome": observation.payment_outcome.value,
            "outgoing_links": list(observation.outgoing_links),
            "visit_completed": observation.visit_completed,
        },
        "signals": {
            "domain_age_days": di.days_since_creation if di else None,
            "domain_days_until_expiration": di.days_until_expiration if di else None,
            "registrar": di.registrar if di else None,
            "registrant_country": di.registrant_country if di else None,
            "privacy_protected": di.privacy_protected if di else None,
            "tls_issuer": tls.issuer_org if tls else None,
            "tls_age_days": tls.age_days if tls else None,
            "tls_is_free_dv": tls.is_free_dv if tls else None,
            "dns_has_mx": dns.has_mx if dns else None,
            "dns_nameservers": list(dns.nameservers) if dns else [],
        },
        "telemetry": {
            "duration_s": report.duration_s,
            "cost_usd": report.grand_total.cost_usd,
            "total_tokens": report.grand_total.total_tokens,
            "stages": [
                {"name": s.name, "duration_s": s.duration_s,
                 "total_tokens": s.totals.total_tokens, "cost_usd": s.totals.cost_usd}
                for s in report.stages
            ],
        },
    }
```

- [ ] **Step 5: Wire it into the pipeline**

In `src/anti_scam_agent/pipeline.py`, after `report = RunReport.build(...)` and before
`folder = write_run_report(...)`, attach the curated dict (all four objects are in scope):

```python
        from anti_scam_agent.web_report import build_curated_report
        report.curated = build_curated_report(assessment, report, static_signals, result)
```

(Place the import at the top of the file with the others instead of inline if preferred.)

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_web_report.py tests/test_pipeline.py tests/test_reporting.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/anti_scam_agent/web_report.py src/anti_scam_agent/reporting.py src/anti_scam_agent/pipeline.py tests/test_web_report.py
git commit -m "feat: add curated report mapping for web/extension

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: SQLite persistence (`db.py`)

**Files:**
- Create: `src/anti_scam_agent/db.py`
- Modify: `.gitignore`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL (`No module named 'anti_scam_agent.db'`).

- [ ] **Step 3: Write `db.py`**

```python
# src/anti_scam_agent/db.py
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Gitignore the DB**

Add to `.gitignore`:

```
anti_scam.db
web/node_modules/
web/dist/
```

- [ ] **Step 6: Commit**

```bash
git add src/anti_scam_agent/db.py tests/test_db.py .gitignore
git commit -m "feat: add sqlite persistence for analysis runs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: FastAPI app + serialized worker (`api.py`)

**Files:**
- Modify: `pyproject.toml` (add `fastapi`, `uvicorn[standard]`)
- Create: `src/anti_scam_agent/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, add to `dependencies`:

```toml
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
```

Run: `uv sync`
Expected: resolves and installs fastapi + uvicorn.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_api.py
import asyncio
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
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL (`No module named 'anti_scam_agent.api'`).

- [ ] **Step 4: Write `api.py`**

```python
# src/anti_scam_agent/api.py
"""FastAPI app wrapping the analysis pipeline. Jobs run through a single serialized
background worker (concurrency=1) and persist to SQLite. Clients submit a URL, get a
job id, and poll. Also serves the built web app from web/dist when present."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from anti_scam_agent import db
from anti_scam_agent.pipeline import run_pipeline

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("ASA_DB_PATH", "anti_scam.db"))
_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def _normalize_url(raw: str) -> str:
    raw = raw.strip()
    return raw if "://" in raw else f"http://{raw}"


class AnalyzeRequest(BaseModel):
    url: str
    source: str = "web"


async def _worker(app: FastAPI) -> None:
    queue: asyncio.Queue[str] = app.state.queue
    while True:
        jid = await queue.get()
        row = db.get(DB_PATH, jid)
        if row is None:
            queue.task_done()
            continue
        db.mark_running(DB_PATH, jid)
        try:
            assessment, report = await run_pipeline(row["url"])
            curated = report.curated or {}
            db.save_result(DB_PATH, jid, curated)
        except Exception as e:  # noqa: BLE001 — never kill the worker loop
            logger.exception("analysis job %s failed", jid)
            db.mark_error(DB_PATH, jid, str(e))
        finally:
            queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db(DB_PATH)
    app.state.queue = asyncio.Queue()
    task = asyncio.create_task(_worker(app))
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Anti-Scam Agent API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.post("/api/analyze", status_code=202)
async def analyze(req: AnalyzeRequest):
    url = _normalize_url(req.url)
    source = req.source if req.source in ("web", "extension") else "web"
    jid = db.create_job(DB_PATH, url, source)
    await app.state.queue.put(jid)
    return {"id": jid, "status": "queued"}


@app.get("/api/analyze/{jid}")
async def analyze_status(jid: str):
    row = db.get(DB_PATH, jid)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    out = {"id": jid, "status": row["status"], "error": row["error"]}
    if row["report_json"]:
        import json
        out["curated"] = json.loads(row["report_json"])
    return out


@app.get("/api/analyses")
async def analyses(limit: int = 50, offset: int = 0, status: str | None = None):
    return db.list_analyses(DB_PATH, limit=limit, offset=offset, status=status)


@app.get("/api/analyses/{jid}")
async def analysis_detail(jid: str):
    row = db.get(DB_PATH, jid)
    if row is None or not row["report_json"]:
        raise HTTPException(status_code=404, detail="not found")
    import json
    return json.loads(row["report_json"])


@app.get("/api/stats")
async def stats():
    return db.stats(DB_PATH)


# Serve the built SPA at / when present (after a `npm run build` in web/).
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/anti_scam_agent/api.py tests/test_api.py uv.lock
git commit -m "feat: add FastAPI app with serialized analysis worker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Server entrypoint + console script

**Files:**
- Create: `src/anti_scam_agent/server.py`
- Modify: `pyproject.toml` (`[project.scripts]`)

- [ ] **Step 1: Write `server.py`**

```python
# src/anti_scam_agent/server.py
"""Entry point: run the API with uvicorn. `uv run anti-scam-server`."""

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="anti-scam-server", description="Run the Anti-Scam Agent API server.")
    parser.add_argument("--host", default=os.environ.get("ASA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ASA_PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("anti_scam_agent.api:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Register the console script**

In `pyproject.toml` under `[project.scripts]`, add a line:

```toml
[project.scripts]
anti-scam-agent = "anti_scam_agent.__main__:main"
anti-scam-server = "anti_scam_agent.server:main"
```

Run: `uv sync`

- [ ] **Step 3: Smoke-test the server boots**

Run: `uv run python -c "from anti_scam_agent.api import app; print(app.title)"`
Expected: prints `Anti-Scam Agent API`.

- [ ] **Step 4: Run the full backend test suite**

Run: `uv run pytest tests/test_db.py tests/test_web_report.py tests/test_api.py tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/anti_scam_agent/server.py pyproject.toml
git commit -m "feat: add anti-scam-server uvicorn entrypoint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# PHASE 2 — Web app (React + Vite + TypeScript)

> Verification for this phase is manual (run the app against the API). Use the
> `vercel-react-best-practices` and `vercel-composition-patterns` skills while writing
> components, and review against `web-design-guidelines` before Task 12's commit.

### Task 6: Scaffold Vite + Tailwind + deps

**Files:**
- Create: `web/` (Vite React-TS scaffold), `web/tailwind.config.js`, `web/postcss.config.js`,
  `web/vite.config.ts`, `web/src/index.css`

- [ ] **Step 1: Scaffold the app**

```bash
cd web 2>/dev/null || (npm create vite@latest web -- --template react-ts && cd web)
# From repo root:
npm --prefix web install
npm --prefix web install react-router-dom recharts
npm --prefix web install -D tailwindcss postcss autoprefixer
```

- [ ] **Step 2: Configure Tailwind**

`web/tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
```

`web/postcss.config.js`:

```js
export default { plugins: { tailwindcss: {}, autoprefixer: {} } };
```

Replace `web/src/index.css` with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 3: Configure the dev proxy**

`web/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  build: { outDir: "dist" },
});
```

- [ ] **Step 4: Verify dev server boots**

Run: `npm --prefix web run dev` (Ctrl-C after it prints the local URL).
Expected: Vite serves on `http://localhost:5173` without errors.

- [ ] **Step 5: Commit**

```bash
git add web/package.json web/package-lock.json web/tailwind.config.js web/postcss.config.js web/vite.config.ts web/src/index.css web/index.html web/tsconfig*.json
git commit -m "chore: scaffold React+Vite+Tailwind web app

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: API client + shared types

**Files:**
- Create: `web/src/api.ts`, `web/src/types.ts`

- [ ] **Step 1: Define types matching the API**

`web/src/types.ts` — mirror the curated/list/stats shapes from `web_report.py` / `db.py`:

```ts
export type Verdict = "scam" | "likely_scam" | "uncertain" | "likely_legitimate" | "legitimate";

export interface Curated {
  url: string; domain: string; started_at: string;
  verdict: Verdict; is_scam: boolean; scam_type: string | null;
  payment_explicitly_declined: boolean;
  reasoning: string; risk_factors: string[];
  observation: {
    website_summary: string; form_fields_requested: string[]; unexpected_events: string[];
    login_attempted: boolean; login_outcome: string; credit_card_submitted: boolean;
    payment_outcome: string; outgoing_links: string[]; visit_completed: boolean;
  };
  signals: {
    domain_age_days: number | null; domain_days_until_expiration: number | null;
    registrar: string | null; registrant_country: string | null; privacy_protected: boolean | null;
    tls_issuer: string | null; tls_age_days: number | null; tls_is_free_dv: boolean | null;
    dns_has_mx: boolean | null; dns_nameservers: string[];
  };
  telemetry: {
    duration_s: number; cost_usd: number | null; total_tokens: number;
    stages: { name: string; duration_s: number; total_tokens: number; cost_usd: number | null }[];
  };
}

export interface JobStatus { id: string; status: "queued" | "running" | "done" | "error"; error: string | null; curated?: Curated; }
export interface AnalysisRow { id: string; url: string; domain: string; status: string; source: string; created_at: string; finished_at: string | null; verdict: Verdict | null; is_scam: number | null; scam_type: string | null; duration_s: number | null; }
export interface Stats { total: number; by_verdict: Record<string, number>; scam_count: number; legit_count: number; uncertain_count: number; scam_rate: number; scam_types: Record<string, number>; avg_duration_s: number; total_cost_usd: number; }
```

- [ ] **Step 2: Write the fetch client**

`web/src/api.ts`:

```ts
import type { AnalysisRow, Curated, JobStatus, Stats } from "./types";

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export const api = {
  analyze: (url: string, source = "web") =>
    fetch("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url, source }) }).then(j<{ id: string; status: string }>),
  status: (id: string) => fetch(`/api/analyze/${id}`).then(j<JobStatus>),
  list: (limit = 50, offset = 0) => fetch(`/api/analyses?limit=${limit}&offset=${offset}`).then(j<AnalysisRow[]>),
  detail: (id: string) => fetch(`/api/analyses/${id}`).then(j<Curated>),
  stats: () => fetch("/api/stats").then(j<Stats>),
};
```

- [ ] **Step 3: Commit**

```bash
git add web/src/api.ts web/src/types.ts
git commit -m "feat(web): add API client and shared types

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: App shell — router, nav layout, shared UI atoms

**Files:**
- Create: `web/src/main.tsx` (modify scaffold), `web/src/App.tsx`,
  `web/src/components/Layout.tsx`, `web/src/components/VerdictBadge.tsx`

- [ ] **Step 1: Router entry**

`web/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter><App /></BrowserRouter>
  </React.StrictMode>
);
```

- [ ] **Step 2: Routes**

`web/src/App.tsx`:

```tsx
import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Query from "./pages/Query";
import Report from "./pages/Report";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/history" element={<History />} />
        <Route path="/query" element={<Query />} />
        <Route path="/report/:id" element={<Report />} />
      </Routes>
    </Layout>
  );
}
```

- [ ] **Step 3: Layout with nav**

`web/src/components/Layout.tsx` — top nav bar with three `NavLink`s (Dashboard `/`,
查詢 `/query`, 歷史紀錄 `/history`), an app title, and a `<main className="max-w-6xl mx-auto p-6">`
content area. Use Tailwind; active link highlighted. Keep it a presentational component
that takes `children`.

- [ ] **Step 4: VerdictBadge atom**

`web/src/components/VerdictBadge.tsx` — given a `Verdict`, render a colored pill:
`scam`/`likely_scam` red, `uncertain` amber, `likely_legitimate`/`legitimate` green,
with Chinese labels (詐騙 / 可能詐騙 / 不確定 / 可能合法 / 合法). Export a `verdictColor(v)`
helper reused by charts.

- [ ] **Step 5: Verify it renders**

Run: `npm --prefix web run dev`, open `http://localhost:5173` — nav renders, empty
pages don't crash. (Pages added next tasks; create minimal placeholder default exports
for `Dashboard`/`History`/`Query`/`Report` returning `null` so the build compiles, to be
filled in by the following tasks.)

- [ ] **Step 6: Commit**

```bash
git add web/src/main.tsx web/src/App.tsx web/src/components web/src/pages
git commit -m "feat(web): app shell with router, nav, verdict badge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Dashboard page

**Files:**
- Create: `web/src/pages/Dashboard.tsx`, `web/src/components/StatCard.tsx`

- [ ] **Step 1: Build the page**

`web/src/pages/Dashboard.tsx`:
- On mount, `api.stats()` into state (loading/error handled).
- Render a row of `StatCard`s: 總分析數 (`total`), 詐騙 (`scam_count`), 合法 (`legit_count`),
  不確定 (`uncertain_count`), 詐騙率 (`scam_rate` as %), 平均耗時 (`avg_duration_s` `Xs`),
  總成本 (`total_cost_usd` `$X.XXXX`).
- A Recharts `BarChart` of `by_verdict` (bars colored via `verdictColor`).
- A Recharts `PieChart` (or BarChart) of `scam_types`.
- Empty state ("尚無分析紀錄") when `total === 0`.

`web/src/components/StatCard.tsx` — presentational: `{ label, value, accent? }`, a
Tailwind card.

- [ ] **Step 2: Verify against a live API**

Start the API (`uv run anti-scam-server`) and the web dev server; submit one analysis
via the Query page later, or insert a row, and confirm the dashboard updates after refresh.
For now confirm it renders the empty state without errors.

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/Dashboard.tsx web/src/components/StatCard.tsx
git commit -m "feat(web): dashboard with stat cards and charts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: History page

**Files:**
- Create: `web/src/pages/History.tsx`

- [ ] **Step 1: Build the page**

`web/src/pages/History.tsx`:
- On mount, `api.list()` into state.
- Render a table: 時間 (`created_at`), 網域 (`domain`), 判定 (`<VerdictBadge>` when `verdict`,
  else the raw `status` for queued/running/error), 來源 (`source`), 連結 (`url` truncated).
- Each row links to `/report/${id}` (only when `status === "done"`; otherwise show status text).
- Empty state when list is empty. A "重新整理" button re-fetches.

- [ ] **Step 2: Verify**

With the API running and at least one completed analysis, confirm rows render and a row
click navigates to the report.

- [ ] **Step 3: Commit**

```bash
git add web/src/pages/History.tsx
git commit -m "feat(web): history table

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 11: Query page (submit + live polling)

**Files:**
- Create: `web/src/pages/Query.tsx`, `web/src/hooks/useAnalysisPolling.ts`

- [ ] **Step 1: Polling hook**

`web/src/hooks/useAnalysisPolling.ts` — `useAnalysisPolling()` returns
`{ start(url), status, elapsed, result, error, reset }`:
- `start(url)` calls `api.analyze(url)`, stores the id, sets status `queued`, starts a
  `setInterval` (every 2s) calling `api.status(id)` and a separate 1s timer incrementing
  `elapsed` (seconds since start).
- When `status.status` becomes `done`, store `status.curated` as `result` and clear timers.
- When `error`, store the error and clear timers. Clean up timers on unmount.

- [ ] **Step 2: Page**

`web/src/pages/Query.tsx`:
- URL `<input>` + 開始檢查 button → `start(url)`.
- While `running`/`queued`: a spinner + "分析中… {elapsed}s" and a note that a real
  browser visit takes ~1-3 minutes.
- On `done`: render the verdict badge + headline + a "查看完整報告" link to `/report/${id}`,
  and inline-render the curated result (reuse the `Report` body component from Task 12).
- On `error`: show the error and a retry.

- [ ] **Step 3: Verify end-to-end**

With the API running, submit a real URL (e.g. a known-safe site) and confirm the live
counter ticks, then a result appears and persists in History.

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Query.tsx web/src/hooks/useAnalysisPolling.ts
git commit -m "feat(web): query page with live polling

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 12: Report view (shared by Query + Report route)

**Files:**
- Create: `web/src/components/ReportBody.tsx`, `web/src/pages/Report.tsx`

- [ ] **Step 1: ReportBody presentational component**

`web/src/components/ReportBody.tsx` — `{ data: Curated }`, renders:
- Header: domain + url + `started_at` + `<VerdictBadge>` + `scam_type`.
- **Headline signal block** highlighting `payment_explicitly_declined`: when `false`,
  emphasize "未出現明確的刷卡失敗 — 詐騙網站常見特徵"; when `true`, "出現明確刷卡失敗（合法金流跡象）".
  Include one sentence of explanatory copy on why this is the canonical signal.
- Reasoning paragraph + `risk_factors` as a list.
- Observation section: website summary, requested fields (chips), unexpected events,
  login/payment outcomes, outgoing links.
- Signals section: domain age/expiry, registrar, country, privacy, TLS issuer + free-DV
  flag + age, DNS MX + nameservers. Render `null` values as "—".
- Telemetry footer: duration, cost (`(pricing unknown)` when null), tokens, per-stage line.

- [ ] **Step 2: Report route**

`web/src/pages/Report.tsx` — reads `:id` param, `api.detail(id)` into state, renders
`<ReportBody data={...} />` with loading/404 handling and a back link to History.

- [ ] **Step 3: Wire Query to reuse ReportBody**

Update `web/src/pages/Query.tsx` to render `<ReportBody data={result} />` on `done`.

- [ ] **Step 4: Design review**

Review the rendered app against the `web-design-guidelines` skill (accessibility,
contrast, spacing). Fix issues found.

- [ ] **Step 5: Verify full build**

Run: `npm --prefix web run build`
Expected: type-checks and produces `web/dist/`. Then `uv run anti-scam-server` and open
`http://localhost:8000` — the SPA is served by FastAPI; all pages work.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ReportBody.tsx web/src/pages/Report.tsx web/src/pages/Query.tsx
git commit -m "feat(web): report view shared by query and history

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# PHASE 3 — Chrome Extension (native MV3)

> Verification is manual: load unpacked in Chrome with the API running.

### Task 13: Manifest + background service worker

**Files:**
- Create: `extension/manifest.json`, `extension/background.js`

- [ ] **Step 1: Manifest**

`extension/manifest.json`:

```json
{
  "manifest_version": 3,
  "name": "Anti-Scam Agent",
  "version": "0.1.0",
  "description": "右鍵檢查任一連結是否為詐騙網站（透過本機 Anti-Scam Agent）。",
  "permissions": ["contextMenus", "storage"],
  "host_permissions": ["http://localhost:8000/*", "http://127.0.0.1:8000/*"],
  "background": { "service_worker": "background.js" },
  "content_scripts": [
    { "matches": ["<all_urls>"], "js": ["content.js"], "css": ["content.css"], "run_at": "document_idle" }
  ],
  "options_page": "options.html"
}
```

- [ ] **Step 2: Background worker**

`extension/background.js`:

```js
const DEFAULT_API = "http://localhost:8000";

async function apiBase() {
  const { apiBase } = await chrome.storage.sync.get("apiBase");
  return apiBase || DEFAULT_API;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "asa-check-link",
    title: "用 Anti-Scam Agent 檢查此連結",
    contexts: ["link"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "asa-check-link" || !tab?.id) return;
  const url = info.linkUrl;
  chrome.tabs.sendMessage(tab.id, { type: "asa:start", url });
  const base = await apiBase();
  try {
    const res = await fetch(`${base}/api/analyze`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, source: "extension" }),
    });
    const { id } = await res.json();
    poll(tab.id, base, id, url);
  } catch (e) {
    chrome.tabs.sendMessage(tab.id, { type: "asa:error", url, error: String(e) });
  }
});

async function poll(tabId, base, id, url) {
  const deadline = Date.now() + 5 * 60 * 1000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 2000));
    let data;
    try {
      data = await (await fetch(`${base}/api/analyze/${id}`)).json();
    } catch (e) {
      continue; // transient; keep polling
    }
    if (data.status === "done") {
      chrome.tabs.sendMessage(tabId, { type: "asa:done", url, id, curated: data.curated, reportUrl: `${base}/report/${id}` });
      return;
    }
    if (data.status === "error") {
      chrome.tabs.sendMessage(tabId, { type: "asa:error", url, error: data.error || "analysis failed" });
      return;
    }
  }
  chrome.tabs.sendMessage(tabId, { type: "asa:error", url, error: "timeout" });
}
```

- [ ] **Step 3: Commit**

```bash
git add extension/manifest.json extension/background.js
git commit -m "feat(extension): MV3 manifest + context-menu background worker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 14: Content-script overlay (Shadow DOM, pure CSS)

**Files:**
- Create: `extension/content.js`, `extension/content.css`

- [ ] **Step 1: Content script**

`extension/content.js`:
- Track the last right-clicked element's viewport coordinates:
  `document.addEventListener("contextmenu", (e) => { lastPos = { x: e.clientX, y: e.clientY }; }, true);`
- On `chrome.runtime.onMessage`:
  - `asa:start` → create (or reuse) a host `<div>` appended to `document.body`, attach
    `attachShadow({ mode: "open" })`, inject `<style>` (the pure CSS below) + a card
    positioned `fixed` near `lastPos` (clamped to viewport). Show a spinner + "檢查中… 0s",
    start a 1s interval updating the seconds counter. Store the host on a module variable.
  - `asa:done` → stop the timer; replace body with a verdict badge (color by verdict),
    the `scam_type`/headline, the `payment_explicitly_declined` one-liner, and a
    "看完整報告" link (`target="_blank"`, href = `reportUrl`). Add a close ✕.
  - `asa:error` → stop the timer; show the error + close ✕.
- All DOM lives inside the shadow root so page CSS cannot affect it. Auto-dismiss the
  success card after ~30s, or on close click.

Provide the full implementation (the worker should write complete code, not a stub):
position clamping, timer cleanup, and a single reusable `ensureHost()` that recreates the
shadow root per check.

- [ ] **Step 2: Pure CSS**

`extension/content.css` is loaded as a content-script stylesheet, but because the overlay
lives in a Shadow DOM, also inline the same rules via a `<style>` in the shadow root.
Provide styles for: `.asa-card` (fixed, high z-index, white bg, rounded, shadow, sans-serif,
fixed width ~260px), `.asa-spinner` (CSS keyframe rotation), `.asa-badge--scam/--uncertain/--legit`
(colored pills), `.asa-link`, `.asa-close`. No external fonts/assets.

- [ ] **Step 3: Manual verification**

1. `uv run anti-scam-server` (API on :8000).
2. `chrome://extensions` → enable Developer mode → Load unpacked → select `extension/`.
3. On any page, right-click a link → "用 Anti-Scam Agent 檢查此連結".
4. Confirm: overlay appears near the link, "檢查中… Xs" counts up, then a verdict card
   shows; the "看完整報告" link opens the web report; the run appears in History.

- [ ] **Step 4: Commit**

```bash
git add extension/content.js extension/content.css
git commit -m "feat(extension): shadow-DOM overlay with live status and result

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 15: Extension options page

**Files:**
- Create: `extension/options.html`, `extension/options.js`

- [ ] **Step 1: Options page**

`extension/options.html` — a single text input for "API 位址" (default
`http://localhost:8000`) + a 儲存 button. `extension/options.js` loads the current value
from `chrome.storage.sync` and saves it on submit. Keep it pure HTML/CSS/JS.

- [ ] **Step 2: Verify**

Reload the extension, open its options (Details → Extension options), change the port,
confirm a check uses the new base URL.

- [ ] **Step 3: Commit**

```bash
git add extension/options.html extension/options.js
git commit -m "feat(extension): options page for API base URL

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

# PHASE 4 — Docs

### Task 16: Rewrite README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the README**

Rewrite `README.md` to cover, in order:
- One-paragraph project description (detection without blacklists; the card-decline signal).
- Prerequisites: `uv`, Node.js (for the web build), a `.env` with `OPENAI_API_KEY` and
  `AGENTMAIL_API_KEY` (point to `.env.example`).
- **CLI (unchanged):** `uv run anti-scam-agent <url-or-domain>` + `--verbose`.
- **API server:** `uv run anti-scam-server` → `http://localhost:8000`; note `--host`/`--port`
  and `ASA_PORT`/`ASA_DB_PATH` env vars; endpoint list.
- **Web app:** two modes —
  - Dev: `npm --prefix web install` then `npm --prefix web run dev` (Vite :5173, proxies
    `/api` to :8000; run the server too).
  - Demo/all-in-one: `npm --prefix web run build`, then `uv run anti-scam-server` and open
    `http://localhost:8000` (FastAPI serves `web/dist`).
- **Chrome extension:** `chrome://extensions` → Developer mode → Load unpacked → select
  `extension/`; default API `http://localhost:8000` (change in the extension's options);
  usage: right-click a link → "用 Anti-Scam Agent 檢查此連結".
- **Data:** runs persist to `./anti_scam.db` (gitignored); the CLI also writes `logs/`.
- **Tests:** `uv run pytest`.

- [ ] **Step 2: Verify the documented commands**

Walk through each command block once on a clean shell to confirm they are accurate
(server boots, `npm run build` produces `dist/`, pytest passes).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document API server, web app, and Chrome extension

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 17: Full verification sweep

- [ ] **Step 1: Backend tests**

Run: `uv run pytest`
Expected: all pass (live WHOIS/OpenAI tests require network + keys; note any skipped).

- [ ] **Step 2: End-to-end demo path**

1. `npm --prefix web run build`
2. `uv run anti-scam-server`
3. Open `http://localhost:8000`, run a Query against a known site, watch the live counter,
   see the report, confirm it appears on Dashboard + History.
4. Load the extension, right-click a link, confirm overlay → result → History row.

- [ ] **Step 3: Final commit (if any docs/tweaks)**

```bash
git add -A
git commit -m "chore: final verification tweaks for web/api/extension

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- **Blind-browser invariant:** nothing in this plan touches the Browsing Agent's prompt,
  `BrowsingResult` field descriptions, or the `read_email_inbox` tool description. Keep it
  that way — all new code is downstream of browsing.
- **`data/` is hand-edited** by the user; the DB lives at repo-root `./anti_scam.db`, never
  inside `data/`. Commit with explicit pathspecs (the commands above already do).
- **Serialized worker:** do not add parallelism — concurrency is intentionally 1 (real
  browser + cost control).
- **Cost is honest:** `cost_usd` may be `None` (unknown model pricing); render it as
  "(pricing unknown)", never a guessed number.
```
