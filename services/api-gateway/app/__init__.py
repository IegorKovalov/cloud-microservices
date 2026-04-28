"""api-gateway: edge entry point for the cloud-microservices framework.

Exposes a single, stable URL surface (``localhost:8000``) and forwards
domain calls to service-a. The gateway also fans out a fleet-wide
health check via ``GET /system/health``, which the orchestrator script
consumes for system-level liveness.
"""
