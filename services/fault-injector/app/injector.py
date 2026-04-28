"""Container lifecycle and fault-state management for the chaos plane.

This module wraps the docker SDK behind a tiny ``ContainerController``
class so the FastAPI handlers stay declarative. Latency and error
injection share a small in-memory ``FaultState`` object — both
endpoints (``/slow`` and ``/broken``) read from it on every request.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any

import docker
import structlog
from docker.errors import APIError, NotFound

_LOGGER = structlog.get_logger()


@dataclass
class FaultState:
    """Mutable, async-safe state for latency and error injection."""

    latency_ms: int = 0
    error_rate: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def set_latency(self, duration_ms: int) -> None:
        """Set the artificial latency applied to ``/slow``.

        Args:
            duration_ms: Non-negative latency in milliseconds.

        Returns:
            None.

        Raises:
            ValueError: If ``duration_ms`` is negative.
        """
        if duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")
        async with self._lock:
            self.latency_ms = duration_ms

    async def set_error_rate(self, rate: float) -> None:
        """Set the probability that ``/broken`` returns a 500.

        Args:
            rate: A float in ``[0.0, 1.0]``.

        Returns:
            None.

        Raises:
            ValueError: If ``rate`` is outside ``[0, 1]``.
        """
        if not 0.0 <= rate <= 1.0:
            raise ValueError("rate must be in [0.0, 1.0]")
        async with self._lock:
            self.error_rate = rate

    async def snapshot(self) -> dict[str, float | int]:
        """Return a JSON-serialisable snapshot of the current state.

        Returns:
            ``{"latency_ms": ..., "error_rate": ...}``.
        """
        async with self._lock:
            return {"latency_ms": self.latency_ms, "error_rate": self.error_rate}

    async def maybe_fail(self) -> bool:
        """Sample the error_rate to decide whether ``/broken`` should fail.

        Returns:
            ``True`` when this call should return an error.
        """
        async with self._lock:
            return random.random() < self.error_rate


class ContainerController:
    """Thin async wrapper around the docker SDK for container lifecycle."""

    def __init__(self, allow_targets: set[str] | None = None) -> None:
        """Construct a controller bound to the local docker daemon.

        Args:
            allow_targets: Optional allow-list of container names this
                controller is permitted to act on. ``None`` allows any
                container (only useful for local demos).
        """
        self._client = docker.from_env()
        self._allow_targets = allow_targets

    def _check(self, target: str) -> None:
        """Validate ``target`` against the allow-list, if any.

        Args:
            target: Container name to validate.

        Raises:
            PermissionError: If the target is not in the allow-list.
        """
        if self._allow_targets is None:
            return
        if target not in self._allow_targets:
            raise PermissionError(
                f"target {target!r} not in allow-list {sorted(self._allow_targets)}"
            )

    async def stop(self, target: str, timeout: int = 5) -> dict[str, Any]:
        """Stop a container by name.

        Args:
            target: Container name (e.g. ``cm-service-a``).
            timeout: Seconds to wait for graceful shutdown.

        Returns:
            ``{"target": ..., "action": "stop", "ok": <bool>, "detail": ...}``.
        """
        self._check(target)
        return await asyncio.to_thread(self._stop_sync, target, timeout)

    def _stop_sync(self, target: str, timeout: int) -> dict[str, Any]:
        try:
            container = self._client.containers.get(target)
            container.stop(timeout=timeout)
            _LOGGER.info("container_stopped", target=target)
            return {
                "target": target,
                "action": "stop",
                "ok": True,
                "detail": "stopped",
            }
        except NotFound:
            return {
                "target": target,
                "action": "stop",
                "ok": False,
                "detail": "container not found",
            }
        except APIError as exc:
            return {
                "target": target,
                "action": "stop",
                "ok": False,
                "detail": str(exc),
            }

    async def start(self, target: str) -> dict[str, Any]:
        """Start a previously stopped container by name.

        Args:
            target: Container name.

        Returns:
            Same shape as :meth:`stop`.
        """
        self._check(target)
        return await asyncio.to_thread(self._start_sync, target)

    def _start_sync(self, target: str) -> dict[str, Any]:
        try:
            container = self._client.containers.get(target)
            container.start()
            _LOGGER.info("container_started", target=target)
            return {
                "target": target,
                "action": "start",
                "ok": True,
                "detail": "started",
            }
        except NotFound:
            return {
                "target": target,
                "action": "start",
                "ok": False,
                "detail": "container not found",
            }
        except APIError as exc:
            return {
                "target": target,
                "action": "start",
                "ok": False,
                "detail": str(exc),
            }

    async def status(self, target: str) -> dict[str, Any]:
        """Return the current runtime status of a target container.

        Args:
            target: Container name.

        Returns:
            ``{"target": ..., "status": <docker_status_or_'missing'>}``.
        """
        return await asyncio.to_thread(self._status_sync, target)

    def _status_sync(self, target: str) -> dict[str, Any]:
        try:
            container = self._client.containers.get(target)
            return {"target": target, "status": container.status}
        except NotFound:
            return {"target": target, "status": "missing"}
        except APIError as exc:
            return {"target": target, "status": "error", "detail": str(exc)}
