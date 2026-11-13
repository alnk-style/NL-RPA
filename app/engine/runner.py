from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from app.engine.artifacts import (
    ARTIFACT_DIR_NAME,
    is_absolute_target,
    sanitize_artifact_name,
)

if TYPE_CHECKING:
    from app.config import Settings


logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    output: str
    error: str | None
    screenshots: list[str]
    step_logs: list[dict[str, Any]]
    duration_seconds: float
    details: dict[str, Any] | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StepLog:
    step_index: int
    action: str
    target: str
    selector: str | None
    status: str
    screenshot_path: str | None
    error: str | None
    started_at: float
    finished_at: float
    screenshot_error: str | None = None


class _CompletedAwaitable:
    """An already-completed value accepted by ``await``."""

    __slots__ = ()

    def __await__(self) -> Any:
        return iter(())


def _portable_edge_candidates() -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for drive in ("C", "D", "E", "F", "G"):
        root = Path(f"{drive}:/")
        if not root.is_dir():
            continue
        try:
            entries = root.iterdir()
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name.casefold()
            if "edge" not in name and "browser" not in name:
                continue
            for candidate in (
                entry / "App" / "msedge.exe",
                entry / "msedge.exe",
            ):
                key = os.path.normcase(str(candidate))
                if key in seen or not candidate.is_file():
                    continue
                seen.add(key)
                candidates.append(candidate)
    return candidates


