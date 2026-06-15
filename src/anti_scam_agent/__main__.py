import argparse
import asyncio
import os
import sys

from anti_scam_agent.pipeline import run_pipeline


def _normalize_url(raw: str) -> str:
    if "://" in raw:
        return raw
    return f"http://{raw}"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="anti-scam-agent",
        description="Assess whether a website is a scam / phishing site.",
    )
    parser.add_argument("url", help="Target URL or bare domain.")
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=os.environ.get("ASA_LOG_VERBOSE", "") not in ("", "0", "false", "False"),
        help="Include full agent thinking in report.log (report.json always has it). "
        "Also enabled via ASA_LOG_VERBOSE=1.",
    )
    args = parser.parse_args()

    url = _normalize_url(args.url)
    assessment = asyncio.run(run_pipeline(url, verbose=args.verbose))
    print(assessment.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.exit(main())
