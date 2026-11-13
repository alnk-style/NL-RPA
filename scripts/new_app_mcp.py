"""Create a safe starter package for a new App MCP integration."""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml"}


class AppTemplateError(ValueError):
    """Template input or destination failed validation."""


def _safe_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 200:
        raise AppTemplateError("Application name must be 1-200 characters.")
    if any(ord(character) < 32 for character in name):
        raise AppTemplateError("Application name contains control characters.")
    return name.replace('"', "'")


def _yaml_scalar(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def create_app_package(
    *,
    app_id: str,
    name: str,
    apps_root: str | Path | None = None,
    templates_root: str | Path | None = None,
) -> Path:
    normalized_id = str(app_id or "").strip()
    if not _APP_ID_RE.fullmatch(normalized_id):
        raise AppTemplateError(
            "App ID must begin with a lowercase letter and contain only "
            "lowercase letters, digits and underscores."
        )
    display_name = _safe_name(name)
    root = (
        Path(apps_root).expanduser().resolve()
        if apps_root is not None
        else PROJECT_ROOT / "mcp_apps"
    )
    templates = (
        Path(templates_root).expanduser().resolve()
        if templates_root is not None
        else PROJECT_ROOT / "mcp_templates"
    )
    source = templates / "app"
    if not (source / "manifest.yaml").is_file():
        raise AppTemplateError("Application template is missing.")

    target = (root / normalized_id).resolve()
    if target.parent != root:
        raise AppTemplateError("Application path escaped the apps directory.")
    if target.exists():
        raise AppTemplateError(
            f"Application directory already exists: {normalized_id}"
        )
    if not root.is_dir():
        root.mkdir(parents=True, exist_ok=True)

    created = False
    try:
        shutil.copytree(source, target)
        created = True
        replacements = {
            "__APP_ID__": normalized_id,
            "__APP_NAME__": _yaml_scalar(display_name),
            "__TEMPLATE_GUARD__": "",
        }
        for path in target.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8")
            for token, value in replacements.items():
                text = text.replace(token, value)
            path.write_text(text, encoding="utf-8")
    except Exception:
        if created and target.is_dir() and target.parent == root:
            shutil.rmtree(target)
        raise
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new built-in App MCP starter package.",
    )
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--apps-root", default=None)
    parser.add_argument("--templates-root", default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        target = create_app_package(
            app_id=args.app_id,
            name=args.name,
            apps_root=args.apps_root,
            templates_root=args.templates_root,
        )
    except AppTemplateError as exc:
        print(f"Error: {exc}")
        return 2
    print(f"Created App MCP package: {target}")
    print("Next files to edit:")
    print(f"  {target / 'manifest.yaml'}")
    print(f"  {target / 'adapter.py'}")
    print(f"  {target / 'tests' / 'test_adapter.py'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
