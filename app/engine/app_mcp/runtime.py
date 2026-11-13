"""Lifecycle, timeout and safety wrapper around one application adapter."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app.engine.app_mcp.contracts import (
    ActionResult,
    AdapterEnvironment,
    AppAdapter,
    AppProbe,
    AppToolError,
    FileArtifact,
    ToolCallContext,
    bind_call_context,
)
from app.engine.app_mcp.manifest import AppManifest, load_manifest

logger = logging.getLogger(__name__)


class AppRuntime:
    """Owns one adapter, one manifest and its safety limits."""

    def __init__(
        self,
        *,
        manifest: AppManifest,
        adapter: AppAdapter,
        export_root: str | Path,
        timeout_seconds: float = 30.0,
        max_concurrency: int = 1,
        probe_cache_seconds: float = 5.0,
    ) -> None:
        self.manifest = manifest
        self.adapter = adapter
        self.export_root = Path(export_root).expanduser().resolve()
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_concurrency = max(1, int(max_concurrency))
        self.probe_cache_seconds = max(0.0, float(probe_cache_seconds))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._probe_cache: AppProbe | None = None
        self._probe_cache_at = 0.0

    async def probe(self, *, refresh: bool = False) -> AppProbe:
        now = time.monotonic()
        if (
            not refresh
            and self._probe_cache is not None
            and now - self._probe_cache_at <= self.probe_cache_seconds
        ):
            return self._probe_cache
        try:
            value = await asyncio.wait_for(
                self.adapter.probe(),
                timeout=self.timeout_seconds,
            )
            probe = (
                value
                if isinstance(value, AppProbe)
                else AppProbe(
                    installed=False,
                    running=False,
                )
            )
        except asyncio.TimeoutError:
            probe = AppProbe(
                installed=False,
                running=False,
            )
        except Exception:
            logger.exception(
                "App adapter probe failed: %s",
                self.manifest.id,
            )
            probe = AppProbe(
                installed=False,
                running=False,
            )
        self._probe_cache = probe
        self._probe_cache_at = now
        return probe

    async def observe(self) -> dict[str, Any]:
        try:
            async with self._semaphore:
                result = await asyncio.wait_for(
                    self.adapter.observe(),
                    timeout=self.timeout_seconds,
                )
            return (
                result
                if isinstance(result, dict)
                else {"error": "Adapter returned an invalid observation."}
            )
        except asyncio.TimeoutError:
            return {"error": "Adapter observation timed out."}
        except Exception:
            logger.exception(
                "App adapter observe failed: %s",
                self.manifest.id,
            )
            return {"error": "Adapter observation failed."}

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        context: ToolCallContext,
    ) -> ActionResult:
        tool = self.manifest.tool_map.get(str(tool_name))
        if tool is None:
            return ActionResult.fail(
                code="unknown_tool",
                message=f"Unknown tool: {tool_name}",
            )
        args = dict(arguments) if isinstance(arguments, dict) else {}
        validation_error = _validate_arguments(
            tool.input_schema,
            args,
        )
        if validation_error is not None:
            return ActionResult.fail(
                code="invalid_arguments",
                message=validation_error,
            )

        try:
            with bind_call_context(context):
                async with self._semaphore:
                    result = await asyncio.wait_for(
                        self.adapter.execute(tool.name, args),
                        timeout=self.timeout_seconds,
                    )
        except asyncio.TimeoutError:
            return ActionResult.fail(
                code="timeout",
                message="Adapter execution timed out.",
                retryable=True,
            )
        except AppToolError as exc:
            return ActionResult.fail(
                code=exc.code,
                message=exc.safe_message,
                details=exc.details,
                retryable=exc.retryable,
            )
        except Exception:
            logger.exception(
                "App adapter execution failed: %s.%s",
                self.manifest.id,
                tool.name,
            )
            return ActionResult.fail(
                code="adapter_error",
                message="Adapter execution failed.",
            )

        if not isinstance(result, ActionResult):
            return ActionResult.fail(
                code="adapter_error",
                message="Adapter returned an invalid result.",
            )
        artifact_error = self._validate_artifacts(result.artifacts)
        if artifact_error is not None:
            return ActionResult.fail(
                code="artifact_path_denied",
                message=artifact_error,
            )
        return result

    async def close(self) -> None:
        try:
            await self.adapter.close()
        except Exception:
            logger.debug(
                "App adapter close failed: %s",
                self.manifest.id,
                exc_info=True,
            )

    def _validate_artifacts(
        self,
        artifacts: list[FileArtifact],
    ) -> str | None:
        for artifact in artifacts:
            path = Path(artifact.path).expanduser()
            try:
                resolved = path.resolve(strict=False)
            except OSError:
                return "Artifact path could not be resolved."
            if not resolved.is_relative_to(self.export_root):
                return "Artifact path is outside the export directory."
        return None


def _validate_arguments(
    schema: dict[str, Any],
    arguments: dict[str, Any],
) -> str | None:
    validator = Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(arguments),
        key=lambda item: list(item.path),
    )
    if not errors:
        return None
    first = errors[0]
    path = ".".join(str(item) for item in first.path) or "arguments"
    validator = str(first.validator or "schema")
    if validator == "required":
        return f"Invalid arguments at {path}: a required field is missing."
    return f"Invalid arguments at {path}: {validator} validation failed."


def load_runtime(
    app_dir: str | Path,
    *,
    export_root: str | Path | None = None,
    connectors: Mapping[str, Any] | None = None,
    options: Mapping[str, Any] | None = None,
    timeout_seconds: float = 30.0,
    max_concurrency: int = 1,
) -> AppRuntime:
    directory = Path(app_dir).expanduser().resolve()
    manifest = load_manifest(directory / "manifest.yaml")
    class_name = manifest.entrypoint.split(":", 1)[1]
    adapter_path = (directory / "adapter.py").resolve()
    if not adapter_path.is_file() or not adapter_path.is_relative_to(directory):
        raise ValueError("Adapter entrypoint is invalid.")

    module_name = f"nl_rpa_app_{manifest.id}_adapter"
    spec = importlib.util.spec_from_file_location(
        module_name,
        adapter_path,
    )
    if spec is None or spec.loader is None:
        raise ValueError("Adapter module could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    adapter_class = getattr(module, class_name, None)
    if adapter_class is None:
        raise ValueError(f"Adapter class not found: {class_name}")

    resolved_export_root = (
        (
            Path(export_root)
            if export_root is not None
            else Path.cwd() / ".tmp" / "app_mcp" / manifest.id / "exports"
        )
        .expanduser()
        .resolve()
    )
    adapter_options = dict(manifest.config)
    adapter_options.update(dict(options or {}))
    environment = AdapterEnvironment(
        app_dir=directory,
        export_root=resolved_export_root,
        connectors=dict(connectors or {}),
        options=adapter_options,
    )
    adapter = adapter_class(environment)
    return AppRuntime(
        manifest=manifest,
        adapter=adapter,
        export_root=resolved_export_root,
        timeout_seconds=timeout_seconds,
        max_concurrency=max_concurrency,
    )


__all__ = ["AppRuntime", "load_runtime"]
