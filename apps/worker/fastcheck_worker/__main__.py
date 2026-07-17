"""Điểm vào worker: `python -m fastcheck_worker` (qua `uv run`)."""

from __future__ import annotations

import asyncio
import logging

from .config import load_config
from .ws_client import WorkerClient


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )


def main() -> None:
    _setup_logging()
    config = load_config()
    logging.getLogger("fastcheck.worker").info(
        "worker khởi động (mode=%s, max_concurrency=%d)",
        config.gemlogin_mode,
        config.max_concurrency,
    )
    client = WorkerClient(config)
    try:
        asyncio.run(client.run_forever())
    except KeyboardInterrupt:
        logging.getLogger("fastcheck.worker").info("worker dừng.")


if __name__ == "__main__":
    main()
