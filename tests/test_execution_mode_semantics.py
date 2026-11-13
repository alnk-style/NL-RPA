from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engine.engine import ExecutionEngine


def _config(**overrides):
    values = {
        "STRUCTURED_EXECUTION_ENABLED": True,
        "WEB_STRUCTURED_ACTIONS": False,
        "LEGACY_CODE_EXECUTION_ENABLED": True,
        "MAX_RETRY": 1,
        "EXECUTION_TIMEOUT": 30,
        "DOCKER_SANDBOX_IMAGE": "rpa-sandbox:test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_legacy_web_switch_cannot_disable_structured_execution() -> None:
    from app.runtime_flags import structured_execution_enabled

    assert structured_execution_enabled(_config()) is True
    assert structured_execution_enabled(
        _config(WEB_STRUCTURED_ACTIONS=True)
    ) is True


def test_canonical_switch_wins_over_legacy_alias() -> None:
    from app.runtime_flags import structured_execution_enabled

    assert structured_execution_enabled(
        _config(
            STRUCTURED_EXECUTION_ENABLED=False,
            WEB_STRUCTURED_ACTIONS=True,
        )
    ) is False


def test_disabled_structured_execution_fails_closed_without_code_fallback(
    monkeypatch,
) -> None:
    engine = ExecutionEngine(_config(STRUCTURED_EXECUTION_ENABLED=False))
    fallback_called = False

    async def fallback(*_args, **_kwargs):
        nonlocal fallback_called
        fallback_called = True
        raise AssertionError("legacy code fallback must not run")

    engine.sandbox.execute_with_fallback = fallback
    workflow = SimpleNamespace(
        execution_mode="structured",
        structured_plan={
            "execution_mode": "structured",
            "steps": [
                {
                    "step_id": 1,
                    "executor": "web",
                    "action": "goto",
                    "params": {"url": "https://example.com"},
                }
            ],
        },
        generated_code="async def main(page): ...",
        executor_type="web",
    )

    with pytest.raises(ValueError, match="Structured execution is disabled"):
        import asyncio

        asyncio.run(engine.execute(workflow, {}))
    assert fallback_called is False
