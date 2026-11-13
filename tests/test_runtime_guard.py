from __future__ import annotations

from runtime_guard import (
    PARENT_PID_ENV,
    parent_pid_from_env,
    watch_parent_process,
)


def test_parent_pid_from_env_accepts_positive_integer() -> None:
    assert parent_pid_from_env({PARENT_PID_ENV: "1234"}) == 1234


def test_parent_pid_from_env_rejects_invalid_values() -> None:
    assert parent_pid_from_env({}) is None
    assert parent_pid_from_env({PARENT_PID_ENV: "abc"}) is None
    assert parent_pid_from_env({PARENT_PID_ENV: "0"}) is None
    assert parent_pid_from_env({PARENT_PID_ENV: "-1"}) is None


def test_watch_parent_process_is_disabled_without_parent_pid(
    monkeypatch,
) -> None:
    monkeypatch.delenv(PARENT_PID_ENV, raising=False)
    monkeypatch.setattr(
        "runtime_guard._parent_process_id",
        lambda pid: None,
    )

    assert watch_parent_process() is False
