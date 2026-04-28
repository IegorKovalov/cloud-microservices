"""Top-level orchestrator: bring the stack up, watch it, tear it down.

Usage:

    python -m orchestration.orchestrator              # full lifecycle
    python -m orchestration.orchestrator --no-up      # assume stack is up
    python -m orchestration.orchestrator --no-down    # leave stack up on exit

The orchestrator delegates the detection-and-restart logic to
:class:`orchestration.recovery.RecoveryWatcher`. Its only extra
responsibility is wiring docker-compose lifecycle around the watcher.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import signal
import sys
from dataclasses import dataclass

import httpx
import structlog

from orchestration import default_targets
from orchestration.health_check import probe_all
from orchestration.recovery import RecoveryWatcher, WatcherConfig
from shared.logging_config import configure_logging


@dataclass(frozen=True)
class OrchestratorOptions:
    """Parsed CLI flags."""

    bring_up: bool
    tear_down: bool


def _parse_args(argv: list[str] | None = None) -> OrchestratorOptions:
    """Parse CLI arguments into an :class:`OrchestratorOptions`.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Parsed options.
    """
    parser = argparse.ArgumentParser(prog="orchestrator")
    parser.add_argument(
        "--no-up", action="store_true", help="don't run `docker compose up`"
    )
    parser.add_argument(
        "--no-down", action="store_true", help="don't run `docker compose down` on exit"
    )
    args = parser.parse_args(argv)
    return OrchestratorOptions(
        bring_up=not args.no_up,
        tear_down=not args.no_down,
    )


async def _docker_compose(
    args: list[str], *, logger: structlog.stdlib.BoundLogger
) -> int:
    """Run ``docker compose <args...>`` and stream its output to logs.

    Args:
        args: Compose subcommand arguments, e.g. ``["up", "-d", "--build"]``.
        logger: Structured logger.

    Returns:
        Process exit code.
    """
    binary = shutil.which("docker") or "docker"
    proc = await asyncio.create_subprocess_exec(
        binary,
        "compose",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if line:
            logger.info("docker_compose", op=" ".join(args), line=line)
    return await proc.wait()


async def _wait_until_healthy(
    *, logger: structlog.stdlib.BoundLogger, attempts: int = 30, delay: float = 2.0
) -> bool:
    """Block until every service replies 200 on ``/health`` or ``attempts`` is hit.

    Args:
        logger: Structured logger.
        attempts: Maximum poll cycles.
        delay: Seconds between polls.

    Returns:
        ``True`` if all services became healthy, ``False`` otherwise.
    """
    targets = default_targets()
    async with httpx.AsyncClient() as client:
        for attempt in range(1, attempts + 1):
            results = await probe_all(targets, client=client)
            failed = [r for r in results if not r.ok]
            if not failed:
                logger.info("stack_healthy", attempt=attempt)
                return True
            logger.info(
                "stack_unhealthy_waiting",
                attempt=attempt,
                failed=[r.target.name for r in failed],
            )
            await asyncio.sleep(delay)
    logger.error("stack_failed_to_become_healthy", attempts=attempts)
    return False


async def _amain(opts: OrchestratorOptions) -> int:
    """Async entry point: lifecycle wrapper around the recovery watcher.

    Args:
        opts: Parsed CLI options.

    Returns:
        Process exit code.
    """
    logger = configure_logging("orchestrator")
    if opts.bring_up:
        logger.info("docker_compose_up")
        rc = await _docker_compose(["up", "-d", "--build"], logger=logger)
        if rc != 0:
            logger.error("docker_compose_up_failed", returncode=rc)
            return rc

    healthy = await _wait_until_healthy(logger=logger)
    if not healthy and opts.tear_down:
        await _docker_compose(["down"], logger=logger)
        return 1

    config = WatcherConfig.from_env()
    watcher = RecoveryWatcher(default_targets(), config, logger=logger)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, watcher.request_stop)
        except NotImplementedError:
            pass

    try:
        await watcher.run()
    finally:
        if opts.tear_down:
            logger.info("docker_compose_down")
            await _docker_compose(["down"], logger=logger)

    return 0


def main(argv: list[str] | None = None) -> int:
    """Synchronous entry point for ``python -m orchestration.orchestrator``.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Process exit code.
    """
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
