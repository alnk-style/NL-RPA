from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

if TYPE_CHECKING:
    from app.config import Settings


logger = logging.getLogger(__name__)


class LLMClient:
    """Shared asynchronous client for DeepSeek chat completions."""

    def __init__(self, config: Settings):
        self.client = AsyncOpenAI(
            base_url=config.DEEPSEEK_BASE_URL,
            api_key=config.DEEPSEEK_API_KEY,
        )
        self.model = config.DEEPSEEK_MODEL

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.0,
    ) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
        except Exception:
            logger.exception("DeepSeek chat completion failed")
            raise


def extract_json_object(content: str) -> dict[str, Any]:
    """Extract and parse a JSON object from an LLM response."""

    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response is empty")

    candidate = content.strip()
    fenced_match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        candidate,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced_match:
        candidate = fenced_match.group(1).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(candidate[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")

    return parsed
