from __future__ import annotations

import asyncio

import pytest

from app.engine.task_runtime import (
    RuntimeState,
    StepResult,
    TaskRuntime,
    evaluate_postconditions,
    execute_foreach,
    resolve_template,
)
from app.actions.registry import ActionRegistry


def test_resolve_template_preserves_nested_values() -> None:
    variables = {
        "lyrics_lines": ["第一句", "第二句"],
        "paths": {"lyrics": r"D:\Administrator\Documents\lyrics.docx"},
    }

    assert resolve_template("${lyrics_lines}", variables) == [
        "第一句",
        "第二句",
    ]
    assert resolve_template("${paths.lyrics}", variables).endswith(
        ".docx"
    )


def test_resolve_template_rejects_unknown_variable() -> None:
    with pytest.raises(ValueError, match="missing"):
        resolve_template("${missing}", {})


def test_step_result_maps_to_result_variable() -> None:
    result = StepResult(
        success=True,
        message="copied",
        text="第一句\n第二句",
    )

    assert result.result_value() == "第一句\n第二句"


def test_task_runtime_assigns_result_var_after_step() -> None:
    async def scenario() -> dict:
        async def execute_step(step, state):
            assert step["params"]["content"] == "hello"
            return StepResult(
                success=True,
                message="transformed",
                value="HELLO",
            )

        runtime = TaskRuntime(step_executor=execute_step)
        result = await runtime.execute(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "transform",
                        "action": "uppercase",
                        "params": {"content": "${source}"},
                        "result_var": "upper",
                    }
                ]
            },
            {"variables": {"source": "hello"}},
        )
        return {
            "success": result.success,
            "variables": result.variables,
        }

    outcome = asyncio.run(scenario())

    assert outcome["success"] is True
    assert outcome["variables"]["upper"] == "HELLO"


def test_task_runtime_resolves_postcondition_after_result_var_binding() -> None:
    async def scenario() -> dict:
        async def execute_step(step, state):
            return StepResult(
                success=True,
                message="lyrics fetched",
                text="[00:00.00]first line",
            )

        runtime = TaskRuntime(step_executor=execute_step)
        result = await runtime.execute(
            {
                "variables": [
                    {"name": "lyrics_text", "default": ""},
                    {"name": "query", "default": "search term"},
                ],
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "windows",
                        "action": "fetch_lyrics",
                        "result_var": "lyrics_text",
                        "params": {},
                        "postconditions": [
                            {
                                "type": "text_not_equals",
                                "value": "${lyrics_text}",
                                "other": "${query}",
                            }
                        ],
                    }
                ],
            }
        )
        return {
            "success": result.success,
            "error": result.error,
            "lyrics_text": result.variables.get("lyrics_text"),
        }

    outcome = asyncio.run(scenario())

    assert outcome["success"] is True
    assert outcome["error"] is None
    assert outcome["lyrics_text"] == "[00:00.00]first line"


def test_text_and_list_postconditions() -> None:
    state = {
        "variables": {
            "lyrics": "第一句\n第二句",
            "rows": [1, 2],
        }
    }
    checked = evaluate_postconditions(
        [
            {"type": "text_non_empty", "value": "${lyrics}"},
            {"type": "list_min_length", "value": "${rows}", "min": 2},
        ],
        state,
    )

    assert checked.passed is True
    assert [item["type"] for item in checked.checks] == [
        "text_non_empty",
        "list_min_length",
    ]


def test_file_postcondition_rejects_empty_file(tmp_path) -> None:
    target = tmp_path / "empty.xlsx"
    target.write_text("", encoding="utf-8")

    checked = evaluate_postconditions(
        [{"type": "file_non_empty", "path": str(target)}],
        {"variables": {}},
    )

    assert checked.passed is False
    assert checked.checks[0]["passed"] is False


def test_page_and_row_postconditions() -> None:
    checked = evaluate_postconditions(
        [
            {"type": "url_contains", "value": "/detail"},
            {"type": "page_title_contains", "value": "商品"},
            {"type": "row_count_min", "value": "${rows}", "min": 2},
        ],
        {
            "variables": {"rows": [{"id": 1}, {"id": 2}]},
            "url": "https://example.com/detail/1",
            "title": "商品详情",
        },
    )

    assert checked.passed is True


def test_text_not_equals_postcondition_rejects_search_term() -> None:
    checked = evaluate_postconditions(
        [
            {
                "type": "text_not_equals",
                "value": "${lyrics}",
                "other": "第一推动力",
            }
        ],
        {"variables": {"lyrics": "第一推动力"}},
    )

    assert checked.passed is False


def test_precondition_failure_skips_step_execution() -> None:
    calls: list[int] = []

    async def execute_step(step, state):
        calls.append(1)
        return StepResult(success=True, value="ok")

    runtime = TaskRuntime(step_executor=execute_step)
    result = asyncio.run(
        runtime.execute(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "transform",
                        "action": "noop",
                        "preconditions": [
                            {"type": "text_non_empty", "value": ""}
                        ],
                    }
                ]
            }
        )
    )

    assert result.success is False
    assert result.error is not None
    assert "verification_failed" in result.error
    assert calls == []


