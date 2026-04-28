"""Unit tests for monitoring.log_aggregator."""

from __future__ import annotations

import json

from monitoring.log_aggregator import _emit, _parse_compose_line
from shared.logging_config import configure_logging


def test_parse_compose_line_with_prefix() -> None:
    container, payload = _parse_compose_line(
        'cm-service-a  | {"level": "info", "service": "service-a"}'
    )
    assert container == "cm-service-a"
    assert payload == '{"level": "info", "service": "service-a"}'


def test_parse_compose_line_without_prefix() -> None:
    container, payload = _parse_compose_line("standalone line")
    assert container == ""
    assert payload == "standalone line"


def test_emit_handles_valid_json(capsys) -> None:  # type: ignore[no-untyped-def]
    logger = configure_logging("test-aggregator")
    payload = json.dumps(
        {
            "level": "info",
            "service": "service-a",
            "message": "hello",
            "timestamp": "2024-01-01T00:00:00Z",
            "extra_field": 42,
        }
    )
    _emit(logger, "cm-service-a", payload)
    captured = capsys.readouterr().out
    assert "service_log" in captured
    assert "service-a" in captured


def test_emit_handles_non_json(capsys) -> None:  # type: ignore[no-untyped-def]
    logger = configure_logging("test-aggregator")
    _emit(logger, "cm-service-a", "INFO uvicorn ready")
    captured = capsys.readouterr().out
    assert "raw_log" in captured


def test_emit_skips_empty_payload(capsys) -> None:  # type: ignore[no-untyped-def]
    logger = configure_logging("test-aggregator")
    _emit(logger, "container", "")
    assert capsys.readouterr().out == ""
