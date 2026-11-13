from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.services.checkpoint_service import (
    CheckpointService,
    read_checkpoint,
)


def test_checkpoint_v3_preserves_resume_state_and_redacts_secrets() -> None:
    execution = SimpleNamespace(
        id=88,
        workflow_version_id=101,
        host_id=7,
        browser_profile_id=3,
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "status": "success",
                "idempotency_key": "login-check",
            }
        ],
    )
    result = SimpleNamespace(
        step_logs=[
            {
                "step_index": 0,
                "step_id": 1,
                "status": "success",
                "idempotency_key": "login-check",
            },
            {
                "step_index": 1,
                "step_id": 2,
                "status": "failed",
                "failure_category": "auth_required",
            },
        ],
        details={
            "failed_step": {
                "step_index": 1,
                "step_id": 2,
                "failure_category": "auth_required",
            },
            "completed_idempotency_keys": ["login-check"],
            "extracted": {
                "report": [{"id": 1}],
                "password": "must-not-persist",
                "token": "must-not-persist-either",
            },
            "login_state": "login_required",
            "browser_profile_id": 3,
        },
        artifacts=[
            {
                "path": "logs/execution/report.xlsx",
                "sha256": "abc",
            }
        ],
    )

    checkpoint = CheckpointService().build(
        execution,
        result,
        next_step_index=1,
        next_step_id=2,
        human_task_id=31,
        verification={"type": "web_logged_in"},
    )
    parsed = read_checkpoint(checkpoint)

    assert checkpoint["schema_version"] == 3
    assert checkpoint["execution_id"] == 88
    assert checkpoint["workflow_version_id"] == 101
    assert checkpoint["host_id"] == 7
    assert checkpoint["browser_profile_id"] == 3
    assert checkpoint["human_task_id"] == 31
    assert checkpoint["next_step_id"] == 2
    assert checkpoint["completed_step_ids"] == [1]
    assert checkpoint["completed_idempotency_keys"] == ["login-check"]
    assert checkpoint["variables"]["report"] == [{"id": 1}]
    assert checkpoint["variables"]["password"] == "***"
    assert checkpoint["variables"]["token"] == "***"
    assert "must-not-persist" not in str(checkpoint)
    assert checkpoint["artifacts"][0]["sha256"] == "abc"
    assert parsed.next_step_index == 1
    assert parsed.next_step_id == 2
    assert parsed.login_state == "login_required"
    assert parsed.completed_step_ids == [1]


def test_checkpoint_service_reads_legacy_v1_payload() -> None:
    legacy = {
        "type": "checkpoint",
        "action": "checkpoint",
        "status": "awaiting_user",
        "next_step_index": 3,
        "completed_idempotency_keys": ["open-page"],
    }

    parsed = read_checkpoint(legacy)

    assert parsed.schema_version == 1
    assert parsed.next_step_index == 3
    assert parsed.completed_idempotency_keys == ["open-page"]
    assert parsed.completed_step_ids == []
    assert parsed.variables == {}
    assert parsed.artifacts == []
    assert parsed.login_state == "unknown"


def test_checkpoint_service_merges_pause_progress_without_losing_existing() -> None:
    existing = {
        "type": "checkpoint",
        "schema_version": 2,
        "next_step_index": 2,
        "next_step_id": 3,
        "completed_step_ids": [1, 2],
        "completed_idempotency_keys": ["a", "b"],
        "variables": {"rows": [1]},
        "artifacts": [],
        "login_state": "ready",
        "host_id": 7,
        "browser_profile_id": 3,
        "created_at": "2026-11-11T10:00:00",
    }
    progress = {
        "type": "checkpoint",
        "schema_version": 2,
        "next_step_index": 4,
        "next_step_id": 5,
        "completed_step_ids": [3, 4],
        "completed_idempotency_keys": ["c"],
        "variables": {"rows": [1, 2]},
        "artifacts": [{"path": "report.xlsx"}],
        "login_state": "ready",
        "host_id": 7,
        "browser_profile_id": 3,
        "created_at": "2026-11-11T10:05:00",
    }

    merged = CheckpointService().merge_pause(existing, progress)

    assert merged["next_step_index"] == 4
    assert merged["next_step_id"] == 5
    assert merged["completed_step_ids"] == [1, 2, 3, 4]
    assert merged["completed_idempotency_keys"] == ["a", "b", "c"]
    assert merged["variables"] == {"rows": [1, 2]}
    assert merged["artifacts"] == [{"path": "report.xlsx"}]
