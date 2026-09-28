"""Structured application logging configured without sensitive request content."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from labjarvis.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure process logging for JSON production logs or readable local output."""

    level = getattr(logging, settings.log_level)
    logging.basicConfig(stream=sys.stdout, format="%(message)s", level=level, force=True)
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
