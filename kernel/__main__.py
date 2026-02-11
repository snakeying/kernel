"""Kernel entry point: ``uv run -m kernel``."""

import asyncio
import logging
import sys

from kernel.bot import run_bot

log = logging.getLogger("kernel")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("Shutting down …")


if __name__ == "__main__":
    main()
