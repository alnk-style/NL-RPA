from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlparse

import httpx

from app.visual.provider import (
    VisualCandidate,
    VisualLocateRequest,
    VisualLocateResponse,
    VisualProviderUnavailable,
)


class RemoteVLMProvider:
    name = "remote_vlm"

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str,
        timeout: float = 15.0,
        http_client: Any | None = None,
    ) -> None:
        parsed = urlparse(str(endpoint or "").strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise VisualProviderUnavailable(
                "remote_vlm endpoint must use HTTPS"
            )
        if not str(model or "").strip():
            raise VisualProviderUnavailable(
                "remote_vlm model is required"
            )
        if not str(api_key or "").strip():
            raise VisualProviderUnavailable(
                "remote_vlm API key is required"
            )
        self.endpoint = str(endpoint).strip()
        self.model = str(model).strip()
        self.api_key = str(api_key)
        self.timeout = max(float(timeout), 0.1)
        self.http_client = http_client

    async def locate(
        self,
        request: VisualLocateRequest,
    ) -> VisualLocateResponse:
        payload = {
            "model": self.model,
            "image_base64": base64.b64encode(
                request.screenshot
            ).decode("ascii"),
            "image_width": request.image_width,
            "image_height": request.image_height,
            "step_id": request.step_id,
            "action": request.action,
            "target_description": request.target_description,
            "viewport": request.viewport,
            "dom_candidates": request.dom_candidates,
            "safe_visible_text": request.safe_visible_text,
            "allowed_actions": request.allowed_actions,
            "max_candidates": request.max_candidates,
            "window_info": request.window_info,
            "coordinate_space": request.coordinate_space,
        }
        try:
            if self.http_client is None:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                ) as client:
                    response = await client.post(
                        self.endpoint,
                        json=payload,
                        headers={
                            "Authorization": (
                                f"Bearer {self.api_key}"
                            )
                        },
                    )
            else:
                response = await self.http_client.post(
                    self.endpoint,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}"
                    },
                )
            response.raise_for_status()
            body = response.json()
        except VisualProviderUnavailable:
            raise
        except Exception as exc:
            raise VisualProviderUnavailable(
                f"remote visual provider failed: {exc}"
            ) from exc
        if not isinstance(body, dict):
            raise VisualProviderUnavailable(
                "remote visual provider returned non-object JSON"
            )
        raw_candidates = body.get("candidates")
        if not isinstance(raw_candidates, list):
            raise VisualProviderUnavailable(
                "remote visual provider returned no candidates array"
            )
        try:
            candidates = [
                VisualCandidate.model_validate(item)
                for item in raw_candidates[: request.max_candidates]
            ]
        except Exception as exc:
            raise VisualProviderUnavailable(
                "remote visual provider returned invalid candidates"
            ) from exc
        return VisualLocateResponse(
            provider="remote_vlm",
            model=self.model,
            candidates=candidates,
        )


__all__ = ["RemoteVLMProvider"]
