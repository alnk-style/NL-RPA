"""Controlled visual fallback providers and redaction."""

from app.visual.provider import (
    DisabledVisualProvider,
    VisualCandidate,
    VisualLocateRequest,
    VisualLocateResponse,
    VisualProvider,
    validate_visual_response,
)

__all__ = [
    "DisabledVisualProvider",
    "VisualCandidate",
    "VisualLocateRequest",
    "VisualLocateResponse",
    "VisualProvider",
    "validate_visual_response",
]
