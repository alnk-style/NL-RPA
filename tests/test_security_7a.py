from __future__ import annotations

# ruff: noqa: E402

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key")

from app.agents.code_generator import (
    CODE_GENERATOR_SYSTEM_PROMPT,
    CodeGeneratorAgent,
)
from app.engine.code_validator import CodeValidator
from app.models import Base
from app.security.credentials import CredentialStore


def _credential_config(database_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        DATABASE_URL=(
            "sqlite+aiosqlite:///"
            + database_path.as_posix()
        ),
        CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )


def _store(
    tmp_path: Path,
) -> tuple[CredentialStore, object]:
    config = _credential_config(tmp_path / "security.db")
    sync_url = config.DATABASE_URL.replace(
        "sqlite+aiosqlite",
        "sqlite",
    )
    database_engine = create_engine(sync_url)
    Base.metadata.create_all(database_engine)
    return CredentialStore(config), database_engine


def _agent_config() -> SimpleNamespace:
    return SimpleNamespace(
        DEEPSEEK_API_KEY="test-key",
        DEEPSEEK_BASE_URL="https://api.deepseek.com",
        DEEPSEEK_MODEL="deepseek-chat",
    )


def test_check_credential_leakage_detected() -> None:
    warnings = asyncio.run(
        CodeValidator.check_credential_leakage(
            'password = "s3cret-password"',
            {"password": "s3cret-password"},
        )
    )

    assert len(warnings) == 1
    assert "password" in warnings[0]
    assert 'context["credentials"]["password"]' in warnings[0]


def test_check_credential_leakage_safe() -> None:
    warnings = asyncio.run(
        CodeValidator.check_credential_leakage(
            'await page.fill("#password", '
            'context["credentials"]["password"])',
            {"password": "s3cret-password"},
        )
    )

    assert warnings == []


def test_inject_credentials(tmp_path: Path) -> None:
    store, database_engine = _store(tmp_path)
    try:
        store.save(7, "api_token", "secret-token")
        store.save(7, "username", "automation-user")
        store.save(8, "other_token", "other-secret")

        assert store.list_credentials(7) == ["api_token", "username"]
        assert "secret-token" not in store.list_credentials(7)
        assert store.inject_credentials(7) == {
            "api_token": "secret-token",
            "username": "automation-user",
        }
    finally:
        store.engine.dispose()
        database_engine.dispose()


def test_code_generator_no_hardcoded_password() -> None:
    leaked_code = """async def main(page):
    password = "s3cret-password"
    await page.fill("#password", password)
"""
    safe_code = """async def main(page):
    password = context["credentials"]["password"]
    await page.fill("#password", password)
"""

    async def scenario() -> tuple[str, AsyncMock]:
        with patch("app.agents.code_generator.LLMClient") as client_class:
            client = client_class.return_value
            client.chat = AsyncMock(
                side_effect=[leaked_code, safe_code]
            )
            agent = CodeGeneratorAgent(_agent_config())
            code = await agent.generate(
                {"steps": []},
                {"credentials": {"password": "s3cret-password"}},
            )
            return code, client.chat

    code, chat = asyncio.run(scenario())

    assert code == safe_code.strip()
    assert chat.await_count == 2
    retry_messages = chat.await_args_list[1].args[0]
    assert (
        'context["credentials"]["password"]'
        in retry_messages[1]["content"]
    )


def test_code_generator_prompt_forbids_main_guard_and_pandas() -> None:
    assert "__name__" in CODE_GENERATOR_SYSTEM_PROMPT
    assert "__main__" in CODE_GENERATOR_SYSTEM_PROMPT
    assert "pandas" in CODE_GENERATOR_SYSTEM_PROMPT
    assert "禁止" in CODE_GENERATOR_SYSTEM_PROMPT
    assert "save_file" in CODE_GENERATOR_SYSTEM_PROMPT
    assert "open()" in CODE_GENERATOR_SYSTEM_PROMPT


if __name__ == "__main__":
    test_check_credential_leakage_detected()
    test_check_credential_leakage_safe()
    print("security tests are run through pytest for fixtures")
