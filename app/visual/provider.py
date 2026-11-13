from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

VisualProviderName = Literal["disabled", "local_ocr", "remote_vlm"]


class VisualProviderUnavailable(RuntimeError):
    pass


class VisualCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=200)
    bbox: tuple[int, int, int, int]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(default="", max_length=1000)
    expected_after: str = Field(default="", max_length=1000)

    @field_validator("bbox")
    @classmethod
    def _valid_bbox(
        cls,
        value: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = value
        if min(x1, y1, x2, y2) < 0:
            raise ValueError("bbox coordinates must be non-negative")
        if x2 <= x1 or y2 <= y1:
            raise ValueError("bbox width and height must be positive")
        return value


class VisualLocateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: int = Field(ge=1)
    action: str = Field(min_length=1)
    target_description: str = Field(min_length=1, max_length=500)
    screenshot: bytes = Field(exclude=True, repr=False)
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    viewport: dict[str, int]
    dom_candidates: list[dict[str, Any]] = Field(default_factory=list)
    safe_visible_text: str = ""
    allowed_actions: list[str] = Field(default_factory=list, max_length=20)
    max_candidates: int = Field(default=3, ge=1, le=3)
    window_info: dict[str, Any] = Field(default_factory=dict)
    coordinate_space: Literal["pixels", "normalized"] = "pixels"

    @model_validator(mode="after")
    def _valid_action_allowlist(self) -> "VisualLocateRequest":
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        if self.action not in self.allowed_actions:
            raise ValueError("action must be present in allowed_actions")
        return self


class VisualLocateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: VisualProviderName
    model: str = Field(default="", max_length=200)
    candidates: list[VisualCandidate] = Field(default_factory=list)


class VisualProvider(Protocol):
    name: str

    async def locate(
        self,
        request: VisualLocateRequest,
    ) -> VisualLocateResponse: ...


class DisabledVisualProvider:
    name = "disabled"

    async def locate(
        self,
        request: VisualLocateRequest,
    ) -> VisualLocateResponse:
        del request
        return VisualLocateResponse(
            provider="disabled",
            model="",
            candidates=[],
        )


def validate_visual_response(
    response: VisualLocateResponse,
    request: VisualLocateRequest,
) -> VisualLocateResponse:
    for candidate in response.candidates:
        x1, y1, x2, y2 = candidate.bbox
        if x2 > request.image_width or y2 > request.image_height:
            raise ValueError(
                "candidate bbox exceeds screenshot bounds"
            )
    return response


__all__ = [
    "DisabledVisualProvider",
    "VisualCandidate",
    "VisualLocateRequest",
    "VisualLocateResponse",
    "VisualProvider",
    "VisualProviderName",
    "VisualProviderUnavailable",
    "validate_visual_response",
]
