from __future__ import annotations

# ruff: noqa: E402

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.sandbox import DockerSandbox, generate_entrypoint_script


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        DOCKER_SANDBOX_IMAGE="rpa-sandbox:test",
        EXECUTION_TIMEOUT=2,
    )


class FakePage:
    def __init__(self) -> None:
        self.urls: list[str] = []

    async def goto(self, url: str, **_kwargs) -> None:
        self.urls.append(url)


class FakeContainer:
    def wait(self) -> dict[str, int]:
        return {"StatusCode": 0}

    def remove(self, force: bool = False) -> None:
        return None


class FakeContainerCollection:
    def __init__(self) -> None:
        self.run_kwargs: dict | None = None
        self.context_text: str | None = None

    def run(self, _image: str, **kwargs) -> FakeContainer:
        self.run_kwargs = kwargs
        host_dir = Path(next(iter(kwargs["volumes"])))
        self.context_text = (host_dir / "context.json").read_text(
            encoding="utf-8"
        )
        output_dir = host_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "output": "",
                    "error": None,
                    "screenshots": [],
                    "step_logs": [],
                }
            ),
            encoding="utf-8",
        )
        return FakeContainer()


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainerCollection()

    def ping(self) -> bool:
        return True


def test_extract_allowed_domains() -> None:
    sandbox = DockerSandbox(_config())
    workflow = SimpleNamespace(
        structured_plan={
            "target_domains": [
                "example.com",
                "https://*.trusted.example",
            ],
            "steps": [
                {
                    "action": "navigate",
                    "params": {"url": "https://api.example.com/v1"},
                },
                {
                    "action": "navigate",
                    "params": {"target_url": "https://cdn.other.net/a"},
                },
                {
                    "action": "click",
                    "target": ".login-button",
                },
            ],
        }
    )

    assert sandbox._extract_allowed_domains(workflow) == [
        "example.com",
        "*.trusted.example",
        "api.example.com",
        "cdn.other.net",
    ]


def test_entrypoint_blocks_unauthorized_domain() -> None:
    namespace: dict = {"__name__": "entrypoint_template_test"}
    exec(
        compile(
            generate_entrypoint_script(),
            "<entrypoint_template>",
            "exec",
        ),
        namespace,
    )

    fake_page = FakePage()
    restricted_page = namespace["NetworkRestrictedPage"](
        fake_page,
        ["example.com"],
    )

    with pytest.raises(PermissionError):
        asyncio.run(restricted_page.goto("https://blocked.example.net"))

    assert fake_page.urls == []

    asyncio.run(restricted_page.goto("https://example.com/login"))
    assert fake_page.urls == ["https://example.com/login"]


def test_execute_in_sandbox_applies_domain_allowlist() -> None:
    async def scenario() -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            sandbox = DockerSandbox(_config())
            sandbox.temp_root = Path(temp_dir) / "rpa_exec"
            sandbox.logs_dir = Path(temp_dir) / "logs"
            client = FakeDockerClient()
            sandbox.client = client
            workflow = SimpleNamespace(
                structured_plan={"target_domains": ["example.com"]}
            )

            await sandbox.execute_in_sandbox(
                "await page.goto('https://example.com')",
                {},
            )
            assert client.containers.run_kwargs is not None
            assert client.containers.run_kwargs["network_mode"] == "none"

            await sandbox.execute_in_sandbox(
                "await page.goto('https://example.com')",
                {},
                workflow,
            )
            assert client.containers.run_kwargs is not None
            return client.containers.run_kwargs

    run_kwargs = asyncio.run(scenario())

    assert run_kwargs["network_mode"] == "bridge"
    assert json.loads(run_kwargs["environment"]["ALLOWED_DOMAINS"]) == [
        "example.com"
    ]


def test_entrypoint_reads_credentials_from_environment() -> None:
    script = generate_entrypoint_script()

    assert "NL_RPA_CREDENTIALS" in script
    assert 'context["credentials"]' in script
    assert "os.environ.pop" in script


def test_execute_in_sandbox_moves_credentials_to_environment() -> None:
    async def scenario() -> tuple[dict, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            sandbox = DockerSandbox(_config())
            sandbox.temp_root = Path(temp_dir) / "rpa_exec"
            sandbox.logs_dir = Path(temp_dir) / "logs"
            client = FakeDockerClient()
            sandbox.client = client

            await sandbox.execute_in_sandbox(
                "pass",
                {
                    "credentials": {
                        "password": "s3cret-password"
                    },
                    "variables": {"query": "demo"},
                },
            )
            assert client.containers.run_kwargs is not None
            assert client.containers.context_text is not None
            return (
                client.containers.run_kwargs,
                client.containers.context_text,
            )

    run_kwargs, context_text = asyncio.run(scenario())

    context_payload = json.loads(context_text)
    assert "credentials" not in context_payload
    assert context_payload["variables"] == {"query": "demo"}
    assert "s3cret-password" not in context_text
    assert json.loads(
        run_kwargs["environment"]["NL_RPA_CREDENTIALS"]
    ) == {"password": "s3cret-password"}
