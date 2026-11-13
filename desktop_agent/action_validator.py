from __future__ import annotations

import fnmatch
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from desktop_agent.actions import ACTION_REGISTRY
from desktop_agent.config import AgentConfig


REGISTERED_ACTION_TYPES = frozenset(
    {
        "launch_app",
        "activate_window",
        "open_document",
        "create_document",
        "insert_image",
        "click_element",
        "click_relative",
        "type_text",
        "press_hotkey",
        "wait",
        "screenshot",
        "read_text",
        "read_window_text",
        "fetch_kugou_lyrics",
        "save_document",
        "export_pdf",
        "close_app",
        "copy_file_to_clipboard",
        "copy_selected_text",
        "create_spreadsheet",
    }
)

DOCUMENT_ACTION_TYPES = frozenset(
    {
        "open_document",
        "create_document",
        "save_document",
        "export_pdf",
    }
)


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _matching_allowed_root(
    path: Path,
    allowed_dirs: list[str],
) -> Path | None:
    normalized = os.path.normcase(str(path))
    for entry in allowed_dirs:
        text = str(entry).strip()
        if not text:
            continue
        root = _resolve_path(text)
        normalized_root = os.path.normcase(str(root))
        if normalized == normalized_root or normalized.startswith(
            normalized_root.rstrip("\\/") + os.sep
        ):
            return root
    return None


@dataclass(frozen=True)
class ValidationResult:
    allowed: bool
    reason: str | None = None


