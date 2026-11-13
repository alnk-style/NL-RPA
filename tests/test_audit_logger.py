from __future__ import annotations

from pathlib import Path

from desktop_agent.audit_logger import AuditLogger


def test_audit_logger_survives_unwritable_log_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fail_mkdir(*args, **kwargs):
        raise PermissionError("log directory is read-only")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    result = AuditLogger(tmp_path / "logs").log(
        task_id=1,
        step_index=0,
        action_type="wait",
        params={},
        status="started",
    )

    assert result is None
