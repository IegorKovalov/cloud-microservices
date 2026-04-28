"""Unit tests for monitoring.metrics_collector."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from monitoring.metrics_collector import _scrape_all, _write_outputs
from orchestration import ServiceTarget


def _make_target(name: str, port: int) -> ServiceTarget:
    return ServiceTarget(
        name=name,
        container=f"cm-{name}",
        health_url=f"http://127.0.0.1:{port}/health",
    )


@respx.mock
async def test_scrape_all_collects_payloads() -> None:
    targets = [_make_target("a", 8001), _make_target("b", 8002)]
    respx.get("http://127.0.0.1:8001/metrics").mock(
        return_value=httpx.Response(
            200,
            json={
                "service": "a",
                "request_count": 10,
                "error_count": 1,
                "avg_latency_ms": 12.5,
                "uptime_seconds": 100,
            },
        )
    )
    respx.get("http://127.0.0.1:8002/metrics").mock(
        side_effect=httpx.ConnectError("boom")
    )
    async with httpx.AsyncClient() as client:
        result = await _scrape_all(targets, client=client)
    assert result["a"]["request_count"] == 10
    assert "error" in result["b"]


def test_write_outputs_creates_files(tmp_path: Path) -> None:
    output = tmp_path / "metrics.json"
    history = output.with_suffix(output.suffix + ".history.jsonl")
    snapshot = {"scraped_at": "now", "services": {"svc": {"x": 1}}}
    _write_outputs(snapshot, output_path=output, history_path=history)
    assert json.loads(output.read_text())["services"]["svc"]["x"] == 1
    assert history.read_text().strip() != ""


def test_write_outputs_appends_history(tmp_path: Path) -> None:
    output = tmp_path / "metrics.json"
    history = output.with_suffix(output.suffix + ".history.jsonl")
    for i in range(3):
        _write_outputs(
            {"scraped_at": str(i), "services": {}},
            output_path=output,
            history_path=history,
        )
    lines = history.read_text().splitlines()
    assert len(lines) == 3
