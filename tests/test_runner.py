from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.runner import ExecutionResult, PlaywrightRunner


class FakeLocator:
    def __init__(self, selector: str) -> None:
        self.selector = selector

    async def evaluate(self, _script: str) -> list[dict[str, str]]:
        return [
            {"name": "Alice", "age": "30"},
            {"name": "Bob", "age": "28"},
        ]


class FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def goto(self, url: str, **_kwargs) -> None:
        self.calls.append(("goto", url))

    async def click(self, selector: str, **_kwargs) -> None:
        self.calls.append(("click", selector))

    async def fill(self, selector: str, value: str, **_kwargs) -> None:
        self.calls.append(("fill", selector, value))

    async def press(self, selector: str, key: str, **_kwargs) -> None:
        self.calls.append(("press", selector, key))

    async def screenshot(self, path: str, **_kwargs) -> bytes:
        self.calls.append(("screenshot", path))
        return b""

    async def wait_for_selector(self, selector: str, timeout: int):
        self.calls.append(("wait_for", selector, timeout))
        return self

    async def wait_for_timeout(self, milliseconds: int) -> None:
        self.calls.append(("wait_for_timeout", milliseconds))

    def locator(self, selector: str) -> FakeLocator:
        self.calls.append(("locator", selector))
        return FakeLocator(selector)


class ScreenshotFailPage(FakePage):
    def __init__(
        self,
        *,
        failing_full_page: bool = True,
        failing_viewport: bool = False,
    ) -> None:
        super().__init__()
        self.screenshot_kwargs: list[dict] = []
        self.failing_full_page = failing_full_page
        self.failing_viewport = failing_viewport

    async def screenshot(self, path: str, **kwargs) -> bytes:
        self.screenshot_kwargs.append(dict(kwargs))
        full_page = bool(kwargs.get("full_page"))
        Path(path).write_bytes(b"fake-png")
        if (full_page and self.failing_full_page) or (
            not full_page and self.failing_viewport
        ):
            raise RuntimeError(
                "Page.screenshot: Timeout 15000ms exceeded."
            )
        self.calls.append(("screenshot", path))
        return b""


class FakeBrowserContext:
    def __init__(self) -> None:
        self.page = FakePage()
        self.viewport: dict[str, int] | None = None
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.context = FakeBrowserContext()
        self.closed = False
        self.launch_kwargs: dict | None = None

    async def new_context(self, **kwargs) -> FakeBrowserContext:
        self.context.viewport = kwargs.get("viewport")
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self.persistent_context = FakeBrowserContext()
        self.persistent_kwargs: dict | None = None

    async def launch(self, **kwargs) -> FakeBrowser:
        self.browser.launch_kwargs = kwargs
        return self.browser

    async def launch_persistent_context(self, **kwargs) -> FakeBrowserContext:
        self.persistent_kwargs = kwargs
        return self.persistent_context


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()


class FakePlaywrightManager:
    def __init__(self) -> None:
        self.playwright = FakePlaywright()

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        return None


def _fake_async_playwright() -> FakePlaywrightManager:
    return FakePlaywrightManager()


def test_execution_result_creation() -> None:
    result = ExecutionResult(
        success=True,
        output="finished",
        error=None,
        screenshots=["logs/abc/step_0_goto.png"],
        step_logs=[{"step_index": 0, "status": "success"}],
        duration_seconds=1.25,
    )

    assert result.success is True
    assert result.output == "finished"
    assert result.error is None
    assert result.screenshots == ["logs/abc/step_0_goto.png"]
    assert result.step_logs == [{"step_index": 0, "status": "success"}]
    assert result.duration_seconds == 1.25


def test_run_simple_code() -> None:
    async def scenario() -> ExecutionResult:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
            runner.logs_dir = Path(temp_dir)

            with patch(
                "app.engine.runner.async_playwright",
                side_effect=_fake_async_playwright,
            ):
                return await runner.run(
                    """
await page.goto("https://example.com")
log("finished")
""",
                    {"account": "demo"},
                )

    result = asyncio.run(scenario())

    assert result.success is True
    assert result.error is None
    assert result.output == "finished"
    assert len(result.step_logs) == 1
    assert result.step_logs[0]["action"] == "goto"
    assert len(result.screenshots) == 1
    assert result.duration_seconds >= 0


