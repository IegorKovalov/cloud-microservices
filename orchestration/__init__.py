"""Host-side orchestration package.

Three small scripts run from the host and drive the dockerised stack:

* ``health_check`` -> reusable async pollers,
* ``recovery``     -> detect-and-restart loop,
* ``orchestrator`` -> bring the stack up, keep it healthy, tear it down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from shared.utils import env_int, env_str


@dataclass(frozen=True)
class ServiceTarget:
    """One service the orchestrator monitors.

    Attributes:
        name: Logical name used in logs (e.g. ``service-a``).
        container: docker container name (e.g. ``cm-service-a``).
        health_url: URL to poll for ``GET /health``.
    """

    name: str
    container: str
    health_url: str


def _url(host_port: int, path: str = "/health") -> str:
    """Construct a localhost URL for the given host-mapped port."""
    return f"http://127.0.0.1:{host_port}{path}"


def default_targets() -> list[ServiceTarget]:
    """Build the default list of monitored services from the environment.

    Returns:
        A list of :class:`ServiceTarget` covering all five services.
    """
    return [
        ServiceTarget(
            name="api-gateway",
            container="cm-api-gateway",
            health_url=_url(env_int("API_GATEWAY_PORT", 8000)),
        ),
        ServiceTarget(
            name="service-a",
            container="cm-service-a",
            health_url=_url(env_int("SERVICE_A_PORT", 8001)),
        ),
        ServiceTarget(
            name="service-b",
            container="cm-service-b",
            health_url=_url(env_int("SERVICE_B_PORT", 8002)),
        ),
        ServiceTarget(
            name="cpp-worker",
            container="cm-cpp-worker",
            health_url=_url(env_int("CPP_WORKER_PORT", 8003)),
        ),
        ServiceTarget(
            name="fault-injector",
            container="cm-fault-injector",
            health_url=_url(env_int("FAULT_INJECTOR_PORT", 8004)),
        ),
    ]


COMPOSE_PROJECT_NAME: Final[str] = env_str("COMPOSE_PROJECT_NAME", "cloud-microservices")
