from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from app.engine.visual_runtime import VisualRuntimeService
from app.visual.provider import (
    VisualCandidate,
    VisualLocateRequest,
    VisualLocateResponse,
)
from app.visual.settings import VisualSettings


def _png(width: int = 120, height: int = 80) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class FakeProvider:
    name = "remote_vlm"

    def __init__(self) -> None:
        self.requests: list[VisualLocateRequest] = []
        self.calls = 0

    async def locate(self, request: VisualLocateRequest) -> VisualLocateResponse:
        self.calls += 1
        self.requests.append(request)
        return VisualLocateResponse(
            provider="remote_vlm",
            model="fake-vlm",
            candidates=[
                VisualCandidate(
                    target="导出",
                    bbox=(50, 20, 90, 50),
                    confidence=0.96,
                    evidence="按钮",
                ),
                VisualCandidate(
                    target="导出全部",
                    bbox=(50, 55, 90, 75),
                    confidence=0.94,
                    evidence="按钮",
                ),
            ],
        )


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []

    async def click(self, x: float, y: float, **kwargs) -> None:
        del kwargs
        self.clicks.append((x, y))


class FakeBodyLocator:
    def __init__(self, page) -> None:
        self.page = page

    async def inner_text(self, **kwargs):
        del kwargs
        return self.page.body_text


class FakePage:
    def __init__(self) -> None:
        self.viewport_size = {"width": 120, "height": 80}
        self.url = "https://example.com/orders"
        self.body_text = "订单 password=secret token=abc123"
        self.mouse = FakeMouse()
        self.screenshot_calls = 0

    async def evaluate(self, script, arg=None):
        del script, arg
        return {
            "width": 120,
            "height": 80,
            "redaction_rects": [[10, 10, 40, 30]],
            "visible_text": self.body_text,
            "dom_candidates": [
                {"role": "button", "name": "导出"},
            ],
        }

    async def screenshot(self, **kwargs):
        del kwargs
        self.screenshot_calls += 1
        return _png()

    def locator(self, selector: str):
        if selector == "body":
            return FakeBodyLocator(self)
        raise AssertionError(selector)


class FakeSettingsLoader:
    def __init__(self, settings: VisualSettings) -> None:
        self.settings = settings
        self.calls = 0

    async def __call__(self, user_id: int) -> VisualSettings:
        del user_id
        self.calls += 1
        return self.settings


def test_runtime_redacts_before_calling_provider_and_creates_ambiguity(
    tmp_path,
) -> None:
    provider = FakeProvider()
    page = FakePage()
    loader = FakeSettingsLoader(
        VisualSettings(
            enabled=True,
            provider="remote_vlm",
            endpoint="https://visual.example.com/locate",
            model="fake-vlm",
            confidence_threshold=0.9,
            max_candidates=3,
            max_attempts=1,
            screenshot_scope="viewport",
        )
    )
    runtime = VisualRuntimeService(
        settings_loader=loader,
        provider_factory=lambda settings: provider,
    )
    step = SimpleNamespace(
        step_id=4,
        executor="web",
        action="click",
        params={
            "visual_fallback": True,
            "target_description": "导出按钮",
        },
        visual_fallback=True,
        postconditions=[],
    )

    outcome = asyncio.run(
        runtime.evaluate_web(
            step,
            page,
            failure_category="locator_not_found",
            context={"user_id": 1, "execution_log_id": 9},
            screenshot_path=str(tmp_path / "visual.png"),
        )
    )
    cached = asyncio.run(
        runtime.evaluate_web(
            step,
            FakePage(),
            failure_category="locator_not_found",
            context={"user_id": 1, "execution_log_id": 9},
            screenshot_path=str(tmp_path / "visual.png"),
        )
    )

    assert outcome.status == "awaiting_user"
    assert outcome.payload["kind"] == "visual_ambiguous"
    assert outcome.payload["action"] == "click"
    assert len(outcome.payload["candidates"]) == 2
    assert outcome.payload["screenshot_path"].endswith(
        ".redacted.png"
    )
    request = provider.requests[0]
    assert "secret" not in request.safe_visible_text
    assert "abc123" not in request.safe_visible_text
    image = Image.open(BytesIO(request.screenshot)).convert("RGB")
    assert image.getpixel((20, 20)) == (0, 0, 0)
    assert page.mouse.clicks == []
    assert cached.status == "awaiting_user"
    assert provider.calls == 1


