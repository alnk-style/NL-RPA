"""结构化网页执行器。

执行器消费 :class:`~app.protocols.StructuredStep`，逐个动作执行、
逐候选定位、按步骤重试，并在失败时给出确定性的失败分类与截图。
每个步骤都写入 ``step_logs``，记录实际命中的定位策略和尝试次数，
供 UI、修复器和基线统计使用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from playwright.async_api import async_playwright

from app.engine.web_catalog import (
    WebPlanError,
    validate_web_plan,
    web_action_catalog_text,
)
from app.engine.artifacts import (
    ARTIFACT_DIR_NAME,
    is_absolute_target,
    sanitize_artifact_name,
)
from app.engine.browser_observer import BrowserObserver
from app.engine.browser_observer import OBSERVATION_SCRIPT
from app.engine.human_intervention import (
    classify_interaction_kind,
    web_verification_spec,
)
from app.engine.runner import ExecutionResult, PlaywrightRunner
from app.protocols import (
    REPAIRABLE_CATEGORIES,
    FailureCategory,
    LocatorFallback,
    LocatorSpec,
    LocatorStrategy,
    OnFailure,
    StructuredStep,
    classify_failure,
    parse_structured_steps,
)
from app.security.redaction import redact_text, secret_values

if TYPE_CHECKING:
    from app.config import Settings


logger = logging.getLogger(__name__)

TABLE_EXTRACTION_SCRIPT = """
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

_MISSING = object()

_CREDENTIAL_PLACEHOLDER = re.compile(
    r"\{\{\s*credentials\.([A-Za-z0-9_.-]+)\s*\}\}"
)


