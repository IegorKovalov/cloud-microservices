"""FastAPI entry point for the api-gateway service.

Endpoints:

* ``POST /process`` — proxy to service-a's main pipeline.
* ``POST /process/{flavour}`` — proxy to ``/process/queue`` or
  ``/process/threadpool`` on service-a.
* ``GET /system/health`` — fan-out health check across the whole fleet.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import HTTPException

from shared.fastapi_app import create_service_app
from shared.models import ProcessRequest, ProcessResponse

from app.config import load_settings

_SETTINGS = load_settings()
_LOGGER = structlog.get_logger()


@asynccontextmanager
async def _lifespan(app):  # type: ignore[no-untyped-def]
    """Manage the gateway's shared async HTTP client."""
    async with httpx.AsyncClient(
        timeout=_SETTINGS.request_timeout_seconds
    ) as client:
        app.state.http = client
        yield


app, metrics = create_service_app(_SETTINGS.service_name)
app.router.lifespan_context = _lifespan


_FLAVOUR_PATHS: dict[str, str] = {
    "default": "/process",
    "queue": "/process/queue",
    "threadpool": "/process/threadpool",
}


async def _proxy_to_service_a(req: ProcessRequest, path: str) -> ProcessResponse:
    """Forward ``req`` to service-a at ``path`` and unwrap the response.

    Args:
        req: Validated request body.
        path: The service-a route to hit, e.g. ``/process``.

    Returns:
        A populated :class:`ProcessResponse`.

    Raises:
        HTTPException: 502 for any HTTP-layer failure talking to service-a.
    """
    client: httpx.AsyncClient = app.state.http
    url = f"{_SETTINGS.service_a_url}{path}"
    try:
        response = await client.post(url, json=req.model_dump())
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _LOGGER.error(
            "service_a_returned_error",
            url=url,
            status=exc.response.status_code,
            body=exc.response.text[:200],
        )
        raise HTTPException(status_code=502, detail="service-a error") from exc
    except httpx.HTTPError as exc:
        _LOGGER.error("service_a_unreachable", url=url, error=str(exc))
        raise HTTPException(
            status_code=502, detail="service-a unreachable"
        ) from exc

    return ProcessResponse.model_validate(response.json())


@app.post("/process", response_model=ProcessResponse, tags=["compute"])
async def process(req: ProcessRequest) -> ProcessResponse:
    """Forward to service-a's main async pipeline.

    Args:
        req: Validated process request.

    Returns:
        The aggregated :class:`ProcessResponse`.
    """
    return await _proxy_to_service_a(req, _FLAVOUR_PATHS["default"])


@app.post("/process/{flavour}", response_model=ProcessResponse, tags=["compute"])
async def process_flavour(flavour: str, req: ProcessRequest) -> ProcessResponse:
    """Forward to service-a using a specific concurrency flavour.

    Args:
        flavour: One of ``queue`` or ``threadpool``.
        req: Validated process request.

    Returns:
        The aggregated :class:`ProcessResponse`.

    Raises:
        HTTPException: 400 if ``flavour`` is not supported.
    """
    if flavour not in _FLAVOUR_PATHS or flavour == "default":
        raise HTTPException(
            status_code=400, detail=f"unknown flavour {flavour!r}"
        )
    return await _proxy_to_service_a(req, _FLAVOUR_PATHS[flavour])


async def _probe(url: str, client: httpx.AsyncClient) -> dict[str, Any]:
    """Probe a single ``/health`` endpoint and report the result.

    Args:
        url: Fully-qualified URL of a service's ``/health`` endpoint.
        client: Shared async HTTP client.

    Returns:
        A dict ``{"url": ..., "ok": <bool>, "detail": ...}``.
    """
    try:
        resp = await client.get(url, timeout=2.0)
        resp.raise_for_status()
        return {"url": url, "ok": True, "detail": resp.json()}
    except httpx.HTTPError as exc:
        return {"url": url, "ok": False, "detail": str(exc)}


@app.get("/system/health", tags=["observability"])
async def system_health() -> dict[str, Any]:
    """Aggregate ``/health`` from every downstream service.

    Returns:
        ``{"ok": <bool>, "services": [{"url", "ok", "detail"}, ...]}``.
    """
    client: httpx.AsyncClient = app.state.http
    targets = [
        f"{_SETTINGS.service_a_url}/health",
        f"{_SETTINGS.service_b_url}/health",
        f"{_SETTINGS.cpp_worker_url}/health",
    ]
    results = await asyncio.gather(*[_probe(t, client) for t in targets])
    overall_ok = all(r["ok"] for r in results)
    return {"ok": overall_ok, "services": results}
