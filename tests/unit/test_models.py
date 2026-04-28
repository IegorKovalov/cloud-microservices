"""Unit tests for shared Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import (
    FaultKind,
    FaultRequest,
    HealthStatus,
    ProcessRequest,
    ServiceMetrics,
    StoreRecord,
)


class TestHealthStatus:
    def test_defaults(self) -> None:
        h = HealthStatus(service="svc")
        assert h.service == "svc"
        assert h.status == "ok"
        assert h.timestamp is not None


class TestServiceMetrics:
    def test_default_zero_values(self) -> None:
        m = ServiceMetrics(service="svc")
        assert m.request_count == 0
        assert m.error_count == 0
        assert m.avg_latency_ms == 0.0
        assert m.uptime_seconds == 0.0
        assert m.extra == {}


class TestProcessRequest:
    def test_requires_at_least_one_item(self) -> None:
        with pytest.raises(ValidationError):
            ProcessRequest(items=[])

    def test_default_operation_is_sum(self) -> None:
        req = ProcessRequest(items=[1.0, 2.0, 3.0])
        assert req.operation == "sum"


class TestStoreRecord:
    def test_round_trip(self) -> None:
        r = StoreRecord(key="k", value=1.5)
        assert r.model_dump() == {"key": "k", "value": 1.5}


class TestFaultRequest:
    def test_clamps_via_validation(self) -> None:
        with pytest.raises(ValidationError):
            FaultRequest(target="t", error_rate=1.5)
        with pytest.raises(ValidationError):
            FaultRequest(target="t", duration_ms=-5)

    def test_kind_enum(self) -> None:
        assert FaultKind("kill") is FaultKind.KILL
