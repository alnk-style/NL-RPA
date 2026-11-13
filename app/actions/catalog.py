from __future__ import annotations

from typing import Any

from app.actions.protocol import (
    ActionDefinition,
    StepError,
    StepResult,
    StepStatus,
)
from app.actions.registry import ActionRegistry


CATALOG_VERSION = "2026-10-28.1"


def _object_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema_properties = dict(properties or {})
    schema_properties.setdefault(
        "visual_fallback",
        {
            "type": "boolean",
            "default": False,
            "title": "允许视觉兜底",
        },
    )
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(required),
        "properties": schema_properties,
    }


def _string() -> dict[str, Any]:
    return {"type": "string"}


def _number() -> dict[str, Any]:
    return {"type": "number"}


def _integer() -> dict[str, Any]:
    return {"type": "integer"}


def _boolean() -> dict[str, Any]:
    return {"type": "boolean"}


def _any() -> dict[str, Any]:
    return {}


def _definition(
    *,
    action_id: str,
    executor: str,
    title: str,
    category: str,
    params_schema: dict[str, Any],
    legacy_only: bool = False,
    risk_level: str = "read_only",
    requires: dict[str, bool] | None = None,
) -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        executor=executor,
        title=title,
        description=title,
        category=category,
        risk_level=risk_level,
        params_schema=params_schema,
        output_schema={},
        requires=requires
        or {
            "interactive_desktop": False,
            "visual": False,
            "network": False,
        },
        catalog_version=CATALOG_VERSION,
        legacy_only=legacy_only,
    )


async def _managed_handler(context: Any, params: dict[str, Any]) -> StepResult:
    raise RuntimeError(
        "This action is executed by its existing domain executor"
    )


async def _transform_handler(context: Any, params: dict[str, Any]) -> StepResult:
    from app.engine.transforms import apply_transform

    variables = getattr(context, "variables", None)
    if not isinstance(variables, dict):
        variables = {}
    action = str(getattr(context, "action", "") or "")
    value = apply_transform(action, params, variables)
    return StepResult(
        status=StepStatus.SUCCESS,
        message=f"transform {action} completed",
        value=value,
    )


def build_default_registry() -> ActionRegistry:
    registry = ActionRegistry(version=CATALOG_VERSION)
    _register_web(registry)
    _register_data(registry)
    _register_files(registry)
    _register_email(registry)
    _register_windows(registry)
    _register_mcp(registry)
    _register_legacy(registry)
    return registry


def _register_email(registry: ActionRegistry) -> None:
    from app.actions.email import execute_email_action

    async def handler(context: Any, params: dict[str, Any]) -> StepResult:
        services = getattr(context, "services", {})
        if not isinstance(services, dict):
            services = {}
        provider = services.get("email_service_provider")
        service = services.get("email_service")
        if provider is not None:
            service = await provider(
                str(params.get("credential_key") or "smtp_default")
            )
        if service is None:
            return StepResult(
                status=StepStatus.FAILED,
                message="Email service is not configured",
                error=StepError(
                    category="delivery_failed",
                    message="Email service is not configured",
                ),
            )
        return await execute_email_action(
            params,
            email_service=service,
            execution_id=getattr(context, "execution_id", None),
            step_id=getattr(context, "step_id", None),
        )

    registry.register(
        _definition(
            action_id="send",
            executor="email",
            title="发送邮件",
            category="email",
            params_schema=_object_schema(
                {
                    "to": {
                        "type": "array",
                        "items": _string(),
                        "minItems": 1,
                    },
                    "cc": {"type": "array", "items": _string()},
                    "bcc": {"type": "array", "items": _string()},
                    "subject": _string(),
                    "body": _string(),
                    "html": _boolean(),
                    "attachments": {
                        "type": "array",
                        "items": _string(),
                    },
                    "credential_key": _string(),
                    "required_delivery": _boolean(),
                },
                required=("to", "subject", "body"),
            ),
            risk_level="mutating",
            requires={
                "interactive_desktop": False,
                "visual": False,
                "network": True,
            },
        ),
        handler,
    )