class _StepFailure(RuntimeError):
    """单个步骤的失败，携带确定性的失败分类。"""

    def __init__(
        self,
        message: str,
        category: FailureCategory = FailureCategory.UNKNOWN,
        *,
        locator_attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.locator_attempts = list(locator_attempts or [])


@dataclass
class _StepOutcome:
    """步骤成功后的轻量结果。"""

    locator_strategy: str | None = None
    value: Any = None
    screenshot_path: str | None = None


class WebExecutor:
    """在 Playwright 页面里执行结构化网页步骤。"""

    def __init__(
        self,
        config: Settings,
        runner: PlaywrightRunner | None = None,
        repair_agent: Any | None = None,
        visual_runtime: Any | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or PlaywrightRunner(config)
        self.repair_agent = repair_agent
        self.visual_runtime = visual_runtime
        self.repair_enabled = bool(
            getattr(config, "STEP_LEVEL_REPAIR", False)
        )
        attempts = getattr(config, "WEB_REPAIR_ATTEMPTS", 2)
        try:
            self.max_repair_attempts = max(0, int(attempts))
        except (TypeError, ValueError):
            self.max_repair_attempts = 2
        self._crawl_policy: dict[str, Any] | None = None
        self._crawl_started_at = 0.0
        self._last_request_at: dict[str, float] = {}

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        plan = getattr(workflow, "structured_plan", None)
        try:
            validated = validate_web_plan(plan)
        except WebPlanError as exc:
            raise ValueError(f"结构化网页计划无效：{exc}") from exc

        steps = parse_structured_steps(validated)
        request_context = dict(context or {})
        policy = request_context.get("crawl_policy")
        if isinstance(policy, dict):
            self._crawl_policy = dict(policy)
        self._crawl_started_at = time.monotonic()
        self._last_request_at = {}
        start_index = self._non_negative_int(
            request_context.get("resume_from_step")
        )
        completed = request_context.get("completed_idempotency_keys")
        initial_logs = request_context.get("initial_step_logs")
        return await self.execute_steps(
            steps,
            context=request_context,
            workflow=workflow,
            start_index=start_index,
            step_offset=start_index,
            initial_step_logs=(
                list(initial_logs)
                if isinstance(initial_logs, list)
                else None
            ),
            completed_idempotency_keys=(
                list(completed)
                if isinstance(completed, list)
                else None
            ),
        )

    async def execute_steps(
        self,
        steps: list[StructuredStep] | list[dict[str, Any]] | dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        execution_id: str | None = None,
        step_offset: int = 0,
        initial_step_logs: list[dict[str, Any]] | None = None,
        workflow: Any | None = None,
        start_index: int = 0,
        completed_idempotency_keys: list[str] | None = None,
        page: Any | None = None,
    ) -> ExecutionResult:
        """在页面里执行步骤。

        ``page`` 为空时执行器自己打开并关闭浏览器；传入页面时复用调用
        方持有的会话，供混合执行器把网页步骤与桌面步骤串在同一流程里。
        """

        parsed = self._coerce_steps(steps)
        start_index = self._non_negative_int(start_index)
        current_steps = (
            list(parsed[start_index:])
            if start_index
            else list(parsed)
        )
        completed_keys = self._normalize_keys(
            completed_idempotency_keys
        )
        completed_key_set = set(completed_keys)
        execution_id = execution_id or uuid4().hex[:8]
        request_context = dict(context or {})
        context_runner = self._runner_for_context(request_context)
        secrets = secret_values(
            request_context.get("credentials")
        )
        extracted: dict[str, Any] = {}
        output: list[str] = []
        screenshots: list[str] = []
        artifacts: list[dict[str, Any]] = []
        step_logs: list[dict[str, Any]] = list(initial_step_logs or [])
        skipped_steps: list[int] = []
        started_at = time.perf_counter()
        success = True
        error: str | None = None
        failed_step: dict[str, Any] | None = None
        top_category = FailureCategory.UNKNOWN
        awaiting_user = False
        repairs: list[dict[str, Any]] = []
        repaired_plan: dict[str, Any] | None = None
        repair_rounds = 0

        try:
            async with self._page_scope(
                page,
                runner=context_runner,
            ) as page:
                position = 0
                while position < len(current_steps):
                    step = current_steps[position]
                    index = step_offset + position
                    if (
                        step.idempotency_key
                        and step.idempotency_key in completed_key_set
                    ):
                        entry = self._new_step_log(step, index)
                        entry.update(
                            {
                                "status": "skipped",
                                "message": (
                                    "幂等标记已完成："
                                    f"{step.idempotency_key}"
                                ),
                                "finished_at": time.perf_counter(),
                            }
                        )
                        step_logs.append(entry)
                        skipped_steps.append(step.step_id)
                        output.append(
                            f"[{index + 1}] {step.action} "
                            "已按幂等标记跳过"
                        )
                        position += 1
                        continue

                    entry = self._new_step_log(step, index)
                    step_logs.append(entry)
                    failure = await self._run_step_attempts(
                        page,
                        step,
                        index,
                        execution_id,
                        extracted,
                        artifacts,
                        request_context,
                        entry,
                        screenshots,
                        output,
                        secrets,
                    )
                    if failure is None:
                        self._record_idempotency_key(
                            step,
                            completed_keys,
                            completed_key_set,
                        )
                        position += 1
                        continue

                    (
                        screenshot_path,
                        screenshot_error,
                    ) = await context_runner.capture_screenshot(
                        page,
                        execution_id,
                        f"step_{index:02d}_{step.action}_failed.png",
                    )
                    entry.update(
                        {
                            "finished_at": time.perf_counter(),
                            "screenshot_path": screenshot_path,
                            "screenshot_error": screenshot_error,
                        }
                    )
                    if screenshot_path:
                        screenshots.append(screenshot_path)

                    visual_outcome = await self._try_visual_fallback(
                        page=page,
                        step=step,
                        failure=failure,
                        index=index,
                        execution_id=execution_id,
                        context=request_context,
                        screenshot_path=screenshot_path,
                    )
                    visual_screenshot_path = None
                    if visual_outcome is not None:
                        visual_screenshot_path = (
                            visual_outcome.payload.get(
                                "screenshot_path"
                            )
                            or visual_outcome.audit.get(
                                "screenshot_path"
                            )
                        )
                    if visual_screenshot_path:
                        screenshots = [
                            (
                                str(visual_screenshot_path)
                                if item == screenshot_path
                                else item
                            )
                            for item in screenshots
                        ]
                        entry["screenshot_path"] = str(
                            visual_screenshot_path
                        )
                    if (
                        visual_outcome is not None
                        and visual_outcome.status == "success"
                    ):
                        entry.update(
                            {
                                "status": "success",
                                "message": (
                                    "视觉候选执行并验证成功"
                                ),
                                "verification": dict(
                                    visual_outcome.verification
                                ),
                                "visual_audit": dict(
                                    visual_outcome.audit
                                ),
                            }
                        )
                        output.append(
                            f"[{index + 1}] {step.action} "
                            "视觉兜底执行成功"
                        )
                        position += 1
                        continue
                    if (
                        visual_outcome is not None
                        and visual_outcome.status == "awaiting_user"
                    ):
                        visual_payload = dict(
                            visual_outcome.payload
                        )
                        visual_payload["screenshot_path"] = (
                            visual_screenshot_path
                            or screenshot_path
                        )
                        visual_payload["current_url"] = (
                            str(getattr(page, "url", "") or "")
                        )
                        entry["status"] = "awaiting_user"
                        entry["visual_audit"] = dict(
                            visual_outcome.audit
                        )
                        success = False
                        error = visual_outcome.reason
                        awaiting_user = True
                        top_category = (
                            FailureCategory.VISUAL_AMBIGUOUS
                        )
                        failed_step = {
                            "step_index": index,
                            "step_id": step.step_id,
                            "executor": "web",
                            "action": step.action,
                            "error": error,
                            "failure_category": (
                                FailureCategory.VISUAL_AMBIGUOUS.value
                            ),
                            "human_action": {
                                "kind": "visual_ambiguous",
                                "instruction": (
                                    visual_payload.get("instruction")
                                    or "请选择正确的页面目标"
                                ),
                                "current_url": (
                                    visual_payload.get("current_url")
                                ),
                                "screenshot_path": (
                                    visual_screenshot_path
                                    or screenshot_path
                                ),
                                "verification": {
                                    "type": "visual_selection"
                                },
                                "visual": visual_payload,
                                **visual_payload,
                            },
                        }
                        break

                    if step.on_failure is OnFailure.SKIP:
                        entry["status"] = "skipped"
                        skipped_steps.append(step.step_id)
                        output.append(
                            f"[{index + 1}] {step.action} 已跳过："
                            f"{failure}"
                        )
                        position += 1
                        continue

                    if self._can_repair(step, failure, repair_rounds):
                        repair_rounds += 1
                        repair = await self._repair_failed_step(
                            page=page,
                            steps=current_steps,
                            position=position,
                            failure=failure,
                            index=index,
                        )
                        if repair is not None:
                            current_steps, merged_plan = repair
                            repaired_plan = merged_plan
                            entry["status"] = "repaired"
                            repairs.append(
                                {
                                    "attempt": repair_rounds,
                                    "step_index": index,
                                    "step_id": step.step_id,
                                    "action": step.action,
                                    "error": str(failure),
                                    "failure_category": (
                                        failure.category.value
                                    ),
                                    "repaired_steps": len(
                                        current_steps
                                    )
                                    - position,
                                }
                            )
                            step_logs.append(
                                {
                                    "step_index": index,
                                    "step_id": step.step_id,
                                    "executor": "web",
                                    "action": "auto_repair",
                                    "target": entry.get("target"),
                                    "status": "repaired",
                                    "attempt": repair_rounds,
                                    "failure_category": (
                                        failure.category.value
                                    ),
                                    "error": str(failure),
                                    "message": (
                                        f"自动修复第 {index + 1} 步："
                                        f"{failure}"
                                    ),
                                    "started_at": time.perf_counter(),
                                    "finished_at": time.perf_counter(),
                                }
                            )
                            output.append(
                                f"[{index + 1}] {step.action} "
                                "已自动修复，重新执行"
                            )
                            if workflow is not None:
                                try:
                                    workflow.structured_plan = merged_plan
                                except Exception:
                                    logger.debug(
                                        "无法把修复结果写回 workflow",
                                        exc_info=True,
                                    )
                            continue

                    entry["status"] = "failed"
                    success = False
                    error = str(failure)
                    top_category = failure.category
                    if failure.category is FailureCategory.AUTH_REQUIRED:
                        awaiting_user = True
                    current_url = str(getattr(page, "url", "") or "")
                    interaction = await self._human_interaction_signals(
                        page,
                        step,
                        str(failure),
                        current_url,
                    )
                    failed_step = {
                        "step_index": index,
                        "step_id": step.step_id,
                        "executor": "web",
                        "action": step.action,
                        "error": str(failure),
                        "failure_category": failure.category.value,
                        "attempts": entry["attempts"],
                        "locator": (
                            step.locator.to_payload()
                            if step.locator
                            else None
                        ),
                        "locator_attempts": failure.locator_attempts,
                        **(interaction or {}),
                    }
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            success = False
            error = str(exc) or exc.__class__.__name__
            top_category = classify_failure(error)

        details: dict[str, Any] = {
            "executor": "web",
            "step_count": len(current_steps),
            "extracted": extracted,
            "completed_idempotency_keys": completed_keys,
        }
        if skipped_steps:
            details["skipped_steps"] = skipped_steps
        if failed_step is not None:
            details["failed_step"] = failed_step
        if not success and failed_step is None:
            details["failure_category"] = top_category.value
        if awaiting_user:
            details["awaiting_user"] = True
        if repairs:
            details["repairs"] = repairs
        if repaired_plan is not None:
            details["repaired_plan"] = repaired_plan

        return ExecutionResult(
            success=success,
            output="\n".join(output),
            error=error,
            screenshots=screenshots,
            step_logs=step_logs,
            duration_seconds=time.perf_counter() - started_at,
            details=details,
            artifacts=artifacts,
        )

    async def _try_visual_fallback(
        self,
        *,
        page: Any,
        step: StructuredStep,
        failure: _StepFailure,
        index: int,
        execution_id: str,
        context: dict[str, Any],
        screenshot_path: str | None,
    ) -> Any | None:
        del index, execution_id
        if self.visual_runtime is None:
            return None
        try:
            return await self.visual_runtime.evaluate_web(
                step,
                page,
                failure_category=failure.category.value,
                context=context,
                screenshot_path=screenshot_path,
            )
        except Exception:
            logger.exception("Visual fallback failed")
            return None

    @asynccontextmanager
    async def browser_session(
        self,
        *,
        runner: PlaywrightRunner | None = None,
    ) -> Any:
        """打开一个可供多次 ``execute_steps(page=...)`` 复用的页面。"""

        active_runner = runner or self.runner
        async with async_playwright() as playwright:
            browser, browser_context = await active_runner.launch_browser(
                playwright
            )
            try:
                yield await browser_context.new_page()
            finally:
                await self._close(browser, browser_context)

    @asynccontextmanager
    async def _page_scope(
        self,
        page: Any | None,
        *,
        runner: PlaywrightRunner | None = None,
    ) -> Any:
        """复用传入页面，或为本次执行临时打开一个浏览器页。"""

        if page is not None:
            yield page
            return
        async with self.browser_session(runner=runner) as owned_page:
            yield owned_page

    def _runner_for_context(
        self,
        context: dict[str, Any],
    ) -> PlaywrightRunner:
        profile = context.get("browser_profile")
        if not isinstance(profile, dict):
            return self.runner
        profile_directory = str(
            profile.get("profile_directory") or "Default"
        ).strip() or "Default"
        raw_dir = str(profile.get("user_data_dir") or "").strip()
        user_data_dir = Path(
            raw_dir
            if raw_dir
            else Path.home() / ".nl-rpa" / "edge-profile"
        ).expanduser()
        user_data_dir.mkdir(parents=True, exist_ok=True)
        (user_data_dir / profile_directory).mkdir(
            parents=True,
            exist_ok=True,
        )
        config = SimpleNamespace(
            EXECUTION_TIMEOUT=getattr(
                self.config,
                "EXECUTION_TIMEOUT",
                300,
            ),
            SCREENSHOT_ENABLED=getattr(
                self.config,
                "SCREENSHOT_ENABLED",
                True,
            ),
            SCREENSHOT_TIMEOUT_MS=getattr(
                self.config,
                "SCREENSHOT_TIMEOUT_MS",
                15000,
            ),
            SCREENSHOT_FULL_PAGE=getattr(
                self.config,
                "SCREENSHOT_FULL_PAGE",
                True,
            ),
            BROWSER_MODE="edge",
            EDGE_EXECUTABLE_PATH=getattr(
                self.config,
                "EDGE_EXECUTABLE_PATH",
                "",
            ),
            EDGE_USER_DATA_DIR=str(user_data_dir),
            EDGE_PROFILE_DIRECTORY=profile_directory,
            BROWSER_HEADLESS=getattr(
                self.config,
                "BROWSER_HEADLESS",
                True,
            ),
        )
        return PlaywrightRunner(config)

    @staticmethod
    def _coerce_steps(
        steps: list[StructuredStep] | list[dict[str, Any]] | dict[str, Any],
    ) -> list[StructuredStep]:
        if isinstance(steps, dict):
            return parse_structured_steps(steps)
        materialized = list(steps)
        if materialized and all(
            isinstance(step, StructuredStep) for step in materialized
        ):
            return materialized
        return parse_structured_steps(materialized)

    async def _run_step_attempts(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
        entry: dict[str, Any],
        screenshots: list[str],
        output: list[str],
        secrets: list[str] | None = None,
    ) -> _StepFailure | None:
        """按重试策略执行一个步骤，返回最终失败或 None。"""

        attempts_allowed = step.retry_policy.max_attempts
        failure: _StepFailure | None = None

        for attempt in range(1, attempts_allowed + 1):
            entry["attempts"] = attempt
            entry["retried"] = attempt > 1
            try:
                outcome = await asyncio.wait_for(
                    self._execute_step_once(
                        page,
                        step,
                        index,
                        execution_id,
                        extracted,
                        artifacts,
                        context,
                    ),
                    timeout=step.timeout_seconds + 0.5,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                message = (
                    f"步骤 {index + 1} 在 "
                    f"{step.timeout_seconds:g} 秒后超时"
                )
                failure = _StepFailure(
                    redact_text(message, secrets),
                    FailureCategory.TIMEOUT,
                )
            except _StepFailure as exc:
                failure = _StepFailure(
                    redact_text(str(exc), secrets),
                    exc.category,
                    locator_attempts=exc.locator_attempts,
                )
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                failure = _StepFailure(
                    redact_text(message, secrets),
                    classify_failure(message),
                )
            else:
                empty_actions = {
                    "extract_text",
                    "extract_table",
                    "extract_fields",
                    "collect_links",
                    "paginate",
                }
                if (
                    step.action in empty_actions
                    and isinstance(outcome.value, list)
                    and not outcome.value
                    and not bool(step.params.get("allow_empty", False))
                ):
                    failure = _StepFailure(
                        "data_empty: 动作返回空数据",
                        FailureCategory.DATA_EMPTY,
                    )
                    entry["error"] = str(failure)
                    entry["failure_category"] = (
                        failure.category.value
                    )
                    if attempt >= attempts_allowed:
                        return failure
                    await asyncio.sleep(
                        step.retry_policy.backoff_seconds
                    )
                    continue
                entry.update(
                    {
                        "status": "success",
                        "finished_at": time.perf_counter(),
                        "locator_strategy": outcome.locator_strategy,
                        "error": None,
                        "failure_category": None,
                    }
                )
                if outcome.screenshot_path:
                    entry["screenshot_path"] = outcome.screenshot_path
                    if outcome.screenshot_path not in screenshots:
                        screenshots.append(outcome.screenshot_path)
                output.append(f"[{index + 1}] {step.action} 完成")
                return None

            entry["error"] = str(failure)
            entry["failure_category"] = failure.category.value
            if (
                failure.category not in REPAIRABLE_CATEGORIES
                or attempt >= attempts_allowed
            ):
                return failure
            await asyncio.sleep(step.retry_policy.backoff_seconds)

        return failure

    def _can_repair(
        self,
        step: StructuredStep,
        failure: _StepFailure,
        repair_rounds: int,
    ) -> bool:
        return (
            self.repair_enabled
            and self.max_repair_attempts > 0
            and repair_rounds < self.max_repair_attempts
            and step.on_failure is OnFailure.REPAIR
            and failure.category in REPAIRABLE_CATEGORIES
        )

    async def _repair_failed_step(
        self,
        *,
        page: Any,
        steps: list[StructuredStep],
        position: int,
        failure: _StepFailure,
        index: int,
    ) -> tuple[list[StructuredStep], dict[str, Any]] | None:
        """重新观察当前页面并重写失败步骤及其后续步骤。"""

        agent = self._repair_agent_instance()
        if agent is None:
            return None

        try:
            observation = await self._observe_live_page(page)
        except Exception:
            logger.warning("网页修复前观察失败", exc_info=True)
            return None

        step = steps[position]
        plan_payload = {
            "executor_type": "web",
            "execution_mode": "structured",
            "steps": [
                item.model_dump(mode="json") for item in steps
            ],
        }
        failure_payload = {
            "step_index": index,
            "step_id": step.step_id,
            "action": step.action,
            "error": str(failure),
            "failure_category": failure.category.value,
            "locator": (
                step.locator.to_payload() if step.locator else None
            ),
            "locator_attempts": failure.locator_attempts,
        }

        try:
            repaired_raw = await agent.repair_steps(
                plan=plan_payload,
                step_index=position,
                failure=failure_payload,
                observation=observation,
                action_catalog=web_action_catalog_text(),
            )
        except Exception:
            logger.warning("网页修复规划失败", exc_info=True)
            return None

        try:
            merged_plan = self._merge_repaired_steps(
                steps,
                position,
                repaired_raw,
            )
        except WebPlanError as exc:
            logger.warning("网页修复计划未通过校验：%s", exc)
            return None
        return parse_structured_steps(merged_plan), merged_plan

    async def _observe_live_page(self, page: Any) -> dict[str, Any]:
        observer = BrowserObserver(self.config)
        snapshot = await observer.observe_page(
            page,
            max_elements=80,
            include_accessibility=True,
        )
        return snapshot.compact(max_elements=60)

    def _repair_agent_instance(self) -> Any | None:
        return self.repair_agent

    @staticmethod
    def _merge_repaired_steps(
        steps: list[StructuredStep],
        position: int,
        repaired_raw: Any,
    ) -> dict[str, Any]:
        if not isinstance(repaired_raw, list) or not repaired_raw:
            raise WebPlanError("修复结果为空")
        prefix = [
            item.model_dump(mode="json")
            for item in steps[:position]
        ]
        return validate_web_plan(
            {
                "executor_type": "web",
                "execution_mode": "structured",
                "steps": [*prefix, *repaired_raw],
            }
        )

    async def _execute_step_once(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        run_step = self._resolve_credential_step(step, context)
        handler = getattr(self, f"_action_{run_step.action}", None)
        if not callable(handler):
            raise _StepFailure(
                f"未实现动作：{run_step.action}",
                FailureCategory.UNKNOWN,
            )
        result = await handler(
            page,
            run_step,
            index,
            execution_id,
            extracted,
            artifacts,
            context,
        )
        if isinstance(result, _StepOutcome):
            return result
        if isinstance(result, dict):
            return _StepOutcome(value=result.get("items", result))
        if result is None:
            return _StepOutcome()
        raise TypeError(
            "Web action handler must return _StepOutcome, dict or None"
        )

    def _resolve_credential_step(
        self,
        step: StructuredStep,
        context: dict[str, Any],
    ) -> StructuredStep:
        """把 params 里的 {{credentials.key}} 替换为运行时明文。

        只有执行用的副本会包含明文，步骤日志与失败信息仍使用原步骤。
        """

        credentials = context.get("credentials")
        if not isinstance(credentials, dict):
            credentials = {}
        if not self._has_credential_placeholder(step.params):
            return step
        resolved = self._resolve_credential_params(
            step.params,
            credentials,
        )
        if resolved == step.params:
            return step
        return step.model_copy(update={"params": resolved})

    def _has_credential_placeholder(self, value: Any) -> bool:
        if isinstance(value, str):
            return bool(_CREDENTIAL_PLACEHOLDER.search(value))
        if isinstance(value, dict):
            return any(
                self._has_credential_placeholder(item)
                for item in value.values()
            )
        if isinstance(value, list):
            return any(
                self._has_credential_placeholder(item)
                for item in value
            )
        return False

    def _resolve_credential_params(
        self,
        value: Any,
        credentials: dict[str, Any],
    ) -> Any:
        if isinstance(value, str):
            def replace(match: re.Match[str]) -> str:
                key = match.group(1)
                if key not in credentials:
                    raise _StepFailure(
                        "business_error: 凭据未配置："
                        f"{key}",
                        FailureCategory.BUSINESS_ERROR,
                    )
                return str(credentials[key])

            return _CREDENTIAL_PLACEHOLDER.sub(replace, value)
        if isinstance(value, dict):
            return {
                key: self._resolve_credential_params(item, credentials)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._resolve_credential_params(item, credentials)
                for item in value
            ]
        return value

    @staticmethod
    def _record_idempotency_key(
        step: StructuredStep,
        completed_keys: list[str],
        completed_key_set: set[str],
    ) -> None:
        key = step.idempotency_key
        if not key or key in completed_key_set:
            return
        completed_key_set.add(key)
        completed_keys.append(key)

    @staticmethod
    def _normalize_keys(values: Any) -> list[str]:
        if not isinstance(values, (list, tuple, set)):
            return []
        normalized: list[str] = []
        for value in values:
            text = str(value).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _non_negative_int(value: Any) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    async def _action_goto(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        override = step.params.get("timeout_ms")
        timeout_ms = (
            max(1, int(override))
            if isinstance(override, (int, float)) and override > 0
            else self._param_timeout(step)
        )
        wait_until = (
            str(step.params.get("wait_until") or "domcontentloaded")
            .strip()
            .lower()
        )
        target_url = str(step.params["url"])
        await self._before_navigation(target_url, context)
        await page.goto(
            target_url,
            wait_until=wait_until,
            timeout=timeout_ms,
        )
        return _StepOutcome(value=getattr(page, "url", None))

    async def _before_navigation(
        self,
        url: str,
        context: dict[str, Any],
    ) -> None:
        policy = context.get("crawl_policy")
        if not isinstance(policy, dict):
            policy = self._crawl_policy
        if not isinstance(policy, dict):
            return
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower()
        domains = policy.get("allowed_domains")
        if not isinstance(domains, (list, tuple, set)) or not domains:
            raise _StepFailure(
                "permission_denied: crawl_policy.allowed_domains 不能为空",
                FailureCategory.PERMISSION_DENIED,
            )
        if not self._domain_allowed(host, domains):
            raise _StepFailure(
                f"permission_denied: 域名不在抓取白名单：{host}",
                FailureCategory.PERMISSION_DENIED,
            )

        max_runtime = self._positive_float(
            policy.get("max_runtime_seconds")
        )
        if (
            max_runtime is not None
            and self._crawl_started_at
            and time.monotonic() - self._crawl_started_at >= max_runtime
        ):
            raise _StepFailure(
                "网页抓取超过最大运行时长",
                FailureCategory.TIMEOUT,
            )

        rate = self._positive_float(
            policy.get("requests_per_second")
        ) or 1.0
        interval = 1.0 / min(rate, 10.0)
        last_request = self._last_request_at.get(host, 0.0)
        delay = interval - (time.monotonic() - last_request)
        if last_request and delay > 0:
            await asyncio.sleep(delay)
        self._last_request_at[host] = time.monotonic()

    async def _action_click(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        kwargs: dict[str, Any] = {"timeout": timeout_ms}
        if step.params.get("button"):
            kwargs["button"] = str(step.params["button"])
        if step.params.get("click_count"):
            kwargs["click_count"] = int(step.params["click_count"])
        if step.params.get("force"):
            kwargs["force"] = bool(step.params["force"])
        if isinstance(step.params.get("position"), dict):
            kwargs["position"] = dict(step.params["position"])

        await locator.click(**kwargs)
        return _StepOutcome(locator_strategy=strategy)

    async def _action_fill(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        await locator.fill(str(step.params["text"]), timeout=timeout_ms)
        return _StepOutcome(locator_strategy=strategy)

    async def _action_press(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        await locator.press(str(step.params["key"]), timeout=timeout_ms)
        return _StepOutcome(locator_strategy=strategy)

    async def _action_select_option(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        kwargs: dict[str, Any] = {"timeout": timeout_ms}
        if "values" in step.params:
            kwargs["value"] = step.params["values"]
        elif "value" in step.params:
            kwargs["value"] = step.params["value"]
        elif "label" in step.params:
            kwargs["label"] = step.params["label"]
        elif "index" in step.params:
            kwargs["index"] = int(step.params["index"])

        await locator.select_option(**kwargs)
        return _StepOutcome(locator_strategy=strategy)

    async def _action_check(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        await locator.check(timeout=timeout_ms)
        return _StepOutcome(locator_strategy=strategy)

    async def _action_uncheck(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        await locator.uncheck(timeout=timeout_ms)
        return _StepOutcome(locator_strategy=strategy)

    async def _action_wait_for(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        milliseconds = step.params.get("milliseconds")
        if milliseconds is not None:
            await page.wait_for_timeout(max(0, int(milliseconds)))
            return _StepOutcome()

        state = (
            str(step.params.get("state") or "visible").strip().lower()
        )
        if step.locator is not None:
            candidate = step.locator.primary()
            target = self._frame_target(page, candidate.frame)
            locator = self._build_locator(target, candidate).nth(
                candidate.nth or 0
            )
        else:
            selector = str(step.params.get("selector") or "").strip()
            locator = page.locator(selector).nth(0)

        await locator.wait_for(state=state, timeout=timeout_ms)
        return _StepOutcome()

    async def _action_extract_text(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        if step.params.get("multiple"):
            value: Any = await locator.all_inner_texts()
        else:
            value = await locator.inner_text(timeout=timeout_ms)
        self._store_extracted(extracted, step, value)
        return _StepOutcome(locator_strategy=strategy, value=value)

    async def _action_extract_table(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        strategy: str | None = None
        if step.locator is not None:
            locator, strategy = await self._resolve_locator(
                page,
                step.locator,
                timeout_ms,
            )
        else:
            locator = page.locator(
                str(step.params["selector"])
            ).nth(0)
            strategy = LocatorStrategy.CSS.value

        value = await locator.evaluate(TABLE_EXTRACTION_SCRIPT)
        if not isinstance(value, list):
            value = []
        self._store_extracted(extracted, step, value)
        return _StepOutcome(locator_strategy=strategy, value=value)

    async def _action_observe_page(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        del index, execution_id, artifacts
        max_elements = max(
            1,
            min(
                300,
                int(step.params.get("max_elements", 80)),
            ),
        )
        observation = await page.evaluate(
            OBSERVATION_SCRIPT,
            {"maxElements": max_elements},
        )
        if not isinstance(observation, dict):
            raise _StepFailure(
                "页面观察没有返回有效结果",
                FailureCategory.UNKNOWN,
            )
        variable = str(
            step.params.get("variable") or ""
        ).strip()
        if variable:
            extracted[variable] = observation
        return _StepOutcome(value=observation)

    async def _action_collect_links(
        self,
        page: Any,
        step: StructuredStep | dict[str, Any],
        index: Any = None,
        execution_id: Any = None,
        extracted: dict[str, Any] | None = None,
        artifacts: Any = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del index, execution_id, artifacts
        params = (
            step.params
            if isinstance(step, StructuredStep)
            else step
            if isinstance(step, dict)
            else {}
        )
        if not isinstance(extracted, dict):
            extracted = {}
        if not isinstance(context, dict):
            context = {}
        selector = str(params.get("selector") or "").strip()
        if not selector:
            raise _StepFailure(
                "collect_links requires selector",
                FailureCategory.BUSINESS_ERROR,
            )
        attribute = str(params.get("attribute") or "href").strip()
        domains = self._crawl_domains(params, context)
        limit = self._crawl_limit(params, context)
        locator = page.locator(selector)
        count = int(await locator.count())
        base_url = str(getattr(page, "url", "") or "")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item_index in range(count):
            if len(items) >= limit:
                break
            node = locator.nth(item_index)
            value = await node.get_attribute(attribute)
            if not isinstance(value, str) or not value.strip():
                continue
            url = urljoin(base_url, value.strip())
            parsed = urlparse(url)
            if parsed.scheme.lower() not in {"http", "https"}:
                continue
            if parsed.hostname and domains and not self._domain_allowed(
                parsed.hostname,
                domains,
            ):
                continue
            if url in seen:
                continue
            seen.add(url)
            text = ""
            try:
                text = str(await node.inner_text()).strip()
            except Exception:
                pass
            items.append({"url": url, "text": text})
        variable = str(params.get("variable") or "").strip()
        if variable:
            extracted[variable] = items
        return {
            "items": items,
            "count": len(items),
            "truncated": count > len(items),
        }

    async def _action_extract_fields(
        self,
        page: Any,
        step: StructuredStep | dict[str, Any],
        index: Any = None,
        execution_id: Any = None,
        extracted: dict[str, Any] | None = None,
        artifacts: Any = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del index, execution_id, artifacts, context
        params = (
            step.params
            if isinstance(step, StructuredStep)
            else step
            if isinstance(step, dict)
            else {}
        )
        if not isinstance(extracted, dict):
            extracted = {}
        fields = params.get("fields")
        if not isinstance(fields, dict) or not fields:
            raise _StepFailure(
                "extract_fields requires fields",
                FailureCategory.BUSINESS_ERROR,
            )
        selector = str(params.get("selector") or "").strip()
        base_url = str(getattr(page, "url", "") or "")
        rows: list[dict[str, Any]] = []
        if selector:
            rows_locator = page.locator(selector)
            row_count = int(await rows_locator.count())
            targets = [
                rows_locator.nth(row_index)
                for row_index in range(row_count)
            ]
        else:
            targets = [page]
        for target in targets:
            row: dict[str, Any] = {}
            for field_name, raw_spec in fields.items():
                spec = raw_spec if isinstance(raw_spec, dict) else {}
                field_selector = str(
                    spec.get("selector") or ":scope"
                ).strip()
                node = (
                    target
                    if field_selector in {"", ":scope"}
                    else target.locator(field_selector)
                )
                attribute = str(spec.get("attribute") or "").strip()
                if attribute:
                    value = await node.get_attribute(attribute)
                else:
                    value = await node.inner_text()
                if spec.get("url") is True and isinstance(value, str):
                    value = urljoin(base_url, value.strip())
                row[str(field_name)] = value
            rows.append(row)
        variable = str(params.get("variable") or "").strip()
        if variable:
            extracted[variable] = rows
        return {"items": rows, "count": len(rows)}

    async def _action_paginate(
        self,
        page: Any,
        step: StructuredStep | dict[str, Any],
        index: Any = None,
        execution_id: Any = None,
        extracted: dict[str, Any] | None = None,
        artifacts: Any = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del index, execution_id, artifacts
        params = (
            step.params
            if isinstance(step, StructuredStep)
            else step
            if isinstance(step, dict)
            else {}
        )
        if not isinstance(extracted, dict):
            extracted = {}
        if not isinstance(context, dict):
            context = {}
        item_selector = str(
            params.get("item_selector") or ""
        ).strip()
        next_selector = str(
            params.get("next_selector") or ""
        ).strip()
        if not item_selector or not next_selector:
            raise _StepFailure(
                "paginate requires item_selector and next_selector",
                FailureCategory.BUSINESS_ERROR,
            )
        policy = context.get("crawl_policy")
        if not isinstance(policy, dict):
            policy = self._crawl_policy or {}
        max_pages = self._positive_int(
            params.get("max_pages")
            or policy.get("max_pages")
        )
        max_items = self._positive_int(
            params.get("max_items")
            or policy.get("max_items")
        )
        if max_pages is None or max_items is None:
            raise _StepFailure(
                "paginate requires max_pages and max_items",
                FailureCategory.BUSINESS_ERROR,
            )
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        pages = 0
        variable = str(params.get("variable") or "").strip()
        try:
            for page_index in range(max_pages):
                if len(items) >= max_items:
                    break
                collected = await self._action_collect_links(
                    page,
                    {
                        **params,
                        "selector": item_selector,
                        "limit": max_items - len(items),
                    },
                    extracted=extracted,
                    context=context,
                )
                new_items = [
                    item
                    for item in collected.get("items", [])
                    if item.get("url")
                    and item["url"] not in seen
                ]
                for item in new_items:
                    seen.add(str(item["url"]))
                    items.append(item)
                pages = page_index + 1
                progress = context.get("progress")
                if callable(progress):
                    progress(
                        {
                            "page": pages,
                            "items": len(items),
                        }
                    )
                if not new_items or len(items) >= max_items:
                    break
                next_locator = page.locator(next_selector).nth(0)
                if int(await next_locator.count()) == 0:
                    break
                is_enabled = getattr(
                    next_locator,
                    "is_enabled",
                    None,
                )
                if callable(is_enabled) and not await is_enabled():
                    break
                await next_locator.click()
                await page.wait_for_timeout(
                    int(params.get("wait_after_ms") or 250)
                )
        except Exception as exc:
            if variable:
                extracted[variable] = items
            raise _StepFailure(
                f"分页抓取中止，已保留 {len(items)} 条：{exc}",
                classify_failure(str(exc)),
            ) from exc
        if variable:
            extracted[variable] = items
        return {"items": items, "pages": pages, "count": len(items)}

    async def _action_wait_for_response(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        del index, execution_id, artifacts, context
        timeout_ms = int(
            step.params.get("timeout_ms")
            or self._param_timeout(step)
        )
        url_contains = str(
            step.params.get("url_contains") or ""
        ).strip()
        expected_status = self._positive_int(
            step.params.get("status")
        )
        trigger_selector = str(
            step.params.get("trigger_selector") or ""
        ).strip()

        def matches(response: Any) -> bool:
            url = str(getattr(response, "url", "") or "")
            status = int(getattr(response, "status", 0) or 0)
            return (
                (not url_contains or url_contains in url)
                and (
                    expected_status is None
                    or status == expected_status
                )
            )

        expect_response = getattr(page, "expect_response", None)
        if callable(expect_response):
            async with expect_response(
                matches,
                timeout=timeout_ms,
            ) as response_info:
                if trigger_selector:
                    await page.locator(trigger_selector).nth(0).click()
            response = await response_info.value
        elif callable(getattr(page, "wait_for_response", None)):
            response = await page.wait_for_response(
                matches,
                timeout=timeout_ms,
            )
        else:
            raise _StepFailure(
                "当前页面不支持响应等待",
                FailureCategory.UNKNOWN,
            )

        status = int(getattr(response, "status", 0) or 0)
        if expected_status is not None and status != expected_status:
            raise _StepFailure(
                f"business_error: 响应状态 {status} 不符合预期",
                FailureCategory.BUSINESS_ERROR,
            )
        data: dict[str, Any] = {
            "url": str(getattr(response, "url", "") or ""),
            "status": status,
        }
        json_method = getattr(response, "json", None)
        if callable(json_method):
            try:
                data["json"] = await json_method()
            except Exception:
                pass
        variable = str(step.params.get("variable") or "").strip()
        if variable:
            extracted[variable] = data
        return _StepOutcome(value=data)

    async def _action_assert_data(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        del page, index, execution_id, artifacts
        from app.engine.task_runtime import evaluate_postconditions

        checks = step.params.get("checks")
        if not isinstance(checks, list):
            checks = []
        variables = context.get("variables")
        if not isinstance(variables, dict):
            variables = {}
        checked = evaluate_postconditions(
            checks,
            {
                "variables": {**variables, **extracted},
                "url": "",
                "title": "",
            },
        )
        if not checked.passed:
            failed = ", ".join(
                str(item.get("type") or "unknown")
                for item in checked.checks
                if not item.get("passed")
            )
            raise _StepFailure(
                "business_error: 数据校验失败：" + failed,
                FailureCategory.BUSINESS_ERROR,
            )
        return _StepOutcome(
            value={
                "passed": True,
                "checks": checked.checks,
            }
        )

    async def _action_download_file(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        return await self._action_download(
            page,
            step,
            index,
            execution_id,
            extracted,
            artifacts,
            context,
        )

    async def _action_screenshot(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        name = str(
            step.params.get("name")
            or f"step_{index:02d}_{step.action}"
        )
        filename = f"{self._sanitize_filename(name)}.png"
        full_page = step.params.get("full_page")
        context_runner = self._runner_for_context(context)
        path, capture_error = await context_runner.capture_screenshot(
            page,
            execution_id,
            filename,
            full_page=(
                bool(full_page) if full_page is not None else None
            ),
        )
        if capture_error is not None:
            raise _StepFailure(
                f"截图失败：{capture_error}",
                FailureCategory.UNKNOWN,
            )
        return _StepOutcome(screenshot_path=path)

    async def _action_save_file(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        requested = str(step.params["path"] or "").strip()
        content = step.params.get("content")
        if content is None and step.params.get("variable") is not None:
            content = self._resolve_variable(
                str(step.params["variable"]),
                extracted,
                context,
            )
            if content is _MISSING:
                raise _StepFailure(
                    "business_error: 变量不存在："
                    f"{step.params['variable']}",
                    FailureCategory.BUSINESS_ERROR,
                )
        if content is None:
            content = ""
        if isinstance(content, (dict, list)):
            text = json.dumps(content, ensure_ascii=False, indent=2)
        else:
            text = str(content)

        filename = sanitize_artifact_name(requested or "artifact")
        artifact_dir = (
            self._runner_for_context(context).logs_dir
            / execution_id
            / ARTIFACT_DIR_NAME
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / filename
        encoding = str(step.params.get("encoding") or "utf-8")
        artifact_path.write_text(text, encoding=encoding)

        artifacts.append(
            {
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
        )
        return _StepOutcome(value=str(artifact_path))

    async def _action_assert_text(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        strategy: str | None = None
        if step.locator is not None:
            locator, strategy = await self._resolve_locator(
                page,
                step.locator,
                timeout_ms,
            )
            actual = await locator.inner_text(timeout=timeout_ms)
        else:
            actual = await page.inner_text("body", timeout=timeout_ms)

        expected = str(step.params["text"])
        actual_normalized = " ".join(str(actual).split())
        expected_normalized = " ".join(expected.split())
        exact = bool(step.params.get("exact"))
        matched = (
            actual_normalized == expected_normalized
            if exact
            else expected_normalized in actual_normalized
        )
        if not matched:
            raise _StepFailure(
                "business_error: 文本断言失败，期望 "
                f"{expected!r}，实际 {str(actual)!r}",
                FailureCategory.BUSINESS_ERROR,
            )
        return _StepOutcome(locator_strategy=strategy)

    async def _action_assert_logged_in(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        success_selector = step.params.get("success_selector")
        if isinstance(success_selector, str) and success_selector.strip():
            try:
                await page.locator(success_selector.strip()).wait_for(
                    state="visible",
                    timeout=timeout_ms,
                )
            except Exception as exc:
                raise _StepFailure(
                    "AUTH_REQUIRED: 未找到登录成功标志 "
                    f"{success_selector}",
                    FailureCategory.AUTH_REQUIRED,
                ) from exc
            return _StepOutcome()

        password_count = await page.locator(
            "input[type='password']:visible"
        ).count()
        login_paths = step.params.get("login_paths") or []
        if isinstance(login_paths, str):
            login_paths = [login_paths]
        elif not isinstance(login_paths, (list, tuple)):
            login_paths = []
        current_url = str(getattr(page, "url", "") or "")
        in_login_path = any(
            str(fragment).strip()
            and str(fragment).strip() in current_url
            for fragment in login_paths
        )
        if int(password_count) > 0 or in_login_path:
            raise _StepFailure(
                "AUTH_REQUIRED: 登录已失效，请重新登录",
                FailureCategory.AUTH_REQUIRED,
            )
        return _StepOutcome()

    async def _human_interaction_signals(
        self,
        page: Any,
        step: StructuredStep,
        message: str,
        current_url: str,
    ) -> dict[str, Any]:
        params = step.params if isinstance(step.params, dict) else {}
        success_selector = str(
            params.get("success_selector") or ""
        ).strip()
        raw_paths = params.get("login_paths") or []
        if isinstance(raw_paths, str):
            login_paths = [raw_paths]
        elif isinstance(raw_paths, (list, tuple)):
            login_paths = [str(item) for item in raw_paths]
        else:
            login_paths = []

        one_time_count = 0
        captcha_count = 0
        body_text = ""
        try:
            one_time_count = int(
                await page.locator(
                    "input[autocomplete='one-time-code']:visible"
                ).count()
            )
        except Exception:
            pass
        try:
            captcha_count = int(
                await page.locator(
                    "iframe[src*='captcha'], "
                    "[class*='captcha'], [id*='captcha']"
                ).count()
            )
        except Exception:
            pass
        try:
            body_text = str(
                await page.locator("body").inner_text(timeout=1000)
            )
        except Exception:
            body_text = ""

        lowered = body_text.casefold()
        qr_count = int(
            any(
                marker in lowered
                for marker in ("扫码", "二维码", "qr code", "qrcode")
            )
        )
        interaction_kind = classify_interaction_kind(
            message,
            current_url=current_url,
            one_time_code_input_count=one_time_count,
            captcha_marker_count=captcha_count,
            qr_marker_count=qr_count,
        )
        verification = web_verification_spec(
            current_url=current_url,
            success_selector=success_selector or None,
            login_paths=login_paths,
        )
        return {
            "current_url": current_url,
            "interaction_kind": interaction_kind,
            "verification": verification,
        }

    async def _action_download(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = int(
            step.params.get("timeout_ms") or self._param_timeout(step)
        )
        filename = sanitize_artifact_name(
            str(
                step.params.get("save_as")
                or f"download_{step.step_id}"
            )
        )
        artifact_dir = (
            self._runner_for_context(context).logs_dir
            / execution_id
            / ARTIFACT_DIR_NAME
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target = artifact_dir / filename

        strategy: str | None = None
        async with page.expect_download(timeout=timeout_ms) as info:
            if step.locator is not None:
                locator, strategy = await self._resolve_locator(
                    page,
                    step.locator,
                    timeout_ms,
                )
                await locator.click(timeout=timeout_ms)

        download = await info.value
        await download.save_as(str(target))
        artifacts.append(
            {
                "name": filename,
                "path": str(target),
                "requested_path": None,
                "size": target.stat().st_size if target.is_file() else 0,
                "delivered": False,
                "delivered_path": None,
                "error": None,
            }
        )
        return _StepOutcome(locator_strategy=strategy, value=str(target))

    async def _action_upload(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        timeout_ms = self._param_timeout(step)
        locator, strategy = await self._resolve_locator(
            page,
            step.locator,
            timeout_ms,
        )
        files = step.params["files"]
        if isinstance(files, str):
            files = [files]
        await locator.set_input_files(list(files), timeout=timeout_ms)
        return _StepOutcome(locator_strategy=strategy)

    async def _action_code_step(
        self,
        page: Any,
        step: StructuredStep,
        index: int,
        execution_id: str,
        extracted: dict[str, Any],
        artifacts: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> _StepOutcome:
        value = await page.evaluate(str(step.params["script"]))
        if step.params.get("expect") and not value:
            raise _StepFailure(
                "business_error: code_step 断言失败",
                FailureCategory.BUSINESS_ERROR,
            )
        if step.params.get("variable") is not None:
            self._store_extracted(extracted, step, value)
        return _StepOutcome(value=value)

    async def _resolve_locator(
        self,
        page: Any,
        spec: LocatorSpec | None,
        timeout_ms: int,
    ) -> tuple[Any, str]:
        if spec is None:
            raise _StepFailure(
                "步骤缺少 locator",
                FailureCategory.LOCATOR_NOT_FOUND,
            )

        attempts: list[dict[str, Any]] = []
        for candidate in spec.candidates():
            try:
                target = self._frame_target(page, candidate.frame)
                locator = self._build_locator(target, candidate)
                count = int(await locator.count())
            except _StepFailure:
                raise
            except Exception as exc:
                attempts.append(
                    {
                        "strategy": candidate.strategy.value,
                        "value": candidate.value,
                        "count": None,
                        "error": str(exc) or exc.__class__.__name__,
                    }
                )
                continue

            if count == 0:
                attempts.append(
                    {
                        "strategy": candidate.strategy.value,
                        "value": candidate.value,
                        "count": 0,
                        "error": "not_found",
                    }
                )
                continue
            if count > 1 and candidate.nth is None:
                attempts.append(
                    {
                        "strategy": candidate.strategy.value,
                        "value": candidate.value,
                        "count": count,
                        "error": "not_unique",
                    }
                )
                continue

            selected = locator.nth(candidate.nth or 0)
            return selected, candidate.strategy.value

        has_duplicates = any(
            attempt.get("error") == "not_unique"
            for attempt in attempts
        )
        primary = spec.primary()
        describe = f"{primary.strategy.value}={primary.value}"
        if has_duplicates:
            raise _StepFailure(
                f"定位不唯一：{describe}（已尝试 "
                f"{len(attempts)} 个定位候选）",
                FailureCategory.LOCATOR_NOT_UNIQUE,
                locator_attempts=attempts,
            )
        raise _StepFailure(
            f"未找到元素：{describe}（已尝试 "
            f"{len(attempts)} 个定位候选）",
            FailureCategory.LOCATOR_NOT_FOUND,
            locator_attempts=attempts,
        )

    def _build_locator(
        self,
        target: Any,
        candidate: LocatorFallback,
    ) -> Any:
        strategy = candidate.strategy
        if strategy is LocatorStrategy.ROLE:
            kwargs: dict[str, Any] = {"exact": candidate.exact}
            if candidate.name:
                kwargs["name"] = candidate.name
            return target.get_by_role(candidate.value, **kwargs)
        if strategy is LocatorStrategy.LABEL:
            return target.get_by_label(
                candidate.value,
                exact=candidate.exact,
            )
        if strategy is LocatorStrategy.PLACEHOLDER:
            return target.get_by_placeholder(
                candidate.value,
                exact=candidate.exact,
            )
        if strategy is LocatorStrategy.TESTID:
            return target.get_by_test_id(candidate.value)
        if strategy is LocatorStrategy.TEXT:
            return target.get_by_text(
                candidate.value,
                exact=candidate.exact,
            )
        if strategy is LocatorStrategy.XPATH:
            return target.locator(f"xpath={candidate.value}")
        return target.locator(candidate.value)

    @staticmethod
    def _frame_target(page: Any, frame: str | None) -> Any:
        if not frame:
            return page
        for candidate in getattr(page, "frames", []) or []:
            name = str(getattr(candidate, "name", "") or "")
            url = str(getattr(candidate, "url", "") or "")
            if frame == name or frame == url:
                return candidate
        raise _StepFailure(
            f"找不到 iframe：{frame}",
            FailureCategory.LOCATOR_NOT_FOUND,
        )

    @staticmethod
    def _store_extracted(
        extracted: dict[str, Any],
        step: StructuredStep,
        value: Any,
    ) -> None:
        name = str(
            step.params.get("variable") or f"step_{step.step_id}"
        )
        extracted[name] = value

    @staticmethod
    def _resolve_variable(
        name: str,
        extracted: dict[str, Any],
        context: dict[str, Any],
    ) -> Any:
        if name in extracted:
            return extracted[name]
        variables = context.get("variables")
        if isinstance(variables, dict) and name in variables:
            return variables[name]
        return _MISSING

    @staticmethod
    def _new_step_log(
        step: StructuredStep,
        index: int,
    ) -> dict[str, Any]:
        if step.locator is not None:
            primary = step.locator.primary()
            target = primary.value
            selector = primary.value
        else:
            target = str(
                step.params.get("url")
                or step.params.get("name")
                or step.params.get("text")
                or step.action
            )
            selector = None

        return {
            "step_index": index,
            "step_id": step.step_id,
            "executor": "web",
            "action": step.action,
            "target": target,
            "selector": selector,
            "locator": (
                step.locator.to_payload() if step.locator else None
            ),
            "status": "running",
            "attempts": 0,
            "retried": False,
            "error": None,
            "failure_category": None,
            "screenshot_path": None,
            "screenshot_error": None,
            "started_at": time.perf_counter(),
            "finished_at": None,
        }

    @staticmethod
    def _param_timeout(step: StructuredStep) -> int:
        return max(1, int(step.timeout_seconds * 1000))

    def _crawl_domains(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> list[str]:
        values = params.get("allowed_domains")
        if not isinstance(values, (list, tuple, set)):
            policy = context.get("crawl_policy")
            if not isinstance(policy, dict):
                policy = self._crawl_policy
            if isinstance(policy, dict):
                values = policy.get("allowed_domains")
        if not isinstance(values, (list, tuple, set)):
            return []
        domains: list[str] = []
        for value in values:
            domain = str(value or "").strip().lower().rstrip(".")
            if domain and domain not in domains:
                domains.append(domain)
        return domains

    def _crawl_limit(
        self,
        params: dict[str, Any],
        context: dict[str, Any],
    ) -> int:
        explicit = self._positive_int(params.get("limit"))
        policy = context.get("crawl_policy")
        if not isinstance(policy, dict):
            policy = self._crawl_policy
        policy_limit = (
            self._positive_int(policy.get("max_items"))
            if isinstance(policy, dict)
            else None
        )
        limits = [
            value
            for value in (explicit, policy_limit)
            if value is not None
        ]
        return min(limits) if limits else 500

    @staticmethod
    def _domain_allowed(
        host: str,
        domains: Any,
    ) -> bool:
        normalized = str(host or "").strip().lower().rstrip(".")
        if not normalized:
            return False
        for raw_domain in domains:
            domain = str(raw_domain or "").strip().lower().rstrip(".")
            if normalized == domain or normalized.endswith("." + domain):
                return True
        return False

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _positive_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _sanitize_filename(value: str) -> str:
        sanitized = re.sub(
            r"[^A-Za-z0-9_.-]+",
            "_",
            str(value),
        ).strip("._")
        return sanitized or "screenshot"

    @staticmethod
    async def _close(browser: Any, context: Any) -> None:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


__all__ = ["WebExecutor"]
