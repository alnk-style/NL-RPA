from __future__ import annotations

import ast
import asyncio
import json
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.runner import ExecutionResult
from app.engine.sandbox import DockerSandbox, generate_entrypoint_script


def _config(timeout: float = 2) -> SimpleNamespace:
    return SimpleNamespace(
        DOCKER_SANDBOX_IMAGE="rpa-sandbox:test",
        EXECUTION_TIMEOUT=timeout,
    )


class FakeContainer:
    def __init__(self) -> None:
        self.killed = False
        self.removed = False

    def wait(self) -> dict[str, int]:
        return {"StatusCode": 0}

    def kill(self) -> None:
        self.killed = True

    def remove(self, force: bool = False) -> None:
        self.removed = force

    def logs(self) -> bytes:
        return b""


class FakeContainerCollection:
    def __init__(self) -> None:
        self.container = FakeContainer()
        self.run_kwargs: dict | None = None

    def run(self, _image: str, **kwargs) -> FakeContainer:
        self.run_kwargs = kwargs
        volumes = kwargs["volumes"]
        host_dir = Path(next(iter(volumes)))
        output_dir = host_dir / "output"
        screenshot_dir = output_dir / "screenshots"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = screenshot_dir / "step_00.png"
        screenshot_path.write_bytes(b"fake-png")

        artifact_dir = output_dir / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "report.csv"
        artifact_path.write_text("a,b\n1,2\n", encoding="utf-8")

        result = {
            "success": True,
            "output": "sandbox ok",
            "error": None,
            "screenshots": ["output/screenshots/step_00.png"],
            "artifacts": [
                {
                    "name": "report.csv",
                    "path": "output/artifacts/report.csv",
                    "requested_path": None,
                    "size": artifact_path.stat().st_size,
                    "delivered": False,
                    "delivered_path": None,
                    "error": None,
                }
            ],
            "step_logs": [
                {
                    "step_index": 0,
                    "action": "goto",
                    "target": "https://example.com",
                    "selector": None,
                    "status": "success",
                    "screenshot_path": "output/screenshots/step_00.png",
                    "error": None,
                    "started_at": 1.0,
                    "finished_at": 1.1,
                }
            ],
            "duration_seconds": 0.42,
        }
        (output_dir / "result.json").write_text(
            json.dumps(result),
            encoding="utf-8",
        )
        return self.container


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainerCollection()
        self.images = MagicMock()

    def ping(self) -> bool:
        return True


class SlowContainer(FakeContainer):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def wait(self) -> dict[str, int]:
        self.release.wait(timeout=5)
        return {"StatusCode": 137}

    def kill(self) -> None:
        self.killed = True
        self.release.set()


class SlowDockerClient(FakeDockerClient):
    def __init__(self) -> None:
        super().__init__()
        self.containers = MagicMock()
        self.slow_container = SlowContainer()
        self.containers.run.return_value = self.slow_container


class CleanupContainer:
    def __init__(self, created_at: datetime) -> None:
        self.attrs = {
            "Created": created_at.isoformat().replace("+00:00", "Z"),
        }
        self.stopped = False
        self.removed = False

    def stop(self, timeout: int = 10) -> None:
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = force


def test_check_docker_available() -> None:
    sandbox = DockerSandbox(_config())
    client = MagicMock()
    client.ping.return_value = True
    sandbox.client = client

    assert sandbox._check_docker_available() is True

    client.ping.side_effect = RuntimeError("docker unavailable")
    assert sandbox._check_docker_available() is False


def test_execute_in_sandbox() -> None:
    async def scenario() -> tuple[ExecutionResult, FakeDockerClient, bool]:
        with tempfile.TemporaryDirectory() as temp_dir:
            sandbox = DockerSandbox(_config())
            sandbox.temp_root = Path(temp_dir) / "rpa_exec"
            sandbox.logs_dir = Path(temp_dir) / "logs"
            client = FakeDockerClient()
            sandbox.client = client

            result = await sandbox.execute_in_sandbox(
                "await page.goto('https://example.com')",
                {"account": "demo"},
            )
            screenshot_exists = bool(
                result.screenshots
                and Path(result.screenshots[0]).exists()
            )
            artifact_exists = bool(
                result.artifacts
                and Path(result.artifacts[0]["path"]).exists()
            )
            return result, client, screenshot_exists, artifact_exists

    result, client, screenshot_exists, artifact_exists = asyncio.run(
        scenario()
    )
    run_kwargs = client.containers.run_kwargs

    assert result.success is True
    assert result.output == "sandbox ok"
    assert result.error is None
    assert result.duration_seconds == 0.42
    assert len(result.screenshots) == 1
    assert screenshot_exists is True
    assert result.step_logs[0]["screenshot_path"] == result.screenshots[0]
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["name"] == "report.csv"
    assert artifact_exists is True
    assert client.containers.container.removed is True

    assert run_kwargs is not None
    assert run_kwargs["command"] == ["python", "/workspace/entrypoint.py"]
    assert run_kwargs["mem_limit"] == "512m"
    assert run_kwargs["cpu_quota"] == 100000
    assert run_kwargs["volumes"]
    assert list(run_kwargs["volumes"].values())[0]["bind"] == "/workspace"


