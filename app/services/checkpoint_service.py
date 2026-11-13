from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.engine.checkpoints import (
    CHECKPOINT_TYPE,
    build_checkpoint,
    find_checkpoint,
    is_checkpoint,
    strip_checkpoints,
)
from app.security.redaction import redact_payload

CHECKPOINT_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class CheckpointState:
    schema_version: int
    next_step_index: int
    next_step_id: int | None
    completed_step_ids: list[int] = field(default_factory=list)
    completed_idempotency_keys: list[str] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    login_state: str = "unknown"
    host_id: int | None = None
    host_environment: str | None = None
    host_bound: bool = False
    host_affinity_hash: str | None = None
    browser_profile_id: int | None = None
    human_task_id: int | None = None
    verification: dict[str, Any] = field(default_factory=dict)
    visual_selection: dict[str, Any] = field(default_factory=dict)
    created_at: str | None = None


class CheckpointService:
    def build(
        self,
        execution: Any,
        result: Any,
        *,
        next_step_index: int,
        next_step_id: int | None = None,
        human_task_id: int | None = None,
        verification: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> dict[str, Any]:
        details = (
            dict(result.details)
            if isinstance(getattr(result, "details", None), dict)
            else {}
        )
        failed_step = (
            details.get("failed_step")
            if isinstance(details.get("failed_step"), dict)
            else {}
        )
        step_logs = strip_checkpoints(
            getattr(result, "step_logs", None)
            or getattr(execution, "step_logs", None)
        )
        completed_step_ids = _completed_step_ids(step_logs)
        keys = _normalized_keys(
            details.get("completed_idempotency_keys")
        )
        for key in _completed_keys(step_logs):
            if key not in keys:
                keys.append(key)
        variables = details.get("extracted")
        if not isinstance(variables, dict):
            variables = details.get("variables")
        if not isinstance(variables, dict):
            variables = {}
        artifacts = getattr(result, "artifacts", None)
        if not isinstance(artifacts, list):
            artifacts = getattr(execution, "artifacts", None)
        if not isinstance(artifacts, list):
            artifacts = []
        login_state = str(
            details.get("login_state")
            or (
                "login_required"
                if failed_step.get("failure_category")
                == "auth_required"
                else "unknown"
            )
        )
        browser_profile_id = _optional_int(
            details.get("browser_profile_id")
            or getattr(execution, "browser_profile_id", None)
        )
        checkpoint = {
            "type": CHECKPOINT_TYPE,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "execution_id": _optional_int(
                getattr(execution, "id", None)
            ),
            "workflow_version_id": _optional_int(
                getattr(execution, "workflow_version_id", None)
            ),
            "step_index": max(int(next_step_index), 0),
            "next_step_index": max(int(next_step_index), 0),
            "next_step_id": _optional_int(
                next_step_id or failed_step.get("step_id")
            ),
            "completed_step_ids": completed_step_ids,
            "completed_idempotency_keys": keys,
            "variables": variables,
            "artifacts": artifacts,
            "login_state": login_state,
            "host_id": _optional_int(
                getattr(execution, "host_id", None)
                or details.get("host_id")
            ),
            "host_environment": str(
                getattr(execution, "host_environment", None)
                or details.get("host_environment")
                or ""
            ).strip()
            or None,
            "host_bound": bool(
                getattr(execution, "host_bound", False)
                or details.get("host_bound")
            ),
            "host_affinity_hash": (
                str(
                    getattr(execution, "host_affinity_hash", None)
                    or details.get("host_affinity_hash")
                ).strip()
                or None
            ),
            "browser_profile_id": browser_profile_id,
            "human_task_id": _optional_int(human_task_id),
            "verification": dict(verification or {}),
            "visual_selection": dict(
                details.get("visual_selection") or {}
            ),
            "status": "awaiting_user",
            "action": "checkpoint",
            "created_at": (
                created_at or datetime.utcnow()
            ).isoformat(),
        }
        return redact_payload(checkpoint)

    def merge_pause(
        self,
        existing: dict[str, Any] | None,
        progress: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(progress, dict) or not is_checkpoint(progress):
            if isinstance(existing, dict) and is_checkpoint(existing):
                return dict(existing)
            return build_checkpoint(next_step_index=0)
        if not isinstance(existing, dict) or not is_checkpoint(existing):
            return dict(progress)

        old_index = _step_index(existing)
        new_index = _step_index(progress)
        newer = progress if new_index >= old_index else existing
        older = existing if newer is progress else progress
        merged = {
            **older,
            **newer,
            "type": CHECKPOINT_TYPE,
            "action": "checkpoint",
            "schema_version": max(
                int(existing.get("schema_version") or 1),
                int(progress.get("schema_version") or 1),
            ),
            "step_index": max(old_index, new_index),
            "next_step_index": max(old_index, new_index),
            "completed_step_ids": _merge_ints(
                existing.get("completed_step_ids"),
                progress.get("completed_step_ids"),
            ),
            "completed_idempotency_keys": _merge_strings(
                existing.get("completed_idempotency_keys"),
                progress.get("completed_idempotency_keys"),
            ),
        }
        for key in ("variables", "verification"):
            value = (
                progress.get(key)
                if isinstance(progress.get(key), dict)
                and progress.get(key)
                else existing.get(key)
            )
            merged[key] = dict(value) if isinstance(value, dict) else {}
        for key in ("artifacts",):
            value = (
                progress.get(key)
                if isinstance(progress.get(key), list)
                and progress.get(key)
                else existing.get(key)
            )
            merged[key] = list(value) if isinstance(value, list) else []
        for key in (
            "next_step_id",
            "login_state",
            "host_id",
            "host_environment",
            "host_bound",
            "host_affinity_hash",
            "browser_profile_id",
            "human_task_id",
        ):
            if newer.get(key) is not None:
                merged[key] = newer[key]
        return redact_payload(merged)


def read_checkpoint(checkpoint: dict[str, Any]) -> CheckpointState:
    if not isinstance(checkpoint, dict) or not is_checkpoint(checkpoint):
        raise ValueError("checkpoint payload is invalid")
    schema_version = _optional_int(
        checkpoint.get("schema_version")
    ) or 1
    variables = checkpoint.get("variables")
    artifacts = checkpoint.get("artifacts")
    verification = checkpoint.get("verification")
    visual_selection = checkpoint.get("visual_selection")
    return CheckpointState(
        schema_version=schema_version,
        next_step_index=_step_index(checkpoint),
        next_step_id=_optional_int(checkpoint.get("next_step_id")),
        completed_step_ids=_normalized_ints(
            checkpoint.get("completed_step_ids")
        ),
        completed_idempotency_keys=_normalized_keys(
            checkpoint.get("completed_idempotency_keys")
        ),
        variables=dict(variables) if isinstance(variables, dict) else {},
        artifacts=[
            dict(item)
            for item in artifacts
            if isinstance(item, dict)
        ]
        if isinstance(artifacts, list)
        else [],
        login_state=str(checkpoint.get("login_state") or "unknown"),
        host_id=_optional_int(checkpoint.get("host_id")),
        host_environment=(
            str(checkpoint.get("host_environment"))
            if checkpoint.get("host_environment")
            else None
        ),
        host_bound=bool(checkpoint.get("host_bound", False)),
        host_affinity_hash=(
            str(checkpoint.get("host_affinity_hash"))
            if checkpoint.get("host_affinity_hash")
            else None
        ),
        browser_profile_id=_optional_int(
            checkpoint.get("browser_profile_id")
        ),
        human_task_id=_optional_int(
            checkpoint.get("human_task_id")
        ),
        verification=(
            dict(verification)
            if isinstance(verification, dict)
            else {}
        ),
        visual_selection=(
            dict(visual_selection)
            if isinstance(visual_selection, dict)
            else {}
        ),
        created_at=(
            str(checkpoint.get("created_at"))
            if checkpoint.get("created_at")
            else None
        ),
    )


def checkpoint_resume_context(
    checkpoint: dict[str, Any],
    step_logs: list[Any] | None,
) -> dict[str, Any]:
    state = read_checkpoint(checkpoint)
    return {
        "resume_from_step": state.next_step_index,
        "next_step_index": state.next_step_index,
        "next_step_id": state.next_step_id,
        "completed_step_ids": list(state.completed_step_ids),
        "completed_idempotency_keys": list(
            state.completed_idempotency_keys
        ),
        "variables": dict(state.variables),
        "artifacts": list(state.artifacts),
        "login_state": state.login_state,
        "host_id": state.host_id,
        "host_environment": state.host_environment,
        "host_bound": state.host_bound,
        "host_affinity_hash": state.host_affinity_hash,
        "browser_profile_id": state.browser_profile_id,
        "human_task_id": state.human_task_id,
        "verification": dict(state.verification),
        "visual_selection": dict(state.visual_selection),
        "initial_step_logs": strip_checkpoints(step_logs),
    }


def _completed_step_ids(step_logs: list[dict[str, Any]]) -> list[int]:
    values: list[int] = []
    for entry in step_logs:
        if str(entry.get("status") or "") != "success":
            continue
        step_id = _optional_int(entry.get("step_id"))
        if step_id is not None and step_id not in values:
            values.append(step_id)
    return values


def _completed_keys(step_logs: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for entry in step_logs:
        if str(entry.get("status") or "") != "success":
            continue
        key = str(entry.get("idempotency_key") or "").strip()
        if key and key not in values:
            values.append(key)
    return values


def _step_index(checkpoint: dict[str, Any]) -> int:
    return max(
        _optional_int(
            checkpoint.get("next_step_index")
            or checkpoint.get("step_index")
        )
        or 0,
        0,
    )


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_ints(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[int] = []
    for item in value:
        parsed = _optional_int(item)
        if parsed is not None and parsed not in result:
            result.append(parsed)
    return result


def _normalized_keys(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _merge_ints(*values: Any) -> list[int]:
    result: list[int] = []
    for value in values:
        for item in _normalized_ints(value):
            if item not in result:
                result.append(item)
    return result


def _merge_strings(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in _normalized_keys(value):
            if item not in result:
                result.append(item)
    return result


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointService",
    "CheckpointState",
    "checkpoint_resume_context",
    "read_checkpoint",
]
