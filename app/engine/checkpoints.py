"""执行检查点。

检查点复用 ``ExecutionLog.step_logs`` JSON 字段保存，避免新增数据库
列；条目带 ``type=checkpoint`` 标识，恢复执行时从最近一个检查点读取
下一步序号和已完成的幂等标记。
"""

from __future__ import annotations

import time
from typing import Any

CHECKPOINT_TYPE = "checkpoint"


def build_checkpoint(
    *,
    next_step_index: int,
    failed_step: dict[str, Any] | None = None,
    completed_idempotency_keys: list[str] | None = None,
) -> dict[str, Any]:
    index = _non_negative_int(next_step_index)
    now = time.perf_counter()
    checkpoint: dict[str, Any] = {
        "type": CHECKPOINT_TYPE,
        "action": "checkpoint",
        "status": "awaiting_user",
        "step_index": index,
        "next_step_index": index,
        "completed_idempotency_keys": _normalize_keys(
            completed_idempotency_keys
        ),
        "started_at": now,
        "finished_at": now,
    }
    if isinstance(failed_step, dict):
        checkpoint["step_id"] = failed_step.get("step_id")
        checkpoint["failure_category"] = failed_step.get(
            "failure_category"
        )
        checkpoint["error"] = failed_step.get("error")
    return checkpoint


def is_checkpoint(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and entry.get("type") == CHECKPOINT_TYPE
    )


def find_checkpoint(
    step_logs: list[Any] | None,
) -> dict[str, Any] | None:
    for entry in reversed(list(step_logs or [])):
        if is_checkpoint(entry):
            return dict(entry)
    return None


def strip_checkpoints(
    step_logs: list[Any] | None,
) -> list[dict[str, Any]]:
    return [
        dict(entry)
        for entry in (step_logs or [])
        if isinstance(entry, dict) and not is_checkpoint(entry)
    ]


def _normalize_keys(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    normalized: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "CHECKPOINT_TYPE",
    "build_checkpoint",
    "find_checkpoint",
    "is_checkpoint",
    "strip_checkpoints",
]
