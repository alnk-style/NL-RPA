from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models.credential import Credential

if TYPE_CHECKING:
    from app.config import Settings


def _sync_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername == "sqlite+aiosqlite":
        url = url.set(drivername="sqlite")
    return url.render_as_string(hide_password=False)


def _fernet_key(value: str | bytes) -> bytes:
    key = value.encode("ascii") if isinstance(value, str) else value
    try:
        decoded = base64.urlsafe_b64decode(key)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY must be a URL-safe base64 key"
        ) from exc

    if len(decoded) != 32:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )
    return key


def build_fernet(config: Settings) -> Fernet:
    return Fernet(_fernet_key(config.CREDENTIAL_ENCRYPTION_KEY))


def encrypt_value(config: Settings, value: str) -> bytes:
    return build_fernet(config).encrypt(str(value).encode("utf-8"))


def decrypt_value(config: Settings, value: bytes) -> str:
    return build_fernet(config).decrypt(bytes(value)).decode("utf-8")


async def save_credential(
    session: AsyncSession,
    config: Settings,
    workflow_id: int,
    key: str,
    value: str,
    workspace_id: int | None = None,
) -> None:
    """加密保存凭据；调用方负责提交事务。"""

    encrypted_value = encrypt_value(config, value)
    result = await session.execute(
        select(Credential).where(
            Credential.workflow_id == workflow_id,
            Credential.key == key,
        )
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        session.add(
            Credential(
                workflow_id=workflow_id,
                workspace_id=workspace_id,
                key=key,
                encrypted_value=encrypted_value,
            )
        )
    else:
        credential.encrypted_value = encrypted_value
    await session.flush()


async def delete_credential(
    session: AsyncSession,
    workflow_id: int,
    key: str,
) -> bool:
    result = await session.execute(
        delete(Credential).where(
            Credential.workflow_id == workflow_id,
            Credential.key == key,
        )
    )
    return bool(result.rowcount)


async def list_credential_keys(
    session: AsyncSession,
    workflow_id: int,
) -> list[str]:
    result = await session.execute(
        select(Credential.key)
        .where(Credential.workflow_id == workflow_id)
        .order_by(Credential.key)
    )
    return list(result.scalars().all())


async def load_credential_values(
    session: AsyncSession,
    config: Settings,
    workflow_id: int,
) -> dict[str, str]:
    """在运行时解密流程凭据，明文不写入任何持久化字段。"""

    result = await session.execute(
        select(Credential).where(
            Credential.workflow_id == workflow_id
        )
    )
    rows = result.scalars().all()
    if not rows:
        return {}

    fernet = build_fernet(config)
    credentials: dict[str, str] = {}
    for credential in rows:
        credentials[credential.key] = fernet.decrypt(
            bytes(credential.encrypted_value)
        ).decode("utf-8")
    return credentials


class CredentialStore:
    def __init__(self, config: Settings):
        self.fernet = build_fernet(config)
        database_url = getattr(
            config,
            "DATABASE_URL",
            "sqlite+aiosqlite:///./rpa.db",
        )
        self.engine = create_engine(_sync_database_url(database_url))
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
        )

    def save(self, workflow_id: int, key: str, value: str) -> None:
        encrypted_value = self.fernet.encrypt(value.encode("utf-8"))

        with self.SessionLocal.begin() as session:
            credential = session.scalar(
                select(Credential).where(
                    Credential.workflow_id == workflow_id,
                    Credential.key == key,
                )
            )
            if credential is None:
                session.add(
                    Credential(
                        workflow_id=workflow_id,
                        key=key,
                        encrypted_value=encrypted_value,
                    )
                )
            else:
                credential.encrypted_value = encrypted_value

    def get(self, workflow_id: int, key: str) -> str | None:
        with self.SessionLocal() as session:
            credential = session.scalar(
                select(Credential).where(
                    Credential.workflow_id == workflow_id,
                    Credential.key == key,
                )
            )
            if credential is None:
                return None

            plaintext = self.fernet.decrypt(
                bytes(credential.encrypted_value)
            )
            return plaintext.decode("utf-8")

    def list_credentials(self, workflow_id: int) -> list[str]:
        with self.SessionLocal() as session:
            keys = session.scalars(
                select(Credential.key)
                .where(Credential.workflow_id == workflow_id)
                .order_by(Credential.key)
            )
            return list(keys)

    def inject_credentials(self, workflow_id: int) -> dict:
        with self.SessionLocal() as session:
            credentials = session.scalars(
                select(Credential).where(
                    Credential.workflow_id == workflow_id
                )
            )
            return {
                credential.key: self.fernet.decrypt(
                    bytes(credential.encrypted_value)
                ).decode("utf-8")
                for credential in credentials
            }

    def delete(self, workflow_id: int, key: str) -> None:
        with self.SessionLocal.begin() as session:
            session.execute(
                delete(Credential).where(
                    Credential.workflow_id == workflow_id,
                    Credential.key == key,
                )
            )

    def close(self) -> None:
        self.engine.dispose()


__all__ = [
    "CredentialStore",
    "build_fernet",
    "decrypt_value",
    "delete_credential",
    "encrypt_value",
    "list_credential_keys",
    "load_credential_values",
    "save_credential",
]
