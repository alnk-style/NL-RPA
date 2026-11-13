from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from app.engine.visual_fallback import (
    VisualCapture,
    VisualFallback,
    should_attempt_visual,
)
from app.visual.provider import (
    VisualCandidate,
    VisualLocateRequest,
    VisualLocateResponse,
)
from app.protocols import StepExecutor


class FakeProvider:
    name = "remote_vlm"

    def __init__(self, candidates: list[VisualCandidate]) -> None:
        self.candidates = candidates
        self.calls = 0
        self.last_request: VisualLocateRequest | None = None

    async def locate(self, request: VisualLocateRequest) -> VisualLocateResponse:
        self.calls += 1
        self.last_request = request
        return VisualLocateResponse(
            provider="remote_vlm",
            model="fake-vlm",
            candidates=list(self.candidates),
        )


def _capture() -> VisualCapture:
    return VisualCapture(
        screenshot=b"redacted-png",
        image_width=1280,
        image_height=720,
        viewport={"width": 1280, "height": 720},
        dom_candidates=[{"role": "button", "name": "导出"}],
        safe_visible_text="订单列表 ***",
        source="web",
        location="https://example.com/orders",
        redaction_rects=[],
    )


def _step(action: str = "click", **params) -> SimpleNamespace:
    values = {
        "step_id": 4,
        "executor": "web",
        "action": action,
        "params": {"target_description": "导出按钮", **params},
        "visual_fallback": True,
    }
    return SimpleNamespace(**values)


