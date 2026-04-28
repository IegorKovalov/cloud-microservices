"""Shared library used across all Python services and orchestration scripts.

Each service Docker image copies this directory in at build time so that
no service has a runtime dependency on another service. The package is
intentionally tiny: it only contains data models, structured logging
configuration, and small helpers (retry/backoff, async timing).
"""