class ActionValidator:
    @staticmethod
    def validate_profile_recipe(
        profile: dict,
        recipe_name: str,
        *,
        manual_confirmation: bool = False,
        config: AgentConfig | None = None,
    ) -> ValidationResult:
        from app.engine.app_profiles import (
            ProfileValidationError,
            validate_profile,
        )

        try:
            normalized = validate_profile(profile)
        except ProfileValidationError as exc:
            return ValidationResult(False, str(exc))
        recipes = normalized.get("recipes") or {}
        recipe = recipes.get(recipe_name)
        if not isinstance(recipe, dict):
            return ValidationResult(
                False,
                f"Unknown application recipe: {recipe_name}",
            )
        if recipe.get("verified") is not True and not manual_confirmation:
            return ValidationResult(
                False,
                "Recipe is unverified and requires manual confirmation: "
                f"{recipe_name}",
            )
        for step in recipe.get("steps") or []:
            action_type = str(step.get("action") or "")
            action = {
                "type": action_type,
                "params": {
                    key: value
                    for key, value in step.items()
                    if key not in {"action", "verified"}
                },
            }
            if config is not None:
                checked = ActionValidator.validate(action, config)
                if not checked.allowed:
                    return checked
            elif action_type not in REGISTERED_ACTION_TYPES:
                return ValidationResult(
                    False,
                    f"Unsupported recipe action: {action_type}",
                )
        return ValidationResult(True)

    @staticmethod
    def validate(
        action: dict,
        config: AgentConfig,
    ) -> ValidationResult:
        if not isinstance(action, dict):
            return ValidationResult(False, "Action must be a JSON object")

        action_type = str(action.get("type", "")).strip()
        if (
            action_type not in REGISTERED_ACTION_TYPES
            and action_type not in ACTION_REGISTRY
        ):
            return ValidationResult(
                False,
                f"Unsupported action type: {action_type or '<empty>'}",
            )

        params = action.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return ValidationResult(False, "Action params must be an object")

        if action_type == "launch_app":
            return ActionValidator._validate_launch_app(params, config)
        if action_type == "screenshot":
            return ActionValidator._validate_screenshot(params, config)
        if action_type == "copy_file_to_clipboard":
            return ActionValidator._validate_clipboard_file(params, config)
        if action_type == "insert_image":
            return ActionValidator._validate_insert_image(params, config)
        if action_type == "create_spreadsheet":
            return ActionValidator._validate_create_spreadsheet(
                params,
                config,
            )
        if action_type in DOCUMENT_ACTION_TYPES:
            return ActionValidator._validate_document_action(
                action_type,
                params,
                config,
            )
        return ValidationResult(True)

    @staticmethod
    def _validate_launch_app(
        params: dict,
        config: AgentConfig,
    ) -> ValidationResult:
        raw_path = params.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ValidationResult(
                False,
                "launch_app requires a non-empty path",
            )

        app_path = raw_path.strip()
        app_name = ActionValidator._app_name(app_path)
        if not app_name:
            return ValidationResult(False, "launch_app path has no filename")

        for configured in config.allowed_apps:
            if ActionValidator._app_entry_matches(
                configured,
                app_path,
                app_name,
            ):
                return ValidationResult(True)

        return ValidationResult(
            False,
            f"Application is not allowlisted: {app_name}",
        )

    @staticmethod
    def _validate_document_action(
        action_type: str,
        params: dict,
        config: AgentConfig,
    ) -> ValidationResult:
        raw_path = params.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ValidationResult(
                False,
                f"{action_type} requires a non-empty path",
            )

        if not config.allowed_doc_dirs:
            document_path = _resolve_path(raw_path.strip())
            return ValidationResult(
                False,
                f"Document path is outside allowlist: {document_path}",
            )

        resolved = _resolve_path(raw_path.strip())
        if _matching_allowed_root(resolved, config.allowed_doc_dirs):
            return ValidationResult(True)

        return ValidationResult(
            False,
            f"Document path is outside allowlist: {resolved}",
        )

    @staticmethod
    def _validate_insert_image(
        params: dict,
        config: AgentConfig,
    ) -> ValidationResult:
        document = ActionValidator._validate_document_action(
            "insert_image",
            params,
            config,
        )
        if not document.allowed:
            return document

        raw_image = params.get("image_path")
        if not isinstance(raw_image, str) or not raw_image.strip():
            return ValidationResult(
                False,
                "insert_image requires a non-empty image_path",
            )

        image_path = _resolve_path(raw_image.strip())
        if _matching_allowed_root(image_path, config.allowed_doc_dirs) is None:
            return ValidationResult(
                False,
                f"图片路径不在白名单内：{image_path}",
            )
        if not image_path.is_file():
            return ValidationResult(
                False,
                f"图片文件不存在：{image_path}",
            )
        return ValidationResult(True)

    @staticmethod
    def _validate_create_spreadsheet(
        params: dict,
        config: AgentConfig,
    ) -> ValidationResult:
        validation = ActionValidator._validate_document_action(
            "create_spreadsheet",
            params,
            config,
        )
        if not validation.allowed:
            return validation

        path = _resolve_path(str(params["path"]).strip())
        if path.suffix.lower() not in {".xlsx", ".xls"}:
            return ValidationResult(
                False,
                "create_spreadsheet requires a .xlsx or .xls path",
            )
        if not str(params.get("sheet_name") or "").strip():
            return ValidationResult(
                False,
                "create_spreadsheet requires a non-empty sheet_name",
            )
        columns = params.get("columns")
        if not isinstance(columns, list) or not columns:
            return ValidationResult(
                False,
                "create_spreadsheet requires non-empty columns",
            )
        if any(
            not isinstance(column, dict)
            or not str(column.get("header") or "").strip()
            for column in columns
        ):
            return ValidationResult(
                False,
                "create_spreadsheet columns require headers",
            )
        return ValidationResult(True)

    @staticmethod
    def _validate_screenshot(
        params: dict,
        config: AgentConfig,
    ) -> ValidationResult:
        raw_path = params.get("path")
        if raw_path is None:
            return ValidationResult(True)
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ValidationResult(
                False,
                "screenshot path must be a non-empty string",
            )

        text = raw_path.strip()
        candidate = Path(text).expanduser()
        if candidate.is_dir() or text.endswith(("\\", "/")):
            target_dir = candidate
        else:
            target_dir = candidate.parent

        resolved_dir = _resolve_path(str(target_dir))
        if _matching_allowed_root(
            resolved_dir,
            config.allowed_doc_dirs,
        ) is None:
            return ValidationResult(
                False,
                f"截图保存目录不在白名单内：{resolved_dir}",
            )
        if not resolved_dir.is_dir():
            return ValidationResult(
                False,
                f"截图保存目录不存在：{resolved_dir}",
            )
        return ValidationResult(True)

    @staticmethod
    def _validate_clipboard_file(
        params: dict,
        config: AgentConfig,
    ) -> ValidationResult:
        validation = ActionValidator._validate_document_action(
            "copy_file_to_clipboard",
            params,
            config,
        )
        if not validation.allowed:
            return validation

        path = _resolve_path(str(params["path"]).strip())
        if not path.is_file():
            return ValidationResult(
                False,
                f"文件不存在：{path}",
            )
        return ValidationResult(True)

    @staticmethod
    def _app_name(app_path: str) -> str:
        if "\\" in app_path:
            return PureWindowsPath(app_path).name.lower()
        return Path(app_path).name.lower()

    @staticmethod
    def _app_entry_matches(
        configured: str,
        app_path: str,
        app_name: str,
    ) -> bool:
        if not isinstance(configured, str) or not configured.strip():
            return False

        entry = configured.strip()
        entry_name = ActionValidator._app_name(entry)
        normalized_entry = ntpath.normcase(ntpath.normpath(entry))
        normalized_path = ntpath.normcase(ntpath.normpath(app_path))

        if any(char in entry for char in "*?["):
            if "\\" in entry or "/" in entry:
                return fnmatch.fnmatchcase(
                    normalized_path,
                    normalized_entry,
                )
            return fnmatch.fnmatchcase(app_name, entry_name)

        if "\\" in entry or "/" in entry:
            return normalized_entry == normalized_path
        return entry_name == app_name


__all__ = [
    "ActionValidator",
    "DOCUMENT_ACTION_TYPES",
    "REGISTERED_ACTION_TYPES",
    "ValidationResult",
]
