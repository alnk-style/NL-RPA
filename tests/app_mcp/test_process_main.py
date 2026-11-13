from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.engine.mcp_client import MCPClient, MCPServerConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_APP = PROJECT_ROOT / "mcp_apps" / "demo_app"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_port_closed(port: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _port_open(port):
            return True
        time.sleep(0.05)
    return False


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_app_main_internal_worker_roundtrip(
    tmp_path: Path,
) -> None:
    port = _free_port()
    export_root = tmp_path / "exports"
    env = os.environ.copy()
    env["NL_RPA_DATA_DIR"] = str(tmp_path / "data")
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "app" / "main.py"),
            "--internal-app-mcp-worker",
            "--app",
            "demo_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--export-root",
            str(export_root),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    async def roundtrip() -> tuple[list[str], object]:
        client = MCPClient(
            MCPServerConfig(
                name="process-main-demo",
                transport="http",
                url=f"http://127.0.0.1:{port}/mcp",
                timeout=5.0,
            ),
            prefer_sdk=False,
        )
        deadline = time.monotonic() + 15
        last_error: Exception | None = None
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = (
                        process.stdout.read()
                        if process.stdout
                        else ""
                    )
                    raise AssertionError(
                        "worker exited early with "
                        f"{process.returncode}: {output}"
                    )
                if _port_open(port):
                    try:
                        tools = await client.list_tools()
                        call = await client.call_tool(
                            "demo_status",
                            {},
                        )
                        return [
                            tool["name"] for tool in tools
                        ], call
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
                await asyncio.sleep(0.1)
            raise AssertionError(
                f"worker did not become ready: {last_error}"
            )
        finally:
            await client.aclose()

    try:
        names, call = asyncio.run(roundtrip())
    finally:
        _stop_process(process)

    assert names == ["demo_status", "demo_execute", "demo_export"]
    assert call.success is True
    assert call.result["structured"]["ok"] is True
    assert _wait_port_closed(port)


def test_frozen_worker_only_resolves_builtin_apps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.engine.app_mcp import process_main

    bundle_root = tmp_path / "bundle"
    builtin_demo = bundle_root / "mcp_apps" / "demo_app"
    shutil.copytree(DEMO_APP, builtin_demo)
    external_demo = tmp_path / "external" / "demo_app"
    shutil.copytree(DEMO_APP, external_demo)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle_root), raising=False)

    assert process_main._resolve_app_dir("demo_app") == builtin_demo.resolve()
    with pytest.raises(SystemExit):
        process_main._resolve_app_dir(str(external_demo))
