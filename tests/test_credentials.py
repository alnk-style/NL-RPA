from __future__ import annotations

# ruff: noqa: E402

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "test-encryption-key")

from app.models import Base, Credential
from app.security.credentials import CredentialStore


def _config(database_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        DATABASE_URL=(
            "sqlite+aiosqlite:///"
            + database_path.as_posix()
        ),
        CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )


def _store(tmp_path: Path) -> tuple[CredentialStore, object]:
    config = _config(tmp_path / "credentials.db")
    sync_url = config.DATABASE_URL.replace(
        "sqlite+aiosqlite",
        "sqlite",
    )
    database_engine = create_engine(sync_url)
    Base.metadata.create_all(database_engine)
    return CredentialStore(config), database_engine


def test_save_and_get(tmp_path: Path) -> None:
    store, database_engine = _store(tmp_path)
    try:
        assert Credential.__tablename__ == "credential"
        store.save(1, "api_token", "super-secret")

        assert store.get(1, "api_token") == "super-secret"

        store.save(1, "api_token", "updated-secret")
        assert store.get(1, "api_token") == "updated-secret"
    finally:
        store.engine.dispose()
        database_engine.dispose()


def test_get_not_found(tmp_path: Path) -> None:
    store, database_engine = _store(tmp_path)
    try:
        assert store.get(999, "missing") is None
    finally:
        store.engine.dispose()
        database_engine.dispose()


def test_delete(tmp_path: Path) -> None:
    store, database_engine = _store(tmp_path)
    try:
        store.save(1, "api_token", "super-secret")

        store.delete(1, "api_token")

        assert store.get(1, "api_token") is None
    finally:
        store.engine.dispose()
        database_engine.dispose()
