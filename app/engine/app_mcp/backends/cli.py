"""Protocol boundary for future allowlisted CLI backends.

This phase intentionally provides no shell runner. A concrete adapter must
define exact executable names and arguments without passing through commands.
"""

from __future__ import annotations

from typing import Any, Protocol


class CliBackend(Protocol):
    async def run(
        self,
        operation: str,
        args: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def close(self) -> None: ...


__all__ = ["CliBackend"]
