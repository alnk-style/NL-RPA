from __future__ import annotations

import json
import logging
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from runtime_paths import stable_agent_config_path
from desktop_agent.capabilities import (
    normalize_capabilities,
    normalize_environment,
)


DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
PAIRING_CODE_ENV = "NL_RPA_AGENT_PAIRING_CODE"
PAIRING_AUTO_ENV = "NL_RPA_AGENT_AUTO_PAIR"

logger = logging.getLogger(__name__)


def _normalize_server_url(value: str) -> str:
    return value.strip().rstrip("/")


def _resolve_config_path(
    configured_path: Path,
    *,
    explicit_path: bool,
) -> Path:
    if explicit_path:
        return configured_path
    normalized = str(configured_path).replace("/", "\\").lower()
    if (
        "\\appdata\\" in normalized
        and "com.nlrpa.desktop" in normalized
    ):
        return stable_agent_config_path()
    return configured_path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _api_url(server_url: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(server_url)
    scheme = {
        "ws": "http",
        "wss": "https",
    }.get(parsed.scheme.lower(), parsed.scheme.lower())
    if scheme not in {"http", "https"}:
        raise ValueError(
            "server_url must use http, https, ws, or wss",
        )
    base_path = parsed.path.rstrip("/")
    return urllib.parse.urlunsplit(
        (
            scheme,
            parsed.netloc,
            f"{base_path}{path}",
            "",
            "",
        )
    )


def _post_json(
    server_url: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        _api_url(server_url, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Agent pairing request failed with HTTP {exc.code}: {detail}",
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach the NL-RPA backend: {exc.reason}",
        ) from exc

    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError("Agent pairing response was not a JSON object")
    return result


def pair_agent(
    server_url: str,
    pairing_code: str | None = None,
    agent_name: str | None = None,
    *,
    environment: str = "local",
    capabilities: list[str] | None = None,
    human_access_url: str | None = None,
    human_access_instructions: str | None = None,
) -> tuple[int, str]:
    normalized_url = _normalize_server_url(server_url)
    code = pairing_code
    if not code:
        pairing = _post_json(
            normalized_url,
            "/api/agents/pairing-code",
            {},
        )
        code = str(pairing.get("pairing_code") or "")
    if not code:
        raise RuntimeError("A pairing code is required")

    normalized_environment = normalize_environment(environment)
    normalized_capabilities = normalize_capabilities(
        capabilities
        if capabilities is not None
        else (
            ["interactive_desktop"]
            if normalized_environment == "local"
            else []
        )
    )
    parsed = urllib.parse.urlsplit(normalized_url)
    connection_security = (
        "wss"
        if parsed.scheme.lower() in {"https", "wss"}
        else "ws"
    )
    response = _post_json(
        normalized_url,
        "/api/agents/pair",
        {
            "pairing_code": code,
            "name": agent_name or socket.gethostname(),
            "platform": "windows",
            "version": "0.1.0",
            "environment": normalized_environment,
            "capabilities": normalized_capabilities,
            "human_access_url": human_access_url,
            "human_access_instructions": (
                human_access_instructions
            ),
            "connection_security": connection_security,
        },
    )
    try:
        agent_id = int(response["agent_id"])
        token = str(response["token"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            "Agent pairing response did not include agent_id and token",
        ) from exc
    return agent_id, token


def _user_document_dirs() -> list[str]:
    home = Path.home()
    return [
        str(home / "Documents"),
        str(home / "Desktop"),
    ]


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValueError(f"{field_name} must be a list of strings")
    return list(value)


@dataclass
class AgentConfig:
    server_url: str
    agent_id: int
    token: str
    environment: str = "local"
    capabilities: list[str] = field(default_factory=list)
    human_access_url: str | None = None
    human_access_instructions: str | None = None
    allowed_apps: list[str] = field(default_factory=list)
    allowed_doc_dirs: list[str] = field(default_factory=list)
    log_dir: str = "logs"
    heartbeat_interval: int = 30

    def __post_init__(self) -> None:
        self._config_path = Path("config.json")
        self.environment = normalize_environment(self.environment)
        self.capabilities = normalize_capabilities(
            self.capabilities
            if self.capabilities
            else (
                ["interactive_desktop"]
                if self.environment == "local"
                else []
            )
        )

    @property
    def connection_security(self) -> str:
        scheme = urllib.parse.urlsplit(
            self.server_url
        ).scheme.lower()
        return "wss" if scheme in {"https", "wss"} else "ws"

    def hello_payload(self) -> dict[str, Any]:
        return {
            "type": "agent.hello",
            "platform": "windows",
            "version": "0.1.0",
            "environment": self.environment,
            "capabilities": list(self.capabilities),
            "human_access_url": self.human_access_url,
            "human_access_instructions": (
                self.human_access_instructions
            ),
            "connection_security": self.connection_security,
        }

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        *,
        server_url: str | None = None,
        pairing_code: str | None = None,
        agent_name: str | None = None,
        auto_pair: bool | None = None,
    ) -> AgentConfig:
        configured_path = Path(
            path
            or os.getenv("NL_RPA_AGENT_CONFIG")
            or "config.json"
        ).expanduser()
        config_path = _resolve_config_path(
            configured_path,
            explicit_path=path is not None,
        )
        server_override = server_url or os.getenv(
            "NL_RPA_AGENT_SERVER_URL",
        )
        pairing_code = pairing_code or os.getenv(PAIRING_CODE_ENV)
        agent_name = agent_name or os.getenv("NL_RPA_AGENT_NAME")
        if auto_pair is None:
            auto_pair = _env_bool(PAIRING_AUTO_ENV)

        needs_save = False
        if not config_path.exists():
            config = cls(
                server_url=_normalize_server_url(
                    server_override or DEFAULT_SERVER_URL,
                ),
                agent_id=0,
                token="",
                environment="local",
                capabilities=["interactive_desktop"],
                allowed_apps=[],
                allowed_doc_dirs=_user_document_dirs(),
            )
            needs_save = True
        else:
            raw = json.loads(
                config_path.read_text(encoding="utf-8-sig")
            )
            if not isinstance(raw, dict):
                raise ValueError("Agent config must be a JSON object")

            server_url_value = str(
                raw.get("server_url", DEFAULT_SERVER_URL),
            )
            agent_id_value = raw.get("agent_id", 0)
            try:
                agent_id = int(agent_id_value)
            except (TypeError, ValueError) as exc:
                raise ValueError("agent_id must be an integer") from exc

            heartbeat_value = raw.get("heartbeat_interval", 30)
            try:
                heartbeat_interval = int(heartbeat_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "heartbeat_interval must be an integer",
                ) from exc
            if heartbeat_interval <= 0:
                raise ValueError(
                    "heartbeat_interval must be greater than zero",
                )

            config = cls(
                server_url=server_url_value,
                agent_id=agent_id,
                token=str(raw.get("token", "")),
                environment=str(raw.get("environment", "local")),
                capabilities=(
                    _string_list(
                        raw.get("capabilities"),
                        "capabilities",
                    )
                    if "capabilities" in raw
                    else []
                ),
                human_access_url=(
                    str(raw["human_access_url"]).strip()
                    if raw.get("human_access_url")
                    else None
                ),
                human_access_instructions=(
                    str(raw["human_access_instructions"]).strip()
                    if raw.get("human_access_instructions")
                    else None
                ),
                allowed_apps=_string_list(
                    raw.get("allowed_apps"),
                    "allowed_apps",
                ),
                allowed_doc_dirs=_string_list(
                    raw.get("allowed_doc_dirs"),
                    "allowed_doc_dirs",
                ),
                log_dir=str(raw.get("log_dir", "logs")),
                heartbeat_interval=heartbeat_interval,
            )

        if server_override:
            normalized_server = _normalize_server_url(server_override)
            if normalized_server != config.server_url:
                config.server_url = normalized_server
                config.agent_id = 0
                config.token = ""
                needs_save = True

        if (not config.token or config.agent_id <= 0) and (
            pairing_code or auto_pair
        ):
            config.agent_id, config.token = pair_agent(
                config.server_url,
                pairing_code=pairing_code,
                agent_name=agent_name,
                environment=config.environment,
                capabilities=config.capabilities,
                human_access_url=config.human_access_url,
                human_access_instructions=(
                    config.human_access_instructions
                ),
            )
            needs_save = True

        normalized_server = _normalize_server_url(config.server_url)
        if normalized_server != config.server_url:
            needs_save = True
        config.server_url = normalized_server

        log_dir = Path(config.log_dir).expanduser()
        if not log_dir.is_absolute():
            log_dir = config_path.parent / log_dir
        config.log_dir = str(log_dir.resolve())

        config._config_path = config_path
        if needs_save:
            try:
                config.save()
            except OSError:
                logger.warning(
                    "Could not persist Desktop Agent configuration at %s; "
                    "continuing with the loaded settings",
                    config_path,
                    exc_info=True,
                )
        return config

    def save(self, path: str | Path | None = None) -> Path:
        config_path = (
            Path(path).expanduser()
            if path is not None
            else getattr(self, "_config_path", Path("config.json")).expanduser()
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        temp_path = config_path.with_name(f".{config_path.name}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temp_path, config_path)
        except OSError:
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
        self._config_path = config_path
        return config_path

    def update_allowlists(
        self,
        *,
        allowed_apps: list[str] | None = None,
        allowed_doc_dirs: list[str] | None = None,
    ) -> None:
        if allowed_apps is not None:
            self.allowed_apps = _string_list(
                allowed_apps,
                "allowed_apps",
            )
        if allowed_doc_dirs is not None:
            self.allowed_doc_dirs = _string_list(
                allowed_doc_dirs,
                "allowed_doc_dirs",
            )
        try:
            self.save()
        except OSError:
            logger.warning(
                "Could not persist Desktop Agent allowlist changes at %s; "
                "the changes remain active for this process",
                getattr(self, "_config_path", "config.json"),
                exc_info=True,
            )


def load_config(path: str | Path = "config.json") -> AgentConfig:
    return AgentConfig.load(path)


__all__ = ["AgentConfig", "load_config", "pair_agent"]
