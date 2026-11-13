"""
Structured JSON logging configuration.

Produces machine-readable logs that can be shipped to any log
aggregation system (ELK, Loki, CloudWatch, etc.) without modification.

Updated for structlog 26.x API — make_filtering_bound_logger and
PrintLoggerFactory are accessed via structlog namespace directly.
"""

import logging
import sys
from typing import Any

import structlog


def configure_logging(environment: str = "development") -> None:
    """
    Configure structlog for the application.

    In development: human-readable console output with colours.
    In production/testing: JSON output suitable for log aggregation.
    """
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        # Note: add_logger_name is omitted — it requires a stdlib logger
        # (which has a .name attribute). PrintLoggerFactory returns a
        # PrintLogger which does not. Logger identity comes from get_logger(name).
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if environment == "development":
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a named structlog logger."""
    return structlog.get_logger(name)
