from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.visual.redaction import (
    VisualRedactionError,
    redact_image_bytes,
    redact_visible_text,
)


def _png() -> bytes:
    image = Image.new("RGB", (120, 80), "white")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_redact_image_masks_configured_rectangles() -> None:
    redacted = redact_image_bytes(
        _png(),
        rects=[(10, 10, 50, 30)],
        image_size=(120, 80),
    )

    image = Image.open(BytesIO(redacted)).convert("RGB")
    assert image.getpixel((20, 20)) == (0, 0, 0)
    assert image.getpixel((100, 60)) == (255, 255, 255)


def test_redact_image_rejects_invalid_or_out_of_bounds_rectangles() -> None:
    with pytest.raises(VisualRedactionError):
        redact_image_bytes(
            _png(),
            rects=[(10, 10, 10, 30)],
            image_size=(120, 80),
        )
    with pytest.raises(VisualRedactionError):
        redact_image_bytes(
            _png(),
            rects=[(110, 10, 130, 30)],
            image_size=(120, 80),
        )


def test_redact_visible_text_masks_secrets_cards_paths_and_user_values() -> None:
    text = (
        "password=hunter2 token=abc123 cookie=session "
        "card 6222 0202 0000 0000 "
        r"C:\Users\alice\secret.txt "
        "custom-secret"
    )

    redacted = redact_visible_text(
        text,
        explicit_values=["custom-secret"],
    )

    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert "session" not in redacted
    assert "6222 0202" not in redacted
    assert "alice" not in redacted
    assert "custom-secret" not in redacted
    assert "***" in redacted


def test_redact_image_requires_valid_image_payload() -> None:
    with pytest.raises(VisualRedactionError):
        redact_image_bytes(
            b"not-an-image",
            rects=[],
            image_size=(120, 80),
        )
