from __future__ import annotations

from typing import Any

CAPTCHA_MARKERS = (
    "captcha",
    "安全验证",
    "扫码",
    "二维码",
    "滑块",
    "人机验证",
)
MFA_MARKERS = (
    "短信验证码",
    "动态码",
    "一次性验证码",
    "one-time code",
    "two-factor",
    "mfa",
    "otp",
)


def classify_interaction_kind(
    message: str,
    *,
    current_url: str = "",
    password_input_count: int = 0,
    one_time_code_input_count: int = 0,
    captcha_marker_count: int = 0,
    qr_marker_count: int = 0,
) -> str:
    text = " ".join(
        [
            str(message or ""),
            str(current_url or ""),
        ]
    ).casefold()
    mfa_detected = (
        one_time_code_input_count > 0
        or any(marker.casefold() in text for marker in MFA_MARKERS)
    )
    if (
        captcha_marker_count > 0
        or qr_marker_count > 0
        or any(marker.casefold() in text for marker in CAPTCHA_MARKERS)
    ):
        return "captcha_required"
    if mfa_detected:
        return "mfa_required"
    if "验证码" in text:
        return "captcha_required"
    if password_input_count > 0:
        return "login_required"
    return "login_required"


def web_verification_spec(
    *,
    current_url: str,
    success_selector: str | None,
    login_paths: list[str] | tuple[str, ...] | None,
) -> dict[str, Any] | None:
    selector = str(success_selector or "").strip()
    paths = [
        str(item).strip()
        for item in (login_paths or [])
        if str(item).strip()
    ]
    url = str(current_url or "").strip()
    if not selector and not paths and not url:
        return None
    if selector:
        return {
            "type": "web_logged_in",
            "success_selector": selector,
            "login_paths": paths,
            "current_url": url,
        }
    return {
        "type": "web_logged_out",
        "login_paths": paths,
        "current_url": url,
    }


__all__ = [
    "CAPTCHA_MARKERS",
    "MFA_MARKERS",
    "classify_interaction_kind",
    "web_verification_spec",
]
