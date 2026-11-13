"""Generic App MCP kit for manifest-driven application adapters."""

from app.engine.app_mcp.contracts import (
    ActionResult,
    AdapterEnvironment,
    AppAdapter,
    AppError,
    AppProbe,
    FileArtifact,
    ToolCallContext,
    bind_call_context,
    current_call_context,
    sanitize_message,
)
from app.engine.app_mcp.manifest import AppManifest, ToolSpec, load_manifest
from app.engine.app_mcp.runtime import AppRuntime, load_runtime

__all__ = [
    "ActionResult",
    "AdapterEnvironment",
    "AppAdapter",
    "AppError",
    "AppManifest",
    "AppProbe",
    "AppRuntime",
    "FileArtifact",
    "ToolCallContext",
    "ToolSpec",
    "bind_call_context",
    "current_call_context",
    "load_manifest",
    "load_runtime",
    "sanitize_message",
]