def _register_files(registry: ActionRegistry) -> None:
    from app.actions.files import execute_file_action

    definitions = {
        "write_csv": {
            "path": {
                "type": "string",
                "title": "输出路径与文件名",
            },
            "rows": _any(),
            "values": _any(),
            "headers": {"type": "array", "items": _string()},
            "encoding": _string(),
            "allow_empty": _boolean(),
            "overwrite": _boolean(),
            "result_var": _string(),
        },
        "write_xlsx": {
            "path": {
                "type": "string",
                "title": "输出路径与文件名",
            },
            "rows": _any(),
            "values": _any(),
            "headers": {"type": "array", "items": _string()},
            "sheet_name": _string(),
            "allow_empty": _boolean(),
            "overwrite": _boolean(),
            "result_var": _string(),
        },
        "save_artifact": {
            "path": {
                "type": "string",
                "title": "输出路径与文件名",
            },
            "content": _any(),
            "variable": _any(),
            "source_path": _string(),
            "encoding": _string(),
            "overwrite": _boolean(),
            "result_var": _string(),
        },
        "copy_file": {
            "source": _string(),
            "destination": _string(),
            "overwrite": _boolean(),
            "result_var": _string(),
        },
        "zip": {
            "destination": _string(),
            "sources": {"type": "array", "items": _string()},
            "base_dir": _string(),
            "overwrite": _boolean(),
            "result_var": _string(),
        },
    }
    for action_id, properties in definitions.items():
        async def handler(
            context: Any,
            params: dict[str, Any],
            action=action_id,
        ) -> StepResult:
            services = getattr(context, "services", {})
            roots = services.get("allowed_roots", []) if isinstance(services, dict) else []
            variables = getattr(context, "variables", {})
            return await execute_file_action(
                action,
                params,
                variables=variables if isinstance(variables, dict) else {},
                allowed_roots=roots,
            )

        registry.register(
            _definition(
                action_id=action_id,
                executor="files",
                title=action_id,
                category="files",
                params_schema=_object_schema(properties),
                risk_level="mutating",
            ),
            handler,
        )


def _register_data(registry: ActionRegistry) -> None:
    from app.actions.data import execute_data_action

    common = {
        "result_var": _string(),
        "allow_empty": _boolean(),
    }
    definitions = {
        "filter": {
            "values": _any(),
            "field": _string(),
            "operator": {
                "type": "string",
                "enum": [
                    "eq",
                    "ne",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "contains",
                    "not_contains",
                    "startswith",
                    "endswith",
                    "matches",
                    "exists",
                    "not_exists",
                    "in",
                    "not_in",
                ],
            },
            "value": _any(),
            "cast": {
                "type": "string",
                "enum": ["string", "number", "integer", "boolean", "date"],
            },
            **common,
        },
        "map": {
            "values": _any(),
            "mapping": {"type": "object"},
            "fields": {"type": "object"},
            "strict": _boolean(),
            **common,
        },
        "cast": {
            "values": _any(),
            "field": _string(),
            "target": {
                "type": "string",
                "enum": ["string", "number", "integer", "boolean", "date"],
            },
            "on_error": {
                "type": "string",
                "enum": ["error", "skip", "null"],
            },
            **common,
        },
        "sort": {
            "values": _any(),
            "field": _string(),
            "order": {"type": "string", "enum": ["asc", "desc"]},
            "missing": {"type": "string", "enum": ["first", "last"]},
            **common,
        },
        "deduplicate": {
            "values": _any(),
            "keys": {"type": ["string", "array"]},
            "keep": {"type": "string", "enum": ["first", "last"]},
            "trim": _boolean(),
            **common,
        },
        "aggregate": {
            "values": _any(),
            "operation": {
                "type": "string",
                "enum": ["count", "sum", "min", "max", "avg"],
            },
            "field": _string(),
            "group_by": _string(),
            **common,
        },
        "join": {
            "left": _any(),
            "right": _any(),
            "left_key": _string(),
            "right_key": _string(),
            "how": {"type": "string", "enum": ["inner", "left"]},
            **common,
        },
        "flatten": {
            "values": _any(),
            "field": _string(),
            "depth": _integer(),
            **common,
        },
        "limit": {
            "values": _any(),
            "count": _integer(),
            "offset": _integer(),
            **common,
        },
        "validate": {
            "values": _any(),
            "required_fields": {"type": ["string", "array"]},
            "min_rows": _integer(),
            "max_rows": _integer(),
            "unique_by": {"type": ["string", "array"]},
            **common,
        },
    }
    for action_id, properties in definitions.items():
        async def handler(
            context: Any,
            params: dict[str, Any],
            action=action_id,
        ) -> StepResult:
            variables = getattr(context, "variables", None)
            return await execute_data_action(
                action,
                params,
                variables if isinstance(variables, dict) else {},
            )

        registry.register(
            _definition(
                action_id=action_id,
                executor="transform",
                title=action_id,
                category="data",
                params_schema=_object_schema(properties),
            ),
            handler,
        )


