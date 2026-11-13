from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import uuid4

from app.engine.artifacts import ARTIFACT_DIR_NAME
from app.engine.runner import ExecutionResult, PlaywrightRunner
from app.security.redaction import redact_value, secret_values

if TYPE_CHECKING:
    from app.config import Settings
    from app.models.workflow import Workflow

try:
    import docker
except ImportError:
    docker = None


def _load_entrypoint_generator():
    template_path = (
        Path(__file__).resolve().parents[2]
        / "docker"
        / "entrypoint_template.py"
    )
    spec = importlib.util.spec_from_file_location(
        "rpa_entrypoint_template",
        template_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load Docker entrypoint template")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_entrypoint_script


generate_entrypoint_script = _load_entrypoint_generator()


class DockerSandbox:
    _DOMAIN_FIELDS = {
        "target_domains",
        "domains",
        "domain",
        "host",
        "hostname",
    }
    _URL_FIELDS = {
        "url",
        "target_url",
        "base_url",
    }

    def __init__(self, config: Settings):
        self.config = config
        self.image = config.DOCKER_SANDBOX_IMAGE
        self.timeout = config.EXECUTION_TIMEOUT
        self.client = None
        self.temp_root = Path(tempfile.gettempdir()) / "rpa_exec"
        self.logs_dir = Path("logs")

    async def execute_in_sandbox(
        self,
        code: str,
        context: dict[str, Any],
        workflow: Workflow | None = None,
    ) -> ExecutionResult:
        allowed_domains = self._extract_allowed_domains(workflow)
        network_mode = "bridge" if allowed_domains else "none"
        execution_id = uuid4().hex[:8]
        execution_dir = self.temp_root / execution_id
        output_dir = execution_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        for directory in (execution_dir, output_dir):
            try:
                directory.chmod(0o777)
            except OSError:
                pass

        host_context = dict(context) if isinstance(context, dict) else {}
        raw_credentials = host_context.pop("credentials", None)
        credentials: dict[str, str] = {}
        if isinstance(raw_credentials, dict):
            credentials = {
                str(key): str(value)
                for key, value in raw_credentials.items()
                if str(key).strip()
            }
        secrets = secret_values(credentials)

        (execution_dir / "main.py").write_text(code, encoding="utf-8")
        (execution_dir / "context.json").write_text(
            json.dumps(host_context, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (execution_dir / "entrypoint.py").write_text(
            generate_entrypoint_script(),
            encoding="utf-8",
        )

        started_at = time.perf_counter()
        container = None
        result_data: dict[str, Any] = {}
        error: str | None = None

        try:
            client = await asyncio.to_thread(self._get_client)
            available = await asyncio.to_thread(client.ping)
            if not available:
                error = "Docker daemon is unavailable"
            else:
                container = await asyncio.to_thread(
                    client.containers.run,
                    self.image,
                    command=["python", "/workspace/entrypoint.py"],
                    volumes={
                        str(execution_dir): {
                            "bind": "/workspace",
                            "mode": "rw",
                        }
                    },
                    mem_limit="512m",
                    cpu_quota=100000,
                    network_mode=network_mode,
                    environment={
                        "ALLOWED_DOMAINS": json.dumps(allowed_domains),
                        "NL_RPA_CREDENTIALS": json.dumps(
                            credentials,
                            ensure_ascii=False,
                        ),
                    },
                    detach=True,
                    labels={
                        "nl-rpa-sandbox": "true",
                        "nl-rpa-execution-id": execution_id,
                    },
                )

                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(container.wait),
                        timeout=self.timeout,
                    )
                except asyncio.TimeoutError:
                    error = (
                        f"Sandbox execution timed out after "
                        f"{self.timeout} seconds"
                    )
                    await asyncio.to_thread(container.kill)
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(container.wait),
                            timeout=5,
                        )
                    except Exception:
                        pass

                if error is None:
                    result_path = output_dir / "result.json"
                    if result_path.exists():
                        result_data = json.loads(
                            result_path.read_text(encoding="utf-8")
                        )
                        if secrets:
                            result_data = redact_value(
                                result_data,
                                secrets,
                            )
                    else:
                        error = "Sandbox did not produce output/result.json"
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
        finally:
            if container is not None:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception:
                    pass

        screenshots = self._copy_screenshots(
            execution_id,
            execution_dir,
            result_data,
        )
        artifacts = self._copy_artifacts(
            execution_id,
            execution_dir,
            result_data,
        )
        shutil.rmtree(execution_dir, ignore_errors=True)

        if error is None:
            error = result_data.get("error")

        return ExecutionResult(
            success=bool(result_data.get("success", False)) and error is None,
            output=str(result_data.get("output", "")),
            error=error,
            screenshots=screenshots,
            step_logs=list(result_data.get("step_logs", [])),
            duration_seconds=float(
                result_data.get(
                    "duration_seconds",
                    time.perf_counter() - started_at,
                )
            ),
            artifacts=artifacts,
        )

    async def execute_with_fallback(
        self,
        code: str,
        context: dict[str, Any],
        workflow: Workflow | None = None,
    ) -> ExecutionResult:
        docker_available = await asyncio.to_thread(
            self._check_docker_available
        )
        if docker_available:
            return await self.execute_in_sandbox(
                code,
                context,
                workflow,
            )

        print(
            "[WARN] Docker is unavailable; falling back to local "
            "PlaywrightRunner execution."
        )
        return await PlaywrightRunner(self.config).run(code, context)

    async def build_image(self) -> None:
        client = await asyncio.to_thread(self._get_client)
        await asyncio.to_thread(
            client.images.build,
            path=".",
            dockerfile="docker/Dockerfile.sandbox",
            tag=self.image,
            rm=True,
        )

    async def cleanup(self) -> None:
        client = await asyncio.to_thread(self._get_client)
        containers = await asyncio.to_thread(
            client.containers.list,
            all=True,
            filters={"label": "nl-rpa-sandbox=true"},
        )
        now = datetime.now(timezone.utc)

        for container in containers:
            created_at = self._parse_container_created_at(
                container.attrs.get("Created")
            )
            if created_at is None or now - created_at <= timedelta(hours=1):
                continue

            try:
                await asyncio.to_thread(container.stop, timeout=10)
            except Exception:
                pass
            try:
                await asyncio.to_thread(container.remove, force=True)
            except Exception:
                pass

    def _check_docker_available(self) -> bool:
        try:
            return bool(self._get_client().ping())
        except Exception:
            return False

    def _extract_allowed_domains(
        self,
        workflow: Workflow | None,
    ) -> list[str]:
        if isinstance(workflow, dict):
            structured_plan = workflow.get("structured_plan")
        else:
            structured_plan = getattr(workflow, "structured_plan", None)

        if not isinstance(structured_plan, dict):
            return []

        domains: list[str] = []
        self._collect_allowed_domains(structured_plan, domains)
        return domains

    @classmethod
    def _collect_allowed_domains(
        cls,
        value: Any,
        domains: list[str],
        key: str | None = None,
    ) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                cls._collect_allowed_domains(
                    child_value,
                    domains,
                    str(child_key).lower(),
                )
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                cls._collect_allowed_domains(item, domains, key)
            return

        if not isinstance(value, str):
            return

        should_parse = key in cls._DOMAIN_FIELDS or key in cls._URL_FIELDS
        if not should_parse:
            should_parse = urlparse(value).scheme in {"http", "https"}
        if not should_parse:
            return

        domain = cls._domain_from_value(value)
        if domain is not None and domain not in domains:
            domains.append(domain)

    @classmethod
    def _domain_from_value(cls, value: str) -> str | None:
        candidate = value.strip()
        if not candidate:
            return None

        if candidate.startswith("//"):
            parsed = urlparse(f"https:{candidate}")
        else:
            parsed = urlparse(candidate)
        if not parsed.scheme:
            parsed = urlparse(f"//{candidate}")

        try:
            hostname = parsed.hostname
        except ValueError:
            return None
        if not hostname:
            return None

        normalized = hostname.lower().rstrip(".")
        wildcard = normalized.startswith("*.")
        domain = normalized[2:] if wildcard else normalized
        if not cls._is_valid_domain(domain):
            return None

        return f"*.{domain}" if wildcard else domain

    @staticmethod
    def _is_valid_domain(domain: str) -> bool:
        if domain == "localhost":
            return True
        if ":" in domain:
            return True
        if not domain or "@" in domain or " " in domain:
            return False

        labels = domain.split(".")
        if len(labels) < 2:
            return False
        return all(
            label
            and not label.startswith("-")
            and not label.endswith("-")
            for label in labels
        )

    def _get_client(self):
        if self.client is not None:
            return self.client
        if docker is None:
            raise RuntimeError("Docker SDK is not installed")
        self.client = docker.from_env()
        return self.client

    def _copy_screenshots(
        self,
        execution_id: str,
        execution_dir: Path,
        result_data: dict[str, Any],
    ) -> list[str]:
        screenshot_values = result_data.get("screenshots", [])
        if not isinstance(screenshot_values, list):
            screenshot_values = []

        destination_dir = self.logs_dir / execution_id
        copied_paths: list[str] = []
        path_mapping: dict[str, str] = {}

        for screenshot in screenshot_values:
            source = self._resolve_workspace_path(execution_dir, str(screenshot))
            if source is None or not source.exists():
                continue
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / source.name
            shutil.copy2(source, destination)
            copied_path = str(destination)
            copied_paths.append(copied_path)
            path_mapping[str(screenshot)] = copied_path

        result_data["screenshots"] = copied_paths
        for step_log in result_data.get("step_logs", []):
            if not isinstance(step_log, dict):
                continue
            screenshot_path = step_log.get("screenshot_path")
            if screenshot_path in path_mapping:
                step_log["screenshot_path"] = path_mapping[screenshot_path]

        return copied_paths

    def _copy_artifacts(
        self,
        execution_id: str,
        execution_dir: Path,
        result_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        artifact_values = result_data.get("artifacts", [])
        if not isinstance(artifact_values, list):
            artifact_values = []

        destination_dir = self.logs_dir / execution_id / ARTIFACT_DIR_NAME
        copied: list[dict[str, Any]] = []

        for value in artifact_values:
            if not isinstance(value, dict):
                continue
            source = self._resolve_workspace_path(
                execution_dir,
                str(value.get("path") or ""),
            )
            if source is None or not source.is_file():
                continue

            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / source.name
            shutil.copy2(source, destination)

            record = dict(value)
            record["path"] = str(destination)
            record["size"] = destination.stat().st_size
            record.setdefault("delivered", False)
            record.setdefault("delivered_path", None)
            record.setdefault("error", None)
            copied.append(record)

        result_data["artifacts"] = copied
        return copied

    @staticmethod
    def _resolve_workspace_path(
        execution_dir: Path,
        path_value: str,
    ) -> Path | None:
        normalized = path_value.replace("\\", "/")
        if normalized.startswith("/workspace/"):
            normalized = normalized.removeprefix("/workspace/")
        candidate = (execution_dir / normalized).resolve()
        execution_root = execution_dir.resolve()
        if candidate != execution_root and execution_root not in candidate.parents:
            return None
        return candidate

    @staticmethod
    def _parse_container_created_at(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
