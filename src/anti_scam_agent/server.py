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
