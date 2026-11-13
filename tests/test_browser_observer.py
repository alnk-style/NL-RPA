from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import pytest

from app.engine.browser_observer import (
    BrowserObserver,
    ObservationError,
    build_observation_summary,
    flatten_accessibility_tree,
)
from app.protocols import ObservationKind


class _FakeAccessibility:
    def __init__(self, tree: Any) -> None:
        self.tree = tree

    async def snapshot(self, interesting_only: bool = True) -> Any:
        return self.tree


class _FakeFrame:
    def __init__(
        self,
        payload: dict[str, Any],
        *,
        name: str = "",
        url: str = "",
        parent: _FakeFrame | None = None,
    ) -> None:
        self.payload = payload
        self.name = name
        self.url = url
        self.parent_frame = parent
        self.configs: list[dict[str, Any]] = []

    async def evaluate(
        self,
        script: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.configs.append(config)
        return self.payload


class _FakePage:
    def __init__(
        self,
        main_payload: dict[str, Any],
        *,
        frames: list[_FakeFrame] | None = None,
        accessibility_tree: Any = None,
    ) -> None:
        self.main_payload = main_payload
        self.frames = frames or []
        self.accessibility = _FakeAccessibility(accessibility_tree)
        self.configs: list[dict[str, Any]] = []

    async def evaluate(
        self,
        script: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        self.configs.append(config)
        return self.main_payload


def _main_frame_payload() -> dict[str, Any]:
    return {
        "url": "https://example.com",
        "title": "示例页面",
        "ready_state": "complete",
        "viewport": {"width": 1280, "height": 720},
        "forms": [{"action": "/login", "method": "post", "field_count": 2}],
        "tables": [],
        "dialogs": [],
        "login": {
            "has_password_input": True,
            "password_input_count": 1,
            "markers": ["登录"],
            "likely_login_page": True,
        },
        "shadow_dom": {"host_count": 0, "hosts": []},
        "interactive_count": 3,
        "elements": [
            {"name": "登录", "visible": True, "frame": None},
            {"name": "密码", "visible": True, "frame": None},
            {"name": "隐藏按钮", "visible": False, "frame": None},
        ],
        "truncated": False,
    }


def test_validate_url_accepts_http_and_rejects_other_schemes() -> None:
    assert (
        BrowserObserver.validate_url("https://example.com/login")
        == "https://example.com/login"
    )
    with pytest.raises(ObservationError):
        BrowserObserver.validate_url("file:///C:/secret.txt")
    with pytest.raises(ObservationError):
        BrowserObserver.validate_url("javascript:alert(1)")
    with pytest.raises(ObservationError):
        BrowserObserver.validate_url("")


def test_flatten_accessibility_tree_limits_and_counts_depth() -> None:
    tree = {
        "role": "WebArea",
        "name": "示例",
        "children": [
            {
                "role": "button",
                "name": "登录",
                "disabled": False,
                "children": [
                    {"role": "text", "name": "登录"},
                ],
            },
            {"role": "textbox", "name": "用户名", "value": "admin"},
        ],
    }

    nodes = flatten_accessibility_tree(tree, limit=2)

    assert len(nodes) == 2
    assert nodes[0]["role"] == "WebArea"
    assert nodes[0]["depth"] == 0
    assert nodes[1]["role"] == "button"
    assert nodes[1]["depth"] == 1


def test_build_observation_summary_mentions_login_and_frames() -> None:
    payload = {
        "title": "登录页",
        "interactive_count": 5,
        "forms": [{}],
        "tables": [],
        "dialogs": [],
        "shadow_dom": {"host_count": 2},
        "frames": [{"is_main": True}, {"is_main": False}],
        "login": {"likely_login_page": True},
        "truncated": False,
    }

    summary = build_observation_summary(payload)

    assert "登录页" in summary
    assert "iframe 1 个" in summary
    assert "Shadow DOM 宿主 2 个" in summary
    assert "疑似登录页面" in summary


def test_collect_merges_frames_and_applies_element_limit() -> None:
    main_frame = _FakeFrame(_main_frame_payload())
    child_frame = _FakeFrame(
        {
            "elements": [
                {"name": "子框架按钮", "visible": True},
            ],
            "login": {"likely_login_page": False},
            "shadow_dom": {"host_count": 1},
            "truncated": True,
        },
        name="login-frame",
        url="https://example.com/frame",
        parent=main_frame,
    )
    page = _FakePage(
        _main_frame_payload(),
        frames=[main_frame, child_frame],
        accessibility_tree={
            "role": "WebArea",
            "name": "示例",
            "children": [{"role": "button", "name": "登录"}],
        },
    )
    observer = BrowserObserver(
        SimpleNamespace(EXECUTION_TIMEOUT=5)
    )

    payload = asyncio.run(
        observer._collect(
            page,
            max_elements=4,
            include_accessibility=True,
        )
    )

    assert len(payload["elements"]) == 4
    assert payload["truncated"] is True
    assert payload["frames"][1]["name"] == "login-frame"
    assert payload["shadow_dom"]["host_count"] == 1
    assert payload["accessibility"]["node_count"] == 2
    assert payload["login"]["likely_login_page"] is True


_HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>登录测试页</title></head>
  <body>
    <h1>请登录</h1>
    <form action="/login" method="post">
      <label for="username">用户名</label>
      <input id="username" name="username" placeholder="请输入用户名">
      <input id="password" name="password" type="password">
      <button type="button" data-testid="login-button">登录</button>
    </form>
    <table><caption>消息列表</caption>
      <tr><th>标题</th><th>时间</th></tr>
      <tr><td>你好</td><td>今天</td></tr>
    </table>
    <div id="shadow-host"></div>
    <iframe name="login-frame" srcdoc="<button>子框架按钮</button>"></iframe>
    <script>
      const host = document.getElementById("shadow-host");
      const shadow = host.attachShadow({mode: "open"});
      shadow.innerHTML = "<button aria-label='影子按钮'>影子按钮</button>";
    </script>
  </body>
</html>
"""


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


def test_browser_observer_collects_real_page() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        observer = BrowserObserver(
            SimpleNamespace(
                EXECUTION_TIMEOUT=10,
                BROWSER_MODE="isolated",
                BROWSER_HEADLESS=True,
            )
        )
        try:
            snapshot = asyncio.run(
                observer.observe(
                    url,
                    wait_until="load",
                    timeout_ms=15000,
                    max_elements=40,
                )
            )
        except ObservationError as exc:
            pytest.skip(f"本机无头浏览器不可用：{exc}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert snapshot.kind is ObservationKind.BROWSER
    assert snapshot.url.startswith("http://127.0.0.1")
    assert snapshot.title == "登录测试页"
    names = {
        str(element.get("name") or "")
        for element in snapshot.elements
    }
    assert "登录" in names
    assert any(
        element.get("test_id") == "login-button"
        for element in snapshot.elements
        if isinstance(element, dict)
    )
    payload = snapshot.payload
    assert payload["login"]["likely_login_page"] is True
    assert payload["shadow_dom"]["host_count"] >= 1
    assert payload["accessibility"]["node_count"] >= 1
    assert len(payload["frames"]) >= 2
