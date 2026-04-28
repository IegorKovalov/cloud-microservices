"""Periodically scrape ``/metrics`` from every service to a JSON file.

The output schema is::

    {
      "scraped_at": "2024-01-01T00:00:00.000Z",
      "services": {
         "<service-name>": { ...ServiceMetrics... },
         ...
      }
    }

Old snapshots are *replaced* on each tick so the file is always small
and trivially diff-able. A history file at ``<output>.history.jsonl``
is appended-to for time-series consumption.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import structlog

from orchestration import ServiceTarget, default_targets
from shared.logging_config import configure_logging
from shared.utils import env_float, env_str


@dataclass(frozen=True)
class CollectorOptions:
    """Parsed CLI options for :func:`main`."""

    output_path: Path
    history_path: Path
    interval_seconds: float
    one_shot: bool


def _parse_args(argv: list[str] | None = None) -> CollectorOptions:
    """Parse CLI arguments.

    Args:
        argv: Optional argv override (for tests).

    Returns:
        Parsed options.
    """
    default_out = env_str("METRICS_OUTPUT_PATH", "./metrics/metrics.json")
    default_interval = env_float("METRICS_SCRAPE_INTERVAL_SECONDS", 10.0)
    parser = argparse.ArgumentParser(prog="metrics_collector")
    parser.add_argument("--output", default=default_out)
    parser.add_argument("--interval", type=float, default=default_interval)
    parser.add_argument("--once", action="store_true", help="single scrape and exit")
    args = parser.parse_args(argv)
    out = Path(args.output)
    return CollectorOptions(
        output_path=out,
        history_path=out.with_suffix(out.suffix + ".history.jsonl"),
        interval_seconds=args.interval,
        one_shot=args.once,
    )


async def _scrape_one(
    target: ServiceTarget, *, client: httpx.AsyncClient
) -> tuple[str, dict[str, Any]]:
    """Scrape ``/metrics`` from a single service.

    Args:
        target: Service to scrape.
        client: Shared async HTTP client.

    Returns:
        Tuple ``(service_name, payload)``. On error the payload contains
        an ``error`` key describing the failure.
    """
    metrics_url = target.health_url.replace("/health", "/metrics")
    try:
        resp = await client.get(metrics_url, timeout=2.0)
        resp.raise_for_status()
        return target.name, resp.json()
    except httpx.HTTPError as exc:
        return target.name, {"service": target.name, "error": str(exc)}


async def _scrape_all(
    targets: list[ServiceTarget], *, client: httpx.AsyncClient
) -> dict[str, dict[str, Any]]:
    """Scrape every service concurrently.

    Args:
        targets: Services to scrape.
        client: Shared async HTTP client.

    Returns:
        ``{service_name: payload}`` mapping.
    """
    pairs = await asyncio.gather(
        *[_scrape_one(t, client=client) for t in targets]
    )
    return {name: payload for name, payload in pairs}


def _write_outputs(
    snapshot: dict[str, Any],
    *,
    output_path: Path,
    history_path: Path,
) -> None:
    """Persist ``snapshot`` to disk (current + appended history).

    Args:
        snapshot: The full document including timestamp + per-service data.
        output_path: Path of the rolling current-state file.
        history_path: Path of the append-only history file.

    Returns:
        None.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
    with history_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, sort_keys=True) + "\n")


def _now_iso() -> str:
    """Return the current UTC time as ISO-8601.

    Returns:
        ISO-8601 formatted string ending in ``Z``.
    """
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


async def _amain(opts: CollectorOptions) -> int:
    """Run the scrape loop until SIGINT/SIGTERM (or once if ``--once``).

    Args:
        opts: Parsed CLI options.

    Returns:
        Exit code.
    """
    logger = configure_logging("metrics-collector")
    targets = default_targets()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            services = await _scrape_all(targets, client=client)
            snapshot = {"scraped_at": _now_iso(), "services": services}
            _write_outputs(
                snapshot,
                output_path=opts.output_path,
                history_path=opts.history_path,
            )
            logger.info(
                "metrics_snapshot_written",
                output=str(opts.output_path),
                services=list(services.keys()),
            )
            if opts.one_shot:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=opts.interval_seconds)
            except asyncio.TimeoutError:
                continue
    return 0


def main(argv: list[str] | None = None) -> int:
    """Synchronous wrapper for ``python -m monitoring.metrics_collector``."""
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
