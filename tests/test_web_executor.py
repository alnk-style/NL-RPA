from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import AsyncMock, patch

import pytest

from app.engine.web_executor import WebExecutor
from app.engine.visual_runtime import VisualRuntimeOutcome
from app.protocols import StructuredStep


class FakeDownload:
    async def save_as(self, target: str) -> None:
        Path(target).write_bytes(b"downloaded")


class FakeDownloadInfo:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    @property
    def value(self):
        async def resolve() -> FakeDownload:
            return FakeDownload()

        return resolve()


class FakeLocator:
    """按计数与预设失败次数模拟 Playwright Locator。"""

    def __init__(
        self,
        *,
        count: int = 1,
        text: str = "",
        table: list[dict[str, str]] | None = None,
        failures: int = 0,
        error: str | None = None,
    ) -> None:
        self._count = count
        self.text = text
        self.table = table
        self.failures = failures
        self.error = error or (
            'Timeout 30000ms exceeded.\nwaiting for locator("#missing")'
        )
        self.calls: list[tuple] = []

    async def count(self) -> int:
        self.calls.append(("count",))
        return self._count

    def nth(self, index: int) -> FakeLocator:
        self.calls.append(("nth", index))
        clone = FakeLocator(
            count=1,
            text=self.text,
            table=self.table,
            failures=self.failures,
            error=self.error,
        )
        clone.calls = self.calls
        self.failures = 0
        return clone

    async def _maybe_fail(self, action: str, **kwargs: Any) -> None:
        self.calls.append((action, kwargs or None))
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError(self.error)

    async def click(self, **kwargs: Any) -> None:
        await self._maybe_fail("click", **kwargs)

    async def fill(self, value: str, **kwargs: Any) -> None:
        await self._maybe_fail("fill", value=value, **kwargs)

    async def press(self, key: str, **kwargs: Any) -> None:
        await self._maybe_fail("press", key=key, **kwargs)

    async def select_option(self, **kwargs: Any) -> list[str]:
        await self._maybe_fail("select_option", **kwargs)
        return ["option"]

    async def check(self, **kwargs: Any) -> None:
        await self._maybe_fail("check", **kwargs)

    async def uncheck(self, **kwargs: Any) -> None:
        await self._maybe_fail("uncheck", **kwargs)

    async def wait_for(self, **kwargs: Any) -> None:
        await self._maybe_fail("wait_for", **kwargs)

    async def inner_text(self, **kwargs: Any) -> str:
        await self._maybe_fail("inner_text", **kwargs)
        return self.text

    async def all_inner_texts(self) -> list[str]:
        await self._maybe_fail("all_inner_texts")
        return [self.text]

    async def evaluate(self, script: str) -> Any:
        await self._maybe_fail("evaluate", script=script)
        return list(self.table or [])

    async def set_input_files(self, files: Any, **kwargs: Any) -> None:
        await self._maybe_fail("set_input_files", files=files, **kwargs)


class FakeFrame:
    def __init__(self, name: str = "") -> None:
        self.name = name
        self.url = f"https://example.com/{name}" if name else ""
        self.calls: list[tuple] = []
        self.locators: dict[tuple, FakeLocator] = {}

    def register(
        self,
        strategy: str,
        value: str,
        locator: FakeLocator,
        *,
        name: str | None = None,
    ) -> None:
        self.locators[(strategy, value, name)] = locator

    def _lookup(
        self,
        strategy: str,
        value: str,
        name: str | None = None,
    ) -> FakeLocator:
        self.calls.append((strategy, value, name))
        return self.locators.get(
            (strategy, value, name),
            FakeLocator(count=0),
        )

    def locator(self, selector: str) -> FakeLocator:
        return self._lookup("css", selector)

    def get_by_role(
        self,
        value: str,
        *,
        name: str | None = None,
        exact: bool = False,
    ) -> FakeLocator:
        return self._lookup("role", value, name)

    def get_by_label(self, value: str, **kwargs: Any) -> FakeLocator:
        return self._lookup("label", value)

    def get_by_placeholder(self, value: str, **kwargs: Any) -> FakeLocator:
        return self._lookup("placeholder", value)

    def get_by_test_id(self, value: str) -> FakeLocator:
        return self._lookup("testid", value)

    def get_by_text(self, value: str, **kwargs: Any) -> FakeLocator:
        return self._lookup("text", value)


class FakePage(FakeFrame):
    def __init__(self) -> None:
        super().__init__()
        self.url = "https://example.com"
        self.frames: list[FakeFrame] = []
        self.screenshots: list[str] = []
        self.screenshot_calls: list[dict[str, Any]] = []

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.calls.append(("goto", url, kwargs))
        self.url = url

    async def screenshot(self, path: str, **kwargs: Any) -> bytes:
        self.screenshots.append(path)
        self.screenshot_calls.append({"path": path, **kwargs})
        Path(path).write_bytes(b"fake-png")
        return b"fake-png"

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.calls.append(("wait_for_timeout", milliseconds))

    async def inner_text(self, selector: str, **kwargs: Any) -> str:
        self.calls.append(("page_inner_text", selector, kwargs))
        return "页面文本"

    async def evaluate(self, script: str, arg: Any = None) -> Any:
        self.calls.append(("page_evaluate", script, arg))
        if "INTERACTIVE_SELECTOR" in script:
            return {
                "url": self.url,
                "title": "测试页",
                "ready_state": "complete",
                "viewport": {"width": 1280, "height": 720},
                "forms": [],
                "tables": [],
                "dialogs": [],
                "login": {
                    "has_password_input": False,
                    "password_input_count": 0,
                    "markers": [],
                    "likely_login_page": False,
                },
                "shadow_dom": {"host_count": 0, "hosts": []},
                "interactive_count": 1,
                "elements": [
                    {
                        "name": "登录",
                        "visible": True,
                        "frame": None,
                        "best_locator": {
                            "strategy": "role",
                            "value": "button",
                            "name": "登录",
                        },
                    }
                ],
                "truncated": False,
            }
        return None

    def expect_download(self, **kwargs: Any) -> FakeDownloadInfo:
        self.calls.append(("expect_download", kwargs))
        return FakeDownloadInfo()


class FakeBrowserContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeBrowserContext) -> None:
        self.context = context
        self.closed = False

    async def new_context(self, **kwargs: Any) -> FakeBrowserContext:
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, context: FakeBrowserContext) -> None:
        self.context = context

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        return FakeBrowser(self.context)

    async def launch_persistent_context(self, **kwargs: Any) -> Any:
        return self.context


class FakePlaywright:
    def __init__(self, context: FakeBrowserContext) -> None:
        self.chromium = FakeChromium(context)


class FakePlaywrightManager:
    def __init__(self, page: FakePage) -> None:
        self.playwright = FakePlaywright(FakeBrowserContext(page))

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeRepairAgent:
    def __init__(self, repaired_steps: list[dict[str, Any]]) -> None:
        self.repaired_steps = list(repaired_steps)
        self.calls: list[dict[str, Any]] = []

    async def repair_steps(
        self,
        *,
        plan: dict[str, Any],
        step_index: int,
        failure: dict[str, Any],
        observation: dict[str, Any],
        action_catalog: str = "",
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "plan": plan,
                "step_index": step_index,
                "failure": failure,
                "observation": observation,
                "action_catalog": action_catalog,
            }
        )
        return [dict(step) for step in self.repaired_steps]


def _config(**overrides: Any) -> SimpleNamespace:
    values = {
        "EXECUTION_TIMEOUT": 15,
        "BROWSER_MODE": "isolated",
        "BROWSER_HEADLESS": True,
        "SCREENSHOT_ENABLED": True,
        "SCREENSHOT_TIMEOUT_MS": 1000,
        "SCREENSHOT_FULL_PAGE": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_executor_uses_context_browser_profile_runner(tmp_path: Path) -> None:
    profile_dir = tmp_path / "edge-profile"
    executor = WebExecutor(_config(BROWSER_MODE="isolated"))

    runner = executor._runner_for_context(
        {
            "browser_profile": {
                "id": 3,
                "user_data_dir": str(profile_dir),
                "profile_directory": "Profile 1",
            }
        }
    )

    assert runner.browser_mode == "edge"
    assert runner.edge_user_data_dir == str(profile_dir)
    assert runner.edge_profile_directory == "Profile 1"
    assert (profile_dir / "Profile 1").is_dir()


class FakeVisualRuntime:
    def __init__(self, outcome: VisualRuntimeOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    async def evaluate_web(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        return self.outcome


class VisualFailureRunner:
    def __init__(self, tmp_path: Path) -> None:
        self.logs_dir = tmp_path / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    async def capture_screenshot(
        self,
        page,
        execution_id,
        filename,
        **kwargs,
    ):
        del page, execution_id, filename, kwargs
        return "logs/execution/visual-failure.png", None


class VisualFailurePage:
    url = "https://example.com/orders"
    viewport_size = {"width": 1280, "height": 720}

    def locator(self, selector: str):
        class Locator:
            async def count(self):
                return 0

            async def wait_for(self, **kwargs):
                del kwargs
                raise RuntimeError("not found")

            @property
            def first(self):
                return self

        return Locator()


def test_web_executor_uses_successful_visual_fallback(tmp_path: Path) -> None:
    runtime = FakeVisualRuntime(
        VisualRuntimeOutcome(
            status="success",
            verification={"passed": True, "type": "dom_changed"},
            audit={"candidate_id": "cand_1", "selection": "automatic"},
        )
    )
    executor = WebExecutor(
        _config(),
        runner=VisualFailureRunner(tmp_path),
        visual_runtime=runtime,
    )
    step = StructuredStep(
        step_id=1,
        action="click",
        locator={"strategy": "css", "value": "#missing"},
        params={"visual_fallback": True},
    )

    result = asyncio.run(
        executor.execute_steps(
            [step],
            context={"execution_log_id": 1},
            page=VisualFailurePage(),
        )
    )

    assert result.success is True
    assert result.step_logs[0]["status"] == "success"
    assert result.step_logs[0]["visual_audit"]["candidate_id"] == "cand_1"
    assert runtime.calls == 1


def test_web_executor_creates_visual_ambiguity_details(tmp_path: Path) -> None:
    runtime = FakeVisualRuntime(
        VisualRuntimeOutcome(
            status="awaiting_user",
            payload={
                "kind": "visual_ambiguous",
                "instruction": "请选择目标",
                "action": "click",
                "risk_level": "read_only",
                "candidates": [
                    {
                        "candidate_id": "cand_1",
                        "target": "导出",
                        "bbox": [1, 2, 3, 4],
                        "confidence": 0.95,
                    }
                ],
                "verification": {"type": "visual_selection"},
                "execution_allowed": True,
            },
            reason="multiple candidates",
        )
    )
    executor = WebExecutor(
        _config(),
        runner=VisualFailureRunner(tmp_path),
        visual_runtime=runtime,
    )
    step = StructuredStep(
        step_id=1,
        action="click",
        locator={"strategy": "css", "value": "#missing"},
        params={"visual_fallback": True},
    )

    result = asyncio.run(
        executor.execute_steps(
            [step],
            context={"execution_log_id": 1},
            page=VisualFailurePage(),
        )
    )

    assert result.success is False
    assert result.details["awaiting_user"] is True
    failed_step = result.details["failed_step"]
    assert failed_step["failure_category"] == "visual_ambiguous"
    assert failed_step["human_action"]["kind"] == "visual_ambiguous"
    assert failed_step["human_action"]["candidates"][0][
        "candidate_id"
    ] == "cand_1"


def _run_plan(
    tmp_path: Path,
    plan: dict[str, Any],
    page: FakePage | None = None,
    **config_overrides: Any,
):
    page = page or FakePage()
    executor = WebExecutor(_config(**config_overrides))
    executor.runner.logs_dir = tmp_path
    workflow = SimpleNamespace(structured_plan=plan)
    with patch(
        "app.engine.web_executor.async_playwright",
        return_value=FakePlaywrightManager(page),
    ):
        result = asyncio.run(executor.execute(workflow))
    return result, page


def _role_locator(**kwargs: Any) -> dict[str, Any]:
    return {"strategy": "role", "value": "button", "name": "登录", **kwargs}


def _click_step(locator: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    step = {"action": "click", "locator": locator, **overrides}
    return step


def test_execute_runs_structured_actions_in_order(tmp_path: Path) -> None:
    page = FakePage()
    page.register("role", "button", FakeLocator(), name="登录")
    username = FakeLocator()
    page.register("placeholder", "请输入用户名", username)
    plan = {
        "steps": [
            {"action": "goto", "params": {"url": "https://example.com"}},
            _click_step(_role_locator()),
            {
                "action": "fill",
                "locator": {
                    "strategy": "placeholder",
                    "value": "请输入用户名",
                },
                "params": {"text": "alice"},
            },
        ]
    }

    result, page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    assert result.error is None
    assert [entry["action"] for entry in result.step_logs] == [
        "goto",
        "click",
        "fill",
    ]
    assert all(
        entry["status"] == "success" for entry in result.step_logs
    )
    assert [entry["step_index"] for entry in result.step_logs] == [0, 1, 2]
    assert result.step_logs[1]["locator_strategy"] == "role"
    assert result.step_logs[1]["attempts"] == 1
    assert any(
        call[0] == "goto" and call[1] == "https://example.com"
        for call in page.calls
    )
    assert ("fill", {"value": "alice", "timeout": 20000}) in username.calls
    assert result.duration_seconds >= 0


def test_step_retries_until_success_and_records_attempts(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register(
        "css",
        "#submit",
        FakeLocator(
            failures=1,
            error=(
                "Timeout 30000ms exceeded.\n"
                'waiting for locator("#submit")'
            ),
        ),
    )
    plan = {
        "steps": [
            _click_step(
                {"strategy": "css", "value": "#submit"},
                retry_policy={"max_attempts": 2, "backoff_seconds": 0.25},
            )
        ]
    }

    with patch(
        "app.engine.web_executor.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep:
        result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    assert result.step_logs[0]["status"] == "success"
    assert result.step_logs[0]["attempts"] == 2
    assert result.step_logs[0]["retried"] is True
    sleep.assert_awaited_once_with(0.25)


def test_goto_step_honors_timeout_ms_param(tmp_path: Path) -> None:
    page = FakePage()
    plan = {
        "steps": [
            {
                "action": "goto",
                "params": {
                    "url": "https://example.com",
                    "timeout_ms": 1234,
                },
            }
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    goto_call = next(call for call in page.calls if call[0] == "goto")
    assert goto_call[2]["timeout"] == 1234


def test_screenshot_step_honors_full_page_param(
    tmp_path: Path,
) -> None:
    page = FakePage()
    plan = {
        "steps": [
            {
                "action": "screenshot",
                "params": {"name": "home", "full_page": True},
            }
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    assert page.screenshot_calls
    assert page.screenshot_calls[0]["full_page"] is True


def test_locator_fallback_is_used_when_primary_is_missing(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("css", "#legacy", FakeLocator(count=0))
    page.register("role", "button", FakeLocator(), name="登录")
    plan = {
        "steps": [
            _click_step(
                {
                    "strategy": "css",
                    "value": "#legacy",
                    "fallbacks": [_role_locator()],
                }
            )
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    assert result.step_logs[0]["locator_strategy"] == "role"


def test_missing_locator_fails_with_category_and_screenshot(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("css", "#missing", FakeLocator(count=0))
    plan = {
        "steps": [
            _click_step(
                {"strategy": "css", "value": "#missing"},
                retry_policy={"max_attempts": 1},
            )
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is False
    assert result.error is not None
    assert "#missing" in result.error
    details = result.details or {}
    failed_step = details["failed_step"]
    assert failed_step["step_id"] == 1
    assert failed_step["action"] == "click"
    assert failed_step["failure_category"] == "locator_not_found"
    entry = result.step_logs[0]
    assert entry["status"] == "failed"
    assert entry["failure_category"] == "locator_not_found"
    assert entry["screenshot_path"]
    assert Path(entry["screenshot_path"]).is_file()
    assert result.screenshots == [entry["screenshot_path"]]


def test_auth_failure_is_not_retried(tmp_path: Path) -> None:
    page = FakePage()
    page.register(
        "css",
        "input[type='password']:visible",
        FakeLocator(count=1),
    )
    plan = {
        "steps": [
            {
                "action": "assert_logged_in",
                "params": {},
                "retry_policy": {"max_attempts": 3},
            }
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is False
    assert result.step_logs[0]["attempts"] == 1
    assert result.step_logs[0]["failure_category"] == "auth_required"
    details = result.details or {}
    assert details["awaiting_user"] is True


def test_on_failure_skip_continues_to_later_steps(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("css", "#missing", FakeLocator(count=0))
    plan = {
        "steps": [
            {
                "action": "click",
                "locator": {"strategy": "css", "value": "#missing"},
                "on_failure": "skip",
            },
            {"action": "screenshot", "params": {"name": "home"}},
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    assert [entry["status"] for entry in result.step_logs] == [
        "skipped",
        "success",
    ]
    assert (result.details or {})["skipped_steps"] == [1]
    assert result.step_logs[1]["screenshot_path"]


def test_extract_table_and_save_file_produce_artifacts(
    tmp_path: Path,
) -> None:
    page = FakePage()
    rows = [{"标题": "你好", "时间": "今天"}]
    page.register("css", "table", FakeLocator(table=rows))
    plan = {
        "steps": [
            {
                "action": "extract_table",
                "locator": {"strategy": "css", "value": "table"},
                "params": {"variable": "rows"},
            },
            {
                "action": "save_file",
                "params": {
                    "path": "report.csv",
                    "content": "标题,时间\n你好,今天\n",
                },
            },
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    assert (result.details or {})["extracted"]["rows"] == rows
    assert result.artifacts
    artifact = result.artifacts[0]
    assert artifact["name"] == "report.csv"
    assert Path(artifact["path"]).is_file()
    assert Path(artifact["path"]).read_text(
        encoding="utf-8"
    ) == "标题,时间\n你好,今天\n"


def test_empty_extracted_table_fails_by_default(tmp_path: Path) -> None:
    page = FakePage()
    page.register("css", "table", FakeLocator(table=[]))
    plan = {
        "steps": [
            {
                "action": "extract_table",
                "locator": {"strategy": "css", "value": "table"},
                "params": {"variable": "rows"},
            }
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is False
    assert result.step_logs[0]["failure_category"] == "data_empty"


def test_empty_extracted_table_can_be_explicitly_allowed(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("css", "table", FakeLocator(table=[]))
    plan = {
        "steps": [
            {
                "action": "extract_table",
                "locator": {"strategy": "css", "value": "table"},
                "params": {
                    "variable": "rows",
                    "allow_empty": True,
                },
            }
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True
    assert (result.details or {})["extracted"]["rows"] == []


def test_save_file_serializes_extracted_table_as_json(
    tmp_path: Path,
) -> None:
    page = FakePage()
    rows = [{"标题": "你好", "时间": "今天"}]
    page.register("css", "#messages", FakeLocator(table=rows))
    plan = {
        "steps": [
            {
                "action": "extract_table",
                "locator": {"strategy": "css", "value": "#messages"},
                "params": {"variable": "messages"},
            },
            {
                "action": "save_file",
                "params": {
                    "path": "messages.json",
                    "variable": "messages",
                },
            },
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    artifact_path = Path(result.artifacts[0]["path"])
    assert json.loads(
        artifact_path.read_text(encoding="utf-8")
    ) == rows


def test_assert_text_failure_is_business_error(tmp_path: Path) -> None:
    page = FakePage()
    page.register("css", "#status", FakeLocator(text="登录失败"))
    plan = {
        "steps": [
            {
                "action": "assert_text",
                "locator": {"strategy": "css", "value": "#status"},
                "params": {"text": "欢迎"},
                "retry_policy": {"max_attempts": 3},
            }
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is False
    assert result.step_logs[0]["attempts"] == 1
    assert result.step_logs[0]["failure_category"] == "business_error"


def test_locator_inside_frame_targets_child_frame(
    tmp_path: Path,
) -> None:
    page = FakePage()
    frame = FakeFrame("login-frame")
    frame.register("role", "button", FakeLocator(), name="登录")
    page.frames = [frame]
    plan = {
        "steps": [
            _click_step(_role_locator(frame="login-frame")),
        ]
    }

    result, _page = _run_plan(tmp_path, plan, page)

    assert result.success is True, result.error
    assert ("role", "button", "登录") in frame.calls


def _run_plan_with_workflow(
    tmp_path: Path,
    plan: dict[str, Any],
    page: FakePage,
    *,
    repair_agent: FakeRepairAgent | None = None,
    **config_overrides: Any,
):
    executor = WebExecutor(
        _config(**config_overrides),
        repair_agent=repair_agent,
    )
    executor.runner.logs_dir = tmp_path
    workflow = SimpleNamespace(structured_plan=plan)
    with patch(
        "app.engine.web_executor.async_playwright",
        return_value=FakePlaywrightManager(page),
    ):
        result = asyncio.run(executor.execute(workflow))
    return result, page, workflow


def test_executor_repairs_failed_step_and_resumes(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("css", "#legacy", FakeLocator(count=0))
    page.register("role", "button", FakeLocator(), name="登录")
    repair_agent = FakeRepairAgent(
        [
            {
                "step_id": 2,
                "action": "click",
                "locator": _role_locator(),
            },
            {
                "step_id": 3,
                "action": "screenshot",
                "params": {"name": "done"},
            },
        ]
    )
    plan = {
        "steps": [
            {
                "step_id": 1,
                "action": "goto",
                "params": {"url": "https://example.com"},
            },
            {
                "step_id": 2,
                "action": "click",
                "locator": {"strategy": "css", "value": "#legacy"},
            },
            {
                "step_id": 3,
                "action": "screenshot",
                "params": {"name": "old"},
            },
        ]
    }

    result, page, workflow = _run_plan_with_workflow(
        tmp_path,
        plan,
        page,
        repair_agent=repair_agent,
        STEP_LEVEL_REPAIR=True,
        WEB_REPAIR_ATTEMPTS=2,
    )

    assert result.success is True, result.error
    assert sum(
        1 for call in page.calls if call[0] == "goto"
    ) == 1
    assert len(repair_agent.calls) == 1
    call = repair_agent.calls[0]
    assert call["step_index"] == 1
    assert call["failure"]["failure_category"] == "locator_not_found"
    assert call["observation"]["elements"]
    assert "click" in call["action_catalog"]

    click_logs = [
        entry
        for entry in result.step_logs
        if entry["action"] == "click"
    ]
    assert [entry["status"] for entry in click_logs] == [
        "repaired",
        "success",
    ]
    assert click_logs[1]["locator_strategy"] == "role"
    details = result.details or {}
    assert details["repairs"][0]["step_index"] == 1
    assert (
        details["repaired_plan"]["steps"][1]["locator"]["strategy"]
        == "role"
    )
    assert (
        workflow.structured_plan["steps"][1]["locator"]["strategy"]
        == "role"
    )


def test_execute_steps_reuses_provided_page(tmp_path: Path) -> None:
    """混合执行器传入页面时不应再启动新浏览器。"""

    page = FakePage()
    page.register("role", "button", FakeLocator(), name="登录")
    executor = WebExecutor(_config())
    executor.runner.logs_dir = tmp_path

    async def scenario():
        first = await executor.execute_steps(
            [
                {
                    "step_id": 1,
                    "action": "goto",
                    "params": {"url": "https://example.com/a"},
                }
            ],
            page=page,
            step_offset=0,
        )
        second = await executor.execute_steps(
            [
                {
                    "step_id": 2,
                    "action": "click",
                    "locator": _role_locator(),
                    "params": {},
                }
            ],
            page=page,
            step_offset=1,
        )
        return first, second

    first, second = asyncio.run(scenario())

    assert first.success is True, first.error
    assert second.success is True, second.error
    assert any(call[0] == "goto" for call in page.calls)
    assert [entry["step_index"] for entry in second.step_logs] == [1]
    assert page.url == "https://example.com/a"


def test_executor_does_not_repair_when_flag_disabled(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("css", "#missing", FakeLocator(count=0))
    repair_agent = FakeRepairAgent(
        [
            {
                "action": "click",
                "locator": _role_locator(),
            }
        ]
    )
    plan = {
        "steps": [
            _click_step({"strategy": "css", "value": "#missing"}),
        ]
    }

    result, _page, _workflow = _run_plan_with_workflow(
        tmp_path,
        plan,
        page,
        repair_agent=repair_agent,
        STEP_LEVEL_REPAIR=False,
    )

    assert result.success is False
    assert repair_agent.calls == []
    assert (result.details or {})["failed_step"]["failure_category"] == (
        "locator_not_found"
    )


def test_executor_aborts_repair_when_repaired_plan_is_invalid(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("css", "#missing", FakeLocator(count=0))
    repair_agent = FakeRepairAgent(
        [{"action": "navigate", "params": {}}]
    )
    plan = {
        "steps": [
            _click_step({"strategy": "css", "value": "#missing"}),
        ]
    }

    result, _page, _workflow = _run_plan_with_workflow(
        tmp_path,
        plan,
        page,
        repair_agent=repair_agent,
        STEP_LEVEL_REPAIR=True,
        WEB_REPAIR_ATTEMPTS=2,
    )

    assert result.success is False
    assert len(repair_agent.calls) == 1
    assert (result.details or {})["failed_step"]["failure_category"] == (
        "locator_not_found"
    )


def test_executor_does_not_repair_auth_failure(tmp_path: Path) -> None:
    page = FakePage()
    page.register(
        "css",
        "input[type='password']:visible",
        FakeLocator(count=1),
    )
    repair_agent = FakeRepairAgent(
        [{"action": "goto", "params": {"url": "https://example.com"}}]
    )
    plan = {
        "steps": [
            {
                "action": "assert_logged_in",
                "params": {},
                "retry_policy": {"max_attempts": 3},
            }
        ]
    }

    result, _page, _workflow = _run_plan_with_workflow(
        tmp_path,
        plan,
        page,
        repair_agent=repair_agent,
        STEP_LEVEL_REPAIR=True,
    )

    assert result.success is False
    assert repair_agent.calls == []
    assert (result.details or {})["awaiting_user"] is True
    assert result.step_logs[0]["failure_category"] == "auth_required"


def test_executor_stops_after_max_repair_rounds(tmp_path: Path) -> None:
    page = FakePage()
    page.register("css", "#old", FakeLocator(count=0))
    page.register("css", "#new", FakeLocator(count=0))
    repair_agent = FakeRepairAgent(
        [
            {
                "action": "click",
                "locator": {"strategy": "css", "value": "#new"},
            }
        ]
    )
    plan = {
        "steps": [
            _click_step(
                {"strategy": "css", "value": "#old"},
                retry_policy={"max_attempts": 1},
            )
        ]
    }

    result, _page, _workflow = _run_plan_with_workflow(
        tmp_path,
        plan,
        page,
        repair_agent=repair_agent,
        STEP_LEVEL_REPAIR=True,
        WEB_REPAIR_ATTEMPTS=2,
    )

    assert result.success is False
    assert len(repair_agent.calls) == 2
    assert (result.details or {})["failed_step"]["failure_category"] == (
        "locator_not_found"
    )


def test_fill_resolves_credential_placeholder_without_logging_secret(
    tmp_path: Path,
) -> None:
    page = FakePage()
    username = FakeLocator()
    page.register("placeholder", "用户名", username)
    plan = {
        "steps": [
            {
                "action": "fill",
                "locator": {
                    "strategy": "placeholder",
                    "value": "用户名",
                },
                "params": {"text": "{{credentials.username}}"},
            }
        ]
    }
    executor = WebExecutor(_config())
    executor.runner.logs_dir = tmp_path
    workflow = SimpleNamespace(structured_plan=plan)
    with patch(
        "app.engine.web_executor.async_playwright",
        return_value=FakePlaywrightManager(page),
    ):
        result = asyncio.run(
            executor.execute(
                workflow,
                {"credentials": {"username": "automation-user"}},
            )
        )

    assert result.success is True, result.error
    assert (
        "fill",
        {"value": "automation-user", "timeout": 20000},
    ) in username.calls
    assert "automation-user" not in json.dumps(
        result.step_logs,
        ensure_ascii=False,
    )
    assert "automation-user" not in result.output
    assert "automation-user" not in str(result.details)


def test_missing_credential_placeholder_is_business_error(
    tmp_path: Path,
) -> None:
    page = FakePage()
    page.register("placeholder", "密码", FakeLocator())
    plan = {
        "steps": [
            {
                "action": "fill",
                "locator": {
                    "strategy": "placeholder",
                    "value": "密码",
                },
                "params": {"text": "{{credentials.password}}"},
            }
        ]
    }
    executor = WebExecutor(_config())
    executor.runner.logs_dir = tmp_path
    workflow = SimpleNamespace(structured_plan=plan)
    with patch(
        "app.engine.web_executor.async_playwright",
        return_value=FakePlaywrightManager(page),
    ):
        result = asyncio.run(
            executor.execute(workflow, {"credentials": {}})
        )

    assert result.success is False
    assert result.step_logs[0]["failure_category"] == "business_error"
    assert result.step_logs[0]["attempts"] == 1
    assert "password" in (result.error or "")
    assert "automation-user" not in (result.error or "")


def test_resume_starts_at_given_step(tmp_path: Path) -> None:
    page = FakePage()
    page.register("role", "button", FakeLocator(), name="登录")
    plan = {
        "steps": [
            {
                "step_id": 1,
                "action": "goto",
                "params": {"url": "https://example.com"},
            },
            {
                "step_id": 2,
                "action": "click",
                "locator": _role_locator(),
            },
            {
                "step_id": 3,
                "action": "screenshot",
                "params": {"name": "done"},
            },
        ]
    }
    executor = WebExecutor(_config())
    executor.runner.logs_dir = tmp_path
    workflow = SimpleNamespace(structured_plan=plan)
    context = {
        "resume_from_step": 1,
        "completed_idempotency_keys": [],
        "initial_step_logs": [
            {
                "step_index": 0,
                "step_id": 1,
                "action": "goto",
                "status": "success",
            }
        ],
    }
    with patch(
        "app.engine.web_executor.async_playwright",
        return_value=FakePlaywrightManager(page),
    ):
        result = asyncio.run(executor.execute(workflow, context))

    assert result.success is True, result.error
    assert not any(call[0] == "goto" for call in page.calls)
    assert [
        (entry["step_index"], entry["action"], entry["status"])
        for entry in result.step_logs
    ] == [
        (0, "goto", "success"),
        (1, "click", "success"),
        (2, "screenshot", "success"),
    ]


def test_completed_idempotency_key_skips_step(tmp_path: Path) -> None:
    page = FakePage()
    click = FakeLocator()
    page.register("role", "button", click, name="登录")
    plan = {
        "steps": [
            {
                "step_id": 1,
                "action": "click",
                "locator": _role_locator(),
                "idempotency_key": "open-page",
            },
            {
                "step_id": 2,
                "action": "screenshot",
                "params": {"name": "done"},
            },
        ]
    }
    executor = WebExecutor(_config())
    executor.runner.logs_dir = tmp_path
    workflow = SimpleNamespace(structured_plan=plan)
    with patch(
        "app.engine.web_executor.async_playwright",
        return_value=FakePlaywrightManager(page),
    ):
        result = asyncio.run(
            executor.execute(
                workflow,
                {"completed_idempotency_keys": ["open-page"]},
            )
        )

    assert result.success is True, result.error
    assert result.step_logs[0]["status"] == "skipped"
    assert click.calls == []
    assert (result.details or {})["completed_idempotency_keys"] == [
        "open-page"
    ]
    assert (result.details or {})["skipped_steps"] == [1]


_HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>结构化执行测试页</title></head>
  <body>
    <input id="username" placeholder="用户名">
    <button id="login" type="button">登录</button>
    <div id="result"></div>
    <table id="messages">
      <tr><th>标题</th><th>时间</th></tr>
      <tr><td>你好</td><td>今天</td></tr>
    </table>
    <script>
      document.getElementById("login").addEventListener("click", () => {
        const name = document.getElementById("username").value;
        document.getElementById("result").textContent = "欢迎 " + name;
      });
    </script>
  </body>
</html>
"""

_REPAIR_HTML_PAGE = """<!doctype html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>修复试点页</title></head>
  <body>
    <h1>消息中心</h1>
    <button id="login" type="button">登录</button>
    <div id="status">未登录</div>
    <script>
      document.getElementById("login").addEventListener("click", () => {
        document.getElementById("status").textContent = "欢迎回来";
      });
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


class _RepairPageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _REPAIR_HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


def test_web_executor_runs_structured_flow_on_real_page(
    tmp_path: Path,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        executor = WebExecutor(_config(EXECUTION_TIMEOUT=30))
        executor.runner.logs_dir = tmp_path
        workflow = SimpleNamespace(
            structured_plan={
                "steps": [
                    {"action": "goto", "params": {"url": url}},
                    {"action": "assert_logged_in", "params": {}},
                    {
                        "action": "fill",
                        "locator": {
                            "strategy": "placeholder",
                            "value": "用户名",
                        },
                        "params": {"text": "alice"},
                    },
                    _click_step(
                        {
                            "strategy": "role",
                            "value": "button",
                            "name": "登录",
                        }
                    ),
                    {
                        "action": "extract_text",
                        "locator": {"strategy": "css", "value": "#result"},
                        "params": {"variable": "welcome"},
                    },
                    {
                        "action": "assert_text",
                        "locator": {"strategy": "css", "value": "#result"},
                        "params": {"text": "欢迎 alice"},
                    },
                    {
                        "action": "extract_table",
                        "locator": {
                            "strategy": "css",
                            "value": "#messages",
                        },
                        "params": {"variable": "messages"},
                    },
                    {
                        "action": "save_file",
                        "params": {
                            "path": "messages.json",
                            "variable": "messages",
                        },
                    },
                ]
            }
        )
        result = asyncio.run(executor.execute(workflow))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if (
        not result.success
        and result.error
        and (
            "Executable doesn't exist" in result.error
            or "playwright install" in result.error.lower()
            or "BrowserType.launch" in result.error
        )
    ):
        pytest.skip(f"本机无头浏览器不可用：{result.error}")

    assert result.success is True, result.error
    assert (result.details or {})["extracted"]["welcome"] == "欢迎 alice"
    assert (result.details or {})["extracted"]["messages"] == [
        {"标题": "你好", "时间": "今天"}
    ]
    assert result.artifacts
    assert json.loads(
        Path(result.artifacts[0]["path"]).read_text(
            encoding="utf-8"
        )
    ) == [{"标题": "你好", "时间": "今天"}]
    assert [entry["status"] for entry in result.step_logs] == [
        "success",
        "success",
        "success",
        "success",
        "success",
        "success",
        "success",
        "success",
    ]


def test_web_executor_repairs_locator_on_real_page(
    tmp_path: Path,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RepairPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        repair_agent = FakeRepairAgent(
            [
                {
                    "step_id": 2,
                    "action": "click",
                    "locator": {
                        "strategy": "role",
                        "value": "button",
                        "name": "登录",
                    },
                },
                {
                    "step_id": 3,
                    "action": "assert_text",
                    "locator": {"strategy": "css", "value": "#status"},
                    "params": {"text": "欢迎回来"},
                },
            ]
        )
        executor = WebExecutor(
            _config(
                EXECUTION_TIMEOUT=30,
                STEP_LEVEL_REPAIR=True,
                WEB_REPAIR_ATTEMPTS=2,
            ),
            repair_agent=repair_agent,
        )
        executor.runner.logs_dir = tmp_path
        workflow = SimpleNamespace(
            structured_plan={
                "steps": [
                    {
                        "step_id": 1,
                        "action": "goto",
                        "params": {"url": url},
                    },
                    {
                        "step_id": 2,
                        "action": "click",
                        "locator": {
                            "strategy": "css",
                            "value": "#missing-login",
                        },
                        "retry_policy": {"max_attempts": 1},
                    },
                    {
                        "step_id": 3,
                        "action": "assert_text",
                        "locator": {
                            "strategy": "css",
                            "value": "#status",
                        },
                        "params": {"text": "欢迎回来"},
                    },
                ]
            }
        )
        result = asyncio.run(executor.execute(workflow))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if (
        not result.success
        and result.error
        and (
            "Executable doesn't exist" in result.error
            or "playwright install" in result.error.lower()
            or "BrowserType.launch" in result.error
        )
    ):
        pytest.skip(f"本机无头浏览器不可用：{result.error}")

    assert result.success is True, result.error
    assert len(repair_agent.calls) == 1
    assert repair_agent.calls[0]["observation"]["elements"]
    click_logs = [
        entry
        for entry in result.step_logs
        if entry["action"] == "click"
    ]
    assert [entry["status"] for entry in click_logs] == [
        "repaired",
        "success",
    ]
    assert click_logs[1]["locator_strategy"] == "role"
    assert (
        workflow.structured_plan["steps"][1]["locator"]["strategy"]
        == "role"
    )


class _LinkNode:
    def __init__(self, href: str, text: str = "") -> None:
        self.href = href
        self.text = text

    async def get_attribute(self, name: str) -> str | None:
        return self.href if name == "href" else None

    async def inner_text(self, **_kwargs: Any) -> str:
        return self.text or self.href


class _LinkLocator:
    def __init__(self, links: list[_LinkNode]) -> None:
        self.links = links

    async def count(self) -> int:
        return len(self.links)

    def nth(self, index: int) -> _LinkNode:
        return self.links[index]


class _CrawlPage:
    def __init__(self, pages: list[list[_LinkNode]]) -> None:
        self.pages = pages
        self.page_index = 0
        self.url = "https://example.com/list?page=1"
        self.progress: list[dict[str, Any]] = []

    def locator(self, selector: str) -> Any:
        if selector == "a.item":
            return _LinkLocator(self.pages[self.page_index])
        if selector == "a.next":
            return _NextLocator(self)
        return _LinkLocator([])

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.progress.append({"wait": milliseconds})


class _NextLocator:
    def __init__(self, page: _CrawlPage) -> None:
        self.page = page

    async def count(self) -> int:
        return int(self.page.page_index < len(self.page.pages) - 1)

    def nth(self, _index: int) -> _NextLocator:
        return self

    async def is_enabled(self) -> bool:
        return self.page.page_index < len(self.page.pages) - 1

    async def click(self, **_kwargs: Any) -> None:
        self.page.page_index += 1
        self.page.url = (
            f"https://example.com/list?page={self.page.page_index + 1}"
        )


def test_collect_links_respects_limit() -> None:
    page = _CrawlPage(
        [
            [
                _LinkNode("/a", "A"),
                _LinkNode("/a", "duplicate"),
                _LinkNode("/b", "B"),
                _LinkNode("/c", "C"),
            ]
        ]
    )
    executor = WebExecutor(_config())

    result = asyncio.run(
        executor._action_collect_links(
            page,
            {
                "selector": "a.item",
                "attribute": "href",
                "limit": 2,
            },
            {},
            {},
            {},
        )
    )

    assert [item["url"] for item in result["items"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_collect_links_rejects_hosts_outside_policy() -> None:
    page = _CrawlPage(
        [
            [
                _LinkNode("https://evil.example.net/item"),
                _LinkNode("/safe"),
            ]
        ]
    )
    executor = WebExecutor(_config())

    result = asyncio.run(
        executor._action_collect_links(
            page,
            {
                "selector": "a.item",
                "allowed_domains": ["example.com"],
            },
            {},
            {},
            {},
        )
    )

    assert [item["url"] for item in result["items"]] == [
        "https://example.com/safe"
    ]


def test_paginate_stops_after_last_page_and_keeps_order() -> None:
    page = _CrawlPage(
        [
            [_LinkNode("/1"), _LinkNode("/2")],
            [_LinkNode("/2"), _LinkNode("/3")],
            [_LinkNode("/4")],
        ]
    )
    executor = WebExecutor(_config())

    result = asyncio.run(
        executor._action_paginate(
            page,
            {
                "item_selector": "a.item",
                "next_selector": "a.next",
                "max_pages": 3,
                "max_items": 10,
                "allowed_domains": ["example.com"],
            },
            {},
            {},
            {},
        )
    )

    assert [item["url"] for item in result["items"]] == [
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/3",
        "https://example.com/4",
    ]
    assert result["pages"] == 3


def test_extract_fields_reads_relative_links() -> None:
    class Row:
        async def inner_text(self, **_kwargs: Any) -> str:
            return "商品 A"

        async def get_attribute(self, name: str) -> str | None:
            return "/detail/1" if name == "href" else None

        def locator(self, selector: str) -> Any:
            if selector == "a":
                return _FieldLocator(self.get_attribute)
            return _FieldLocator(self.inner_text)

    class RowsLocator:
        async def count(self) -> int:
            return 1

        def nth(self, _index: int) -> Row:
            return Row()

    class Page(_CrawlPage):
        def locator(self, selector: str) -> Any:
            if selector == ".item":
                return RowsLocator()
            return super().locator(selector)

    class _FieldLocator:
        def __init__(self, read: Any) -> None:
            self.read = read

        async def inner_text(self, **kwargs: Any) -> str:
            return await self.read(**kwargs)

        async def get_attribute(self, name: str) -> str | None:
            return await self.read(name)

    page = Page([])
    executor = WebExecutor(_config())

    result = asyncio.run(
        executor._action_extract_fields(
            page,
            {
                "selector": ".item",
                "fields": {
                    "title": {"selector": ":scope"},
                    "url": {
                        "selector": "a",
                        "attribute": "href",
                        "url": True,
                    },
                },
            },
            {},
            {},
            {},
        )
    )

    assert result["items"] == [
        {
            "title": "商品 A",
            "url": "https://example.com/detail/1",
        }
    ]


def test_data_action_dict_result_is_wrapped_for_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = WebExecutor(_config())
    step = executor._coerce_steps(
        [
            {
                "step_id": 1,
                "executor": "web",
                "action": "extract_fields",
                "params": {
                    "selector": ".record",
                    "fields": {"name": {"selector": ".name"}},
                },
            }
        ]
    )[0]
    rows = [{"name": "Alpha"}]

    async def fake_extract_fields(*_args, **_kwargs):
        return {"items": rows, "count": 1}

    monkeypatch.setattr(
        executor,
        "_action_extract_fields",
        fake_extract_fields,
    )

    outcome = asyncio.run(
        executor._execute_step_once(
            None,
            step,
            0,
            "test",
            {},
            [],
            {},
        )
    )

    assert outcome.value == rows


def test_web_plan_requires_crawl_policy_for_collection() -> None:
    from app.agents.web_catalog import WebPlanError, validate_web_plan

    with pytest.raises(WebPlanError, match="crawl_policy"):
        validate_web_plan(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "action": "collect_links",
                        "params": {"selector": "a.item"},
                    }
                ]
            }
        )


def test_navigation_rejects_domain_outside_crawl_policy(
    tmp_path: Path,
) -> None:
    executor = WebExecutor(_config())
    executor.runner.logs_dir = tmp_path
    page = FakePage()
    result = asyncio.run(
        executor.execute_steps(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "action": "goto",
                        "params": {"url": "https://evil.example.net/"},
                    }
                ]
            },
            page=page,
            context={
                "crawl_policy": {
                    "allowed_domains": ["example.com"],
                    "requests_per_second": 1,
                    "max_pages": 2,
                    "max_items": 10,
                }
            },
        )
    )

    assert result.success is False
    assert "permission_denied" in (result.error or "")
    assert not any(call[0] == "goto" for call in page.calls)


def test_navigation_applies_domain_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.engine import web_executor as module

    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    executor = WebExecutor(_config())
    executor._crawl_policy = {
        "allowed_domains": ["example.com"],
        "requests_per_second": 1,
        "max_pages": 2,
        "max_items": 10,
    }
    executor._last_request_at["example.com"] = (
        module.time.monotonic()
    )

    async def scenario() -> None:
        await executor._before_navigation(
            "https://example.com/two",
            {},
        )

    asyncio.run(scenario())

    sleep.assert_awaited_once()
    assert 0.8 <= sleep.await_args.args[0] <= 1.0


def test_wait_for_response_returns_status_and_json() -> None:
    response = SimpleNamespace(
        url="https://example.com/api/items",
        status=200,
    )

    async def read_json() -> dict[str, Any]:
        return {"items": [1, 2]}

    response.json = read_json

    class Info:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        @property
        def value(self):
            async def resolve():
                return response

            return resolve()

    class ResponsePage(FakePage):
        def expect_response(self, *_args: Any, **_kwargs: Any) -> Info:
            return Info()

    page = ResponsePage()
    executor = WebExecutor(_config())
    result = asyncio.run(
        executor._action_wait_for_response(
            page,
            StructuredStep(
                step_id=1,
                action="wait_for_response",
                params={"url_contains": "/api/items", "status": 200},
            ),
            0,
            "exec",
            {},
            [],
            {},
        )
    )

    assert result.value == {
        "url": "https://example.com/api/items",
        "status": 200,
        "json": {"items": [1, 2]},
    }