def test_run_generated_main_entrypoint() -> None:
    async def scenario() -> tuple[ExecutionResult, list[tuple]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
            runner.logs_dir = Path(temp_dir)
            page = FakePage()
            browser = FakeBrowser()
            browser.context.page = page
            playwright = FakePlaywright()
            playwright.chromium.browser = browser

            class FakeManager:
                async def __aenter__(self):
                    return playwright

                async def __aexit__(self, *_args):
                    return None

            with patch(
                "app.engine.runner.async_playwright",
                return_value=FakeManager(),
            ):
                result = await runner.run(
                    """
async def main(page):
    await page.goto("https://example.com")
    log("finished")
""",
                    {},
                )
            return result, page.calls

    result, page_calls = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "finished"
    assert ("goto", "https://example.com") in page_calls
    assert len(result.step_logs) == 1


def test_run_uses_edge_persistent_profile(tmp_path: Path) -> None:
    async def scenario() -> tuple[ExecutionResult, FakeChromium, Path]:
        edge_executable = tmp_path / "msedge.exe"
        edge_executable.write_bytes(b"")
        user_data_dir = tmp_path / "edge-profile"
        (user_data_dir / "Default").mkdir(parents=True)
        runner = PlaywrightRunner(
            SimpleNamespace(
                EXECUTION_TIMEOUT=5,
                BROWSER_MODE="edge",
                EDGE_EXECUTABLE_PATH=str(edge_executable),
                EDGE_USER_DATA_DIR=str(user_data_dir),
                EDGE_PROFILE_DIRECTORY="Default",
                BROWSER_HEADLESS=True,
            )
        )
        runner.logs_dir = tmp_path / "logs"
        playwright = FakePlaywright()

        class FakeManager:
            async def __aenter__(self):
                return playwright

            async def __aexit__(self, *_args):
                return None

        with (
            patch(
                "app.engine.runner.async_playwright",
                return_value=FakeManager(),
            ),
            patch.object(
                runner,
                "_is_edge_running",
                return_value=False,
            ),
        ):
            result = await runner.run("log('edge ok')", {})

        return result, playwright.chromium, user_data_dir

    result, chromium, user_data_dir = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "edge ok"
    assert chromium.persistent_kwargs is not None
    assert chromium.persistent_kwargs["user_data_dir"] == str(
        user_data_dir
    )
    assert chromium.persistent_kwargs["executable_path"].endswith(
        "msedge.exe"
    )
    assert chromium.persistent_kwargs["headless"] is True
    assert "--profile-directory=Default" in (
        chromium.persistent_kwargs["args"]
    )
    assert chromium.persistent_context.closed is True


def test_run_reports_when_edge_is_open(tmp_path: Path) -> None:
    async def scenario() -> ExecutionResult:
        edge_executable = tmp_path / "msedge.exe"
        edge_executable.write_bytes(b"")
        user_data_dir = tmp_path / "edge-profile"
        (user_data_dir / "Default").mkdir(parents=True)
        runner = PlaywrightRunner(
            SimpleNamespace(
                EXECUTION_TIMEOUT=5,
                BROWSER_MODE="edge",
                EDGE_EXECUTABLE_PATH=str(edge_executable),
                EDGE_USER_DATA_DIR=str(user_data_dir),
                EDGE_PROFILE_DIRECTORY="Default",
                BROWSER_HEADLESS=True,
            )
        )
        runner.logs_dir = tmp_path / "logs"

        with (
            patch(
                "app.engine.runner.async_playwright",
                side_effect=_fake_async_playwright,
            ),
            patch.object(
                runner,
                "_is_edge_running",
                return_value=True,
            ),
        ):
            return await runner.run("log('edge ok')", {})

    result = asyncio.run(scenario())

    assert result.success is False
    assert result.error is not None
    assert "Edge" in result.error
    assert "close" in result.error.lower()


def test_browser_settings_override_is_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_file = tmp_path / "browser-settings.json"
    settings_file.write_text(
        """
{
  "mode": "edge",
  "executable_path": "C:\\\\CheeseEdge\\\\App\\\\msedge.exe",
  "user_data_dir": "C:\\\\CheeseEdge\\\\Data",
  "profile_directory": "Profile 1",
  "headless": false
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "NL_RPA_BROWSER_SETTINGS",
        str(settings_file),
    )

    runner = PlaywrightRunner(
        SimpleNamespace(EXECUTION_TIMEOUT=5)
    )

    assert runner.browser_mode == "edge"
    assert runner.edge_executable_path == (
        r"C:\CheeseEdge\App\msedge.exe"
    )
    assert runner.edge_user_data_dir == r"C:\CheeseEdge\Data"
    assert runner.edge_profile_directory == "Profile 1"
    assert runner.browser_headless is False


def test_edge_profile_directory_auto_detection(
    tmp_path: Path,
) -> None:
    user_data_dir = tmp_path / "Data"
    profile_dir = user_data_dir / "Profile 1"
    (profile_dir / "Network").mkdir(parents=True)
    (profile_dir / "Network" / "Cookies").write_bytes(b"cookie")
    runner = PlaywrightRunner(
        SimpleNamespace(
            EXECUTION_TIMEOUT=5,
            BROWSER_MODE="edge",
            EDGE_PROFILE_DIRECTORY="auto",
        )
    )

    assert (
        runner._resolve_edge_profile_directory(user_data_dir)
        == "Profile 1"
    )


def test_run_timeout() -> None:
    async def scenario() -> ExecutionResult:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=0.01))
            runner.logs_dir = Path(temp_dir)

            with patch(
                "app.engine.runner.async_playwright",
                side_effect=_fake_async_playwright,
            ):
                return await runner.run(
                    "await asyncio.sleep(1)",
                    {},
                )

    result = asyncio.run(scenario())

    assert result.success is False
    assert result.error is not None
    assert "timed out" in result.error.lower()


def test_auto_screenshot() -> None:
    async def scenario() -> ExecutionResult:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
            runner.logs_dir = Path(temp_dir)

            with patch(
                "app.engine.runner.async_playwright",
                side_effect=_fake_async_playwright,
            ):
                return await runner.run(
                    """
await page.goto("https://example.com")
await page.click("#submit")
await page.fill("#email", "user@example.com")
await page.press("#email", "Enter")
""",
                    {},
                )

    result = asyncio.run(scenario())

    assert result.success is True
    assert [entry["action"] for entry in result.step_logs] == [
        "goto",
        "click",
        "fill",
        "press",
    ]
    assert all(entry["status"] == "success" for entry in result.step_logs)
    assert all(entry["screenshot_path"] for entry in result.step_logs)
    assert len(result.screenshots) == 4


def test_helper_functions() -> None:
    async def scenario() -> tuple[str, list[str], list[dict], list[tuple]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
            runner.logs_dir = Path(temp_dir)
            page = FakePage()
            output: list[str] = []
            step_logs: list[dict] = []

            helpers = runner._build_helper_functions(
                "abc12345",
                page,
                [0],
                output,
                step_logs,
            )

            screenshot_path = await helpers["screenshot"]("finished")
            helpers["log"]("helper output")
            await helpers["wait_for"]("#ready", 1000)
            table = await helpers["extract_table"]("table")

            return screenshot_path, output, table, page.calls

    screenshot_path, output, table, page_calls = asyncio.run(scenario())

    assert screenshot_path.endswith("abc12345\\finished.png") or screenshot_path.endswith(
        "abc12345/finished.png"
    )
    assert output == ["helper output"]
    assert table == [
        {"name": "Alice", "age": "30"},
        {"name": "Bob", "age": "28"},
    ]
    assert ("wait_for", "#ready", 1000) in page_calls
    assert ("locator", "table") in page_calls


def test_wait_for_accepts_milliseconds() -> None:
    async def scenario() -> tuple[object, list[tuple]]:
        runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
        page = FakePage()
        helpers = runner._build_helper_functions(
            "abc12345",
            page,
            [0],
            [],
            [],
        )

        result = await helpers["wait_for"](2000)
        return result, page.calls

    result, page_calls = asyncio.run(scenario())

    assert result is None
    assert ("wait_for_timeout", 2000) in page_calls


def test_log_supports_sync_and_awaited_calls() -> None:
    async def scenario() -> list[str]:
        runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
        output: list[str] = []
        helpers = runner._build_helper_functions(
            "abc12345",
            FakePage(),
            [0],
            output,
            [],
        )

        helpers["log"]("sync output")
        await helpers["log"]("awaited output")
        return output

    output = asyncio.run(scenario())

    assert output == ["sync output", "awaited output"]


def test_run_legacy_wait_and_log_generated_code() -> None:
    async def scenario() -> tuple[ExecutionResult, list[tuple]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
            runner.logs_dir = Path(temp_dir)
            page = FakePage()
            browser = FakeBrowser()
            browser.context.page = page
            playwright = FakePlaywright()
            playwright.chromium.browser = browser

            class FakeManager:
                async def __aenter__(self):
                    return playwright

                async def __aexit__(self, *_args):
                    return None

            with patch(
                "app.engine.runner.async_playwright",
                return_value=FakeManager(),
            ):
                result = await runner.run(
                    """
async def main(page):
    await page.goto("https://www.bilibili.com")
    await wait_for(2000)
    await screenshot("step_1_open_bilibili")
    await log("opened bilibili")
""",
                    {},
                )
            return result, page.calls

    result, page_calls = asyncio.run(scenario())

    assert result.success is True
    assert result.error is None
    assert result.output == "opened bilibili"
    assert [entry["action"] for entry in result.step_logs] == [
        "goto",
        "screenshot",
    ]
    assert ("wait_for_timeout", 2000) in page_calls


def test_save_file_helper(tmp_path: Path) -> None:
    runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
    runner.logs_dir = tmp_path
    artifacts: list[dict] = []
    output: list[str] = []

    helpers = runner._build_helper_functions(
        "abc12345",
        FakePage(),
        [0],
        output,
        [],
        artifacts,
    )
    saved_path = asyncio.run(
        helpers["save_file"](
            r"D:\Administrator\Documents\B站+2026-10-01.csv",
            "name,count\nalice,2\n",
        )
    )

    assert Path(saved_path).is_file()
    assert saved_path.endswith(
        (
            "abc12345\\artifacts\\B站+2026-10-01.csv",
            "abc12345/artifacts/B站+2026-10-01.csv",
        )
    )
    assert artifacts[0]["name"] == "B站+2026-10-01.csv"
    assert artifacts[0]["requested_path"] == (
        r"D:\Administrator\Documents\B站+2026-10-01.csv"
    )
    assert output


def test_run_collects_artifacts(tmp_path: Path) -> None:
    async def scenario() -> ExecutionResult:
        runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
        runner.logs_dir = tmp_path

        with patch(
            "app.engine.runner.async_playwright",
            side_effect=_fake_async_playwright,
        ):
            return await runner.run(
                """
await page.goto("https://example.com")
saved = await save_file("report.csv", "a,b\\n1,2\\n")
log(saved)
""",
                {},
            )

    result = asyncio.run(scenario())

    assert result.success is True
    assert len(result.artifacts) == 1
    assert result.artifacts[0]["name"] == "report.csv"
    assert Path(result.artifacts[0]["path"]).is_file()


def test_capture_screenshot_falls_back_to_viewport(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[str | None, str | None, list[dict]]:
        runner = PlaywrightRunner(
            SimpleNamespace(EXECUTION_TIMEOUT=5)
        )
        runner.logs_dir = tmp_path
        page = ScreenshotFailPage()
        path, error = await runner._capture_screenshot(
            page,
            "abc12345",
            "shot.png",
        )
        return path, error, page.screenshot_kwargs

    path, error, calls = asyncio.run(scenario())

    assert error is None
    assert path is not None
    assert Path(path).is_file()
    assert [call["full_page"] for call in calls] == [True, False]
    assert calls[0]["timeout"] == 15000
    assert calls[0]["animations"] == "disabled"
    assert calls[0]["caret"] == "hide"


def test_capture_screenshot_reports_timeout(tmp_path: Path) -> None:
    async def scenario() -> tuple[str | None, str | None]:
        runner = PlaywrightRunner(
            SimpleNamespace(EXECUTION_TIMEOUT=5)
        )
        runner.logs_dir = tmp_path
        page = ScreenshotFailPage(failing_viewport=True)
        return await runner._capture_screenshot(
            page,
            "abc12345",
            "shot.png",
        )

    path, error = asyncio.run(scenario())

    assert path is None
    assert error is not None
    assert "Timeout" in error
    assert not (tmp_path / "abc12345" / "shot.png").exists()


def test_capture_screenshot_can_be_disabled(tmp_path: Path) -> None:
    async def scenario() -> tuple[str | None, str | None, list[dict]]:
        runner = PlaywrightRunner(
            SimpleNamespace(
                EXECUTION_TIMEOUT=5,
                SCREENSHOT_ENABLED=False,
            )
        )
        runner.logs_dir = tmp_path
        page = ScreenshotFailPage()
        path, error = await runner._capture_screenshot(
            page,
            "abc12345",
            "shot.png",
        )
        return path, error, page.screenshot_kwargs

    path, error, calls = asyncio.run(scenario())

    assert path is None
    assert error is None
    assert calls == []


def _run_with_page(code: str, page: FakePage) -> ExecutionResult:
    async def scenario() -> ExecutionResult:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = PlaywrightRunner(
                SimpleNamespace(EXECUTION_TIMEOUT=5)
            )
            runner.logs_dir = Path(temp_dir)
            browser = FakeBrowser()
            browser.context.page = page
            playwright = FakePlaywright()
            playwright.chromium.browser = browser

            class FakeManager:
                async def __aenter__(self):
                    return playwright

                async def __aexit__(self, *_args):
                    return None

            with patch(
                "app.engine.runner.async_playwright",
                return_value=FakeManager(),
            ):
                return await runner.run(code, {})

    return asyncio.run(scenario())


def test_screenshot_failure_does_not_fail_execution() -> None:
    result = _run_with_page(
        """
async def main(page):
    await page.goto("https://example.com")
    await screenshot("finished")
""",
        ScreenshotFailPage(failing_viewport=True),
    )

    assert result.success is True
    assert result.error is None
    assert result.screenshots == []
    assert [entry["status"] for entry in result.step_logs] == [
        "success",
        "warning",
    ]
    assert "Timeout" in (result.step_logs[1]["error"] or "")


def test_auto_screenshot_failure_keeps_action_success() -> None:
    result = _run_with_page(
        """
async def main(page):
    await page.click("#submit")
""",
        ScreenshotFailPage(failing_viewport=True),
    )

    assert result.success is True
    assert result.step_logs[0]["status"] == "success"
    assert result.step_logs[0]["screenshot_path"] is None
    assert "Timeout" in (
        result.step_logs[0]["screenshot_error"] or ""
    )


def test_run_with_retry() -> None:
    async def scenario() -> tuple[ExecutionResult, int, tuple]:
        runner = PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))
        failed_result = ExecutionResult(
            success=False,
            output="",
            error="temporary failure",
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )
        successful_result = ExecutionResult(
            success=True,
            output="recovered",
            error=None,
            screenshots=[],
            step_logs=[],
            duration_seconds=0.1,
        )
        runner.run = AsyncMock(side_effect=[failed_result, successful_result])
        sleep = AsyncMock()

        with patch("app.engine.runner.asyncio.sleep", sleep):
            result = await runner.run_with_retry("pass", {}, max_retries=2)

        return result, runner.run.await_count, sleep.await_args.args

    result, attempts, sleep_args = asyncio.run(scenario())

    assert result.success is True
    assert result.output == "recovered"
    assert attempts == 2
    assert sleep_args == (2,)


if __name__ == "__main__":
    test_execution_result_creation()
    test_run_simple_code()
    test_run_timeout()
    test_auto_screenshot()
    test_helper_functions()
    test_run_with_retry()
    print("runner tests passed")
