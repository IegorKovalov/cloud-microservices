"""Runtime configuration for service-a, loaded entirely from env vars."""

from __future__ import annotations

from dataclasses import dataclass

from shared.utils import env_int, env_str


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot read once at startup."""

    service_name: str
    service_port: int
    service_b_url: str
    cpp_worker_url: str
    fanout_concurrency: int
    queue_workers: int
    threadpool_workers: int


def load_settings() -> Settings:
    """Build a :class:`Settings` from the current environment.

    Returns:
        A frozen ``Settings`` instance.
    """
    return Settings(
        service_name=env_str("SERVICE_NAME", "service-a"),
        service_port=env_int("SERVICE_PORT", 8001),
        service_b_url=env_str("SERVICE_B_URL", "http://service-b:8002"),
        cpp_worker_url=env_str("CPP_WORKER_URL", "http://cpp-worker:8003"),
        fanout_concurrency=env_int("FANOUT_CONCURRENCY", 16),
        queue_workers=env_int("QUEUE_WORKERS", 4),
        threadpool_workers=env_int("THREADPOOL_WORKERS", 4),
    )
