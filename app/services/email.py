from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid
from pathlib import Path
from typing import Any

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.database import AsyncSessionLocal
from app.engine.artifacts import resolve_within_roots
from app.models.app_setting import AppSetting
from app.models.credential import Credential
from app.models.delivery_record import DeliveryRecord
from app.security.credentials import decrypt_value, encrypt_value

EMAIL_SETTING_KEY = "email"
DEFAULT_CREDENTIAL_KEY = "smtp_default"
_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


@dataclass
class EmailSettings:
    host: str | None
    port: int
    username: str | None
    password: str | None
    from_email: str | None
    start_tls: bool = True
    default_recipient: str | None = None
    credential_key: str = DEFAULT_CREDENTIAL_KEY

    @classmethod
    def from_config(cls, config: Any) -> EmailSettings:
        return cls(
            host=getattr(config, "SMTP_HOST", None),
            port=int(getattr(config, "SMTP_PORT", 587)),
            username=getattr(config, "SMTP_USER", None),
            password=getattr(config, "SMTP_PASSWORD", None),
            from_email=(
                getattr(config, "SMTP_FROM_EMAIL", None)
                or getattr(config, "SMTP_FROM", None)
                or getattr(config, "SMTP_USER", None)
            ),
            start_tls=bool(getattr(config, "SMTP_START_TLS", True)),
            default_recipient=getattr(
                config,
                "SMTP_DEFAULT_RECIPIENT",
                None,
            ),
            credential_key=DEFAULT_CREDENTIAL_KEY,
        )


@dataclass
class EmailResult:
    status: str
    provider_message_id: str | None = None
    error_category: str | None = None
    error: str | None = None
    delivery_record_id: int | None = None
    attachment_checksums: list[dict[str, Any]] | None = None