def test_runtime_state_checkpoint_round_trip() -> None:
    state = RuntimeState(
        variables={"lyrics": "第一句"},
        results={1: StepResult(success=True, text="第一句")},
        completed_steps=[1],
        loop_cursors={"details": 2},
        artifacts=[{"path": "lyrics.docx"}],
    )

    restored = RuntimeState.from_checkpoint(state.checkpoint())

    assert restored.variables == {"lyrics": "第一句"}
    assert restored.completed_steps == [1]
    assert restored.loop_cursors == {"details": 2}
    assert restored.artifacts == [{"path": "lyrics.docx"}]


def test_step_result_preserves_human_action_for_manual_handoff() -> None:
    result = TaskRuntime._coerce_result(
        {
            "success": False,
            "message": "需要人工确认",
            "human_action": {
                "kind": "manual_confirmation",
                "instruction": "请确认窗口",
                "verification": {
                    "type": "manual_confirmation",
                },
            },
        }
    )

    assert result.human_action == {
        "kind": "manual_confirmation",
        "instruction": "请确认窗口",
        "verification": {
            "type": "manual_confirmation",
        },
    }
    restored = TaskRuntime._coerce_result(result.to_dict())
    assert restored.human_action == result.human_action


def test_foreach_preserves_order_and_limit() -> None:
    async def scenario() -> list[int]:
        async def execute_step(step, state):
            return {"value": state["variables"]["item"] * 2}

        state = {
            "variables": {"items": [1, 2, 3], "result": []},
            "results": {},
        }
        await execute_foreach(
            {
                "output_var": "result",
                "params": {
                    "items": "${items}",
                    "item_var": "item",
                    "max_iterations": 2,
                    "steps": [{"action": "double"}],
                },
            },
            state,
            execute_step,
        )
        return state["variables"]["result"]

    assert asyncio.run(scenario()) == [2, 4]


def test_foreach_keeps_partial_results_after_failure() -> None:
    async def scenario() -> tuple[list[int], int]:
        async def execute_step(step, state):
            item = state["variables"]["item"]
            if item == 3:
                raise RuntimeError("boom")
            return StepResult(success=True, value=item)

        state = RuntimeState(variables={"items": [1, 2, 3, 4]})
        await execute_foreach(
            {
                "output_var": "result",
                "params": {
                    "items": "${items}",
                    "max_iterations": 4,
                    "steps": [{"action": "collect"}],
                },
            },
            state,
            execute_step,
        )
        return state.variables["result"], state.loop_cursors["result"]

    results, cursor = asyncio.run(scenario())

    assert results == [1, 2]
    assert cursor == 2


def test_foreach_stops_at_total_step_budget() -> None:
    async def scenario() -> tuple[list[int], int]:
        async def execute_step(step, state):
            return StepResult(
                success=True,
                value=int(state["variables"]["item"]),
            )

        state = RuntimeState(
            variables={"items": [1, 2, 3, 4, 5]},
        )
        await execute_foreach(
            {
                "output_var": "result",
                "params": {
                    "items": "${items}",
                    "max_iterations": 5,
                    "max_steps": 3,
                    "steps": [{"action": "collect"}],
                },
            },
            state,
            execute_step,
        )
        return state.variables["result"], state.variables["_step_count"]

    results, step_count = asyncio.run(scenario())

    assert results == [1, 2, 3]
    assert step_count == 3


def test_task_runtime_executes_foreach_step() -> None:
    async def execute_step(step, state):
        assert step["action"] == "double"
        return StepResult(
            success=True,
            value=int(state["variables"]["item"]) * 2,
        )

    runtime = TaskRuntime(step_executor=execute_step)
    result = asyncio.run(
        runtime.execute(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "transform",
                        "action": "foreach",
                        "output_var": "doubled",
                        "params": {
                            "items": "${items}",
                            "item_var": "item",
                            "max_iterations": 3,
                            "steps": [{"action": "double"}],
                        },
                    }
                ]
            },
            {"variables": {"items": [1, 2, 3]}},
        )
    )

    assert result.success is True
    assert result.variables["doubled"] == [2, 4, 6]


def test_task_runtime_rejects_unknown_catalog_action_before_callback() -> None:
    async def scenario():
        called = False

        async def execute_step(step, state):
            nonlocal called
            called = True
            return {"success": True, "value": "should not run"}

        runtime = TaskRuntime(
            step_executor=execute_step,
            action_registry=ActionRegistry(version="test"),
        )
        result = await runtime.execute(
            {
                "execution_mode": "structured",
                "steps": [
                    {
                        "step_id": 1,
                        "executor": "web",
                        "action": "unknown",
                        "params": {},
                    }
                ],
            }
        )
        assert called is False
        return result

    result = asyncio.run(scenario())
    assert result.success is False
    assert "Unknown action" in (result.error or "")
