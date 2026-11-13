from __future__ import annotations

from pathlib import Path

from app.engine.app_mcp.contracts import (
    ActionResult,
    AppProbe,
    FileArtifact,
    ToolCallContext,
    bind_call_context,
    current_call_context,
)


def test_success_envelope_contains_data_and_artifacts() -> None:
    artifact = FileArtifact(
        name="result.txt",
        path=Path("result.txt"),
        size=5,
        media_type="text/plain",
        summary="hello",
    )
    result = ActionResult.ok(
        message="completed",
        data={"value": 7},
        artifacts=[artifact],
    )

    envelope = result.to_envelope(
        app_id="demo_app",
        app_version="0.1.0",
        context=ToolCallContext(
            request_id="request-1",
            workflow_id=9,
        ),
    )

    assert envelope["ok"] is True
    assert envelope["message"] == "completed"
    assert envelope["data"] == {"value": 7}
    assert envelope["error"] is None
    assert envelope["artifacts"][0]["name"] == "result.txt"
    assert envelope["context"]["workflow_id"] == 9
    assert envelope["app"] == {"id": "demo_app", "version": "0.1.0"}


def test_failure_envelope_is_stable() -> None:
    result = ActionResult.fail(
        code="adapter_error",
        message="Adapter execution failed.",
    )

    envelope = result.to_envelope(
        app_id="demo_app",
        app_version="0.1.0",
        context=ToolCallContext(request_id="request-2"),
    )

    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "adapter_error",
        "message": "Adapter execution failed.",
        "details": {},
        "retryable": False,
    }


def test_call_context_is_scoped() -> None:
    context = ToolCallContext(
        request_id="request-3",
        workflow_id=12,
        execution_id=13,
    )

    with bind_call_context(context):
        assert current_call_context().workflow_id == 12


def test_probe_tracks_availability() -> None:
    probe = AppProbe(
        installed=True,
        running=True,
        requires_login=False,
        version="1.2.3",
    )

    assert probe.available is True
    assert probe.to_dict()["version"] == "1.2.3"
