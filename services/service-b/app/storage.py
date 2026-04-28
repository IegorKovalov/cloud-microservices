"""In-memory key/value storage backing service-b.

The store is intentionally tiny — the goal of this project is to
demonstrate inter-service patterns, not to build a database. An
``asyncio.Lock`` guards mutation to keep semantics predictable under
concurrent FastAPI requests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class KeyValueStore:
    """Async-safe in-memory key/value store of floats."""

    _data: dict[str, float] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def put(self, key: str, value: float) -> None:
        """Insert or overwrite ``key`` with ``value``.

        Args:
            key: Non-empty string identifier.
            value: Float to associate with the key.

        Returns:
            None.

        Raises:
            ValueError: If ``key`` is empty.
        """
        if not key:
            raise ValueError("key must be non-empty")
        async with self._lock:
            self._data[key] = value

    async def get(self, key: str) -> float | None:
        """Return the value associated with ``key`` or ``None``.

        Args:
            key: Identifier to look up.

        Returns:
            The stored float, or ``None`` if no entry exists.
        """
        async with self._lock:
            return self._data.get(key)

    async def delete(self, key: str) -> bool:
        """Remove ``key`` from the store.

        Args:
            key: Identifier to remove.

        Returns:
            ``True`` if the key was present and removed, ``False`` otherwise.
        """
        async with self._lock:
            return self._data.pop(key, None) is not None

    async def all(self) -> dict[str, float]:
        """Return a snapshot of every stored entry.

        Returns:
            A shallow copy of the underlying dict.
        """
        async with self._lock:
            return dict(self._data)

    async def size(self) -> int:
        """Return the number of stored entries.

        Returns:
            Number of currently stored key/value pairs.
        """
        async with self._lock:
            return len(self._data)
