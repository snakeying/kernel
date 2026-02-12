from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from kernel.config import Config
from kernel.tg_common import MaskingFormatter

def setup_logging(config: Config) -> None:
    log_dir = config.data_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fh = RotatingFileHandler(
        log_dir / "kernel.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(MaskingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(fh)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
