from __future__ import annotations

from pathlib import Path

from desktop_agent.action_validator import ActionValidator
from desktop_agent.config import AgentConfig


def _config(
    tmp_path: Path,
    *,
    allowed_apps: list[str] | None = None,
    allowed_doc_dirs: list[str] | None = None,
) -> AgentConfig:
    return AgentConfig(
        server_url="http://127.0.0.1:8000",
        agent_id=1,
        token="test-token",
        allowed_apps=allowed_apps or [],
        allowed_doc_dirs=allowed_doc_dirs or [str(tmp_path)],
    )


def test_validate_allowed_app(tmp_path: Path) -> None:
    config = _config(tmp_path, allowed_apps=["notepad.exe"])

    result = ActionValidator.validate(
        {
            "type": "launch_app",
            "params": {"path": r"C:\Windows\System32\notepad.exe"},
        },
        config,
    )

    assert result.allowed is True
    assert result.reason is None


def test_validate_blocked_app(tmp_path: Path) -> None:
    config = _config(tmp_path, allowed_apps=["notepad.exe"])

    result = ActionValidator.validate(
        {
            "type": "launch_app",
            "params": {"path": r"C:\Windows\System32\powershell.exe"},
        },
        config,
    )

    assert result.allowed is False
    assert result.reason is not None
    assert "powershell.exe" in result.reason


def test_validate_wildcard_all(tmp_path: Path) -> None:
    config = _config(tmp_path, allowed_apps=["*.exe"])

    result = ActionValidator.validate(
        {
            "type": "launch_app",
            "params": {"path": r"C:\Tools\custom-tool.exe"},
        },
        config,
    )

    assert result.allowed is True


def test_validate_case_insensitive(tmp_path: Path) -> None:
    config = _config(tmp_path, allowed_apps=["notepad.exe"])

    result = ActionValidator.validate(
        {
            "type": "launch_app",
            "params": {"path": r"C:\Windows\System32\Notepad.EXE"},
        },
        config,
    )

    assert result.allowed is True


def test_validate_allowed_doc_dir(tmp_path: Path) -> None:
    document = tmp_path / "report.docx"
    config = _config(tmp_path, allowed_doc_dirs=[str(tmp_path)])

    result = ActionValidator.validate(
        {
            "type": "open_document",
            "params": {"path": str(document)},
        },
        config,
    )

    assert result.allowed is True


def test_validate_blocked_doc_dir(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "allowed"
    blocked_dir = tmp_path / "blocked"
    config = _config(tmp_path, allowed_doc_dirs=[str(allowed_dir)])

    result = ActionValidator.validate(
        {
            "type": "open_document",
            "params": {"path": str(blocked_dir / "report.docx")},
        },
        config,
    )

    assert result.allowed is False
    assert result.reason is not None


def test_validate_subdirectory(tmp_path: Path) -> None:
    nested = tmp_path / "finance" / "2026" / "report.pdf"
    config = _config(tmp_path, allowed_doc_dirs=[str(tmp_path)])

    result = ActionValidator.validate(
        {
            "type": "export_pdf",
            "params": {"path": str(nested)},
        },
        config,
    )

    assert result.allowed is True


def test_validate_screenshot_path_inside_allowlist(tmp_path: Path) -> None:
    pictures = tmp_path / "pictures"
    pictures.mkdir()
    config = _config(tmp_path, allowed_doc_dirs=[str(tmp_path)])

    result = ActionValidator.validate(
        {
            "type": "screenshot",
            "params": {"path": str(pictures / "2026-09-24.png")},
        },
        config,
    )

    assert result.allowed is True


def test_validate_screenshot_path_outside_allowlist(tmp_path: Path) -> None:
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    config = _config(tmp_path, allowed_doc_dirs=[str(allowed_dir)])

    result = ActionValidator.validate(
        {
            "type": "screenshot",
            "params": {"path": str(blocked_dir / "shot.png")},
        },
        config,
    )

    assert result.allowed is False
    assert result.reason is not None
    assert "白名单" in result.reason


def test_validate_screenshot_missing_directory(tmp_path: Path) -> None:
    config = _config(tmp_path, allowed_doc_dirs=[str(tmp_path)])

    result = ActionValidator.validate(
        {
            "type": "screenshot",
            "params": {"path": str(tmp_path / "missing" / "shot.png")},
        },
        config,
    )

    assert result.allowed is False
    assert result.reason is not None
    assert "不存在" in result.reason


def test_validate_screenshot_without_path_uses_log_dir(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, allowed_doc_dirs=[])

    result = ActionValidator.validate(
        {"type": "screenshot", "params": {"name": "step"}},
        config,
    )

    assert result.allowed is True


def test_validate_unknown_action_type(tmp_path: Path) -> None:
    config = _config(tmp_path)

    result = ActionValidator.validate(
        {
            "type": "run_powershell",
            "params": {"command": "Get-Process"},
        },
        config,
    )

    assert result.allowed is False
    assert result.reason is not None
    assert "run_powershell" in result.reason


def test_validate_non_file_actions_pass(tmp_path: Path) -> None:
    config = _config(tmp_path)

    for action_type in ("wait", "screenshot"):
        result = ActionValidator.validate(
            {"type": action_type, "params": {}},
            config,
        )
        assert result.allowed is True
        assert result.reason is None
