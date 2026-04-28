"""Reusable async health-check primitives shared by the orchestrator.

Importable as a library (used by ``recovery.py`` and the integration
tests) and runnable directly via ``python -m orchestration.health_check``
which prints a one-shot summary to stdout.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import httpx
import structlog

from orchestration import ServiceTarget, default_targets
from shared.logging_config import configure_logging


@dataclass(frozen=True)
class HealthResult:
    """Outcome of a single health probe."""

    target: ServiceTarget
    ok: bool
    detail: str


async def probe_one(
    target: ServiceTarget,
    *,
    client: httpx.AsyncClient,
    timeout_seconds: float = 2.0,
) -> HealthResult:
    """Probe ``target.health_url`` once and return a :class:`HealthResult`.

    Args:
        target: Service to probe.
        client: Shared async HTTP client.
        timeout_seconds: Per-request timeout.

    Returns:
        A :class:`HealthResult` describing the outcome.
    """
    try:
        response = await client.get(target.health_url, timeout=timeout_seconds)
        response.raise_for_status()
        return HealthResult(target=target, ok=True, detail="ok")
    except httpx.HTTPError as exc:
        return HealthResult(target=target, ok=False, detail=str(exc))


async def probe_all(
    targets: list[ServiceTarget],
    *,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float = 2.0,
) -> list[HealthResult]:
    """Probe every target concurrently and return their results.

    Args:
        targets: List of services to probe.
        client: Optional shared client. If omitted, one is created and
            closed for the duration of this call.
        timeout_seconds: Per-request timeout.

    Returns:
        A list of :class:`HealthResult`, one per target.
    """
    if client is not None:
        return await asyncio.gather(
            *[
                probe_one(t, client=client, timeout_seconds=timeout_seconds)
                for t in targets
            ]
        )

    async with httpx.AsyncClient() as new_client:
        return await asyncio.gather(
            *[
                probe_one(t, client=new_client, timeout_seconds=timeout_seconds)
                for t in targets
            ]
        )


async def _amain() -> int:
    """CLI entry point: probe every default target once and print results.

    Returns:
        Exit code: 0 if all healthy, 1 otherwise.
    """
    logger = configure_logging("health-check")
    targets = default_targets()
    results = await probe_all(targets)
    all_ok = True
    for r in results:
        all_ok = all_ok and r.ok
        logger.info(
            "probe_result",
            target=r.target.name,
            url=r.target.health_url,
            ok=r.ok,
            detail=r.detail,
        )
    return 0 if all_ok else 1


def main() -> int:
    """Synchronous wrapper used by ``python -m orchestration.health_check``.

    Returns:
        Process exit code.
    """
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
