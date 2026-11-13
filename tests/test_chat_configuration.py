from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import chat as chat_api


def test_missing_deepseek_key_returns_actionable_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        chat_api,
        "get_settings",
        lambda: SimpleNamespace(DEEPSEEK_API_KEY=""),
    )

    with pytest.raises(HTTPException) as captured:
        chat_api.require_llm_configuration()

    assert captured.value.status_code == 400
    assert "DeepSeek API Key" in captured.value.detail
