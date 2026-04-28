"""Concurrency primitives used by service-a.

This module implements three orthogonal concurrency patterns required
by the project rules:

* :func:`fan_out_to_service_b` — ``asyncio.gather`` parallel HTTP fan-out
  to service-b. Each downstream call goes through a bounded
  ``asyncio.Semaphore`` so the worker doesn't open unbounded sockets.
* :func:`queue_pipeline` — an ``asyncio.Queue`` task-queue with a fixed
  pool of producer/consumer coroutines, demonstrating a classic
  producer/consumer pattern.
* :func:`threadpool_pipeline` — a ``concurrent.futures.ThreadPoolExecutor``
  fan-out for blocking I/O-style work.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import structlog

_LOGGER = structlog.get_logger()


async def fan_out_to_service_b(
    items: list[float],
    *,
    service_b_url: str,
    concurrency: int,
    client: httpx.AsyncClient,
) -> list[float]:
    """Square each item by calling service-b in parallel.

    Uses ``asyncio.gather`` plus an ``asyncio.Semaphore`` so we get
    parallelism without blowing up the connection pool.

    Args:
        items: The list of floats to process.
        service_b_url: Base URL of service-b, e.g. ``http://service-b:8002``.
        concurrency: Maximum number of concurrent in-flight requests.
        client: A shared ``httpx.AsyncClient`` (re-uses connections).

    Returns:
        A list of squared values, in the same order as ``items``.

    Raises:
        httpx.HTTPError: Propagated if any downstream call fails after
            its individual retry budget is exhausted (handled by the
            caller, which decides whether to fail fast).
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _one(idx: int, value: float) -> float:
        async with semaphore:
            response = await client.post(
                f"{service_b_url}/squared",
                json={"key": f"item-{idx}", "value": value},
                timeout=5.0,
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return float(data["result"])

    tasks = [asyncio.create_task(_one(i, v)) for i, v in enumerate(items)]
    return await asyncio.gather(*tasks)


async def queue_pipeline(
    items: list[float],
    *,
    workers: int,
    work_fn: "callable[[float], asyncio.Future[float]] | Any",  # type: ignore[name-defined]
) -> list[float]:
    """Process ``items`` through a fixed pool of ``asyncio.Queue`` workers.

    Each worker pulls from a single queue and applies ``work_fn`` to
    each item. Results are collected in input order.

    Args:
        items: The list of floats to process.
        workers: Number of concurrent worker coroutines.
        work_fn: Async callable that takes a float and returns a float.

    Returns:
        The processed values, in the same order as ``items``.

    Raises:
        Exception: Propagated from ``work_fn`` after all queued items
            have either been processed or marked done.
    """
    queue: asyncio.Queue[tuple[int, float]] = asyncio.Queue()
    results: list[float | None] = [None] * len(items)
    failures: list[BaseException] = []

    async def _consumer(worker_id: int) -> None:
        while True:
            try:
                idx, value = await queue.get()
            except asyncio.CancelledError:
                return
            try:
                results[idx] = await work_fn(value)
            except Exception as exc:
                failures.append(exc)
                _LOGGER.error(
                    "queue_worker_failed",
                    worker_id=worker_id,
                    idx=idx,
                    error=str(exc),
                )
            finally:
                queue.task_done()

    consumers = [
        asyncio.create_task(_consumer(i)) for i in range(max(1, workers))
    ]
    for idx, value in enumerate(items):
        queue.put_nowait((idx, value))

    await queue.join()
    for c in consumers:
        c.cancel()
    await asyncio.gather(*consumers, return_exceptions=True)

    if failures:
        raise failures[0]
    return [r if r is not None else 0.0 for r in results]


def threadpool_pipeline(
    items: list[float],
    *,
    workers: int,
    work_fn: Any,
) -> list[float]:
    """Apply ``work_fn`` to each item via a ``ThreadPoolExecutor``.

    This is intentionally a *sync* function that blocks the calling
    thread; service-a wraps it in ``asyncio.to_thread`` so that the
    event loop stays responsive.

    Args:
        items: Floats to process.
        workers: Thread pool size.
        work_fn: Sync callable taking a float and returning a float.

    Returns:
        Processed values in the original order.
    """
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        return list(executor.map(work_fn, items))


async def call_cpp_worker_square_sum(
    items: list[float],
    *,
    cpp_worker_url: str,
    client: httpx.AsyncClient,
) -> float:
    """Delegate the squared-sum to the C++ worker over HTTP.

    Args:
        items: The list of floats to send.
        cpp_worker_url: Base URL of cpp-worker.
        client: Shared async HTTP client.

    Returns:
        The C++ worker's reported sum-of-squares.

    Raises:
        httpx.HTTPError: Propagated if the C++ worker is unreachable.
    """
    start = time.perf_counter()
    response = await client.post(
        f"{cpp_worker_url}/square_sum",
        json={"items": items},
        timeout=5.0,
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    _LOGGER.info(
        "cpp_worker_call",
        items=len(items),
        result=data.get("result"),
        duration_ms=round(elapsed_ms, 2),
    )
    return float(data["result"])
