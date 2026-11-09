"""
app/logger.py — Structured logging setup using rich for console output.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from app.config import settings


def setup_logging() -> logging.Logger:
    """Configure root logger once at application startup."""
    log_dir = Path(settings.log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [
        RichHandler(
            console=Console(stderr=True),
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
        )
    ]

    # File handler
    file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True,
    )

    # Quiet noisy libraries
    for quiet in ("httpx", "httpcore", "sqlalchemy.engine", "apscheduler"):
        logging.getLogger(quiet).setLevel(logging.WARNING)

    return logging.getLogger("faceless")


logger = setup_logging()


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"faceless.{name}")
