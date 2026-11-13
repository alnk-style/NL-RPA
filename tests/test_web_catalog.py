from __future__ import annotations

import pytest

from app.agents.web_catalog import (
    WEB_ACTIONS,
    WebPlanError,
    validate_web_plan,
    web_action_catalog_text,
)


def _goto_step(**overrides) -> dict:
    step = {
        "action": "goto",
        "params": {"url": "https://example.com"},
    }
    step.update(overrides)
    return step


def _click_step(**overrides) -> dict:
    step = {
        "action": "click",
        "locator": {
            "strategy": "role",
            "value": "button",
            "name": "登录",
        },
    }
    step.update(overrides)
    return step


def test_web_catalog_covers_stage_one_actions() -> None:
    expected = {
        "goto",
        "click",
        "fill",
        "press",
        "select_option",
        "check",
        "uncheck",
        "wait_for",
        "extract_text",
        "extract_table",
        "screenshot",
        "save_file",
        "assert_text",
        "assert_logged_in",
        "download",
        "upload",
        "code_step",
    }

    assert expected <= set(WEB_ACTIONS)
    for action, spec in WEB_ACTIONS.items():
        assert str(spec.get("summary") or "").strip(), action
        assert isinstance(spec.get("params"), dict), action
        assert isinstance(spec.get("requires_locator"), bool), action


def test_web_action_catalog_text_lists_actions_and_params() -> None:
    text = web_action_catalog_text()

    assert "- goto：" in text
    assert "url" in text
    assert "- click：" in text


def test_validate_web_plan_normalizes_steps_without_mutating_input() -> None:
    plan = {
        "steps": [
            _goto_step(),
            _click_step(depends_on=[1]),
        ]
    }

    validated = validate_web_plan(plan)

    assert validated["executor_type"] == "web"
    assert validated["execution_mode"] == "structured"
    assert [step["step_id"] for step in validated["steps"]] == [1, 2]
    assert validated["steps"][1]["executor"] == "web"
    assert validated["steps"][1]["retry_policy"] == {
        "max_attempts": 2,
        "backoff_seconds": 1.0,
    }
    assert "step_id" not in plan["steps"][0]


def test_validate_web_plan_rejects_unknown_action() -> None:
    with pytest.raises(WebPlanError, match="动作不在网页动作目录"):
        validate_web_plan({"steps": [{"action": "navigate", "params": {}}]})


def test_validate_web_plan_rejects_missing_locator() -> None:
    with pytest.raises(WebPlanError, match="需要 locator"):
        validate_web_plan({"steps": [{"action": "click", "params": {}}]})


def test_validate_web_plan_rejects_unknown_params() -> None:
    with pytest.raises(WebPlanError, match="未知参数"):
        validate_web_plan(
            {
                "steps": [
                    _goto_step(params={"url": "https://example.com", "selector": "#x"})
                ]
            }
        )


def test_validate_web_plan_rejects_missing_required_params() -> None:
    with pytest.raises(WebPlanError, match="url"):
        validate_web_plan({"steps": [{"action": "goto", "params": {}}]})


def test_validate_web_plan_rejects_non_http_goto() -> None:
    with pytest.raises(WebPlanError, match="http"):
        validate_web_plan(
            {"steps": [_goto_step(params={"url": "file:///C:/secret.txt"})]}
        )


def test_validate_web_plan_rejects_windows_executor() -> None:
    with pytest.raises(WebPlanError, match="executor"):
        validate_web_plan(
            {
                "steps": [
                    _goto_step(executor="windows"),
                ]
            }
        )


def test_validate_web_plan_rejects_empty_steps() -> None:
    with pytest.raises(WebPlanError, match="至少需要一个步骤"):
        validate_web_plan({"steps": []})
