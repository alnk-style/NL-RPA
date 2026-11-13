"""Application automation profile loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ProfileValidationError(ValueError):
    """Application profile does not satisfy the auditable schema."""


def load_app_profiles(
    root: str | Path,
) -> list[dict[str, Any]]:
    directory = Path(root).expanduser()
    if not directory.is_dir():
        return []
    profiles: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            profiles.append(validate_profile(payload))
        except (OSError, json.JSONDecodeError, ProfileValidationError) as exc:
            raise ProfileValidationError(
                f"Invalid app profile {path.name}: {exc}"
            ) from exc
    return profiles


def resolve_profile(
    app_hint: object,
    profiles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    hint = _compact(str(app_hint or ""))
    if not hint:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        terms = {
            _compact(str(profile.get("app_id") or "")),
            _compact(str(profile.get("display_name") or "")),
            _compact(
                Path(str(profile.get("executable_path") or "")).stem
            ),
        }
        score = max(
            (
                len(term)
                for term in terms
                if term and term in hint
            ),
            default=0,
        )
        if score and (best is None or score > best[0]):
            best = (score, profile)
    return best[1] if best is not None else None


def validate_profile(profile: object) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ProfileValidationError("profile must be an object")
    normalized = json.loads(json.dumps(profile, ensure_ascii=False))
    if normalized.get("schema_version") != 1:
        raise ProfileValidationError("schema_version must be 1")
    for key in ("app_id", "display_name", "executable_path"):
        if not isinstance(normalized.get(key), str) or not normalized[
            key
        ].strip():
            raise ProfileValidationError(f"{key} is required")
    window = normalized.get("window")
    if not isinstance(window, dict) or not window:
        raise ProfileValidationError("window is required")
    recipes = normalized.get("recipes")
    if not isinstance(recipes, dict):
        raise ProfileValidationError("recipes must be an object")

    for recipe_name, recipe in recipes.items():
        if not isinstance(recipe_name, str) or not recipe_name.strip():
            raise ProfileValidationError("recipe name must not be empty")
        if not isinstance(recipe, dict):
            raise ProfileValidationError(
                f"recipe {recipe_name} must be an object"
            )
        steps = recipe.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ProfileValidationError(
                f"recipe {recipe_name} requires steps"
            )
        recipe.setdefault("verified", False)
        if not isinstance(recipe["verified"], bool):
            raise ProfileValidationError(
                f"recipe {recipe_name}.verified must be boolean"
            )
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ProfileValidationError(
                    f"recipe {recipe_name} step {index + 1} "
                    "must be an object"
                )
            action = str(step.get("action") or "").strip()
            if not action:
                raise ProfileValidationError(
                    f"recipe {recipe_name} step {index + 1} "
                    "requires action"
                )
            if action == "click_relative":
                for axis in ("x", "y"):
                    try:
                        value = float(step.get(axis))
                    except (TypeError, ValueError) as exc:
                        raise ProfileValidationError(
                            f"recipe {recipe_name} click_relative "
                            f"{axis} must be in 0..1"
                        ) from exc
                    if not 0 <= value <= 1:
                        raise ProfileValidationError(
                            f"recipe {recipe_name} click_relative "
                            f"{axis} must be in 0..1"
                        )
    return normalized


def _compact(value: str) -> str:
    return "".join(
        character
        for character in value.casefold()
        if character.isalnum()
    )


__all__ = [
    "ProfileValidationError",
    "load_app_profiles",
    "resolve_profile",
    "validate_profile",
]
