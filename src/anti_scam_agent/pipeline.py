import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from anti_scam_agent.analysis import run_analysis_agent
from anti_scam_agent.browsing import run_browsing_agent
from anti_scam_agent.email_evidence import make_client, pick_inbox
from anti_scam_agent.models import ScamAssessment
from anti_scam_agent.persona import generate_persona
from anti_scam_agent.reporting import (
    LLMCallMetrics,
    RunReport,
    StageReport,
    run_debug_log,
    write_run_report,
)
from anti_scam_agent.signals import collect_static_signals

_LOGS_ROOT = Path("logs")


def _extract_domain(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


async def run_pipeline(url: str, verbose: bool = False) -> tuple[ScamAssessment, RunReport]:
    domain = _extract_domain(url)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    # Pre-compute the run folder so debug.log lands beside report.log/report.json.
    run_folder = _LOGS_ROOT / f"{started_at.replace(':', '-')}_{domain}"

    with run_debug_log(run_folder / "debug.log"):
        run_start = time.monotonic()
        persona = generate_persona()

        client = make_client()  # raises if unconfigured
        inbox = pick_inbox()
        persona = persona.model_copy(update={"email": inbox})

        result, browsing_stage = await run_browsing_agent(url, persona, client, inbox)

        sig_start = time.monotonic()
        static_signals = await asyncio.to_thread(collect_static_signals, url)
        signals_stage = StageReport.build(
            name="signals", model=None, duration_s=time.monotonic() - sig_start, steps=[], other_metrics=LLMCallMetrics()
        )

        assessment, analysis_stage = await run_analysis_agent(result, domain, static_signals)

        run_duration = time.monotonic() - run_start
        report = RunReport.build(
            target_domain=domain,
            url=url,
            started_at=started_at,
            duration_s=run_duration,
            stages=[browsing_stage, signals_stage, analysis_stage],
            verdict=assessment.verdict.value,
            is_scam=assessment.is_scam,
            scam_type=assessment.scam_type,
        )
        folder = write_run_report(report, logs_root=_LOGS_ROOT, verbose=verbose)

    # stderr so stdout stays the assessment-JSON contract.
    print(f"📄 report: {folder / 'report.log'}", file=sys.stderr)
    return assessment, report
