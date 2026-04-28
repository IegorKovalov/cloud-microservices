"""FastAPI entry point for the fault-injector service.

Endpoints:

* ``POST /inject/kill``     -> ``docker stop`` a target container.
* ``POST /inject/restart``  -> ``docker start`` a target container.
* ``POST /inject/latency``  -> set the latency for ``GET /slow``.
* ``POST /inject/error``    -> set the error rate for ``GET /broken``.
* ``GET  /faults``          -> snapshot of the active chaos state.
* ``GET  /slow``            -> sleeps ``latency_ms`` then returns 200.
* ``GET  /broken``          -> returns 500 with probability ``error_rate``.
* ``GET  /target/{name}``   -> docker container status of ``name``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from shared.fastapi_app import create_service_app
from shared.models import FaultKind, FaultRequest, FaultResponse

from app.injector import ContainerController, FaultState

SERVICE_NAME = os.getenv("SERVICE_NAME", "fault-injector")

_ALLOWED_TARGETS = {
    "cm-api-gateway",
    "cm-service-a",
    "cm-service-b",
    "cm-cpp-worker",
}

app, metrics = create_service_app(SERVICE_NAME)
_state = FaultState()
_controller = ContainerController(allow_targets=_ALLOWED_TARGETS)


def _normalise_target(target: str) -> str:
    """Allow callers to pass the bare service name and add the ``cm-`` prefix.

    Args:
        target: Either ``service-a`` or ``cm-service-a``.

    Returns:
        The fully-qualified container name as docker sees it.
    """
    if target.startswith("cm-"):
        return target
    return f"cm-{target}"


@app.post("/inject/kill", response_model=FaultResponse, tags=["chaos"])
async def inject_kill(req: FaultRequest) -> FaultResponse:
    """Stop a target container via the docker daemon.

    Args:
        req: ``FaultRequest`` with a ``target`` container name.

    Returns:
        :class:`FaultResponse` capturing whether the action succeeded.

    Raises:
        HTTPException: 403 if ``target`` is not in the allow-list,
            500 on docker daemon errors.
    """
    target = _normalise_target(req.target)
    try:
        result = await _controller.stop(target)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return FaultResponse(
        kind=FaultKind.KILL,
        target=target,
        accepted=bool(result["ok"]),
        detail=str(result["detail"]),
    )


@app.post("/inject/restart", response_model=FaultResponse, tags=["chaos"])
async def inject_restart(req: FaultRequest) -> FaultResponse:
    """Start a previously stopped container via the docker daemon.

    Args:
        req: ``FaultRequest`` with a ``target`` container name.

    Returns:
        :class:`FaultResponse`.

    Raises:
        HTTPException: 403 if ``target`` is not in the allow-list.
    """
    target = _normalise_target(req.target)
    try:
        result = await _controller.start(target)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return FaultResponse(
        kind=FaultKind.KILL,
        target=target,
        accepted=bool(result["ok"]),
        detail=str(result["detail"]),
    )


@app.post("/inject/latency", response_model=FaultResponse, tags=["chaos"])
async def inject_latency(req: FaultRequest) -> FaultResponse:
    """Configure the latency applied by ``GET /slow``.

    Args:
        req: ``FaultRequest`` whose ``duration_ms`` is the requested latency.

    Returns:
        :class:`FaultResponse`.
    """
    await _state.set_latency(req.duration_ms)
    return FaultResponse(
        kind=FaultKind.LATENCY,
        target=req.target,
        accepted=True,
        detail=f"latency_ms={req.duration_ms}",
    )


@app.post("/inject/error", response_model=FaultResponse, tags=["chaos"])
async def inject_error(req: FaultRequest) -> FaultResponse:
    """Configure the error rate of ``GET /broken``.

    Args:
        req: ``FaultRequest`` whose ``error_rate`` is the new probability.

    Returns:
        :class:`FaultResponse`.
    """
    await _state.set_error_rate(req.error_rate)
    return FaultResponse(
        kind=FaultKind.ERROR,
        target=req.target,
        accepted=True,
        detail=f"error_rate={req.error_rate}",
    )


@app.get("/faults", tags=["chaos"])
async def list_faults() -> dict[str, Any]:
    """Return a snapshot of the chaos state.

    Returns:
        ``{"latency_ms": ..., "error_rate": ...}``.
    """
    return await _state.snapshot()


@app.get("/slow", tags=["chaos"])
async def slow() -> dict[str, Any]:
    """Sleep ``latency_ms`` then return ``{"slept_ms": ...}``.

    Returns:
        Confirmation of the sleep duration applied.
    """
    snap = await _state.snapshot()
    duration_ms = int(snap["latency_ms"])
    if duration_ms > 0:
        await asyncio.sleep(duration_ms / 1000.0)
    return {"slept_ms": duration_ms}


@app.get("/broken", tags=["chaos"])
async def broken() -> JSONResponse:
    """Return 500 with probability ``error_rate``, else 200.

    Returns:
        A 200 ``{"status": "ok"}`` or a 500 ``{"detail": "deliberate failure"}``.
    """
    if await _state.maybe_fail():
        return JSONResponse({"detail": "deliberate failure"}, status_code=500)
    return JSONResponse({"status": "ok"}, status_code=200)


@app.get("/target/{name}", tags=["chaos"])
async def target_status(name: str) -> dict[str, Any]:
    """Report the docker status of ``name``.

    Args:
        name: Bare service name or fully-qualified container name.

    Returns:
        ``{"target": ..., "status": ...}``.
    """
    return await _controller.status(_normalise_target(name))
