from __future__ import annotations

from app.engine.human_intervention import (
    classify_interaction_kind,
    web_verification_spec,
)


def test_classify_interaction_kind_distinguishes_login_mfa_and_captcha() -> None:
    assert classify_interaction_kind(
        "AUTH_REQUIRED: 登录已失效",
        current_url="https://example.com/login",
        password_input_count=1,
    ) == "login_required"
    assert classify_interaction_kind(
        "请输入短信验证码",
        current_url="https://example.com/verify",
        one_time_code_input_count=1,
    ) == "mfa_required"
    assert classify_interaction_kind(
        "请完成安全验证",
        current_url="https://example.com/challenge",
        captcha_marker_count=1,
    ) == "captcha_required"
    assert classify_interaction_kind(
        "请扫码登录",
        current_url="https://example.com/qr-login",
        qr_marker_count=1,
    ) == "captcha_required"


def test_web_verification_spec_uses_selector_or_logged_out_check() -> None:
    with_selector = web_verification_spec(
        current_url="https://example.com/home",
        success_selector="[data-user]",
        login_paths=["/login"],
    )
    assert with_selector == {
        "type": "web_logged_in",
        "success_selector": "[data-user]",
        "login_paths": ["/login"],
        "current_url": "https://example.com/home",
    }

    without_selector = web_verification_spec(
        current_url="https://example.com/login",
        success_selector=None,
        login_paths=["/login"],
    )
    assert without_selector["type"] == "web_logged_out"
    assert without_selector["login_paths"] == ["/login"]


def test_web_verification_spec_rejects_missing_signals() -> None:
    assert web_verification_spec(
        current_url="",
        success_selector=None,
        login_paths=[],
    ) is None
