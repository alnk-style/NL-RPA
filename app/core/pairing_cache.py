from __future__ import annotations

import time
from threading import Lock


class PairingCache:
    """Small in-memory pairing-code store with TTL-based expiry."""

    def __init__(self) -> None:
        self._codes: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    def _cleanup_expired(self, now: float | None = None) -> None:
        current_time = time.time() if now is None else now
        expired_codes = [
            code
            for code, (_, expires_at) in self._codes.items()
            if expires_at <= current_time
        ]
        for code in expired_codes:
            self._codes.pop(code, None)

    def store_code(
        self,
        code: str,
        user_id: int,
        ttl: int = 300,
    ) -> None:
        now = time.time()
        expires_at = now + ttl
        with self._lock:
            self._cleanup_expired(now)
            self._codes[code] = (user_id, expires_at)

    def consume_code(self, code: str) -> int | None:
        now = time.time()
        with self._lock:
            self._cleanup_expired(now)
            entry = self._codes.pop(code, None)

        if entry is None:
            return None

        user_id, expires_at = entry
        if expires_at <= now:
            return None
        return user_id

    def clear(self) -> None:
        with self._lock:
            self._codes.clear()


pairing_cache = PairingCache()


__all__ = ["PairingCache", "pairing_cache"]
