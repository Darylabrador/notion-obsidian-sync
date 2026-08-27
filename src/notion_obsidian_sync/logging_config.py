"""Logging setup: console output in the ACTION-prefixed style requested by the
project spec, plus a rotating file log. The Notion token is never logged.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

SYNC_ACTION_LEVEL = 25  # between INFO(20) and WARNING(30)
logging.addLevelName(SYNC_ACTION_LEVEL, "SYNC")

_LOGGER_NAME = "notion_obsidian_sync"


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        action = getattr(record, "action", None)
        if action:
            record.msg = f"{action:<6} {record.msg}"
        return super().format(record)


def setup_logging(
    level: str = "INFO", log_dir: Path | None = None, verbose: bool = False
) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else level.upper())
    logger.handlers.clear()
    logger.propagate = False

    fmt = "%(asctime)s %(levelname)-5s %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    console = logging.StreamHandler()
    console.setFormatter(_ConsoleFormatter(fmt, datefmt=datefmt))
    logger.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "sync.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(_ConsoleFormatter(fmt, datefmt=datefmt))
        logger.addHandler(file_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_action(logger: logging.Logger, action: str, message: str) -> None:
    """Log a sync action line, e.g. `log_action(logger, "CREATE", "Project Alpha")`."""
    level = logging.WARNING if action == "WARN" else SYNC_ACTION_LEVEL
    logger.log(level, message, extra={"action": action})