def test_runtime_requires_switch_and_never_calls_disabled_provider() -> None:
    provider = FakeProvider()
    runtime = VisualRuntimeService(
        settings_loader=FakeSettingsLoader(
            VisualSettings(enabled=False, provider="disabled")
        ),
        provider_factory=lambda settings: provider,
    )
    outcome = asyncio.run(
        runtime.evaluate_web(
            SimpleNamespace(
                step_id=1,
                action="click",
                params={"visual_fallback": False},
                visual_fallback=False,
                postconditions=[],
            ),
            FakePage(),
            failure_category="locator_not_found",
            context={"user_id": 1},
        )
    )

    assert outcome.status == "skipped"
    assert provider.calls == 0


def test_desktop_runtime_creates_window_scoped_ambiguity(tmp_path) -> None:
    provider = FakeProvider()
    runtime = VisualRuntimeService(
        settings_loader=FakeSettingsLoader(
            VisualSettings(
                enabled=True,
                provider="remote_vlm",
                endpoint="https://visual.example.com/locate",
                model="fake-vlm",
                max_attempts=1,
                screenshot_scope="window",
            )
        ),
        provider_factory=lambda settings: provider,
    )
    step = SimpleNamespace(
        step_id=3,
        executor="windows",
        action="click_element",
        params={
            "visual_fallback": True,
            "target_description": "保存按钮",
            "window_title": "编辑器",
        },
        visual_fallback=True,
        postconditions=[],
    )

    outcome = asyncio.run(
        runtime.evaluate_desktop(
            step,
            screenshot=_png(),
            image_size=(120, 80),
            window_info={
                "title": "编辑器",
                "pid": 10,
                "handle": 99,
                "redaction_rects": [[0, 0, 10, 10]],
            },
            visible_text="password=secret",
            uia_candidates=[
                {"name": "保存", "control_type": "Button"}
            ],
            failure_category="element_not_visible",
            context={"user_id": 1, "execution_log_id": 9},
            screenshot_path=str(tmp_path / "window.png"),
        )
    )

    assert outcome.status == "awaiting_user"
    assert outcome.payload["screenshot_scope"] == "window"
    assert outcome.payload["window_info"]["handle"] == 99
    assert "secret" not in provider.requests[0].safe_visible_text
    assert provider.requests[0].window_info["handle"] == 99


def test_desktop_selected_candidate_uses_controlled_callback(
    tmp_path,
) -> None:
    calls: list[dict] = []

    async def executor(candidate, step, context):
        del step, context
        calls.append(candidate)
        return True, {"passed": True, "type": "uia_changed"}

    runtime = VisualRuntimeService(
        settings_loader=FakeSettingsLoader(
            VisualSettings(enabled=True, provider="remote_vlm")
        ),
        provider_factory=lambda settings: FakeProvider(),
    )
    step = SimpleNamespace(
        step_id=3,
        executor="windows",
        action="click_element",
        params={"visual_fallback": True},
        visual_fallback=True,
        postconditions=[],
    )
    outcome = asyncio.run(
        runtime.evaluate_desktop(
            step,
            screenshot=_png(),
            image_size=(120, 80),
            window_info={"title": "编辑器", "handle": 99},
            visible_text="",
            uia_candidates=[],
            failure_category="element_not_visible",
            context={
                "user_id": 1,
                "visual_selection": {
                    "step_id": 3,
                    "candidate_id": "cand_1",
                    "target": "保存",
                    "bbox": [10, 10, 30, 30],
                    "confidence": 0.96,
                    "action": "click_element",
                    "risk_level": "read_only",
                },
            },
            screenshot_path=str(tmp_path / "window.png"),
            execute_callback=executor,
        )
    )

    assert outcome.status == "success"
    assert outcome.verification["passed"] is True
    assert calls[0]["candidate_id"] == "cand_1"


def test_redaction_failure_aborts_before_provider_call() -> None:
    provider = FakeProvider()

    class InvalidScreenshotPage(FakePage):
        async def screenshot(self, **kwargs):
            del kwargs
            return b"not-an-image"

    runtime = VisualRuntimeService(
        settings_loader=FakeSettingsLoader(
            VisualSettings(
                enabled=True,
                provider="remote_vlm",
                endpoint="https://visual.example.com/locate",
                model="fake-vlm",
                max_attempts=1,
            )
        ),
        provider_factory=lambda settings: provider,
    )

    outcome = asyncio.run(
        runtime.evaluate_web(
            SimpleNamespace(
                step_id=1,
                action="click",
                params={"visual_fallback": True},
                visual_fallback=True,
                postconditions=[],
            ),
            InvalidScreenshotPage(),
            failure_category="locator_not_found",
            context={"user_id": 1},
        )
    )

    assert outcome.status == "failed"
    assert "脱敏" in outcome.reason
    assert provider.calls == 0