class EmailService:
    def __init__(
        self,
        settings: EmailSettings,
        *,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        allowed_roots: list[str | Path] | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.allowed_roots = (
            [Path(root).expanduser().resolve() for root in allowed_roots]
            if allowed_roots is not None
            else None
        )

    async def send_message(
        self,
        message: EmailMessage,
        *,
        attachments: list[str | Path] | None = None,
        execution_id: int | None = None,
        step_id: int | None = None,
    ) -> EmailResult:
        requested_attachments = self._describe_attachments(attachments or [])
        record_id = None
        if execution_id is not None:
            record_id = await self._create_record(
                execution_id,
                step_id,
                message,
                attachments=requested_attachments,
            )
        if not self.settings.host:
            return await self._fail(
                "delivery_failed",
                "SMTP is not configured",
                record_id,
            )
        recipients = self._recipients(message)
        invalid = [
            address
            for address in recipients
            if not _valid_email(address)
        ]
        if invalid or not recipients:
            return await self._fail(
                "delivery_failed",
                (
                    "Email has no recipient"
                    if not recipients
                    else f"Invalid email recipient: {invalid[0]}"
                ),
                record_id,
            )
        disallowed = [
            item
            for item in requested_attachments
            if item.get("allowed") is False
        ]
        if disallowed:
            return await self._fail(
                "permission_denied",
                f"Attachment path is not allowed: {disallowed[0]['path']}",
                record_id,
            )
        try:
            checksums = self._attach_files(message, attachments or [])
        except FileNotFoundError as exc:
            return await self._fail(
                "artifact_missing",
                str(exc),
                record_id,
            )
        except PermissionError as exc:
            return await self._fail(
                "permission_denied",
                str(exc),
                record_id,
            )
        except ValueError as exc:
            return await self._fail(
                "artifact_missing",
                str(exc),
                record_id,
            )
        if not message.get("From"):
            message["From"] = self.settings.from_email or ""
        if not _valid_email(str(message.get("From") or "")):
            return await self._fail(
                "delivery_failed",
                "Sender email is not configured or invalid",
                record_id,
            )
        if not message.get("Message-ID"):
            message["Message-ID"] = make_msgid()
        if message.get_all("Bcc"):
            del message["Bcc"]
        try:
            await aiosmtplib.send(
                message,
                recipients=recipients,
                hostname=self.settings.host,
                port=self.settings.port,
                username=self.settings.username,
                password=self.settings.password,
                start_tls=self.settings.start_tls,
                timeout=10,
            )
        except Exception as exc:
            return await self._fail(
                "delivery_failed",
                str(exc) or exc.__class__.__name__,
                record_id,
            )
        message_id = str(message.get("Message-ID") or "")
        await self._finish_record(
            record_id,
            status="success",
            provider_message_id=message_id,
            attachments=checksums,
        )
        return EmailResult(
            status="success",
            provider_message_id=message_id,
            delivery_record_id=record_id,
            attachment_checksums=checksums,
        )

    async def test_connection(self) -> EmailResult:
        if not self.settings.host:
            return EmailResult(
                status="failed",
                error_category="delivery_failed",
                error="SMTP is not configured",
            )
        client = aiosmtplib.SMTP(
            hostname=self.settings.host,
            port=self.settings.port,
            use_tls=False,
            start_tls=self.settings.start_tls,
            timeout=10,
        )
        try:
            await client.connect()
            if self.settings.username:
                await client.login(
                    self.settings.username,
                    self.settings.password or "",
                )
        except Exception as exc:
            return EmailResult(
                status="failed",
                error_category="delivery_failed",
                error=str(exc) or exc.__class__.__name__,
            )
        finally:
            try:
                await client.quit()
            except Exception:
                pass
        return EmailResult(status="success")

    async def send_test(self, recipient: str) -> EmailResult:
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = "NL-RPA SMTP test"
        message.set_content("SMTP configuration test succeeded.")
        return await self.send_message(message)

    async def _create_record(
        self,
        execution_id: int,
        step_id: int | None,
        message: EmailMessage,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> int:
        async with self.session_factory() as session:
            statement = select(DeliveryRecord).where(
                DeliveryRecord.execution_id == execution_id,
                DeliveryRecord.channel == "email",
            )
            if step_id is None:
                statement = statement.where(
                    DeliveryRecord.step_id.is_(None)
                )
            else:
                statement = statement.where(
                    DeliveryRecord.step_id == step_id
                )
            record = await session.scalar(
                statement.order_by(DeliveryRecord.id.desc()).limit(1)
            )
            recipient = {
                "to": _header_addresses(message, "To"),
                "cc": _header_addresses(message, "Cc"),
                "bcc": _header_addresses(message, "Bcc"),
            }
            if record is None:
                recipient["attempt_count"] = 1
                record = DeliveryRecord(
                    execution_id=execution_id,
                    step_id=step_id,
                    channel="email",
                    status="pending",
                    recipient=recipient,
                    attachment_paths=list(attachments or []),
                )
                session.add(record)
            else:
                previous = (
                    record.recipient
                    if isinstance(record.recipient, dict)
                    else {}
                )
                recipient["attempt_count"] = (
                    int(previous.get("attempt_count") or 1) + 1
                )
                record.recipient = recipient
                record.attachment_paths = list(attachments or [])
                record.status = "pending"
                record.provider_message_id = None
                record.error = None
                record.finished_at = None
            await session.commit()
            await session.refresh(record)
            return int(record.id)

    async def _finish_record(
        self,
        record_id: int | None,
        *,
        status: str,
        provider_message_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        if record_id is None:
            return
        async with self.session_factory() as session:
            record = await session.get(DeliveryRecord, record_id)
            if record is None:
                return
            record.status = status
            record.provider_message_id = provider_message_id
            if attachments is not None:
                record.attachment_paths = list(attachments)
            record.error = error
            record.finished_at = datetime.utcnow()
            await session.commit()

    async def _fail(
        self,
        category: str,
        error: str,
        record_id: int | None,
    ) -> EmailResult:
        await self._finish_record(
            record_id,
            status="failed",
            error=error,
        )
        return EmailResult(
            status="failed",
            error_category=category,
            error=error,
            delivery_record_id=record_id,
        )

    def _attach_files(
        self,
        message: EmailMessage,
        attachments: list[str | Path],
    ) -> list[dict[str, Any]]:
        checksums: list[dict[str, Any]] = []
        for raw in attachments:
            path = Path(raw).expanduser().resolve()
            if not self._is_allowed(path):
                raise PermissionError(
                    f"Attachment path is not allowed: {path}"
                )
            if not path.is_file():
                raise FileNotFoundError(f"Attachment not found: {path}")
            data = path.read_bytes()
            if not data:
                raise ValueError(f"Attachment is empty: {path}")
            maintype, subtype = (
                "application",
                "octet-stream",
            )
            message.add_attachment(
                data,
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )
            checksums.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        return checksums

    @staticmethod
    def _recipients(message: EmailMessage) -> list[str]:
        return [
            address
            for _name, address in getaddresses(
                [
                    *message.get_all("To", []),
                    *message.get_all("Cc", []),
                    *message.get_all("Bcc", []),
                ]
            )
            if address
        ]

    def _describe_attachments(
        self,
        attachments: list[str | Path],
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for raw in attachments:
            path = Path(raw).expanduser().resolve()
            allowed = self._is_allowed(path)
            exists = path.is_file()
            size = path.stat().st_size if allowed and exists else None
            records.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "allowed": allowed,
                    "exists": exists,
                    "size": size,
                    "sha256": (
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        if (
                            allowed
                            and exists
                            and size is not None
                            and size > 0
                        )
                        else None
                    ),
                }
            )
        return records

    def _is_allowed(self, path: Path) -> bool:
        if self.allowed_roots is None:
            return True
        return resolve_within_roots(path, self.allowed_roots) is not None


def _header_addresses(
    message: EmailMessage,
    header: str,
) -> list[str]:
    return [
        address
        for _name, address in getaddresses(message.get_all(header, []))
        if address
    ]


def _valid_email(value: str) -> bool:
    return bool(_EMAIL_PATTERN.fullmatch(value.strip()))


async def load_email_settings(
    session: AsyncSession,
    config: Settings,
    user_id: int,
    *,
    credential_key: str | None = None,
    include_password: bool = True,
) -> EmailSettings:
    setting = await session.scalar(
        select(AppSetting).where(
            AppSetting.user_id == user_id,
            AppSetting.key == EMAIL_SETTING_KEY,
        )
    )
    value = setting.value if setting is not None else {}
    if not isinstance(value, dict):
        value = {}
    fallback = EmailSettings.from_config(config)
    has_stored_settings = setting is not None
    selected_key = str(
        credential_key
        or value.get("credential_key")
        or DEFAULT_CREDENTIAL_KEY
    ).strip() or DEFAULT_CREDENTIAL_KEY
    credential = await session.scalar(
        select(Credential).where(
            Credential.user_id == user_id,
            Credential.scope == "email",
            Credential.key == selected_key,
        )
    )
    password = fallback.password if include_password else ""
    if credential is not None and include_password:
        password = decrypt_value(
            config,
            bytes(credential.encrypted_value),
        )
    elif not include_password:
        password = ""
    return EmailSettings(
        host=(
            (str(value.get("host") or "").strip() or None)
            if has_stored_settings
            else fallback.host
        ),
        port=(
            int(value.get("port") or 587)
            if has_stored_settings
            else fallback.port
        ),
        username=(
            (str(value.get("username") or "").strip() or None)
            if has_stored_settings
            else fallback.username
        ),
        password=password,
        from_email=(
            (str(value.get("from_email") or "").strip() or None)
            if has_stored_settings
            else fallback.from_email
        ),
        start_tls=(
            bool(value.get("start_tls", True))
            if has_stored_settings
            else fallback.start_tls
        ),
        default_recipient=(
            (
                str(value.get("default_recipient") or "").strip()
                or None
            )
            if has_stored_settings
            else fallback.default_recipient
        ),
        credential_key=selected_key,
    )


async def save_email_settings(
    session: AsyncSession,
    config: Settings,
    user_id: int,
    value: dict[str, Any],
    *,
    password: str | None = None,
) -> EmailSettings:
    credential_key = str(
        value.get("credential_key") or DEFAULT_CREDENTIAL_KEY
    ).strip() or DEFAULT_CREDENTIAL_KEY
    stored_value = {
        "host": str(value.get("host") or "").strip(),
        "port": int(value.get("port") or 587),
        "username": str(value.get("username") or "").strip() or None,
        "from_email": str(value.get("from_email") or "").strip() or None,
        "start_tls": bool(value.get("start_tls", True)),
        "default_recipient": (
            str(value.get("default_recipient") or "").strip() or None
        ),
        "credential_key": credential_key,
    }
    setting = await session.scalar(
        select(AppSetting).where(
            AppSetting.user_id == user_id,
            AppSetting.key == EMAIL_SETTING_KEY,
        )
    )
    if setting is None:
        setting = AppSetting(
            user_id=user_id,
            key=EMAIL_SETTING_KEY,
            value=stored_value,
        )
        session.add(setting)
    else:
        setting.value = stored_value

    if password is not None:
        credential = await session.scalar(
            select(Credential).where(
                Credential.user_id == user_id,
                Credential.scope == "email",
                Credential.key == credential_key,
            )
        )
        encrypted = encrypt_value(config, password)
        if credential is None:
            session.add(
                Credential(
                    user_id=user_id,
                    workflow_id=None,
                    key=credential_key,
                    scope="email",
                    name="SMTP password",
                    encrypted_value=encrypted,
                )
            )
        else:
            credential.encrypted_value = encrypted
    await session.flush()
    return await load_email_settings(
        session,
        config,
        user_id,
        credential_key=credential_key,
        include_password=False,
    )


def email_settings_payload(
    value: EmailSettings,
    *,
    password_configured: bool,
) -> dict[str, Any]:
    configured = bool(value.host and value.from_email)
    return {
        "host": value.host or "",
        "port": value.port,
        "username": value.username or "",
        "from_email": value.from_email or "",
        "start_tls": value.start_tls,
        "default_recipient": value.default_recipient or "",
        "credential_key": value.credential_key,
        "configured": configured,
        "password_configured": password_configured,
    }


def email_result_payload(result: EmailResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "provider_message_id": result.provider_message_id,
        "error_category": result.error_category,
        "error": result.error,
        "delivery_record_id": result.delivery_record_id,
        "attachment_checksums": result.attachment_checksums or [],
    }


__all__ = [
    "DEFAULT_CREDENTIAL_KEY",
    "EMAIL_SETTING_KEY",
    "EmailResult",
    "EmailService",
    "EmailSettings",
    "email_result_payload",
    "email_settings_payload",
    "load_email_settings",
    "save_email_settings",
]
