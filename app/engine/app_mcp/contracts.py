"""Shared contracts for manifest-driven application MCP adapters."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4


@dataclass(frozen=True)
class ToolCallContext:
    """Trusted call metadata supplied by the platform, never by the model."""

    request_id: str = field(default_factory=lambda: uuid4().hex)
    user_id: int | None = None
    workspace_id: int | None = None
    workflow_id: int | None = None
    execution_id: int | None = None
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "workflow_id": self.workflow_id,
            "execution_id": self.execution_id,
            "confirmed": self.confirmed,
        }


_CURRENT_CONTEXT: ContextVar[ToolCallContext | None] = ContextVar(
    "app_mcp_tool_call_context",
    default=None,
)

_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:[/\\][^\r\n\"'<>|]*")
_POSIX_PRIVATE_PATH = re.compile(
    r"(?<![\w:])/(?:Users|home|tmp|private|var|opt|mnt|workspace|app)"
    r"/[^\r\n\"'<>|]*"
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|"
    r"authorization)\s*[:=]\s*[^\s,;]+"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def current_call_context() -> ToolCallContext:
    return _CURRENT_CONTEXT.get() or ToolCallContext(request_id="unbound")


@contextmanager
def bind_call_context(
    context: ToolCallContext,
) -> Iterator[ToolCallContext]:
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


@dataclass(frozen=True)
class AdapterEnvironment:
    """Dependencies and safe filesystem roots injected into an adapter."""

    app_dir: Path
    export_root: Path
    connectors: Mapping[str, Any] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FileArtifact:
    name: str
    path: Path
    size: int
    media_type: str = "application/octet-stream"
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "size": self.size,
            "media_type": self.media_type,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class AppError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
            "retryable": self.retryable,
        }


@dataclass
class ActionResult:
    success: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: AppError | None = None
    artifacts: list[FileArtifact] = field(default_factory=list)

    @classmethod
    def ok(
        cls,
        *,
        message: str = "completed",
        data: dict[str, Any] | None = None,
        artifacts: list[FileArtifact] | None = None,
    ) -> ActionResult:
        return cls(
            success=True,
            message=message,
            data=dict(data or {}),
            artifacts=list(artifacts or []),
        )

    @classmethod
    def fail(
        cls,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
        data: dict[str, Any] | None = None,
    ) -> ActionResult:
        return cls(
            success=False,
            message=message,
            data=dict(data or {}),
            error=AppError(
                code=code,
                message=message,
                details=dict(details or {}),
                retryable=retryable,
            ),
        )

    def to_envelope(
        self,
        *,
        app_id: str,
        app_version: str,
        context: ToolCallContext,
    ) -> dict[str, Any]:
        safe_message = sanitize_message(self.message)
        safe_error = (
            {
                **self.error.to_dict(),
                "message": sanitize_message(self.error.message),
                "details": _sanitize_value(self.error.details),
            }
            if self.error
            else None
        )
        return {
            "ok": bool(self.success),
            "message": safe_message,
            "data": (
                _json_safe(self.data) if self.success else _sanitize_value(self.data)
            ),
            "error": safe_error,
            "artifacts": [
                {
                    **_json_safe(artifact.to_dict()),
                    "path": str(artifact.path),
                }
                for artifact in self.artifacts
            ],
            "context": context.to_dict(),
            "app": {"id": app_id, "version": app_version},
        }


@dataclass(frozen=True)
class AppProbe:
    installed: bool
    running: bool
    requires_login: bool = False
    version: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.installed and self.running and not self.requires_login)

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": bool(self.installed),
            "running": bool(self.running),
            "requires_login": bool(self.requires_login),
            "version": self.version,
            "available": self.available,
        }


class AppToolError(RuntimeError):
    """An adapter failure that is safe and useful to expose to a caller."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.safe_message = str(message)
        self.details = dict(details or {})
        self.retryable = bool(retryable)


@runtime_checkable
class AppAdapter(Protocol):
    async def probe(self) -> AppProbe: ...

    async def observe(self) -> dict[str, Any]: ...

    async def execute(
        self,
        action: str,
        args: dict[str, Any],
    ) -> ActionResult: ...

    async def close(self) -> None: ...


def sanitize_message(value: Any, *, max_length: int = 1000) -> str:
    text = str(value or "")
    text = _CONTROL_CHARS.sub("", text)
    text = _WINDOWS_PATH.sub("[redacted path]", text)
    text = _POSIX_PRIVATE_PATH.sub("[redacted path]", text)
    text = _SENSITIVE_VALUE.sub(
        lambda match: f"{match.group(1)}=[redacted]",
        text,
    )
    text = text.strip()
    return text[:max_length] or "Operation failed."


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        payload: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if re.search(
                r"(?i)(password|passwd|token|secret|api[_-]?key|"
                r"authorization|credential)",
                text_key,
            ):
                payload[text_key] = "[redacted]"
            else:
                payload[text_key] = _sanitize_value(item)
        return payload
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_message(value)
    return value


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


__all__ = [
    "ActionResult",
    "AdapterEnvironment",
    "AppAdapter",
    "AppError",
    "AppProbe",
    "AppToolError",
    "FileArtifact",
    "ToolCallContext",
    "bind_call_context",
    "current_call_context",
    "sanitize_message",
]
