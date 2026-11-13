"""Run one manifest-verified App MCP worker over loopback HTTP."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Sequence

import uvicorn

from app.engine.agent_connection_manager import AgentConnectionManager
from app.engine.app_mcp.contracts import ToolCallContext
from app.engine.app_mcp.manifest import load_manifest
from app.engine.app_mcp.runtime import AppRuntime, load_runtime
from app.engine.app_mcp.server import AppMCPServer, create_http_app
from app.engine.connectors import DesktopBridgeConnector
from app.engine.desktop_dispatcher import DesktopDispatcher
from runtime_paths import app_mcp_logs_dir, builtin_apps_dir

INTERNAL_WORKER_FLAG = "--internal-app-mcp-worker"
_APP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def is_internal_worker_invocation(argv: Sequence[str]) -> bool:
    return INTERNAL_WORKER_FLAG in argv


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a built-in App MCP server.",
    )
    parser.add_argument(
        INTERNAL_WORKER_FLAG,
        action="store_true",
        dest="internal_worker",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--app",
        required=True,
        help="Built-in app ID or source-mode app manifest directory.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--export-root", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--workflow-id", type=int, default=None)
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--execution-id", type=int, default=None)
    return parser.parse_args(None if argv is None else list(argv))


def _resolve_app_dir(
    value: str,
    *,
    frozen: bool | None = None,
) -> Path:
    is_frozen = (
        bool(getattr(sys, "frozen", False))
        if frozen is None
        else frozen
    )
    apps_root = builtin_apps_dir().resolve()
    if is_frozen:
        if not _APP_ID_RE.fullmatch(value):
            raise SystemExit(
                "Packaged workers only accept a built-in app ID."
            )
        candidate = (apps_root / value).resolve()
        if not candidate.is_relative_to(apps_root):
            raise SystemExit(
                "Packaged workers only accept a built-in app ID."
            )
        if candidate.is_dir():
            return candidate
        raise SystemExit(f"Built-in app not found: {value}")

    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    built_in = apps_root / value
    if built_in.is_dir():
        return built_in.resolve()
    raise SystemExit(f"App not found: {value}")


def validate_bind_host(host: str) -> str:
    normalized = str(host or "").strip().lower()
    if normalized in {"127.0.0.1", "localhost", "::1"}:
        return normalized
    raise SystemExit(
        "This phase only supports loopback hosts "
        "(127.0.0.1, localhost or ::1)."
    )


def _connectors(app_id: str) -> dict:
    if app_id != "generic_desktop":
        return {}
    manager = AgentConnectionManager()
    dispatcher = DesktopDispatcher(manager)
    return {"desktop": DesktopBridgeConnector(dispatcher)}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    validate_bind_host(args.host)
    app_dir = _resolve_app_dir(args.app)
    manifest = load_manifest(app_dir / "manifest.yaml")
    if manifest.local.host_mode != "subprocess":
        raise SystemExit(
            "This App MCP package uses host_mode=in_process and must be "
            "served by the platform backend."
        )

    export_root = (
        Path(args.export_root).expanduser().resolve()
        if args.export_root
        else app_mcp_logs_dir() / "exports" / manifest.id
    )
    export_root.mkdir(parents=True, exist_ok=True)
    runtime: AppRuntime | None = None
    try:
        runtime = load_runtime(
            app_dir,
            export_root=export_root,
            connectors=_connectors(manifest.id),
            timeout_seconds=args.timeout,
            max_concurrency=args.max_concurrency,
        )
        context = ToolCallContext(
            workflow_id=args.workflow_id,
            user_id=args.user_id,
            execution_id=args.execution_id,
        )
        server = AppMCPServer(runtime)
        app = create_http_app(server, default_context=context)
        print(
            f"App MCP: {runtime.manifest.id} "
            f"http://{args.host}:{args.port}/mcp",
            flush=True,
        )
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
    finally:
        if runtime is not None:
            asyncio.run(runtime.close())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
