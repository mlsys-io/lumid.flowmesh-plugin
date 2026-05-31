"""TTL caches used by the identity provider and usage sink.

Async-safe under single-threaded asyncio (CPython): every operation is
synchronous and uses only the dict's atomic methods. No locks needed.
"""

import time


class TTLCache[V]:
    """Dict-backed TTL cache with FIFO eviction at capacity.

    `get` returns the value if present and unexpired, else None. `set` stores
    a value with the cache's TTL and prunes head entries that are either
    expired or over the capacity ceiling.
    """

    def __init__(self, ttl_sec: float, capacity: int) -> None:
        self._ttl = float(ttl_sec)
        self._capacity = int(capacity)
        self._store: dict[str, tuple[float, V]] = {}

    def get(self, key: str, now: float | None = None) -> V | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        current = time.time() if now is None else now
        if (current - stored_at) >= self._ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: V, now: float | None = None) -> None:
        current = time.time() if now is None else now
        self._prune(current)
        self._store[key] = (current, value)

    def _prune(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._store:
            head_key = next(iter(self._store))
            stored_at, _ = self._store[head_key]
            if stored_at >= cutoff and len(self._store) < self._capacity:
                break
            self._store.pop(head_key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
