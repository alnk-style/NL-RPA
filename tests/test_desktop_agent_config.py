from __future__ import annotations

import codecs
import json
from pathlib import Path

from desktop_agent import config as config_module


def test_agent_config_auto_pairs_from_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agent-config.json"
    pairing_calls: list[tuple[str, str | None, str | None]] = []

    def fake_pair_agent(
        server_url: str,
        pairing_code: str | None = None,
        agent_name: str | None = None,
        **_kwargs,
    ) -> tuple[int, str]:
        pairing_calls.append((server_url, pairing_code, agent_name))
        return 42, "paired-token"

    monkeypatch.setattr(config_module, "pair_agent", fake_pair_agent)
    monkeypatch.setenv("NL_RPA_AGENT_CONFIG", str(config_path))
    monkeypatch.setenv(
        "NL_RPA_AGENT_SERVER_URL",
        "http://127.0.0.1:8000/",
    )
    monkeypatch.setenv("NL_RPA_AGENT_AUTO_PAIR", "1")
    monkeypatch.setenv("NL_RPA_AGENT_NAME", "TEST-DESKTOP")

    loaded = config_module.AgentConfig.load()

    assert pairing_calls == [
        ("http://127.0.0.1:8000", None, "TEST-DESKTOP"),
    ]
    assert loaded.agent_id == 42
    assert loaded.token == "paired-token"
    assert loaded.server_url == "http://127.0.0.1:8000"
    assert config_path.exists()


def test_agent_config_repairs_when_server_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agent-config.json"
    config_path.write_text(
        """
{
  "server_url": "http://127.0.0.1:8000",
  "agent_id": 7,
  "token": "local-token",
  "allowed_apps": [],
  "allowed_doc_dirs": [],
  "log_dir": "logs",
  "heartbeat_interval": 30
}
""".strip(),
        encoding="utf-8",
    )
    pairing_calls: list[tuple[str, str | None, str | None]] = []

    def fake_pair_agent(
        server_url: str,
        pairing_code: str | None = None,
        agent_name: str | None = None,
        **_kwargs,
    ) -> tuple[int, str]:
        pairing_calls.append((server_url, pairing_code, agent_name))
        return 99, "remote-token"

    monkeypatch.setattr(config_module, "pair_agent", fake_pair_agent)

    loaded = config_module.AgentConfig.load(
        config_path,
        server_url="https://remote.example.com/",
        pairing_code="123456",
        agent_name="OFFICE-PC",
    )

    assert pairing_calls == [
        ("https://remote.example.com", "123456", "OFFICE-PC"),
    ]
    assert loaded.server_url == "https://remote.example.com"
    assert loaded.agent_id == 99
    assert loaded.token == "remote-token"


def test_agent_config_accepts_utf8_bom(tmp_path: Path) -> None:
    config_path = tmp_path / "agent-config.json"
    payload = {
        "server_url": "http://127.0.0.1:8000",
        "agent_id": 5,
        "token": "token",
        "allowed_apps": [],
        "allowed_doc_dirs": [],
        "log_dir": "logs",
        "heartbeat_interval": 30,
    }
    config_path.write_bytes(
        codecs.BOM_UTF8 + json.dumps(payload).encode("utf-8")
    )

    loaded = config_module.AgentConfig.load(config_path)

    assert loaded.agent_id == 5
    assert loaded.token == "token"


def test_agent_config_loads_without_rewriting_existing_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "agent-config.json"
    config_path.write_text(
        """
{
  "server_url": "http://127.0.0.1:8000",
  "agent_id": 7,
  "token": "local-token",
  "allowed_apps": [],
  "allowed_doc_dirs": [],
  "log_dir": "logs",
  "heartbeat_interval": 30
}
""".strip(),
        encoding="utf-8",
    )

    def fail_write(*args, **kwargs):
        raise PermissionError("configuration is read-only")

    monkeypatch.setattr(Path, "write_text", fail_write)

    loaded = config_module.AgentConfig.load(config_path)

    assert loaded.agent_id == 7
    assert loaded.token == "local-token"
    assert loaded.log_dir == str((tmp_path / "logs").resolve())

    loaded.update_allowlists(
        allowed_apps=["notepad.exe"],
        allowed_doc_dirs=[str(tmp_path)],
    )

    assert loaded.allowed_apps == ["notepad.exe"]
    assert loaded.allowed_doc_dirs == [str(tmp_path)]


def test_vm_agent_config_pairs_with_environment_and_capabilities(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "vm-agent.json"
    config_path.write_text(
        json.dumps(
            {
                "server_url": "https://rpa.example.com",
                "agent_id": 0,
                "token": "",
                "environment": "vm",
                "capabilities": ["isolated_desktop"],
                "human_access_url": (
                    "https://console.example.com/vm/1"
                ),
                "human_access_instructions": "Use the approved console.",
                "allowed_apps": [],
                "allowed_doc_dirs": [],
                "log_dir": "logs",
                "heartbeat_interval": 30,
            }
        ),
        encoding="utf-8",
    )
    sent: list[tuple[str, dict]] = []

    def fake_post(
        server_url: str,
        path: str,
        payload: dict,
    ) -> dict:
        sent.append((path, payload))
        return {"agent_id": 8, "token": "vm-token"}

    monkeypatch.setattr(config_module, "_post_json", fake_post)
    loaded = config_module.AgentConfig.load(
        config_path,
        pairing_code="654321",
        agent_name="vm-agent",
    )

    assert loaded.environment == "vm"
    assert loaded.capabilities == ["isolated_desktop"]
    assert loaded.connection_security == "wss"
    path, payload = sent[0]
    assert path == "/api/agents/pair"
    assert payload["environment"] == "vm"
    assert payload["capabilities"] == ["isolated_desktop"]
    assert payload["connection_security"] == "wss"
    assert payload["human_access_url"].startswith("https://")


def test_agent_hello_payload_has_no_token_or_cookie(
    tmp_path: Path,
) -> None:
    config = config_module.AgentConfig(
        server_url="https://rpa.example.com",
        agent_id=1,
        token="secret-token",
        environment="vm",
        capabilities=["isolated_desktop"],
    )
    payload = config.hello_payload()
    assert payload["type"] == "agent.hello"
    assert payload["environment"] == "vm"
    assert payload["capabilities"] == ["isolated_desktop"]
    assert "secret-token" not in json.dumps(payload)
