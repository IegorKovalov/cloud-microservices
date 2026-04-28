"""Detection and recovery loop.

Polls every service's ``/health`` endpoint on a fixed interval. After
``HEALTH_FAILURE_THRESHOLD`` consecutive failures for a given service,
issues a ``docker start`` against the container. Each recovery attempt
is recorded as a :class:`shared.models.RecoveryEvent` and emitted as a
structured log line so the log aggregator can pick it up.
"""

from __future__ import annotations

import asyncio
import shutil
import signal
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import structlog

from orchestration import ServiceTarget, default_targets
from orchestration.health_check import probe_all
from shared.logging_config import configure_logging
from shared.models import RecoveryEvent
from shared.utils import env_float, env_int


@dataclass
class WatcherConfig:
    """Knobs governing the recovery loop, populated from env vars."""

    poll_interval_seconds: float = 5.0
    failure_threshold: int = 3
    recovery_backoff_seconds: float = 2.0
    docker_bin: str = "docker"

    @classmethod
    def from_env(cls) -> "WatcherConfig":
        """Build a :class:`WatcherConfig` from the environment.

        Returns:
            A populated config instance.
        """
        return cls(
            poll_interval_seconds=env_float("HEALTH_POLL_INTERVAL_SECONDS", 5.0),
            failure_threshold=env_int("HEALTH_FAILURE_THRESHOLD", 3),
            recovery_backoff_seconds=env_float("RECOVERY_BACKOFF_SECONDS", 2.0),
            docker_bin=shutil.which("docker") or "docker",
        )


@dataclass
class _ServiceRuntime:
    """Per-service running state inside :class:`RecoveryWatcher`."""

    target: ServiceTarget
    consecutive_failures: int = 0
    history: list[RecoveryEvent] = field(default_factory=list)


class RecoveryWatcher:
    """Long-running recovery loop coordinator."""

    def __init__(
        self,
        targets: list[ServiceTarget],
        config: WatcherConfig,
        *,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        """Construct a watcher.

        Args:
            targets: The services to watch.
            config: Tuning knobs.
            logger: Bound structured logger.
        """
        self._targets = targets
        self._config = config
        self._logger = logger
        self._runtimes: dict[str, _ServiceRuntime] = {
            t.name: _ServiceRuntime(target=t) for t in targets
        }
        self._stop = asyncio.Event()
        self._recovery_count: dict[str, int] = defaultdict(int)

    def request_stop(self) -> None:
        """Signal the watcher loop to exit at the next poll boundary."""
        self._stop.set()

    @property
    def history(self) -> list[RecoveryEvent]:
        """Return every recovery event observed so far, in time order."""
        all_events: list[RecoveryEvent] = []
        for rt in self._runtimes.values():
            all_events.extend(rt.history)
        return sorted(all_events, key=lambda e: e.detected_at)

    async def run(self) -> None:
        """Poll services in a loop until :meth:`request_stop` is invoked."""
        self._logger.info(
            "recovery_watcher_started",
            targets=[t.name for t in self._targets],
            poll_interval_seconds=self._config.poll_interval_seconds,
            failure_threshold=self._config.failure_threshold,
        )
        async with httpx.AsyncClient() as client:
            while not self._stop.is_set():
                await self._tick(client)
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._config.poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    continue
        self._logger.info("recovery_watcher_stopped")

    async def _tick(self, client: httpx.AsyncClient) -> None:
        """Run one poll cycle across every target."""
        results = await probe_all(self._targets, client=client)
        for result in results:
            runtime = self._runtimes[result.target.name]
            if result.ok:
                if runtime.consecutive_failures > 0:
                    self._logger.info(
                        "service_recovered_passively",
                        target=result.target.name,
                        previous_failures=runtime.consecutive_failures,
                    )
                runtime.consecutive_failures = 0
                continue
            runtime.consecutive_failures += 1
            self._logger.warning(
                "health_check_failed",
                target=result.target.name,
                consecutive_failures=runtime.consecutive_failures,
                detail=result.detail,
            )
            if runtime.consecutive_failures >= self._config.failure_threshold:
                await self._recover(runtime)

    async def _recover(self, runtime: _ServiceRuntime) -> None:
        """Attempt to restart a downed container and record the event."""
        target = runtime.target
        detected = datetime.now(tz=timezone.utc)
        start = time.perf_counter()
        ok = await self._docker_start(target.container)
        duration = time.perf_counter() - start
        recovered = datetime.now(tz=timezone.utc) if ok else None
        event = RecoveryEvent(
            service=target.name,
            detected_at=detected,
            recovered_at=recovered,
            duration_seconds=round(duration, 3),
            action="docker_start",
            success=ok,
        )
        runtime.history.append(event)
        runtime.consecutive_failures = 0
        self._recovery_count[target.name] += 1
        self._logger.info(
            "recovery_event",
            target=target.name,
            success=ok,
            duration_seconds=event.duration_seconds,
            attempt=self._recovery_count[target.name],
        )
        await asyncio.sleep(self._config.recovery_backoff_seconds)

    async def _docker_start(self, container: str) -> bool:
        """Run ``docker start <container>`` and return True on success.

        Args:
            container: Docker container name.

        Returns:
            ``True`` if the docker command exits 0.
        """
        proc = await asyncio.create_subprocess_exec(
            self._config.docker_bin,
            "start",
            container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True
        self._logger.error(
            "docker_start_failed",
            container=container,
            returncode=proc.returncode,
            stdout=stdout.decode(errors="replace").strip(),
            stderr=stderr.decode(errors="replace").strip(),
        )
        return False


async def _amain() -> int:
    """Run the recovery loop until SIGINT/SIGTERM.

    Returns:
        Process exit code.
    """
    logger = configure_logging("recovery")
    config = WatcherConfig.from_env()
    targets = default_targets()
    watcher = RecoveryWatcher(targets, config, logger=logger)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, watcher.request_stop)
        except NotImplementedError:
            pass

    await watcher.run()
    return 0


def main() -> int:
    """Synchronous wrapper for ``python -m orchestration.recovery``."""
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
