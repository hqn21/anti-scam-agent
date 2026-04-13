import argparse
import asyncio
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
    args = parser.parse_args()

    url = _normalize_url(args.url)
    assessment = asyncio.run(run_pipeline(url))
    print(assessment.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.exit(main())
