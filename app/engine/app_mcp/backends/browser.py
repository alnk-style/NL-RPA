"""Protocol boundary for future browser automation backends."""

from __future__ import annotations

from typing import Any, Protocol


class BrowserBackend(Protocol):
    async def observe(self) -> dict[str, Any]: ...

    async def act(
        self,
        action: str,
        args: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


__all__ = ["BrowserBackend"]