class PlaywrightRunner:
    def __init__(self, config: Settings):
        self.timeout = config.EXECUTION_TIMEOUT
        self.screenshot_enabled = bool(
            getattr(config, "SCREENSHOT_ENABLED", True)
        )
        self.screenshot_timeout_ms = int(
            getattr(config, "SCREENSHOT_TIMEOUT_MS", 15000) or 15000
        )
        self.screenshot_full_page = bool(
            getattr(config, "SCREENSHOT_FULL_PAGE", True)
        )
        self.logs_dir = Path("logs")
        self.browser_mode = str(
            getattr(config, "BROWSER_MODE", "auto") or "auto"
        ).strip().lower()
        self.edge_executable_path = str(
            getattr(config, "EDGE_EXECUTABLE_PATH", "") or ""
        ).strip()
        self.edge_user_data_dir = str(
            getattr(config, "EDGE_USER_DATA_DIR", "") or ""
        ).strip()
        self.edge_profile_directory = (
            str(
                getattr(
                    config,
                    "EDGE_PROFILE_DIRECTORY",
                    "auto",
                )
                or "auto"
            ).strip()
            or "auto"
        )
        self.browser_headless = bool(
            getattr(config, "BROWSER_HEADLESS", True)
        )
        self._load_browser_overrides()

    async def run(
        self,
        code: str,
        context: dict[str, Any],
    ) -> ExecutionResult:
        execution_id = uuid4().hex[:8]
        execution_dir = self.logs_dir / execution_id
        execution_dir.mkdir(parents=True, exist_ok=True)

        output: list[str] = []
        screenshots: list[str] = []
        step_logs: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        step_counter = [0]
        started_at = time.perf_counter()
        error: str | None = None
        success = False
        browser = None
        browser_context = None

        try:
            async with async_playwright() as playwright:
                browser, browser_context = await self._launch_browser(
                    playwright
                )
                page = await browser_context.new_page()

                setattr(page, "_rpa_step_logs", step_logs)
                self._patch_page_auto_screenshot(
                    page,
                    execution_id,
                    step_counter,
                )
                helpers = self._build_helper_functions(
                    execution_id,
                    page,
                    step_counter,
                    output,
                    step_logs,
                    artifacts,
                )

                namespace: dict[str, Any] = dict(context)
                namespace.update(
                    {
                        "page": page,
                        "context": context,
                        "asyncio": asyncio,
                        "execution_id": execution_id,
                        **helpers,
                    }
                )

                main_source = code if code.strip() else "pass"
                wrapped_code = (
                    "async def __rpa_main__():\n"
                    + textwrap.indent(main_source, "    ")
                )
                has_page_entrypoint = bool(
                    re.search(
                        r"^\s*async\s+def\s+main\s*\(\s*page\b",
                        main_source,
                        flags=re.MULTILINE,
                    )
                )

                if has_page_entrypoint:
                    exec(
                        compile(
                            main_source,
                            "<rpa_generated_code>",
                            "exec",
                        ),
                        namespace,
                    )
                    main = namespace.get("main")
                    if not callable(main):
                        raise RuntimeError(
                            "Generated code does not define main(page)"
                        )
                    await asyncio.wait_for(
                        main(page),
                        timeout=self.timeout,
                    )
                else:
                    exec(
                        compile(
                            wrapped_code,
                            "<rpa_generated_code>",
                            "exec",
                        ),
                        namespace,
                    )
                    main = namespace["__rpa_main__"]
                    await asyncio.wait_for(
                        main(),
                        timeout=self.timeout,
                    )
                success = True
        except asyncio.TimeoutError:
            error = f"Execution timed out after {self.timeout} seconds"
        except PlaywrightError as exc:
            error = f"Playwright error: {exc}"
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
        finally:
            if browser_context is not None:
                try:
                    await browser_context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

        for step_log in step_logs:
            screenshot_path = step_log.get("screenshot_path")
            if isinstance(screenshot_path, str) and screenshot_path not in screenshots:
                screenshots.append(screenshot_path)

        return ExecutionResult(
            success=success,
            output="\n".join(output),
            error=error,
            screenshots=screenshots,
            step_logs=step_logs,
            duration_seconds=time.perf_counter() - started_at,
            artifacts=artifacts,
        )

    async def _launch_browser(self, playwright: Any) -> tuple[Any, Any]:
        mode = self._resolved_browser_mode()
        if mode == "edge":
            context = await self._launch_edge_context(playwright)
            return None, context

        browser = await playwright.chromium.launch(
            headless=self.browser_headless
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080}
        )
        return browser, context

    async def launch_browser(self, playwright: Any) -> tuple[Any, Any]:
        """供执行器与观察层共用的浏览器启动入口。"""

        return await self._launch_browser(playwright)

    def _resolved_browser_mode(self) -> str:
        if self.browser_mode in {"", "auto"}:
            if os.getenv("NL_RPA_DESKTOP_MODE") == "1":
                return "edge"
            return "isolated"
        if self.browser_mode in {"edge", "edge_profile", "existing"}:
            return "edge"
        return "isolated"

    async def _launch_edge_context(self, playwright: Any) -> Any:
        edge_executable = self._resolve_edge_executable()
        user_data_dir = self._resolve_edge_user_data_dir(
            edge_executable
        )
        profile_directory = self._resolve_edge_profile_directory(
            user_data_dir
        )
        profile_dir = user_data_dir / profile_directory
        if not profile_dir.is_dir():
            raise RuntimeError(
                "Microsoft Edge profile directory was not found: "
                f"{profile_dir}. Open Edge, sign in, and close it "
                "before running this workflow."
            )
        if self._is_edge_running():
            raise RuntimeError(
                "Microsoft Edge is running. Close Edge completely, "
                "including background processes, before running "
                "this workflow."
            )

        return await playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            executable_path=str(edge_executable),
            headless=self.browser_headless,
            viewport={"width": 1920, "height": 1080},
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                f"--profile-directory={profile_directory}",
            ],
        )

    def _load_browser_overrides(self) -> None:
        settings_path = self._browser_settings_path()
        if settings_path is None or not settings_path.is_file():
            return
        try:
            payload = json.loads(
                settings_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return

        mode = payload.get("mode")
        if isinstance(mode, str) and mode.strip():
            self.browser_mode = mode.strip().lower()

        for key, attribute in (
            ("executable_path", "edge_executable_path"),
            ("user_data_dir", "edge_user_data_dir"),
            ("profile_directory", "edge_profile_directory"),
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                setattr(self, attribute, value.strip())

        headless = payload.get("headless")
        if isinstance(headless, bool):
            self.browser_headless = headless

    @staticmethod
    def _browser_settings_path() -> Path | None:
        override = os.getenv("NL_RPA_BROWSER_SETTINGS")
        if override:
            return Path(override).expanduser()
        if os.getenv("NL_RPA_DESKTOP_MODE") == "1":
            return Path.home() / ".nl-rpa" / "browser-settings.json"
        return None

    def _resolve_edge_executable(self) -> Path:
        if self.edge_executable_path:
            executable = Path(
                self.edge_executable_path
            ).expanduser()
            if executable.is_file():
                return executable
            raise RuntimeError(
                "Configured Microsoft Edge executable was not found: "
                f"{executable}"
            )

        program_files_x86 = Path(
            os.environ.get(
                "ProgramFiles(x86)",
                r"C:\Program Files (x86)",
            )
        )
        candidates: list[Path] = []
        edge_core = program_files_x86 / "Microsoft" / "EdgeCore"
        if edge_core.is_dir():
            versions = sorted(
                (
                    directory
                    for directory in edge_core.iterdir()
                    if directory.is_dir()
                ),
                key=lambda directory: directory.name,
                reverse=True,
            )
            candidates.extend(
                version / "msedge.exe" for version in versions
            )
        candidates.extend(
            [
                program_files_x86
                / "Microsoft"
                / "Edge"
                / "Application"
                / "msedge.exe",
                Path(
                    os.environ.get(
                        "ProgramFiles",
                        r"C:\Program Files",
                    )
                )
                / "Microsoft"
                / "Edge"
                / "Application"
                / "msedge.exe",
            ]
        )
        candidates.extend(_portable_edge_candidates())
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise RuntimeError(
            "Microsoft Edge was not found on this computer."
        )

    def _resolve_edge_user_data_dir(
        self,
        edge_executable: Path,
    ) -> Path:
        if self.edge_user_data_dir:
            user_data_dir = Path(
                self.edge_user_data_dir
            ).expanduser()
            if user_data_dir.is_dir():
                return user_data_dir
            raise RuntimeError(
                "Configured Microsoft Edge user data directory was "
                f"not found: {user_data_dir}"
            )

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = (
                Path(local_app_data)
                / "Microsoft"
                / "Edge"
                / "User Data"
            )
            if candidate.is_dir():
                return candidate
        portable_root = edge_executable.parent.parent
        for candidate in (
            portable_root / "Data",
            portable_root / "User Data",
        ):
            if candidate.is_dir():
                return candidate
        raise RuntimeError(
            "Microsoft Edge user data directory was not found. "
            "Run scripts/open_edge_login.py to sign in once, "
            "then close Edge before running this workflow."
        )

    def _resolve_edge_profile_directory(
        self,
        user_data_dir: Path,
    ) -> str:
        configured = self.edge_profile_directory
        if configured not in {"", "auto"}:
            if (user_data_dir / configured).is_dir():
                return configured
            raise RuntimeError(
                "Configured Microsoft Edge profile directory was "
                f"not found: {user_data_dir / configured}"
            )

        candidates: list[Path] = []
        for entry in user_data_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.casefold() == "default":
                candidates.append(entry)
                continue
            if entry.name.casefold().startswith("profile "):
                candidates.append(entry)
        if not candidates:
            raise RuntimeError(
                "No Microsoft Edge profile directories were found in "
                f"{user_data_dir}."
            )

        def score(profile_dir: Path) -> tuple[bool, float]:
            cookies = profile_dir / "Network" / "Cookies"
            try:
                modified_at = profile_dir.stat().st_mtime
            except OSError:
                modified_at = 0.0
            return (cookies.is_file(), modified_at)

        return max(candidates, key=score).name

    def _is_edge_running(self) -> bool:
        if os.name != "nt":
            return False
        try:
            completed = subprocess.run(
                [
                    "tasklist",
                    "/FI",
                    "IMAGENAME eq msedge.exe",
                    "/FO",
                    "CSV",
                    "/NH",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return "msedge.exe" in completed.stdout.casefold()

    async def run_with_retry(
        self,
        code: str,
        context: dict[str, Any],
        max_retries: int = 3,
    ) -> ExecutionResult:
        attempts = max(1, max_retries)
        result: ExecutionResult | None = None

        for attempt in range(attempts):
            result = await self.run(code, context)
            if result.success or attempt == attempts - 1:
                return result
            await asyncio.sleep(2)

        if result is None:
            raise RuntimeError("Playwright runner did not execute")
        return result

    def _patch_page_auto_screenshot(
        self,
        page: Any,
        execution_id: str,
        step_counter: list[int],
    ) -> None:
        for action in ("goto", "click", "fill", "press"):
            if not hasattr(page, action):
                continue

            original = getattr(page, action)

            def make_wrapper(
                action_name: str,
                original_method: Callable[..., Awaitable[Any]],
            ) -> Callable[..., Awaitable[Any]]:
                async def wrapper(*args: Any, **kwargs: Any) -> Any:
                    step_index = step_counter[0]
                    step_counter[0] += 1
                    started_at = time.perf_counter()
                    target = self._get_step_target(action_name, args, kwargs)
                    selector = (
                        target
                        if action_name in {"click", "fill", "press"}
                        else None
                    )
                    step_log = StepLog(
                        step_index=step_index,
                        action=action_name,
                        target=target,
                        selector=selector,
                        status="running",
                        screenshot_path=None,
                        error=None,
                        started_at=started_at,
                        finished_at=started_at,
                    )
                    step_log_entry = asdict(step_log)
                    active_step_logs = getattr(page, "_rpa_step_logs", None)
                    if isinstance(active_step_logs, list):
                        active_step_logs.append(step_log_entry)

                    try:
                        result = await original_method(*args, **kwargs)
                    except (Exception, asyncio.CancelledError) as exc:
                        screenshot_path, screenshot_error = (
                            await self._capture_screenshot(
                                page,
                                execution_id,
                                (
                                    f"step_{step_index:02d}_"
                                    f"{action_name}_failed.png"
                                ),
                            )
                        )
                        step_log_entry.update(
                            {
                                "status": "failed",
                                "screenshot_path": screenshot_path,
                                "screenshot_error": screenshot_error,
                                "error": str(exc)
                                or exc.__class__.__name__,
                                "finished_at": time.perf_counter(),
                            }
                        )
                        raise

                    screenshot_path, screenshot_error = (
                        await self._capture_screenshot(
                            page,
                            execution_id,
                            (
                                f"step_{step_index:02d}_"
                                f"{action_name}.png"
                            ),
                        )
                    )
                    step_log_entry.update(
                        {
                            "status": "success",
                            "screenshot_path": screenshot_path,
                            "screenshot_error": screenshot_error,
                            "finished_at": time.perf_counter(),
                        }
                    )
                    return result

                return wrapper

            setattr(page, action, make_wrapper(action, original))

    def _build_helper_functions(
        self,
        execution_id: str,
        page: Any,
        step_counter: list[int],
        output: list[str],
        step_logs: list[dict[str, Any]],
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Callable[..., Any]]:
        async def screenshot(step_name: str) -> str | None:
            step_index = step_counter[0]
            step_counter[0] += 1
            started_at = time.perf_counter()
            step_log = StepLog(
                step_index=step_index,
                action="screenshot",
                target=step_name,
                selector=None,
                status="running",
                screenshot_path=None,
                error=None,
                started_at=started_at,
                finished_at=started_at,
            )
            step_log_entry = asdict(step_log)
            step_logs.append(step_log_entry)

            filename = f"{self._sanitize_filename(step_name)}.png"
            path, capture_error = await self._capture_screenshot(
                page,
                execution_id,
                filename,
            )
            if capture_error is not None:
                step_log_entry.update(
                    {
                        "status": "warning",
                        "error": capture_error,
                        "finished_at": time.perf_counter(),
                    }
                )
                output.append(
                    f"screenshot failed: {step_name}: "
                    f"{capture_error}"
                )
                return None

            step_log_entry.update(
                {
                    "status": "success",
                    "screenshot_path": path,
                    "finished_at": time.perf_counter(),
                }
            )
            return path

        def log(message: str) -> _CompletedAwaitable:
            output.append(str(message))
            return _CompletedAwaitable()

        async def extract_table(selector: str) -> list[dict[str, str]]:
            return await page.locator(selector).evaluate(
                """
                (table) => {
                    const rows = Array.from(table.querySelectorAll("tr"));
                    if (rows.length === 0) {
                        return [];
                    }
                    const readCells = (row) => Array.from(
                        row.querySelectorAll("th, td")
                    ).map((cell) => (cell.textContent || "").trim());
                    const headers = readCells(rows[0]);
                    return rows.slice(1).map((row) => {
                        const values = readCells(row);
                        return headers.reduce((record, header, index) => {
                            const key = header || `column_${index + 1}`;
                            record[key] = values[index] || "";
                            return record;
                        }, {});
                    });
                }
                """
            )

        async def wait_for(
            selector_or_milliseconds: str | float,
            timeout: float | None = None,
        ) -> Any:
            if isinstance(
                selector_or_milliseconds,
                (int, float),
            ) and not isinstance(selector_or_milliseconds, bool):
                if timeout is not None:
                    raise TypeError(
                        "wait_for(milliseconds) does not accept timeout"
                    )
                await page.wait_for_timeout(selector_or_milliseconds)
                return None

            selector = str(selector_or_milliseconds).strip()
            if not selector:
                raise ValueError("wait_for selector must not be empty")
            wait_timeout = 30000 if timeout is None else int(timeout)
            return await page.wait_for_selector(
                selector,
                timeout=wait_timeout,
            )

        async def save_file(
            path: str,
            content: str | bytes,
            *,
            encoding: str = "utf-8",
        ) -> str:
            """把结果写到执行产物目录，白名单目录由引擎在结束后投递。"""

            requested = str(path or "").strip()
            filename = sanitize_artifact_name(
                requested or "artifact"
            )
            artifact_dir = self.logs_dir / execution_id / ARTIFACT_DIR_NAME
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / filename

            if isinstance(content, bytes):
                artifact_path.write_bytes(content)
            else:
                artifact_path.write_text(str(content), encoding=encoding)

            record = {
                "name": filename,
                "path": str(artifact_path),
                "requested_path": (
                    requested if is_absolute_target(requested) else None
                ),
                "size": artifact_path.stat().st_size,
                "delivered": False,
                "delivered_path": None,
                "error": None,
            }
            if artifacts is not None:
                artifacts.append(record)
            output.append(f"artifact saved: {artifact_path}")
            return str(artifact_path)

        return {
            "screenshot": screenshot,
            "log": log,
            "extract_table": extract_table,
            "wait_for": wait_for,
            "save_file": save_file,
        }

    async def _capture_screenshot(
        self,
        page: Any,
        execution_id: str,
        filename: str,
        *,
        full_page: bool | None = None,
    ) -> tuple[str | None, str | None]:
        """采集一张截图，永不抛出异常。

        返回 ``(path, error)``：成功时 ``error`` 为 ``None``；失败时
        ``path`` 为 ``None`` 并附带错误文案；截图被禁用时两者都是
        ``None``。``full_page`` 为 ``None`` 时使用配置默认值。
        """

        if not self.screenshot_enabled:
            return None, None

        path = self.logs_dir / execution_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if full_page is None:
            attempts = (
                [True, False] if self.screenshot_full_page else [False]
            )
        else:
            attempts = [bool(full_page)]
        last_error: str | None = None

        for attempt_full_page in attempts:
            try:
                await page.screenshot(
                    path=str(path),
                    full_page=attempt_full_page,
                    timeout=self.screenshot_timeout_ms,
                    animations="disabled",
                    caret="hide",
                )
                return str(path), None
            except Exception as exc:
                last_error = str(exc) or exc.__class__.__name__
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                logger.warning(
                    "截图失败（full_page=%s, file=%s）：%s",
                    attempt_full_page,
                    filename,
                    last_error,
                )

        return None, last_error

    async def capture_screenshot(
        self,
        page: Any,
        execution_id: str,
        filename: str,
        *,
        full_page: bool | None = None,
    ) -> tuple[str | None, str | None]:
        """供结构化执行器复用的截图入口。"""

        return await self._capture_screenshot(
            page,
            execution_id,
            filename,
            full_page=full_page,
        )

    @staticmethod
    def _get_step_target(
        action: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        if args:
            return str(args[0])
        if action == "goto":
            return str(kwargs.get("url", ""))
        return str(kwargs.get("selector", ""))

    @staticmethod
    def _sanitize_filename(value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
        return sanitized or "screenshot"
