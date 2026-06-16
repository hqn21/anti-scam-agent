"""FastAPI app wrapping the analysis pipeline. Jobs run through a single serialized
background worker (concurrency=1) and persist to SQLite. Clients submit a URL, get a
job id, and poll. Also serves the built web app from web/dist when present."""

from __future__ import annotations

import asyncio
import json
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
    return json.loads(row["report_json"])


@app.get("/api/stats")
async def stats():
    return db.stats(DB_PATH)


# Serve the built SPA at / when present (after a `npm run build` in web/).
if _WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_WEB_DIST, html=True), name="web")
