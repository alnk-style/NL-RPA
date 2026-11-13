from __future__ import annotations

from typing import Any

from app.visual.provider import (
    VisualLocateRequest,
    VisualLocateResponse,
    VisualProviderUnavailable,
)


class LocalOCRProvider:
    name = "local_ocr"

    def __init__(self, *, ocr_backend: Any | None = None) -> None:
        self.ocr_backend = ocr_backend

    @property
    def available(self) -> bool:
        return self.ocr_backend is not None

    async def locate(
        self,
        request: VisualLocateRequest,
    ) -> VisualLocateResponse:
        if self.ocr_backend is None:
            raise VisualProviderUnavailable(
                "local OCR runtime is not installed"
            )
        try:
            result = self.ocr_backend.locate(request)
            if hasattr(result, "__await__"):
                result = await result
        except VisualProviderUnavailable:
            raise
        except Exception as exc:
            raise VisualProviderUnavailable(
                f"local OCR failed: {exc}"
            ) from exc
        if isinstance(result, VisualLocateResponse):
            return result
        if isinstance(result, dict):
            return VisualLocateResponse(
                provider="local_ocr",
                model=str(result.get("model") or ""),
                candidates=list(result.get("candidates") or []),
            )
        raise VisualProviderUnavailable(
            "local OCR returned an invalid response"
        )


__all__ = ["LocalOCRProvider", "VisualProviderUnavailable"]
