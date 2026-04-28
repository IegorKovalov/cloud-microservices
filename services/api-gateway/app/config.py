"""Runtime configuration for the api-gateway service."""

from __future__ import annotations

from dataclasses import dataclass

from shared.utils import env_int, env_str


@dataclass(frozen=True)
class Settings:
    """Static configuration loaded from the environment at startup."""

    service_name: str
    service_port: int
    service_a_url: str
    service_b_url: str
    cpp_worker_url: str
    request_timeout_seconds: float


def load_settings() -> Settings:
    """Construct a :class:`Settings` from environment variables.

    Returns:
        A frozen ``Settings`` instance.
    """
    timeout = float(env_str("REQUEST_TIMEOUT_SECONDS", "10"))
    return Settings(
        service_name=env_str("SERVICE_NAME", "api-gateway"),
        service_port=env_int("SERVICE_PORT", 8000),
        service_a_url=env_str("SERVICE_A_URL", "http://service-a:8001"),
        service_b_url=env_str("SERVICE_B_URL", "http://service-b:8002"),
        cpp_worker_url=env_str("CPP_WORKER_URL", "http://cpp-worker:8003"),
        request_timeout_seconds=timeout,
    )
