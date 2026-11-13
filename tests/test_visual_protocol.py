from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.visual.provider import (
    VisualCandidate,
    VisualLocateRequest,
    VisualLocateResponse,
    validate_visual_response,
)


def _request(**overrides) -> VisualLocateRequest:
    values = {
        "step_id": 2,
        "action": "click",
        "target_description": "导出按钮",
        "screenshot": b"fake-png",
        "image_width": 1280,
        "image_height": 720,
        "viewport": {"width": 1280, "height": 720},
        "dom_candidates": [
            {
                "role": "button",
                "name": "导出",
                "visible": True,
            }
        ],
        "safe_visible_text": "订单列表 导出",
        "allowed_actions": ["click"],
        "max_candidates": 3,
    }
    values.update(overrides)
    return VisualLocateRequest(**values)


def test_visual_candidate_validates_bbox_and_confidence() -> None:
    candidate = VisualCandidate(
        target="导出",
        bbox=(100, 200, 180, 240),
        confidence=0.91,
        evidence="右上角按钮",
        expected_after="出现下载文件",
    )
    assert candidate.bbox == (100, 200, 180, 240)

    with pytest.raises(ValidationError):
        VisualCandidate(
            target="导出",
            bbox=(-1, 10, 20, 30),
            confidence=0.9,
        )
    with pytest.raises(ValidationError):
        VisualCandidate(
            target="导出",
            bbox=(10, 20, 10, 30),
            confidence=0.9,
        )
    with pytest.raises(ValidationError):
        VisualCandidate(
            target="导出",
            bbox=(10, 20, 30, 40),
            confidence=1.1,
        )


def test_visual_response_rejects_out_of_bounds_candidate() -> None:
    request = _request()
    response = VisualLocateResponse(
        provider="remote_vlm",
        model="fake-vlm",
        candidates=[
            VisualCandidate(
                target="导出",
                bbox=(1200, 680, 1300, 730),
                confidence=0.95,
            )
        ],
    )

    with pytest.raises(ValueError, match="bbox"):
        validate_visual_response(response, request)


def test_visual_response_accepts_empty_candidates_and_provider_names() -> None:
    response = VisualLocateResponse(
        provider="disabled",
        model="",
        candidates=[],
    )
    assert validate_visual_response(response, _request()).candidates == []

    with pytest.raises(ValidationError):
        VisualLocateResponse(
            provider="arbitrary_browser_script",
            model="x",
            candidates=[],
        )


def test_visual_request_does_not_serialize_screenshot_bytes() -> None:
    request = _request()
    dumped = request.model_dump(mode="json")
    assert "screenshot" not in dumped
    assert request.max_candidates == 3
