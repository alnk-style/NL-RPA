from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.artifacts import (  # noqa: E402
    deliver_artifacts,
    deliver_execution_artifacts,
    parse_export_dirs,
    resolve_within_roots,
    sanitize_artifact_name,
)


def _artifact(tmp_path: Path, name: str = "report.csv") -> Path:
    artifact_dir = tmp_path / "logs" / "exec1" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / name
    path.write_text("name,count\nalice,2\n", encoding="utf-8-sig")
    return path


def test_parse_export_dirs_splits_and_deduplicates(tmp_path: Path) -> None:
    first = tmp_path / "one"
    second = tmp_path / "two"

    parsed = parse_export_dirs(f"{first};{second}\n{first}")

    assert parsed == [first.resolve(), second.resolve()]
    assert parse_export_dirs("") == []
    assert parse_export_dirs(None) == []


def test_resolve_within_roots_only_matches_descendants(
    tmp_path: Path,
) -> None:
    root = tmp_path / "documents"
    root.mkdir()

    assert resolve_within_roots(root / "a" / "b.csv", [root]) == root
    assert resolve_within_roots(root, [root]) == root
    assert resolve_within_roots(tmp_path / "elsewhere" / "b.csv", [root]) is None


def test_sanitize_artifact_name_strips_directory_and_invalid_chars() -> None:
    assert sanitize_artifact_name("D:\\out\\B站+2026-10-01.csv") == (
        "B站+2026-10-01.csv"
    )
    assert sanitize_artifact_name("../../evil?.csv") == "evil_.csv"
    assert sanitize_artifact_name("") == "artifact"


def test_deliver_artifacts_copies_into_allowed_directory(
    tmp_path: Path,
) -> None:
    source = _artifact(tmp_path)
    allowed = tmp_path / "documents"
    allowed.mkdir()
    target = allowed / "B站+2026-10-01.csv"

    delivered = deliver_artifacts(
        [
            {
                "name": source.name,
                "path": str(source),
                "requested_path": str(target),
            }
        ],
        [allowed],
    )

    assert delivered[0]["delivered"] is True
    assert delivered[0]["delivered_path"] == str(target)
    assert target.exists()


def test_deliver_artifacts_keeps_files_outside_allowed_dirs(
    tmp_path: Path,
) -> None:
    source = _artifact(tmp_path)
    allowed = tmp_path / "documents"
    allowed.mkdir()
    blocked = tmp_path / "elsewhere" / "report.csv"

    delivered = deliver_artifacts(
        [
            {
                "name": source.name,
                "path": str(source),
                "requested_path": str(blocked),
            }
        ],
        [allowed],
    )

    assert delivered[0]["delivered"] is False
    assert delivered[0]["delivered_path"] is None
    assert "白名单" in str(delivered[0]["error"])
    assert not blocked.exists()


def test_deliver_artifacts_handles_missing_target(tmp_path: Path) -> None:
    source = _artifact(tmp_path, name="plain.csv")

    delivered = deliver_artifacts(
        [
            {
                "name": source.name,
                "path": str(source),
                "requested_path": None,
            }
        ],
        [tmp_path],
    )

    assert delivered[0]["delivered"] is False
    assert "执行记录" in str(delivered[0]["error"])


def test_deliver_artifacts_preserves_existing_source_error(
    tmp_path: Path,
) -> None:
    delivered = deliver_artifacts(
        [
            {
                "name": "missing.txt",
                "path": str(tmp_path / "missing.txt"),
                "requested_path": None,
                "error": "MCP artifact file does not exist.",
            }
        ],
        [tmp_path],
    )

    assert delivered[0]["delivered"] is False
    assert delivered[0]["error"] == "MCP artifact file does not exist."


@pytest.mark.asyncio
async def test_deliver_execution_artifacts_uses_agent_allowed_dirs(
    db_session,
    tmp_path: Path,
) -> None:
    from app.models.agent import DesktopAgent
    from app.models.user import User
    from app.models.workflow import Workflow

    user = User(
        username="artifact-user",
        email="artifact@example.com",
        hashed_password="hashed",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    allowed = tmp_path / "documents"
    allowed.mkdir()
    db_session.add(
        DesktopAgent(
            user_id=user.id,
            name="DESKTOP-TEST",
            version="0.1.0",
            token_hash="token-hash",
            allowed_doc_dirs=[str(allowed)],
        )
    )
    workflow = Workflow(
        user_id=user.id,
        name="导出流程",
        natural_language_input="把结果导出到文档目录",
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)

    source = _artifact(tmp_path, name="result.csv")
    target = allowed / "result.csv"

    delivered = await deliver_execution_artifacts(
        db_session,
        workflow,
        [
            {
                "name": source.name,
                "path": str(source),
                "requested_path": str(target),
            }
        ],
    )

    assert delivered[0]["delivered"] is True
    assert target.exists()


@pytest.mark.asyncio
async def test_deliver_execution_artifacts_uses_saved_export_directories(
    db_session,
    tmp_path: Path,
) -> None:
    from app.models.app_setting import AppSetting
    from app.models.user import User
    from app.models.workflow import Workflow

    user = User(
        username="export-setting-user",
        email="export-setting@example.com",
        hashed_password="hashed",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    allowed = tmp_path / "saved-exports"
    allowed.mkdir()
    db_session.add(
        AppSetting(
            user_id=user.id,
            key="export_directories",
            value={"directories": [str(allowed)]},
        )
    )
    workflow = Workflow(
        user_id=user.id,
        name="saved export",
        natural_language_input="export",
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)

    source = _artifact(tmp_path, name="saved.csv")
    target = allowed / "saved.csv"
    delivered = await deliver_execution_artifacts(
        db_session,
        workflow,
        [
            {
                "name": source.name,
                "path": str(source),
                "requested_path": str(target),
            }
        ],
    )

    assert delivered[0]["delivered"] is True
    assert target.exists()
