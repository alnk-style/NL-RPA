"""依赖锁定文件与启动自检。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.security import dependency_check
from app.security.dependency_check import (
    DependencyReport,
    check_dependencies,
    parse_lock,
    project_lock_path,
    run_startup_check,
)


def test_parse_lock_ignores_comments_and_reports_errors() -> None:
    pins, errors = parse_lock(
        "\n".join(
            [
                "# comment",
                "fastapi==0.115.12",
                "SQLAlchemy == 2.0.41",
                "not-a-pin",
            ]
        )
    )
    assert pins == {"fastapi": "0.115.12", "sqlalchemy": "2.0.41"}
    assert errors == ["not-a-pin"]


def test_check_dependencies_detects_mismatch_and_missing() -> None:
    text = "alpha==1.0\nbeta==2.0\ngamma==3.0\n"
    lock = dependency_check.parse_lock(text)[0]

    def provider(name: str) -> str | None:
        return {
            "alpha": "1.0",
            "beta": "2.1",
            "gamma": None,
        }[name]

    # 直接用 parse 结果构造一个临时锁定文件。
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "requirements.lock"
        path.write_text(text, encoding="utf-8")
        report = check_dependencies(path, version_provider=provider)

    assert lock["alpha"] == "1.0"
    assert not report.ok
    assert report.mismatched == [
        {"name": "beta", "expected": "2.0", "actual": "2.1"}
    ]
    assert report.missing == [{"name": "gamma", "expected": "3.0"}]


def test_project_lock_file_matches_environment() -> None:
    lock_path = project_lock_path()
    assert lock_path.is_file()
    content = lock_path.read_text(encoding="utf-8")
    assert "fastapi==" in content
    assert "asyncpg==" in content
    report = check_dependencies()
    assert report.ok, report.to_payload()
    assert report.checked > 50


def test_run_startup_check_modes(monkeypatch) -> None:
    disabled = run_startup_check(
        SimpleNamespace(
            STARTUP_SELF_CHECK=False,
            STARTUP_SELF_CHECK_STRICT=False,
        )
    )
    assert disabled.ok

    failing = DependencyReport(
        ok=False,
        checked=1,
        mismatched=[{"name": "x", "expected": "1", "actual": "2"}],
    )
    monkeypatch.setattr(
        dependency_check,
        "check_dependencies",
        lambda: failing,
    )
    assert not run_startup_check(
        SimpleNamespace(
            STARTUP_SELF_CHECK=True,
            STARTUP_SELF_CHECK_STRICT=False,
        )
    ).ok
    with pytest.raises(RuntimeError):
        run_startup_check(
            SimpleNamespace(
                STARTUP_SELF_CHECK=True,
                STARTUP_SELF_CHECK_STRICT=True,
            )
        )


def test_run_startup_check_skips_frozen_bundle(monkeypatch) -> None:
    monkeypatch.setattr(
        dependency_check.sys,
        "frozen",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        dependency_check,
        "check_dependencies",
        lambda: pytest.fail("frozen 包不应执行依赖比对"),
    )
    report = run_startup_check(
        SimpleNamespace(
            STARTUP_SELF_CHECK=True,
            STARTUP_SELF_CHECK_STRICT=True,
        )
    )
    assert report.ok
    assert report.checked == 0