def test_execute_timeout() -> None:
    async def scenario() -> tuple[ExecutionResult, SlowDockerClient]:
        with tempfile.TemporaryDirectory() as temp_dir:
            sandbox = DockerSandbox(_config(timeout=0.05))
            sandbox.temp_root = Path(temp_dir) / "rpa_exec"
            sandbox.logs_dir = Path(temp_dir) / "logs"
            client = SlowDockerClient()
            sandbox.client = client

            result = await sandbox.execute_in_sandbox("await asyncio.sleep(10)", {})
            return result, client

    result, client = asyncio.run(scenario())

    assert result.success is False
    assert result.error is not None
    assert "timed out" in result.error.lower()
    assert client.slow_container.killed is True
    assert client.slow_container.removed is True


def test_fallback_to_local() -> None:
    async def scenario() -> tuple[ExecutionResult, MagicMock, MagicMock]:
        sandbox = DockerSandbox(_config())
        expected = ExecutionResult(
            success=True,
            output="local fallback",
            error=None,
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )

        with (
            patch.object(
                sandbox,
                "_check_docker_available",
                return_value=False,
            ),
            patch("app.engine.sandbox.PlaywrightRunner") as runner_class,
            patch("builtins.print") as print_mock,
        ):
            runner_class.return_value.run = AsyncMock(return_value=expected)
            result = await sandbox.execute_with_fallback("pass", {"key": "value"})
            return result, runner_class, print_mock

    result, runner_class, print_mock = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "local fallback"
    runner_class.assert_called_once()
    runner_class.return_value.run.assert_awaited_once_with(
        "pass",
        {"key": "value"},
    )
    printed = " ".join(str(call.args[0]) for call in print_mock.call_args_list)
    assert "Docker" in printed
    assert "falling back" in printed


def test_generate_entrypoint() -> None:
    script = generate_entrypoint_script()
    tree = ast.parse(script)

    async_functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert {
        "main",
        "screenshot",
        "extract_table",
        "wait_for",
        "save_file",
    } <= async_functions
    compile(script, "<entrypoint>", "exec")
    assert "/workspace/context.json" in script
    assert "/workspace/main.py" in script
    assert "/workspace/output/result.json" in script


def test_generated_entrypoint_helpers_support_legacy_calls() -> None:
    script = generate_entrypoint_script()
    tree = ast.parse(script)
    helper_nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.ClassDef)
            and node.name == "_CompletedAwaitable"
        )
    ]
    main_function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "main"
    )
    helper_nodes.extend(
        node
        for node in ast.walk(main_function)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"log", "wait_for"}
    )
    module = ast.Module(body=helper_nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    class FakePage:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        async def wait_for_timeout(self, milliseconds: int) -> None:
            self.calls.append(("wait_for_timeout", milliseconds))

        async def wait_for_selector(
            self,
            selector: str,
            timeout: int,
        ) -> str:
            self.calls.append(("wait_for_selector", selector, timeout))
            return selector

    async def scenario() -> tuple[list[str], list[tuple]]:
        namespace: dict = {"output": []}
        exec(  # noqa: S102
            compile(module, "<entrypoint-helpers>", "exec"),
            namespace,
        )
        page = FakePage()
        namespace["page"] = page

        namespace["log"]("sync output")
        await namespace["log"]("awaited output")
        await namespace["wait_for"](2000)
        await namespace["wait_for"]("#ready", 1000)
        return namespace["output"], page.calls

    output, calls = asyncio.run(scenario())

    assert output == ["sync output", "awaited output"]
    assert ("wait_for_timeout", 2000) in calls
    assert ("wait_for_selector", "#ready", 1000) in calls


def test_build_image() -> None:
    async def scenario() -> MagicMock:
        sandbox = DockerSandbox(_config())
        client = MagicMock()
        client.images.build.return_value = (MagicMock(), [])
        sandbox.client = client

        await sandbox.build_image()
        return client

    client = asyncio.run(scenario())

    client.images.build.assert_called_once_with(
        path=".",
        dockerfile="docker/Dockerfile.sandbox",
        tag="rpa-sandbox:test",
        rm=True,
    )


def test_cleanup_old_containers() -> None:
    async def scenario() -> MagicMock:
        sandbox = DockerSandbox(_config())
        client = MagicMock()
        now = datetime.now(timezone.utc)
        old_container = CleanupContainer(now - timedelta(hours=2))
        recent_container = CleanupContainer(now - timedelta(minutes=15))
        client.containers.list.return_value = [
            old_container,
            recent_container,
        ]
        sandbox.client = client

        await sandbox.cleanup()
        return client

    client = asyncio.run(scenario())
    old_container, recent_container = client.containers.list.return_value

    assert old_container.stopped is True
    assert old_container.removed is True
    assert recent_container.stopped is False
    assert recent_container.removed is False
    client.containers.list.assert_called_once_with(
        all=True,
        filters={"label": "nl-rpa-sandbox=true"},
    )


if __name__ == "__main__":
    test_check_docker_available()
    test_execute_in_sandbox()
    test_execute_timeout()
    test_fallback_to_local()
    test_generate_entrypoint()
    test_build_image()
    test_cleanup_old_containers()
    print("sandbox tests passed")
