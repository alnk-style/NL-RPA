from __future__ import annotations

from pathlib import Path


def _python_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_runtime_and_action_modules_do_not_import_planning_agents() -> None:
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for folder in (root / "app" / "engine", root / "app" / "actions"):
        for path in _python_files(folder):
            text = path.read_text(encoding="utf-8")
            if "from app.agents" in text or "import app.agents" in text:
                violations.append(str(path.relative_to(root)))
    assert violations == []


def test_desktop_agent_does_not_import_workflow_authoring() -> None:
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for path in _python_files(root / "desktop_agent"):
        text = path.read_text(encoding="utf-8")
        if "workflow_authoring" in text:
            violations.append(str(path.relative_to(root)))
    assert violations == []
