from __future__ import annotations

import asyncio

import pytest

from app.visual.local_ocr import LocalOCRProvider, VisualProviderUnavailable
from app.visual.provider import DisabledVisualProvider, VisualLocateRequest
from app.visual.remote_vlm import RemoteVLMProvider


def _request() -> VisualLocateRequest:
    return VisualLocateRequest(
        step_id=1,
        action="click",
        target_description="导出",
        screenshot=b"redacted-image",
        image_width=100,
        image_height=80,
        viewport={"width": 100, "height": 80},
        dom_candidates=[],
        safe_visible_text="***",
        allowed_actions=["click"],
        max_candidates=3,
    )


def test_disabled_provider_never_returns_candidates() -> None:
    result = asyncio.run(
        DisabledVisualProvider().locate(_request())
    )
    assert result.provider == "disabled"
    assert result.candidates == []


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeHTTPClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url: str, *, json, headers):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
            }
        )
        return FakeResponse(
            {
                "candidates": [
                    {
                        "target": "导出",
                        "bbox": [10, 20, 40, 50],
                        "confidence": 0.95,
                        "evidence": "按钮文字",
                        "expected_after": "打开下载",
                    }
                ]
            }
        )


def test_remote_provider_sends_only_validated_candidate_payload() -> None:
    client = FakeHTTPClient()
    provider = RemoteVLMProvider(
        endpoint="https://visual.example.com/locate",
        model="fake-vlm",
        api_key="secret-key",
        http_client=client,
    )

    result = asyncio.run(provider.locate(_request()))

    assert result.provider == "remote_vlm"
    assert result.model == "fake-vlm"
    assert result.candidates[0].bbox == (10, 20, 40, 50)
    sent = client.calls[0]
    assert sent["url"] == "https://visual.example.com/locate"
    assert sent["headers"]["Authorization"] == "Bearer secret-key"
    assert sent["json"]["model"] == "fake-vlm"
    assert "redacted-image" not in str(sent["json"])


def test_remote_provider_requires_https_and_key() -> None:
    with pytest.raises(VisualProviderUnavailable):
        RemoteVLMProvider(
            endpoint="http://visual.example.com/locate",
            model="fake",
            api_key="key",
        )
    with pytest.raises(VisualProviderUnavailable):
        RemoteVLMProvider(
            endpoint="https://visual.example.com/locate",
            model="fake",
            api_key="",
        )


def test_local_ocr_declares_unavailable_without_optional_runtime() -> None:
    provider = LocalOCRProvider(ocr_backend=None)
    assert provider.available is False
    with pytest.raises(VisualProviderUnavailable):
        asyncio.run(provider.locate(_request()))