def _settings(**overrides) -> SimpleNamespace:
    values = {
        "enabled": True,
        "provider": "remote_vlm",
        "confidence_threshold": 0.9,
        "max_candidates": 3,
        "max_attempts": 2,
        "screenshot_scope": "viewport",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_disabled_provider_is_never_called() -> None:
    provider = FakeProvider(
        [
            VisualCandidate(
                target="导出",
                bbox=(100, 200, 180, 240),
                confidence=0.99,
            )
        ]
    )

    decision = asyncio.run(
        VisualFallback().try_locate(
            _step(),
            _capture(),
            provider,
            settings=_settings(enabled=False, provider="disabled"),
            risk_level="read_only",
        )
    )

    assert decision.action == "disabled"
    assert provider.calls == 0


def test_high_confidence_unique_candidate_can_execute() -> None:
    candidate = VisualCandidate(
        target="导出",
        bbox=(100, 200, 180, 240),
        confidence=0.96,
        evidence="按钮文字",
        expected_after="开始下载",
    )
    provider = FakeProvider([candidate])

    decision = asyncio.run(
        VisualFallback().try_locate(
            _step(),
            _capture(),
            provider,
            settings=_settings(),
            risk_level="read_only",
        )
    )

    assert decision.action == "execute"
    assert decision.selected is not None
    assert decision.selected.candidate_id
    assert decision.selected.candidate.target == "导出"
    assert decision.request is not None
    assert decision.request.screenshot == b"redacted-png"
    assert decision.provider == "remote_vlm"
    assert decision.audit["selection"] == "automatic"


def test_multiple_candidates_require_human_confirmation() -> None:
    provider = FakeProvider(
        [
            VisualCandidate(
                target="导出",
                bbox=(100, 200, 180, 240),
                confidence=0.97,
            ),
            VisualCandidate(
                target="导出全部",
                bbox=(100, 250, 180, 290),
                confidence=0.94,
            ),
        ]
    )

    decision = asyncio.run(
        VisualFallback().try_locate(
            _step(),
            _capture(),
            provider,
            settings=_settings(),
            risk_level="read_only",
        )
    )

    assert decision.action == "confirm"
    assert len(decision.candidates) == 2
    assert [item.candidate_id for item in decision.candidates] == [
        item.candidate_id for item in decision.candidates
    ]
    assert len(set(item.candidate_id for item in decision.candidates)) == 2


def test_low_confidence_requires_human_confirmation() -> None:
    provider = FakeProvider(
        [
            VisualCandidate(
                target="导出",
                bbox=(100, 200, 180, 240),
                confidence=0.55,
            )
        ]
    )

    decision = asyncio.run(
        VisualFallback().try_locate(
            _step(),
            _capture(),
            provider,
            settings=_settings(),
            risk_level="read_only",
        )
    )

    assert decision.action == "confirm"
    assert "confidence" in decision.reason


def test_high_risk_action_requires_confirmation_even_if_unique() -> None:
    provider = FakeProvider(
        [
            VisualCandidate(
                target="删除",
                bbox=(100, 200, 180, 240),
                confidence=0.99,
            )
        ]
    )

    decision = asyncio.run(
        VisualFallback().try_locate(
            _step(),
            _capture(),
            provider,
            settings=_settings(),
            risk_level="high_risk",
        )
    )

    assert decision.action == "confirm"
    assert "risk" in decision.reason


def test_non_allowlisted_action_does_not_call_provider() -> None:
    provider = FakeProvider([])

    decision = asyncio.run(
        VisualFallback().try_locate(
            _step(action="send"),
            _capture(),
            provider,
            settings=_settings(),
            risk_level="read_only",
        )
    )

    assert decision.action == "failed"
    assert "allow" in decision.reason
    assert provider.calls == 0


def test_visual_trigger_requires_explicit_step_switch_and_budget() -> None:
    assert should_attempt_visual(
        _step(),
        failure_category="locator_not_found",
        attempts=0,
        max_attempts=2,
        provider="remote_vlm",
        enabled=True,
    ) is True
    assert should_attempt_visual(
        _step(visual_fallback=False),
        failure_category="locator_not_found",
        attempts=0,
        max_attempts=2,
        provider="remote_vlm",
        enabled=True,
    ) is False
    assert should_attempt_visual(
        _step(),
        failure_category="business_error",
        attempts=0,
        max_attempts=2,
        provider="remote_vlm",
        enabled=True,
    ) is False
    assert should_attempt_visual(
        _step(),
        failure_category="locator_not_found",
        attempts=2,
        max_attempts=2,
        provider="remote_vlm",
        enabled=True,
    ) is False
    assert should_attempt_visual(
        _step(),
        failure_category="locator_not_found",
        attempts=0,
        max_attempts=2,
        provider="disabled",
        enabled=True,
    ) is False


def test_visual_trigger_falls_back_to_safe_action_whitelist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(
        "app.engine.visual_fallback"
    )

    def broken_registry():
        raise ImportError("registry initialization cycle")

    monkeypatch.setattr(
        module,
        "default_action_registry",
        broken_registry,
    )
    assert module.should_attempt_visual(
        _step("click"),
        failure_category="locator_not_found",
        attempts=0,
        max_attempts=2,
        provider="remote_vlm",
        enabled=True,
    ) is True
    non_web_step = _step("click")
    non_web_step.executor = "transform"
    assert module.should_attempt_visual(
        non_web_step,
        failure_category="locator_not_found",
        attempts=0,
        max_attempts=2,
        provider="remote_vlm",
        enabled=True,
    ) is False


def test_visual_trigger_accepts_step_executor_enum() -> None:
    step = _step("click")
    step.executor = StepExecutor.WEB

    assert should_attempt_visual(
        step,
        failure_category="locator_not_found",
        attempts=0,
        max_attempts=2,
        provider="remote_vlm",
        enabled=True,
    ) is True


def test_visual_ambiguous_regression_fixture_is_stable() -> None:
    fixture_path = (
        Path(__file__).parent
        / "regression"
        / "fixtures"
        / "visual_ambiguous"
        / "fixture.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    provider = FakeProvider(
        [
            VisualCandidate.model_validate(item)
            for item in fixture["candidates"]
        ]
    )
    step = SimpleNamespace(**fixture["step"])
    settings = SimpleNamespace(**fixture["settings"])

    first = asyncio.run(
        VisualFallback().try_locate(
            step,
            _capture(),
            provider,
            settings=settings,
            risk_level="read_only",
        )
    )
    second = asyncio.run(
        VisualFallback().try_locate(
            step,
            _capture(),
            provider,
            settings=settings,
            risk_level="read_only",
        )
    )

    assert first.action == fixture["expected_action"]
    assert second.action == first.action
    assert [
        item.candidate.target for item in first.candidates
    ] == [
        item.candidate.target for item in second.candidates
    ]
