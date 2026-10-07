"""Run the service: `python -m procurement_decision_api`."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8088"))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(
        "procurement_decision_api.app:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
