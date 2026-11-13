import pytest
from pydantic import ValidationError

from app.protocols import (
    FailureCategory,
    LocatorFallback,
    LocatorSpec,
    LocatorStrategy,
    ObservationKind,
    ObservationSnapshot,
    OnFailure,
    StepExecutor,
    StructuredStep,
    classify_failure,
    parse_structured_steps,
)


def test_locator_spec_orders_candidates_by_priority() -> None:
    spec = LocatorSpec(
        strategy=LocatorStrategy.CSS,
        value="#login",
        fallbacks=[
            LocatorFallback(
                strategy=LocatorStrategy.XPATH,
                value="//button",
            ),
            LocatorFallback(
                strategy=LocatorStrategy.ROLE,
                value="button",
                name="登录",
            ),
            LocatorFallback(
                strategy=LocatorStrategy.TEXT,
                value="登录",
            ),
        ],
    )

    candidates = spec.candidates()

    assert [item.strategy for item in candidates] == [
        LocatorStrategy.CSS,
        LocatorStrategy.ROLE,
        LocatorStrategy.TEXT,
        LocatorStrategy.XPATH,
    ]
    assert candidates[0].value == "#login"
    assert candidates[1].name == "登录"


def test_locator_spec_rejects_blank_value() -> None:
    with pytest.raises(ValidationError):
        LocatorSpec(strategy=LocatorStrategy.CSS, value="   ")


def test_structured_step_defaults() -> None:
    step = StructuredStep(
        step_id=1,
        action="goto",
        params={"url": "https://example.com"},
    )

    assert step.executor is StepExecutor.WEB
    assert step.retry_policy.max_attempts == 2
    assert step.retry_policy.backoff_seconds == 1.0
    assert step.timeout_seconds == 20.0
    assert step.on_failure is OnFailure.REPAIR
    assert step.depends_on == []


def test_structured_step_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError):
        StructuredStep(step_id=1, action="click", depends_on=[1])


def test_parse_structured_steps_normalizes_and_keeps_order() -> None:
    steps = parse_structured_steps(
        {
            "steps": [
                {
                    "step_id": 1,
                    "action": "goto",
                    "params": {"url": "https://example.com"},
                },
                {
                    "step_id": 2,
                    "action": "click",
                    "depends_on": [1, 1],
                    "locator": {
                        "strategy": "role",
                        "value": "button",
                        "name": "登录",
                    },
                },
            ]
        }
    )

    assert [step.step_id for step in steps] == [1, 2]
    assert steps[1].depends_on == [1]
    assert steps[1].locator is not None
    assert steps[1].locator.strategy is LocatorStrategy.ROLE


def test_parse_structured_steps_rejects_missing_dependency() -> None:
    with pytest.raises(ValueError, match="依赖不存在"):
        parse_structured_steps(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "action": "click",
                        "depends_on": [9],
                    }
                ]
            }
        )


def test_parse_structured_steps_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="依赖存在环"):
        parse_structured_steps(
            {
                "steps": [
                    {"step_id": 1, "action": "a", "depends_on": [2]},
                    {"step_id": 2, "action": "b", "depends_on": [1]},
                ]
            }
        )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            'Timeout 30000ms exceeded.\nwaiting for locator("#login")',
            FailureCategory.LOCATOR_NOT_FOUND,
        ),
        (
            "Timeout 30000ms exceeded while loading page",
            FailureCategory.TIMEOUT,
        ),
        (
            "strict mode violation: locator resolved to 2 elements",
            FailureCategory.LOCATOR_NOT_UNIQUE,
        ),
        (
            "RuntimeError: AUTH_REQUIRED: 请重新登录",
            FailureCategory.AUTH_REQUIRED,
        ),
        (
            "Access denied: 403",
            FailureCategory.PERMISSION_DENIED,
        ),
        (
            "Element is not visible",
            FailureCategory.ELEMENT_NOT_VISIBLE,
        ),
        (
            "element is not enabled",
            FailureCategory.ELEMENT_DISABLED,
        ),
        (
            "Window not found: 微信",
            FailureCategory.WINDOW_NOT_FOUND,
        ),
        (
            "程序不存在：notepad.exe",
            FailureCategory.APP_NOT_FOUND,
        ),
        (
            "业务错误：余额不足",
            FailureCategory.BUSINESS_ERROR,
        ),
        ("", FailureCategory.UNKNOWN),
    ],
)
def test_classify_failure(
    message: str,
    expected: FailureCategory,
) -> None:
    assert classify_failure(message) is expected


def test_observation_snapshot_compact_limits_elements() -> None:
    snapshot = ObservationSnapshot(
        kind=ObservationKind.BROWSER,
        source="playwright",
        url="https://example.com",
        elements=[
            {"name": str(index)} for index in range(5)
        ],
        payload={"raw_dom": "<html></html>"},
    )

    compact = snapshot.compact(max_elements=2)

    assert compact["kind"] == "browser"
    assert compact["url"] == "https://example.com"
    assert len(compact["elements"]) == 2
    assert compact["truncated"] is True
    assert "payload" not in compact


def test_observation_snapshot_payload_round_trip() -> None:
    snapshot = ObservationSnapshot(
        kind=ObservationKind.DESKTOP,
        title="记事本",
        window={"handle": 100, "pid": 42},
    )

    payload = snapshot.to_payload()
    restored = ObservationSnapshot.model_validate(payload)

    assert restored.kind is ObservationKind.DESKTOP
    assert restored.window == {"handle": 100, "pid": 42}
