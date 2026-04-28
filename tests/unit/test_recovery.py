"""Unit tests for the orchestration.recovery module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import respx

from orchestration import ServiceTarget
from orchestration.recovery import RecoveryWatcher, WatcherConfig
from shared.logging_config import configure_logging


def _make_target(name: str, port: int) -> ServiceTarget:
    return ServiceTarget(
        name=name,
        container=f"cm-{name}",
        health_url=f"http://127.0.0.1:{port}/health",
    )


def _make_watcher(target: ServiceTarget, *, threshold: int = 3) -> RecoveryWatcher:
    logger = configure_logging("test-recovery")
    config = WatcherConfig(
        poll_interval_seconds=0.0,
        failure_threshold=threshold,
        recovery_backoff_seconds=0.0,
        docker_bin="docker",
    )
    return RecoveryWatcher([target], config, logger=logger)


@respx.mock
async def test_no_recovery_when_healthy() -> None:
    target = _make_target("svc", 8080)
    respx.get(target.health_url).mock(return_value=httpx.Response(200))
    watcher = _make_watcher(target)
    watcher._docker_start = AsyncMock(return_value=True)  # type: ignore[method-assign]
    async with httpx.AsyncClient() as client:
        await watcher._tick(client)
    watcher._docker_start.assert_not_called()
    assert watcher.history == []


@respx.mock
async def test_failures_below_threshold_do_not_trigger() -> None:
    target = _make_target("svc", 8080)
    respx.get(target.health_url).mock(return_value=httpx.Response(500))
    watcher = _make_watcher(target, threshold=3)
    watcher._docker_start = AsyncMock(return_value=True)  # type: ignore[method-assign]
    async with httpx.AsyncClient() as client:
        for _ in range(2):
            await watcher._tick(client)
    watcher._docker_start.assert_not_called()


@respx.mock
async def test_failures_at_threshold_trigger_recovery() -> None:
    target = _make_target("svc", 8080)
    respx.get(target.health_url).mock(return_value=httpx.Response(500))
    watcher = _make_watcher(target, threshold=3)
    watcher._docker_start = AsyncMock(return_value=True)  # type: ignore[method-assign]
    async with httpx.AsyncClient() as client:
        for _ in range(3):
            await watcher._tick(client)
    watcher._docker_start.assert_awaited_once_with(target.container)
    assert len(watcher.history) == 1
    event = watcher.history[0]
    assert event.success is True
    assert event.action == "docker_start"


@respx.mock
async def test_failed_docker_start_records_unsuccessful_event() -> None:
    target = _make_target("svc", 8080)
    respx.get(target.health_url).mock(return_value=httpx.Response(500))
    watcher = _make_watcher(target, threshold=1)
    watcher._docker_start = AsyncMock(return_value=False)  # type: ignore[method-assign]
    async with httpx.AsyncClient() as client:
        await watcher._tick(client)
    assert len(watcher.history) == 1
    assert watcher.history[0].success is False


async def test_request_stop_breaks_loop() -> None:
    target = _make_target("svc", 8080)
    watcher = _make_watcher(target, threshold=999)
    watcher._docker_start = AsyncMock(return_value=True)  # type: ignore[method-assign]

    async def _stop_soon() -> None:
        await asyncio.sleep(0.01)
        watcher.request_stop()

    with respx.mock:
        respx.get(target.health_url).mock(return_value=httpx.Response(200))
        await asyncio.gather(watcher.run(), _stop_soon())
