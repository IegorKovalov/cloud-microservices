"""FastAPI entry point for service-b.

service-b is the canonical "downstream" worker in this project. It owns
an async-safe in-memory key/value store, plus a small ``/squared``
endpoint used by service-a to demonstrate parallel HTTP fan-out via
``asyncio.gather``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import HTTPException

from shared.fastapi_app import create_service_app
from shared.models import StoreRecord

from app.storage import KeyValueStore

SERVICE_NAME = os.getenv("SERVICE_NAME", "service-b")

app, metrics = create_service_app(SERVICE_NAME)
_store = KeyValueStore()


@app.post("/store", tags=["store"], status_code=201)
async def put_record(record: StoreRecord) -> dict[str, Any]:
    """Persist ``record`` in the in-memory store.

    Args:
        record: Validated ``StoreRecord`` body.

    Returns:
        A dict echoing the record and the resulting store size.
    """
    await _store.put(record.key, record.value)
    return {"key": record.key, "value": record.value, "size": await _store.size()}


@app.get("/store/{key}", tags=["store"])
async def get_record(key: str) -> dict[str, Any]:
    """Return the value previously stored under ``key``.

    Args:
        key: Identifier to look up.

    Returns:
        ``{"key": ..., "value": ...}`` on success.

    Raises:
        HTTPException: 404 when ``key`` is not present in the store.
    """
    value = await _store.get(key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"key {key!r} not found")
    return {"key": key, "value": value}


@app.delete("/store/{key}", tags=["store"])
async def delete_record(key: str) -> dict[str, Any]:
    """Remove ``key`` from the store, if present.

    Args:
        key: Identifier to remove.

    Returns:
        ``{"key": ..., "deleted": <bool>}``.
    """
    deleted = await _store.delete(key)
    return {"key": key, "deleted": deleted}


@app.get("/store", tags=["store"])
async def list_records() -> dict[str, Any]:
    """Snapshot every stored entry.

    Returns:
        ``{"size": <int>, "records": {<key>: <value>, ...}}``.
    """
    snapshot = await _store.all()
    return {"size": len(snapshot), "records": snapshot}


@app.post("/squared", tags=["compute"])
async def squared(payload: StoreRecord) -> dict[str, float | str]:
    """Square the incoming value and return it.

    A tiny CPU-light endpoint exercised by service-a's parallel fan-out.
    Includes a small await to simulate I/O latency.

    Args:
        payload: ``StoreRecord`` whose ``value`` field will be squared.

    Returns:
        ``{"key": ..., "input": ..., "result": ...}``.
    """
    await asyncio.sleep(0.01)
    return {"key": payload.key, "input": payload.value, "result": payload.value ** 2}
