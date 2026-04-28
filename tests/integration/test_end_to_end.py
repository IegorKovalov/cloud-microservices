"""End-to-end smoke tests against the running stack.

Skipped automatically when the api-gateway is not reachable, so a
plain ``pytest tests/`` run on a freshly cloned repo doesn't fail.
"""

from __future__ import annotations

import os

import httpx
import pytest

API_GATEWAY_PORT = int(os.getenv("API_GATEWAY_PORT", "8000"))
SERVICE_B_PORT = int(os.getenv("SERVICE_B_PORT", "8002"))
CPP_WORKER_PORT = int(os.getenv("CPP_WORKER_PORT", "8003"))
FAULT_INJECTOR_PORT = int(os.getenv("FAULT_INJECTOR_PORT", "8004"))

GATEWAY = f"http://127.0.0.1:{API_GATEWAY_PORT}"
SERVICE_B = f"http://127.0.0.1:{SERVICE_B_PORT}"
CPP_WORKER = f"http://127.0.0.1:{CPP_WORKER_PORT}"
FAULT = f"http://127.0.0.1:{FAULT_INJECTOR_PORT}"


def _stack_is_up() -> bool:
    try:
        with httpx.Client(timeout=1.0) as c:
            return c.get(f"{GATEWAY}/health").status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _stack_is_up(),
        reason="docker-compose stack is not running; run `make up` first",
    ),
]


async def test_gateway_health_ok() -> None:
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=5.0) as c:
        r = await c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "api-gateway"


async def test_system_health_aggregates() -> None:
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=5.0) as c:
        r = await c.get("/system/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["services"]) >= 3


async def test_process_pipeline_sum() -> None:
    payload = {"items": [1.0, 2.0, 3.0, 4.0], "operation": "sum"}
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=10.0) as c:
        r = await c.post("/process", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["items_processed"] == 4
    assert body["result"] == pytest.approx(1 + 4 + 9 + 16)


async def test_process_via_queue_and_threadpool() -> None:
    payload = {"items": [2.0, 3.0], "operation": "sum"}
    async with httpx.AsyncClient(base_url=GATEWAY, timeout=10.0) as c:
        r1 = await c.post("/process/queue", json=payload)
        r2 = await c.post("/process/threadpool", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["result"] == pytest.approx(13.0)
    assert r2.json()["result"] == pytest.approx(13.0)


async def test_cpp_worker_square_sum_directly() -> None:
    async with httpx.AsyncClient(base_url=CPP_WORKER, timeout=5.0) as c:
        r = await c.post("/square_sum", json={"items": [1.0, 2.0, 3.0]})
    assert r.status_code == 200
    body = r.json()
    assert body["items_processed"] == 3
    assert body["result"] == pytest.approx(14.0)


async def test_service_b_store_round_trip() -> None:
    async with httpx.AsyncClient(base_url=SERVICE_B, timeout=5.0) as c:
        await c.post("/store", json={"key": "k1", "value": 42.0})
        r = await c.get("/store/k1")
    assert r.status_code == 200
    assert r.json() == {"key": "k1", "value": 42.0}


async def test_fault_injector_latency_endpoint() -> None:
    async with httpx.AsyncClient(base_url=FAULT, timeout=5.0) as c:
        await c.post(
            "/inject/latency",
            json={"target": "self", "duration_ms": 100, "error_rate": 0.0},
        )
        r = await c.get("/slow")
    assert r.status_code == 200
    assert r.json()["slept_ms"] == 100
