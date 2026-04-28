"""Aggregate structured logs from every service into one stream.

Tails ``docker compose logs --follow`` for the project, parses each
line as JSON when possible, and re-emits a normalised record. Lines
that aren't valid JSON (e.g. uvicorn's startup banner) are still
preserved with a ``raw`` field so nothing gets silently dropped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import signal
import sys
from dataclasses import dataclass

import structlog

from shared.logging_config import configure_logging


@dataclass(frozen=True)
class AggregatorOptions:
    """Parsed CLI options for :func:`main`."""

    project: str
    follow: bool
    tail: int


def _parse_args(argv: list[str] | None = None) -> AggregatorOptions:
    """Parse CLI arguments.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Parsed options.
    """
    parser = argparse.ArgumentParser(prog="log_aggregator")
    parser.add_argument(
        "--project", default="cloud-microservices", help="docker compose project name"
    )
    parser.add_argument("--no-follow", action="store_true")
    parser.add_argument("--tail", type=int, default=200)
    args = parser.parse_args(argv)
    return AggregatorOptions(
        project=args.project,
        follow=not args.no_follow,
        tail=args.tail,
    )


def _parse_compose_line(raw: str) -> tuple[str, str]:
    """Split a docker-compose log line into ``(container, payload)``.

    docker-compose v2 prefixes lines with ``container-name  | payload``.
    If the prefix is missing we return ``("", raw)``.

    Args:
        raw: A single line from ``docker compose logs``.

    Returns:
        ``(container, payload)`` tuple.
    """
    if "|" not in raw:
        return "", raw
    head, _, tail = raw.partition("|")
    return head.strip(), tail.strip()


def _emit(
    logger: structlog.stdlib.BoundLogger, container: str, payload: str
) -> None:
    """Emit one normalised aggregated log record.

    Args:
        logger: Bound structured logger.
        container: Source container name.
        payload: Raw log payload.

    Returns:
        None.
    """
    if not payload:
        return
    try:
        record = json.loads(payload)
    except json.JSONDecodeError:
        logger.info("raw_log", container=container, raw=payload)
        return
    if not isinstance(record, dict):
        logger.info("raw_log", container=container, raw=payload)
        return
    logger.info(
        "service_log",
        container=container,
        upstream_service=record.get("service"),
        upstream_level=record.get("level"),
        upstream_message=record.get("message"),
        upstream_timestamp=record.get("timestamp"),
        extra={
            k: v
            for k, v in record.items()
            if k not in {"service", "level", "message", "timestamp"}
        },
    )


async def _amain(opts: AggregatorOptions) -> int:
    """Spawn ``docker compose logs`` and re-emit each line.

    Args:
        opts: Parsed CLI options.

    Returns:
        Exit code from the underlying compose process.
    """
    logger = configure_logging("log-aggregator")
    binary = shutil.which("docker") or "docker"
    args = [
        "compose",
        "-p",
        opts.project,
        "logs",
        f"--tail={opts.tail}",
    ]
    if opts.follow:
        args.append("--follow")

    logger.info("aggregator_started", argv=[binary, *args])
    proc = await asyncio.create_subprocess_exec(
        binary,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        async for raw in proc.stdout:
            if stop_event.is_set():
                break
            line = raw.decode(errors="replace").rstrip()
            container, payload = _parse_compose_line(line)
            _emit(logger, container, payload)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
    return proc.returncode or 0


def main(argv: list[str] | None = None) -> int:
    """Synchronous wrapper for ``python -m monitoring.log_aggregator``."""
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