def _register_web(registry: ActionRegistry) -> None:
    definitions = [
        _definition(
            action_id="goto",
            executor="web",
            title="打开页面",
            category="web",
            params_schema=_object_schema(
                {
                    "url": _string(),
                    "wait_until": _string(),
                    "timeout_ms": _number(),
                },
                required=("url",),
            ),
            risk_level="read_only",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="click",
            executor="web",
            title="点击元素",
            category="web",
            params_schema=_object_schema(
                {
                    "button": _string(),
                    "click_count": _integer(),
                    "force": _boolean(),
                    "position": {"type": "object"},
                }
            ),
            risk_level="mutating",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="fill",
            executor="web",
            title="填写输入",
            category="web",
            params_schema=_object_schema(
                {"text": _string()}, required=("text",)
            ),
            risk_level="mutating",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="press",
            executor="web",
            title="按键",
            category="web",
            params_schema=_object_schema(
                {"key": _string()}, required=("key",)
            ),
            risk_level="mutating",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="select_option",
            executor="web",
            title="选择选项",
            category="web",
            params_schema=_object_schema(
                {
                    "values": _any(),
                    "value": _any(),
                    "label": _string(),
                    "index": _integer(),
                }
            ),
            risk_level="mutating",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="check",
            executor="web",
            title="勾选",
            category="web",
            params_schema=_object_schema(),
            risk_level="mutating",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="uncheck",
            executor="web",
            title="取消勾选",
            category="web",
            params_schema=_object_schema(),
            risk_level="mutating",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="wait_for",
            executor="web",
            title="等待条件",
            category="web",
            params_schema=_object_schema(
                {
                    "milliseconds": _number(),
                    "selector": _string(),
                    "state": _string(),
                }
            ),
        ),
        _definition(
            action_id="wait_for_response",
            executor="web",
            title="等待响应",
            category="web",
            params_schema=_object_schema(
                {
                    "url_contains": _string(),
                    "status": _integer(),
                    "timeout_ms": _number(),
                    "trigger_selector": _string(),
                    "variable": _string(),
                }
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="upload",
            executor="web",
            title="上传文件",
            category="web",
            params_schema=_object_schema(
                {"files": _any()}, required=("files",)
            ),
            risk_level="mutating",
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="download",
            executor="web",
            title="下载文件",
            category="web",
            params_schema=_object_schema(
                {"save_as": _string(), "timeout_ms": _number()}
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="extract_text",
            executor="web",
            title="提取文本",
            category="web",
            params_schema=_object_schema(
                {
                    "multiple": _boolean(),
                    "variable": _string(),
                    "allow_empty": _boolean(),
                }
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="extract_table",
            executor="web",
            title="提取表格",
            category="web",
            params_schema=_object_schema(
                {
                    "selector": _string(),
                    "variable": _string(),
                    "allow_empty": _boolean(),
                }
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="extract_fields",
            executor="web",
            title="提取字段",
            category="web",
            params_schema=_object_schema(
                {
                    "selector": _string(),
                    "fields": {"type": "object"},
                    "variable": _string(),
                    "allow_empty": _boolean(),
                },
                required=("fields",),
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="collect_links",
            executor="web",
            title="收集链接",
            category="web",
            params_schema=_object_schema(
                {
                    "selector": _string(),
                    "attribute": _string(),
                    "limit": _integer(),
                    "allowed_domains": {"type": "array", "items": _string()},
                    "variable": _string(),
                    "allow_empty": _boolean(),
                },
                required=("selector",),
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="paginate",
            executor="web",
            title="分页收集",
            category="web",
            params_schema=_object_schema(
                {
                    "item_selector": _string(),
                    "next_selector": _string(),
                    "attribute": _string(),
                    "max_pages": _integer(),
                    "max_items": _integer(),
                    "allowed_domains": {"type": "array", "items": _string()},
                    "variable": _string(),
                    "allow_empty": _boolean(),
                },
                required=(
                    "item_selector",
                    "next_selector",
                    "max_pages",
                    "max_items",
                ),
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
        _definition(
            action_id="screenshot",
            executor="web",
            title="页面截图",
            category="web",
            params_schema=_object_schema(
                {"name": _string(), "full_page": _boolean()}
            ),
        ),
        _definition(
            action_id="assert_text",
            executor="web",
            title="断言文本",
            category="web",
            params_schema=_object_schema(
                {"text": _string(), "exact": _boolean()},
                required=("text",),
            ),
        ),
        _definition(
            action_id="assert_logged_in",
            executor="web",
            title="断言登录状态",
            category="web",
            params_schema=_object_schema(
                {
                    "success_selector": _string(),
                    "login_paths": {"type": "array", "items": _string()},
                }
            ),
            requires={"interactive_desktop": False, "visual": False, "network": True},
        ),
    ]
    for definition in definitions:
        registry.register(
            definition,
            _managed_handler,
            aliases=("navigate",) if definition.id == "goto" else (),
        )


def _register_windows(registry: ActionRegistry) -> None:
    parameters = {
        "launch_app": {
            "path": _string(),
            "args": {"type": "array", "items": _string()},
            "wait_for_window": _boolean(),
            "window_timeout": _number(),
        },
        "activate_window": {
            "title": _string(),
            "class_name": _string(),
            "pid": _any(),
            "index": _integer(),
        },
        "click_element": {
            "automation_id": _string(),
            "name": _string(),
            "control_type": _string(),
            "x": _number(),
            "y": _number(),
            "button": _string(),
            "double_click": _boolean(),
        },
        "click_relative": {
            "x": _number(),
            "y": _number(),
            "button": _string(),
            "double_click": _boolean(),
        },
        "type_text": {
            "text": _string(),
            "clear_first": _boolean(),
            "method": _string(),
        },
        "press_hotkey": {"keys": _string()},
        "wait": {
            "seconds": _number(),
            "condition": _string(),
            "title": _string(),
            "automation_id": _string(),
            "name": _string(),
            "control_type": _string(),
        },
        "screenshot": {
            "path": _string(),
            "name": _string(),
            "window": _any(),
            "region": {"type": "object"},
            "overwrite": _boolean(),
        },
        "read_text": {
            "automation_id": _string(),
            "name": _string(),
            "control_type": _string(),
        },
        "read_window_text": {
            "title": _string(),
            "class_name": _string(),
            "pid": _any(),
            "handle": _any(),
            "min_chars": _integer(),
        },
        "close_app": {
            "target": _string(),
            "force": _boolean(),
            "graceful_timeout": _number(),
        },
    }
    for action_id, props in parameters.items():
        registry.register(
            _definition(
                action_id=action_id,
                executor="windows",
                title=action_id,
                category="desktop",
                params_schema=_object_schema(props),
                risk_level="mutating",
                requires={
                    "interactive_desktop": True,
                    "visual": False,
                    "network": False,
                },
            ),
            _managed_handler,
        )


def _register_mcp(registry: ActionRegistry) -> None:
    registry.register(
        _definition(
            action_id="call_tool",
            executor="mcp",
            title="调用 MCP 工具",
            category="mcp",
            params_schema=_object_schema(
                {
                    "server": _string(),
                    "server_id": _string(),
                    "mcp_server": _string(),
                    "tool": _string(),
                    "name": _string(),
                    "arguments": {"type": "object"},
                    "result_path": _string(),
                    "timeout_seconds": _number(),
                    "confirm": _boolean(),
                    "result_var": _string(),
                }
            ),
            risk_level="mutating",
            requires={
                "interactive_desktop": False,
                "visual": False,
                "network": True,
            },
        ),
        _managed_handler,
    )


def _register_legacy(registry: ActionRegistry) -> None:
    registry.register(
        _definition(
            action_id="code_step",
            executor="web",
            title="Legacy page script",
            category="web",
            params_schema=_object_schema(
                {
                    "script": _string(),
                    "expect": _boolean(),
                    "variable": _string(),
                },
                required=("script",),
            ),
            legacy_only=True,
            risk_level="high",
        ),
        _managed_handler,
    )
    for action_id in (
        "fetch_kugou_lyrics",
        "create_document",
        "create_spreadsheet",
        "insert_image",
        "copy_file_to_clipboard",
        "copy_selected_text",
    ):
        registry.register(
            _definition(
                action_id=action_id,
                executor="windows",
                title=f"Legacy {action_id}",
                category="desktop",
                params_schema=_object_schema(),
                legacy_only=True,
                risk_level="mutating",
                requires={
                    "interactive_desktop": True,
                    "visual": False,
                    "network": False,
                },
            ),
            _managed_handler,
        )
    for action_id in (
        "split_lines",
        "trim_lines",
        "remove_blank_lines",
        "filter_contains",
        "map_fields",
        "merge_rows",
        "count_chars",
        "aggregate_sum",
        "sort_by",
    ):
        registry.register(
            _definition(
                action_id=action_id,
                executor="transform",
                title=f"Legacy {action_id}",
                category="data",
                params_schema=_object_schema(
                    {
                        "text": _string(),
                        "values": _any(),
                        "field": _string(),
                        "mapping": {"type": "object"},
                        "trim": _boolean(),
                        "needle": _string(),
                        "reverse": _boolean(),
                    }
                ),
                legacy_only=True,
            ),
            _transform_handler,
        )
    for action_id in ("observe_page", "assert_data", "save_file"):
        registry.register(
            _definition(
                action_id=action_id,
                executor="web",
                title=f"Legacy {action_id}",
                category="web",
                params_schema=_object_schema(),
                legacy_only=True,
            ),
            _managed_handler,
        )


__all__ = ["CATALOG_VERSION", "build_default_registry"]
