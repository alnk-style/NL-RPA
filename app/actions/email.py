from __future__ import annotations

from email.message import EmailMessage
from typing import Any

from app.actions.protocol import StepError, StepResult, StepStatus
from app.services.email import EmailService


async def execute_email_action(
    params: dict[str, Any],
    *,
    email_service: EmailService,
    execution_id: int | None = None,
    step_id: int | None = None,
) -> StepResult:
    recipients = _addresses(params.get("to"))
    if not recipients:
        return _failed("delivery_failed", "Email has no recipient")
    message = EmailMessage()
    message["To"] = ", ".join(recipients)
    cc = _addresses(params.get("cc"))
    bcc = _addresses(params.get("bcc"))
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = str(params.get("subject") or "")
    body = str(params.get("body") or "")
    if params.get("html") is True:
        message.set_content(body, subtype="html")
    else:
        message.set_content(body)
    attachments = params.get("attachments")
    if attachments is None:
        attachments = []
    if isinstance(attachments, str):
        attachments = [attachments]
    if not isinstance(attachments, list):
        return _failed(
            "delivery_failed",
            "Email attachments must be an array",
        )
    result = await email_service.send_message(
        message,
        attachments=attachments,
        execution_id=execution_id,
        step_id=step_id,
    )
    if result.status == "success":
        return StepResult(
            status=StepStatus.SUCCESS,
            message="Email sent",
            value=result.provider_message_id,
            verification={
                "message_id": result.provider_message_id,
                "attachments": result.attachment_checksums or [],
                "delivery_record_id": result.delivery_record_id,
            },
        )
    required = bool(params.get("required_delivery", True))
    status = StepStatus.FAILED if required else StepStatus.WARNING
    return StepResult(
        status=status,
        message=result.error or "Email delivery failed",
        error=StepError(
            category=result.error_category or "delivery_failed",
            message=result.error or "Email delivery failed",
        ),
        verification={
            "delivery_record_id": result.delivery_record_id,
        },
    )


def _addresses(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]


def _failed(category: str, message: str) -> StepResult:
    return StepResult(
        status=StepStatus.FAILED,
        message=message,
        error=StepError(category=category, message=message),
    )


__all__ = ["execute_email_action"]
