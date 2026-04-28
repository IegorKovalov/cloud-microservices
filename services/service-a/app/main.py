"""FastAPI entry point for service-a (data-processing worker).

Endpoints:

* ``POST /process`` — main pipeline. Validates input, fans out to
  service-b in parallel, optionally calls cpp-worker, and aggregates.
* ``POST /process/queue`` — same input but driven through an
  ``asyncio.Queue`` worker pool.
* ``POST /process/threadpool`` — same input via a
  ``ThreadPoolExecutor`` to demonstrate the blocking-I/O variant.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import HTTPException

from shared.fastapi_app import create_service_app
from shared.models import ProcessRequest, ProcessResponse
from shared.utils import retry_async

from app.config import load_settings
from app.worker import (
    call_cpp_worker_square_sum,
    fan_out_to_service_b,
    queue_pipeline,
    threadpool_pipeline,
)

_SETTINGS = load_settings()
_LOGGER = structlog.get_logger()


@asynccontextmanager
async def _lifespan(app):  # type: ignore[no-untyped-def]
    """Manage the shared ``httpx.AsyncClient`` for the service lifetime."""
    async with httpx.AsyncClient(http2=False) as client:
        app.state.http = client
        yield


app, metrics = create_service_app(_SETTINGS.service_name)
app.router.lifespan_context = _lifespan


def _aggregate(operation: str, fanout: list[float]) -> float:
    """Combine fan-out per-item results into a single number.

    Args:
        operation: One of ``sum``, ``mean``, ``square_sum``.
        fanout: The per-item results (each one is the squared value).

    Returns:
        The aggregated scalar.

    Raises:
        ValueError: If ``operation`` is not recognised.
    """
    if not fanout:
        return 0.0
    if operation == "sum":
        return sum(fanout)
    if operation == "mean":
        return sum(fanout) / len(fanout)
    if operation == "square_sum":
        return sum(fanout)
    raise ValueError(f"unsupported operation: {operation!r}")


@app.post("/process", response_model=ProcessResponse, tags=["compute"])
async def process(req: ProcessRequest) -> ProcessResponse:
    """Run the main async fan-out pipeline.

    Args:
        req: ``ProcessRequest`` with the items + operation.

    Returns:
        A populated :class:`ProcessResponse`.

    Raises:
        HTTPException: 502 if a downstream service repeatedly fails.
    """
    start = time.perf_counter()
    client: httpx.AsyncClient = app.state.http

    async def _do_fanout() -> list[float]:
        return await fan_out_to_service_b(
            req.items,
            service_b_url=_SETTINGS.service_b_url,
            concurrency=_SETTINGS.fanout_concurrency,
            client=client,
        )

    try:
        fanout = await retry_async(
            _do_fanout,
            attempts=3,
            base_delay_seconds=0.2,
            exceptions=(httpx.HTTPError,),
            logger=_LOGGER,
            op="fan_out_to_service_b",
        )
    except httpx.HTTPError as exc:
        _LOGGER.error("fanout_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="service-b unreachable") from exc

    cpp_result: float | None = None
    if req.operation == "square_sum":
        try:
            cpp_result = await call_cpp_worker_square_sum(
                req.items, cpp_worker_url=_SETTINGS.cpp_worker_url, client=client
            )
        except httpx.HTTPError as exc:
            _LOGGER.warning("cpp_worker_unreachable", error=str(exc))

    try:
        result = _aggregate(req.operation, fanout)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    duration_ms = (time.perf_counter() - start) * 1000.0
    return ProcessResponse(
        operation=req.operation,
        result=result,
        items_processed=len(req.items),
        fanout_results=fanout,
        cpp_result=cpp_result,
        duration_ms=round(duration_ms, 2),
    )


@app.post("/process/queue", response_model=ProcessResponse, tags=["compute"])
async def process_via_queue(req: ProcessRequest) -> ProcessResponse:
    """Run the pipeline through an ``asyncio.Queue`` consumer pool.

    Args:
        req: ``ProcessRequest`` to process.

    Returns:
        A populated :class:`ProcessResponse`.
    """
    start = time.perf_counter()
    client: httpx.AsyncClient = app.state.http

    async def _square_one(value: float) -> float:
        response = await client.post(
            f"{_SETTINGS.service_b_url}/squared",
            json={"key": "queued", "value": value},
            timeout=5.0,
        )
        response.raise_for_status()
        return float(response.json()["result"])

    fanout = await queue_pipeline(
        req.items, workers=_SETTINGS.queue_workers, work_fn=_square_one
    )
    duration_ms = (time.perf_counter() - start) * 1000.0
    return ProcessResponse(
        operation=req.operation,
        result=_aggregate(req.operation, fanout),
        items_processed=len(req.items),
        fanout_results=fanout,
        duration_ms=round(duration_ms, 2),
    )


@app.post("/process/threadpool", response_model=ProcessResponse, tags=["compute"])
async def process_via_threadpool(req: ProcessRequest) -> ProcessResponse:
    """Demonstrate the blocking-I/O variant via ``ThreadPoolExecutor``.

    The worker function is intentionally synchronous and busy-waits a
    few microseconds per item to imitate blocking I/O.

    Args:
        req: ``ProcessRequest`` to process.

    Returns:
        A populated :class:`ProcessResponse`.
    """
    start = time.perf_counter()

    def _square_blocking(value: float) -> float:
        time.sleep(0.001)
        return value * value

    fanout: list[float] = await asyncio.to_thread(
        threadpool_pipeline,
        req.items,
        workers=_SETTINGS.threadpool_workers,
        work_fn=_square_blocking,
    )
    duration_ms = (time.perf_counter() - start) * 1000.0
    return ProcessResponse(
        operation=req.operation,
        result=_aggregate(req.operation, fanout),
        items_processed=len(req.items),
        fanout_results=fanout,
        duration_ms=round(duration_ms, 2),
    )


@app.get("/info", tags=["meta"])
async def info() -> dict[str, Any]:
    """Return the active configuration of this worker.

    Returns:
        A dict snapshot of the static :class:`Settings`.
    """
    return {
        "service_name": _SETTINGS.service_name,
        "service_b_url": _SETTINGS.service_b_url,
        "cpp_worker_url": _SETTINGS.cpp_worker_url,
        "fanout_concurrency": _SETTINGS.fanout_concurrency,
        "queue_workers": _SETTINGS.queue_workers,
        "threadpool_workers": _SETTINGS.threadpool_workers,
    }
