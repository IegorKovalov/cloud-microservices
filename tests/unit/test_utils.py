"""Unit tests for shared.utils."""

from __future__ import annotations

import asyncio
import time

import pytest

from shared.utils import MetricsState, env_float, env_int, env_str, retry_async


class TestEnvHelpers:
    def test_env_str_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FOO", raising=False)
        assert env_str("FOO", "bar") == "bar"

    def test_env_str_uses_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FOO", "qux")
        assert env_str("FOO", "bar") == "qux"

    def test_env_int_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PORT", "9000")
        assert env_int("PORT", 8000) == 9000

    def test_env_int_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PORT", "not-a-number")
        assert env_int("PORT", 8000) == 8000

    def test_env_float_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE", "0.25")
        assert env_float("RATE", 1.0) == pytest.approx(0.25)


class TestMetricsState:
    def test_record_updates_counters(self) -> None:
        m = MetricsState(service="svc")
        m.record(10.0, is_error=False)
        m.record(20.0, is_error=True)
        m.record(30.0, is_error=False)
        assert m.request_count == 3
        assert m.error_count == 1
        assert m.avg_latency_ms == pytest.approx(20.0)

    def test_uptime_is_positive(self) -> None:
        m = MetricsState(service="svc")
        time.sleep(0.01)
        assert m.uptime_seconds > 0

    def test_snapshot_shape(self) -> None:
        m = MetricsState(service="svc")
        m.record(5.0, is_error=False)
        snap = m.snapshot()
        assert snap["service"] == "svc"
        assert snap["request_count"] == 1
        assert snap["error_count"] == 0


class TestRetryAsync:
    async def test_succeeds_first_try(self) -> None:
        calls: list[int] = []

        async def func() -> str:
            calls.append(1)
            return "ok"

        assert await retry_async(func, attempts=3, base_delay_seconds=0.0) == "ok"
        assert len(calls) == 1

    async def test_retries_on_failure_then_succeeds(self) -> None:
        attempts: list[int] = []

        async def func() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("boom")
            return "ok"

        result = await retry_async(
            func, attempts=5, base_delay_seconds=0.0, exceptions=(RuntimeError,)
        )
        assert result == "ok"
        assert len(attempts) == 3

    async def test_exhausts_attempts(self) -> None:
        async def func() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await retry_async(
                func, attempts=2, base_delay_seconds=0.0, exceptions=(ValueError,)
            )

    async def test_invalid_attempts_raises(self) -> None:
        async def func() -> None:
            return None

        with pytest.raises(ValueError):
            await retry_async(func, attempts=0)

    async def test_does_not_swallow_unexpected_exceptions(self) -> None:
        async def func() -> None:
            raise KeyError("not retryable")

        with pytest.raises(KeyError):
            await retry_async(
                func,
                attempts=3,
                base_delay_seconds=0.0,
                exceptions=(ValueError,),
            )
