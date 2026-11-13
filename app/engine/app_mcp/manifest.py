"""Manifest models and safe YAML loading for App MCP integrations."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

RiskLevel = Literal["read_only", "mutating", "destructive"]
Platform = Literal["windows", "macos", "linux", "cross_platform"]
BackendKind = Literal["api", "cli", "desktop", "browser"]
HostMode = Literal["subprocess", "in_process"]

_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_ENTRYPOINT_RE = re.compile(r"^adapter:[A-Za-z_][A-Za-z0-9_]*$")


class ManifestError(ValueError):
    """Manifest could not be loaded or validated safely."""


class LocalAppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host_mode: HostMode = "subprocess"
    default_port: int | None = Field(default=None, ge=1024, le=65535)
    auto_start: bool = False
    startup_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    health_interval_seconds: float = Field(default=10.0, ge=1, le=3600)
    unsupported_reason: str | None = Field(default=None, max_length=500)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=2000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel
    output_description: str = Field(min_length=1, max_length=2000)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = value.strip()
        if not _TOOL_NAME_RE.fullmatch(name):
            raise ValueError("工具名只能包含字母、数字、下划线或连字符")
        return name

    @field_validator("input_schema")
    @classmethod
    def _validate_input_schema(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        schema = dict(value)
        if schema.get("type") != "object":
            raise ValueError("输入 Schema 顶层 type 必须是 object")
        properties = schema.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ValueError("输入 Schema 的 properties 必须是对象")
        required = schema.get("required")
        if required is not None and not isinstance(required, list):
            raise ValueError("输入 Schema 的 required 必须是数组")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError(f"输入 Schema 不合法：{exc.message}") from exc
        return schema


class AppManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    version: str = Field(min_length=1, max_length=64)
    platform: Platform = "cross_platform"
    backend_priority: list[BackendKind] = Field(min_length=1)
    entrypoint: str = "adapter:Adapter"
    tools: list[ToolSpec] = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    local: LocalAppConfig = Field(default_factory=LocalAppConfig)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        app_id = value.strip().lower()
        if not _APP_ID_RE.fullmatch(app_id):
            raise ValueError(
                "应用 ID 必须以小写字母开头，且只包含小写字母、数字和下划线"
            )
        return app_id

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, value: str) -> str:
        entrypoint = value.strip()
        if not _ENTRYPOINT_RE.fullmatch(entrypoint):
            raise ValueError("entrypoint 必须使用 adapter:ClassName 格式")
        return entrypoint

    @field_validator("backend_priority")
    @classmethod
    def _validate_backend_priority(
        cls,
        value: list[BackendKind],
    ) -> list[BackendKind]:
        if len(set(value)) != len(value):
            raise ValueError("backend_priority 不能包含重复项")
        return value

    @model_validator(mode="after")
    def _validate_unique_tools(self) -> AppManifest:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("工具名必须唯一")
        return self

    @property
    def tool_map(self) -> dict[str, ToolSpec]:
        return {tool.name: tool for tool in self.tools}


def load_manifest(path: str | Path) -> AppManifest:
    manifest_path = Path(path).expanduser().resolve()
    if not manifest_path.is_file():
        raise ManifestError(f"Manifest 不存在：{manifest_path.name}")
    try:
        payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError("Manifest 无法读取或不是合法 YAML") from exc
    if not isinstance(payload, dict):
        raise ManifestError("Manifest 顶层必须是对象")
    try:
        manifest = AppManifest.model_validate(payload)
    except Exception as exc:
        raise ManifestError(f"Manifest 校验失败：{exc}") from exc
    expected_id = manifest_path.parent.name
    if manifest.id != expected_id:
        raise ManifestError(f"Manifest ID {manifest.id} 与目录名 {expected_id} 不一致")
    return manifest


__all__ = [
    "AppManifest",
    "BackendKind",
    "HostMode",
    "LocalAppConfig",
    "ManifestError",
    "Platform",
    "RiskLevel",
    "ToolSpec",
    "load_manifest",
]
