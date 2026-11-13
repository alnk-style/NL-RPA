from __future__ import annotations

import pytest

from app.engine.dag import (
    ExecutionPlanError,
    evaluate_condition,
    merge_dag_definition,
    normalize_execution_plan,
)


def test_normalize_execution_plan_reads_executor_and_dependencies() -> None:
    steps = normalize_execution_plan(
        {
            "steps": [
                {"step_id": 1, "executor": "web", "action": "goto"},
                {
                    "step_id": 2,
                    "executor": "desktop",
                    "action": "launch_app",
                    "depends_on": [1],
                },
                {
                    "step_id": 3,
                    "action": "click",
                    "depends_on": [2],
                },
            ]
        },
        default_executor="web",
    )

    assert [step.executor for step in steps] == [
        "web",
        "windows",
        "web",
    ]
    assert steps[1].depends_on == [1]
    assert steps[2].depends_on == [2]


def test_normalize_execution_plan_rejects_cycles() -> None:
    with pytest.raises(ExecutionPlanError, match="环"):
        normalize_execution_plan(
            {
                "steps": [
                    {"step_id": 1, "action": "a", "depends_on": [2]},
                    {"step_id": 2, "action": "b", "depends_on": [1]},
                ]
            }
        )


def test_normalize_execution_plan_rejects_unknown_dependency() -> None:
    with pytest.raises(ExecutionPlanError, match="不存在"):
        normalize_execution_plan(
            {
                "steps": [
                    {"step_id": 1, "action": "a", "depends_on": [9]},
                ]
            }
        )


def test_merge_dag_definition_fills_missing_dependencies() -> None:
    steps = merge_dag_definition(
        [
            {"step_id": 2, "action": "click"},
            {"step_id": 1, "action": "goto"},
        ],
        {
            "nodes": [
                {"id": "step-1", "kind": "step"},
                {"id": "step-2", "kind": "step"},
            ],
            "edges": [{"source": "step-1", "target": "step-2"}],
        },
    )

    by_id = {step["step_id"]: step for step in steps}
    assert by_id[1].get("depends_on") is None
    assert by_id[2]["depends_on"] == ["1"]


def test_merge_dag_definition_keeps_explicit_dependencies() -> None:
    steps = merge_dag_definition(
        [
            {
                "step_id": 1,
                "action": "goto",
                "depends_on": [],
            },
            {
                "step_id": 2,
                "action": "click",
                "depends_on": [],
            },
        ],
        {
            "nodes": [
                {"id": "step-1"},
                {"id": "step-2"},
            ],
            "edges": [{"source": "step-1", "target": "step-2"}],
        },
    )

    assert steps[1]["depends_on"] == []


def test_evaluate_variable_condition() -> None:
    condition = {
        "type": "variable",
        "name": "status",
        "operator": "equals",
        "value": "done",
    }

    assert evaluate_condition(condition, variables={"status": "done"})
    assert not evaluate_condition(
        condition,
        variables={"status": "pending"},
    )
    assert not evaluate_condition(condition, variables={})


def test_evaluate_step_status_and_composite_conditions() -> None:
    statuses = {1: "success", 2: "failed"}
    assert evaluate_condition(
        {"type": "step_success", "step_id": 1},
        statuses=statuses,
    )
    assert evaluate_condition(
        {"type": "step_failed", "step_id": 2},
        statuses=statuses,
    )
    assert evaluate_condition(
        {
            "type": "all",
            "conditions": [
                {"type": "step_success", "step_id": 1},
                {"type": "variable", "name": "ready", "equals": True},
            ],
        },
        variables={"ready": True},
        statuses=statuses,
    )
    assert not evaluate_condition(
        {"type": "unknown_condition"},
        statuses=statuses,
    )


def test_condition_from_params_only_accepts_structured_objects() -> None:
    from app.engine.dag import step_condition

    assert step_condition(
        {"params": {"condition": "window_appears"}}
    ) is None
    assert step_condition(
        {"params": {"condition": {"type": "never"}}}
    ) == {"type": "never"}
    assert step_condition({"enabled": False}) == {"type": "never"}
