"""Dependency-free demo adapter for the generic App MCP kit."""

from __future__ import annotations

import re
from typing import Any

from app.engine.app_mcp.contracts import (
    ActionResult,
    AdapterEnvironment,
    AppProbe,
    FileArtifact,
    current_call_context,
)

_SAFE_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class DemoAppAdapter:
    version = "0.1.0"

    def __init__(self, environment: AdapterEnvironment) -> None:
        self.environment = environment
        self.export_root = environment.export_root.expanduser().resolve()

    async def probe(self) -> AppProbe:
        return AppProbe(
            installed=True,
            running=True,
            requires_login=False,
            version=self.version,
        )

    async def observe(self) -> dict[str, Any]:
        probe = await self.probe()
        return {
            "app_id": "demo_app",
            "version": self.version,
            "probe": probe.to_dict(),
        }

    async def execute(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ActionResult:
        name = str(action or "").strip()
        payload = dict(args or {})
        if name == "demo_status":
            return await self._status()
        if name == "demo_execute":
            return self._execute(payload)
        if name == "demo_export":
            return self._export(payload)
        return ActionResult.fail(
            code="unknown_tool",
            message=f"Unknown demo tool: {name}",
        )

    async def close(self) -> None:
        return None

    async def _status(self) -> ActionResult:
        probe = await self.probe()
        return ActionResult.ok(
            message="demo_status ok",
            data={
                "app_id": "demo_app",
                "version": self.version,
                "runtime": {
                    **probe.to_dict(),
                    "backend": "in_process",
                },
            },
        )

    def _execute(self, args: dict[str, Any]) -> ActionResult:
        value = args.get("value")
        note = args.get("note", "")
        if not isinstance(value, str):
            return ActionResult.fail(
                code="invalid_arguments",
                message="value must be a string.",
            )
        if not isinstance(note, str):
            return ActionResult.fail(
                code="invalid_arguments",
                message="note must be a string.",
            )
        return ActionResult.ok(
            message="demo_execute ok",
            data={
                "value": value,
                "note": note,
                "context": current_call_context().to_dict(),
                "backend": "in_process",
            },
        )

    def _export(self, args: dict[str, Any]) -> ActionResult:
        text = args.get("text")
        if not isinstance(text, str):
            return ActionResult.fail(
                code="invalid_arguments",
                message="text must be a string.",
            )
        file_name = args.get("file_name") or "demo-export.txt"
        if not isinstance(file_name, str):
            return ActionResult.fail(
                code="invalid_file_name",
                message="file_name must be a plain file name.",
            )
        name = file_name.strip()
        if (
            not _SAFE_FILE_NAME.fullmatch(name)
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
        ):
            return ActionResult.fail(
                code="invalid_file_name",
                message="file_name must be a plain file name.",
            )

        self.export_root.mkdir(parents=True, exist_ok=True)
        target = (self.export_root / name).resolve()
        if not target.is_relative_to(self.export_root):
            return ActionResult.fail(
                code="invalid_file_name",
                message="file_name escapes the export directory.",
            )

        try:
            target.write_text(text, encoding="utf-8")
            size = target.stat().st_size
        except OSError:
            return ActionResult.fail(
                code="export_failed",
                message="Unable to write the export file.",
            )

        summary = " ".join(text.split())[:160]
        artifact = FileArtifact(
            name=name,
            path=target,
            size=size,
            media_type="text/plain",
            summary=summary,
        )
        return ActionResult.ok(
            message="demo_export ok",
            data={
                "file_name": name,
                "path": str(target),
                "size": size,
                "summary": summary,
            },
            artifacts=[artifact],
        )


__all__ = ["DemoAppAdapter"]
