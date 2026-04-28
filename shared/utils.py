"""Small reusable helpers: retry/backoff, timing, env parsing, metrics state.

Everything in this module is dependency-light (stdlib + structlog) so that
each service Docker image stays small.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TypeVar

import structlog

T = TypeVar("T")

_LOGGER = structlog.get_logger()


def env_str(name: str, default: str) -> str:
    """Read a string environment variable with a default fallback.

    Args:
        name: Variable name.
        default: Value to return if the variable is unset or empty.

    Returns:
        The variable's value, or ``default`` when missing/empty.
    """
    value = os.getenv(name)
    return value if value else default


def env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back on parse errors.

    Args:
        name: Variable name.
        default: Value returned when missing or non-integer.

    Returns:
        Parsed integer or ``default``.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        _LOGGER.warning("env_int_parse_failed", name=name, raw=raw, default=default)
        return default


def env_float(name: str, default: float) -> float:
    """Read a float environment variable with a default fallback.

    Args:
        name: Variable name.
        default: Value returned when missing or non-numeric.

    Returns:
        Parsed float or ``default``.
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        _LOGGER.warning("env_float_parse_failed", name=name, raw=raw, default=default)
        return default


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 0.2,
    max_delay_seconds: float = 5.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    logger: structlog.stdlib.BoundLogger | None = None,
    op: str = "retry_async",
) -> T:
    """Run ``func`` with exponential backoff + jitter.

    Args:
        func: Zero-arg async callable to execute.
        attempts: Total attempts before giving up. Must be >= 1.
        base_delay_seconds: Initial delay between attempts.
        max_delay_seconds: Cap on per-attempt delay after exponential growth.
        exceptions: Exception classes considered retryable.
        logger: Optional structured logger; one is created if omitted.
        op: Logical operation name used in log entries.

    Returns:
        The successful result of ``func``.

    Raises:
        ValueError: If ``attempts`` is less than 1.
        BaseException: The last exception raised by ``func`` if all
            attempts fail.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    log = logger or _LOGGER
    last_exc: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except exceptions as exc:
            last_exc = exc
            if attempt == attempts:
                log.error("retry_exhausted", op=op, attempt=attempt, error=str(exc))
                raise
            delay = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
            delay += random.uniform(0, base_delay_seconds)
            log.warning(
                "retry_backoff",
                op=op,
                attempt=attempt,
                delay_seconds=round(delay, 3),
                error=str(exc),
            )
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc


@asynccontextmanager
async def timed(label: str, logger: structlog.stdlib.BoundLogger | None = None):
    """Async context manager that logs how long the wrapped block took.

    Args:
        label: Operation label included in the log entry.
        logger: Optional bound logger; one is created if omitted.

    Yields:
        None. Use as ``async with timed("op"): ...``.
    """
    log = logger or _LOGGER
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        log.info("operation_timed", op=label, duration_ms=round(elapsed_ms, 2))


@dataclass
class MetricsState:
    """In-process metrics counters used by every Python service.

    Threadsafe-ish: FastAPI runs single-event-loop so we don't need locks
    for these scalar updates. ``record()`` is the only mutation entry point.
    """

    service: str
    started_at: float = field(default_factory=time.monotonic)
    request_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0

    def record(self, duration_ms: float, is_error: bool) -> None:
        """Update counters for a single completed request.

        Args:
            duration_ms: Elapsed wall-clock time in milliseconds.
            is_error: ``True`` if the request resulted in a 5xx response.

        Returns:
            None.
        """
        self.request_count += 1
        self.total_latency_ms += duration_ms
        if is_error:
            self.error_count += 1

    @property
    def avg_latency_ms(self) -> float:
        """Mean latency across all observed requests, or 0 when none.

        Returns:
            The arithmetic mean of recorded latencies in milliseconds.
        """
        if self.request_count == 0:
            return 0.0
        return self.total_latency_ms / self.request_count

    @property
    def uptime_seconds(self) -> float:
        """Seconds elapsed since this object was created.

        Returns:
            Monotonic uptime in seconds.
        """
        return time.monotonic() - self.started_at

    def snapshot(self) -> dict[str, float | int | str]:
        """Return a JSON-serialisable view of the counters.

        Returns:
            A dict with the same keys as ``shared.models.ServiceMetrics``.
        """
        return {
            "service": self.service,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "uptime_seconds": round(self.uptime_seconds, 3),
        }
