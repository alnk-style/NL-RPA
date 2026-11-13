"""步骤日志的共享解析规则（指标与执行管理共用）。"""

from __future__ import annotations

from typing import Any, Iterable

from app.protocols import FailureCategory, classify_failure

CHECKPOINT_TYPE = "checkpoint"
FAILED_STEP_STATUSES = frozenset({"failed", "error", "failure"})
SUCCESS_STEP_STATUSES = frozenset({"success", "repaired", "completed"})


def iter_steps(
    step_logs: Iterable[Any] | None,
) -> Iterable[dict[str, Any]]:
    for entry in step_logs or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == CHECKPOINT_TYPE:
            continue
        yield entry


def step_status(entry: dict[str, Any]) -> str:
    return str(entry.get("status") or "").strip().lower()


def step_repair_attempted(entry: dict[str, Any]) -> bool:
    if step_status(entry) == "repaired":
        return True
    if entry.get("repaired_step") or entry.get("repaired_from"):
        return True
    for key in ("repair_attempts", "repair_rounds", "repairs"):
        raw = entry.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            return True
    attempts = entry.get("attempts")
    if (
        isinstance(attempts, (int, float))
        and attempts > 1
        and entry.get("failure_category")
    ):
        return True
    return False


def entry_failure_category(entry: dict[str, Any]) -> FailureCategory:
    raw = entry.get("failure_category")
    if isinstance(raw, str) and raw.strip():
        try:
            return FailureCategory(raw.strip())
        except ValueError:
            pass
    return classify_failure(entry.get("error"))


def summarize_steps(step_logs: Iterable[Any] | None) -> dict[str, Any]:
    """步骤级统计：失败分类、定位失败、修复尝试与成功。"""

    summary: dict[str, Any] = {
        "step_count": 0,
        "failed_steps": 0,
        "locator_failures": 0,
        "repairs_attempted": 0,
        "repairs_succeeded": 0,
        "failure_categories": {},
    }
    locator_categories = {
        FailureCategory.LOCATOR_NOT_FOUND,
        FailureCategory.LOCATOR_NOT_UNIQUE,
        FailureCategory.ELEMENT_NOT_VISIBLE,
        FailureCategory.ELEMENT_DISABLED,
    }
    for entry in iter_steps(step_logs):
        summary["step_count"] += 1
        status = step_status(entry)
        category = entry_failure_category(entry)
        if status in FAILED_STEP_STATUSES:
            summary["failed_steps"] += 1
            summary["failure_categories"][category.value] = (
                summary["failure_categories"].get(category.value, 0) + 1
            )
        # 修复成功的步骤保留原始 failure_category，同样计入定位失败率，
        # 否则“先失败后修复”的场景会被指标低估。
        if category in locator_categories and (
            status in FAILED_STEP_STATUSES
            or step_repair_attempted(entry)
            or bool(entry.get("failure_category"))
        ):
            summary["locator_failures"] += 1
        if step_repair_attempted(entry):
            summary["repairs_attempted"] += 1
            if status in SUCCESS_STEP_STATUSES:
                summary["repairs_succeeded"] += 1
    return summary


__all__ = [
    "CHECKPOINT_TYPE",
    "FAILED_STEP_STATUSES",
    "SUCCESS_STEP_STATUSES",
    "entry_failure_category",
    "iter_steps",
    "step_repair_attempted",
    "step_status",
    "summarize_steps",
]
