from __future__ import annotations

import re
from io import BytesIO
from typing import Iterable

from PIL import Image, ImageDraw


class VisualRedactionError(RuntimeError):
    pass


_KEY_VALUE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|access_token|refresh_token|"
    r"cookie|authorization|api[_-]?key|secret)\b"
    r"\s*[:=]\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_CARD = re.compile(
    r"\b(?:\d[ -]?){13,19}\b"
)
_WINDOWS_PATH = re.compile(
    r"(?i)\b[A-Z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*"
)
_POSIX_PATH = re.compile(
    r"(?<!\w)/(?:[^/\s]+/)+[^/\s]*"
)


def redact_visible_text(
    text: str,
    *,
    explicit_values: Iterable[str] = (),
) -> str:
    result = str(text or "")
    for value in sorted(
        {
            str(item)
            for item in explicit_values
            if item is not None and str(item)
        },
        key=len,
        reverse=True,
    ):
        result = result.replace(value, "***")
    result = _KEY_VALUE.sub(
        lambda match: f"{match.group(1)}=***",
        result,
    )
    result = _BEARER.sub("Bearer ***", result)
    result = _CARD.sub("****", result)
    result = _WINDOWS_PATH.sub("<path>", result)
    result = _POSIX_PATH.sub("<path>", result)
    return result


def redact_image_bytes(
    image_bytes: bytes,
    *,
    rects: Iterable[tuple[int, int, int, int]],
    image_size: tuple[int, int],
) -> bytes:
    width, height = image_size
    if width <= 0 or height <= 0:
        raise VisualRedactionError("invalid image size")
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise VisualRedactionError(
            f"cannot decode visual screenshot: {exc}"
        ) from exc
    if image.size != (width, height):
        raise VisualRedactionError(
            "screenshot dimensions do not match supplied image size"
        )
    draw = ImageDraw.Draw(image)
    for rect in rects:
        x1, y1, x2, y2 = (int(item) for item in rect)
        if (
            min(x1, y1, x2, y2) < 0
            or x2 <= x1
            or y2 <= y1
            or x2 > width
            or y2 > height
        ):
            raise VisualRedactionError(
                f"invalid redaction rectangle: {rect}"
            )
        draw.rectangle((x1, y1, x2, y2), fill=(0, 0, 0))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


__all__ = [
    "VisualRedactionError",
    "redact_image_bytes",
    "redact_visible_text",
]
