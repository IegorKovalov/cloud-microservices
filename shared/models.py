"""Pydantic models shared across services.

The models in this file describe the wire format used for inter-service
HTTP communication. Keeping them in one place gives every Python service
a single authoritative schema; the C++ worker matches the JSON shape by
hand (see services/cpp-worker/src/main.cpp).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``.

    Returns:
        Current time as a UTC ``datetime`` with tzinfo attached.
    """
    return datetime.now(tz=timezone.utc)


class HealthStatus(BaseModel):
    """Standard payload returned by every service's ``GET /health`` endpoint."""

    status: str = Field(default="ok", description="`ok` when the service is alive.")
    service: str = Field(..., description="Logical service name, e.g. `service-a`.")
    timestamp: datetime = Field(default_factory=_utcnow)


class ServiceMetrics(BaseModel):
    """Standard payload returned by every service's ``GET /metrics`` endpoint."""

    service: str
    request_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    uptime_seconds: float = 0.0
    extra: dict[str, Any] = Field(default_factory=dict)


class ProcessRequest(BaseModel):
    """Domain payload sent to the API gateway and forwarded to ``service-a``."""

    items: list[float] = Field(..., min_length=1, description="Numbers to process.")
    operation: str = Field(
        default="sum",
        description="Operation to apply: `sum`, `mean`, or `square_sum` (C++).",
    )


class ProcessResponse(BaseModel):
    """Result of a processing pipeline run."""

    operation: str
    result: float
    items_processed: int
    fanout_results: list[float] = Field(default_factory=list)
    cpp_result: float | None = None
    duration_ms: float


class StoreRecord(BaseModel):
    """Record persisted by ``service-b`` (in-memory key/value store)."""

    key: str
    value: float


class FaultKind(str, Enum):
    """Supported fault types the chaos injector can apply."""

    KILL = "kill"
    LATENCY = "latency"
    ERROR = "error"


class FaultRequest(BaseModel):
    """Body of ``POST /inject/<kind>`` on the fault-injector service."""

    target: str = Field(..., description="Container name to apply the fault to.")
    duration_ms: int = Field(default=2000, ge=0, le=60_000)
    error_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class FaultResponse(BaseModel):
    """Result of a fault-injection request."""

    kind: FaultKind
    target: str
    accepted: bool
    detail: str
    timestamp: datetime = Field(default_factory=_utcnow)


class RecoveryEvent(BaseModel):
    """Single recovery action recorded by ``orchestration/recovery.py``."""

    service: str
    detected_at: datetime
    recovered_at: datetime | None = None
    duration_seconds: float | None = None
    action: str = "docker_restart"
    success: bool = False
