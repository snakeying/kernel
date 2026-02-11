from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)


def cleanup_old_files(dir_path: Path, *, max_age_days: int) -> int:
    if not dir_path.exists():
        return 0
    cutoff = time.time() - max_age_days * 86400
    deleted = 0
    for p in dir_path.iterdir():
        try:
            if not p.is_file():
                continue
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                deleted += 1
        except Exception:
            log.debug("Cleanup failed for %s", p, exc_info=True)
    return deleted


async def periodic_cleanup(
    data_path: Path, *, max_age_days: int, interval_hours: int = 24
) -> None:
    dirs = ("downloads", "cli_outputs", "voice_replies")
    while True:
        try:
            total = 0
            for d in dirs:
                total += cleanup_old_files(data_path / d, max_age_days=max_age_days)
            if total:
                log.info("Cleanup: removed %d old files (>%dd)", total, max_age_days)
        except Exception:
            log.debug("Cleanup loop failed", exc_info=True)
        await asyncio.sleep(interval_hours * 3600)

