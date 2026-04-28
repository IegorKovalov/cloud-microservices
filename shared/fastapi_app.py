"""FastAPI helper used by every Python service in this repo.

Provides:

* A factory ``create_service_app`` that wires a service's name into the
  FastAPI app, registers the standard ``/health`` and ``/metrics``
  endpoints, and installs a request-timing middleware that updates a
  shared :class:`shared.utils.MetricsState`.

Keeping this here means each service's ``main.py`` only contains the
domain-specific routes — health, metrics, and observability are uniform
across the whole system.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from shared.logging_config import (
    bind_request_context,
    clear_request_context,
    configure_logging,
)
from shared.models import HealthStatus, ServiceMetrics
from shared.utils import MetricsState


def create_service_app(service_name: str) -> tuple[FastAPI, MetricsState]:
    """Build a FastAPI app pre-wired with ``/health`` + ``/metrics``.

    Args:
        service_name: Logical service name, e.g. ``service-a``. Used as
            the structured-log identifier and in health responses.

    Returns:
        A tuple of ``(app, metrics)``. The caller registers domain
        routes on ``app`` and increments ``metrics`` if it has its own
        custom counters; the standard request_count / error_count /
        latency tracking is already wired up via middleware.

    Raises:
        ValueError: If ``service_name`` is empty.
    """
    if not service_name:
        raise ValueError("service_name must be non-empty")

    logger = configure_logging(service_name)
    metrics = MetricsState(service=service_name)
    app = FastAPI(title=service_name, version="1.0.0")

    @app.middleware("http")
    async def _observability_middleware(  # type: ignore[no-untyped-def]
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        bind_request_context(path=request.url.path, method=request.method)
        start = time.perf_counter()
        is_error = False
        try:
            response = await call_next(request)
        except Exception as exc:
            is_error = True
            logger.exception("request_unhandled_exception", error=str(exc))
            response = JSONResponse(
                {"detail": "internal server error"}, status_code=500
            )
        else:
            is_error = response.status_code >= 500
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            metrics.record(duration_ms=duration_ms, is_error=is_error)
            logger.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                is_error=is_error,
            )
            clear_request_context()
        return response

    @app.get("/health", response_model=HealthStatus, tags=["observability"])
    async def _health() -> HealthStatus:
        """Liveness probe consumed by docker-compose and the orchestrator."""
        return HealthStatus(service=service_name)

    @app.get("/metrics", response_model=ServiceMetrics, tags=["observability"])
    async def _metrics() -> ServiceMetrics:
        """Lightweight metrics endpoint scraped by ``metrics_collector``."""
        snap = metrics.snapshot()
        return ServiceMetrics(
            service=str(snap["service"]),
            request_count=int(snap["request_count"]),
            error_count=int(snap["error_count"]),
            avg_latency_ms=float(snap["avg_latency_ms"]),
            uptime_seconds=float(snap["uptime_seconds"]),
        )

    return app, metrics
