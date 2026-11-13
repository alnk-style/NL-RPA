"""Protocol boundary for future application API backends.

No generic HTTP execution is exposed in this phase. Concrete adapters may
implement this protocol with a fixed, documented endpoint.
"""

from __future__ import annotations

from typing import Any, Protocol


class ApiBackend(Protocol):
    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


__all__ = ["ApiBackend"]
