"""Structured logging setup shared by every Python service and script.

We use ``structlog`` to emit one JSON object per log line. Every line is
guaranteed to include ``service``, ``timestamp``, ``level`` and
``message`` keys, which is what the log_aggregator parses downstream.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def _level_from_env(default: str = "INFO") -> int:
    """Resolve the configured log level from the ``LOG_LEVEL`` env var.

    Args:
        default: Level name to fall back to if the env var is missing or invalid.

    Returns:
        The integer log level that ``logging`` understands.
    """
    raw = os.getenv("LOG_LEVEL", default).upper()
    return getattr(logging, raw, logging.INFO)


def configure_logging(service_name: str) -> structlog.stdlib.BoundLogger:
    """Configure ``structlog`` once and return a logger bound to ``service``.

    The returned logger emits a single JSON object per log call, on stdout,
    with a stable schema understood by ``monitoring/log_aggregator.py``.

    Args:
        service_name: Logical service name, attached to every log entry.

    Returns:
        A bound ``structlog`` logger ready to use.

    Raises:
        ValueError: If ``service_name`` is empty.
    """
    if not service_name:
        raise ValueError("service_name must be a non-empty string")

    level = _level_from_env()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    return structlog.get_logger().bind(service=service_name)


def bind_request_context(**fields: Any) -> None:
    """Attach per-request fields to the current logging context.

    Args:
        **fields: Arbitrary key/value pairs to merge into every subsequent
            log line emitted in this asyncio task / thread.

    Returns:
        None.
    """
    structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    """Remove all fields bound by :func:`bind_request_context`."""
    structlog.contextvars.clear_contextvars()
