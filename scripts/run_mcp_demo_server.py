"""本地演示用 MCP Streamable HTTP Server（仅标准库）。

用于验证平台作为 MCP Client 的连接测试、工具发现、权限门与审计链路：

- ``echo``：read_only，原样返回参数；
- ``write_note``：mutating，把文本追加到内存列表；
- ``purge_notes``：destructive，清空内存列表。

只实现了 MCP 的最小 JSON-RPC 子集（initialize / tools/list /
tools/call / ping），响应 content-type 为 application/json。

用法：
    python scripts/run_mcp_demo_server.py --port 8765
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PROTOCOL_VERSION = "2025-03-26"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "description": "回显传入的文本（只读）",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "write_note",
        "description": "追加一条记录（写入）",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "purge_notes",
        "description": "清空全部记录（破坏性）",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
]


class DemoState:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.lock = threading.Lock()


class DemoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NL-RPA-Demo-MCP/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(
            "[demo-mcp] " + (fmt % args) + "\n"
        )

    def _write_json(
        self,
        payload: dict[str, Any],
        *,
        status: int = 200,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._write_empty(405)

    def do_DELETE(self) -> None:  # noqa: N802
        self._write_empty(200)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            )
            return

        method = payload.get("method")
        request_id = payload.get("id")
        self.log_message("method=%s", method)

        if method == "initialize":
            requested = (payload.get("params") or {}).get(
                "protocolVersion"
            ) or PROTOCOL_VERSION
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": requested,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {
                            "name": "nl-rpa-demo",
                            "version": "0.1",
                        },
                    },
                }
            )
            return

        if method == "notifications/initialized":
            self._write_empty(202)
            return

        if method == "ping":
            self._write_json(
                {"jsonrpc": "2.0", "id": request_id, "result": {}}
            )
            return

        if method == "tools/list":
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": TOOLS},
                }
            )
            return

        if method == "tools/call":
            self._handle_call(payload, request_id)
            return

        self._write_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )

    def _handle_call(
        self,
        payload: dict[str, Any],
        request_id: Any,
    ) -> None:
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        state: DemoState = self.server.state  # type: ignore[attr-defined]

        if name == "echo":
            text = str(arguments.get("text") or "")
        elif name == "write_note":
            text = str(arguments.get("text") or "")
            with state.lock:
                state.notes.append(text)
            text = f"notes={len(state.notes)}"
        elif name == "purge_notes":
            with state.lock:
                removed = len(state.notes)
                state.notes.clear()
            text = f"purged={removed}"
        else:
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32602,
                        "message": f"Unknown tool: {name}",
                    },
                }
            )
            return

        self._write_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="NL-RPA 演示用 MCP Server",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer(
        (args.host, args.port),
        DemoHandler,
    )
    server.daemon_threads = True
    server.state = DemoState()  # type: ignore[attr-defined]
    print(
        f"[demo-mcp] listening on http://{args.host}:{args.port}/mcp"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
