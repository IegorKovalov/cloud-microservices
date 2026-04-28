"""Unit tests for the orchestration.health_check module.

Uses ``respx`` to stub httpx so the tests run with no network.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from orchestration import ServiceTarget
from orchestration.health_check import probe_all, probe_one


def _make_target(name: str, port: int) -> ServiceTarget:
    return ServiceTarget(
        name=name,
        container=f"cm-{name}",
        health_url=f"http://127.0.0.1:{port}/health",
    )


@respx.mock
async def test_probe_one_success() -> None:
    target = _make_target("svc", 8080)
    respx.get(target.health_url).mock(
        return_value=httpx.Response(200, json={"status": "ok", "service": "svc"})
    )
    async with httpx.AsyncClient() as client:
        result = await probe_one(target, client=client)
    assert result.ok is True
    assert result.target.name == "svc"


@respx.mock
async def test_probe_one_failure_on_5xx() -> None:
    target = _make_target("svc", 8080)
    respx.get(target.health_url).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        result = await probe_one(target, client=client)
    assert result.ok is False


@respx.mock
async def test_probe_one_failure_on_connect_error() -> None:
    target = _make_target("svc", 8080)
    respx.get(target.health_url).mock(
        side_effect=httpx.ConnectError("refused")
    )
    async with httpx.AsyncClient() as client:
        result = await probe_one(target, client=client)
    assert result.ok is False
    assert "refused" in result.detail


@respx.mock
async def test_probe_all_fans_out() -> None:
    targets = [_make_target("a", 9000), _make_target("b", 9001)]
    respx.get(targets[0].health_url).mock(return_value=httpx.Response(200))
    respx.get(targets[1].health_url).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        results = await probe_all(targets, client=client)
    by_name = {r.target.name: r for r in results}
    assert by_name["a"].ok is True
    assert by_name["b"].ok is False
