# 截图失败容错 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让诊断截图（自动截图与 `screenshot()` 辅助函数）失败时不再把整条执行标记为失败，同时把失败原因以 `warning` 步骤展示在执行详情页。

**Architecture:** 把截图采集从「失败即抛异常」改为「返回 `(path, error)` 元组，永不抛出」；动作类步骤在截图失败时保持 `success` 并附加 `screenshot_error`，独立的 `screenshot` 步骤记为 `warning`。采集本身增加可配置超时、禁用动画、整页失败后降级为视口截图，因此默认行为仍是「整页优先」，只是多了一次兜底。

**Tech Stack:** Python 3.11+ / Playwright async API / SQLAlchemy JSON 字段 / pytest / React 18 + TypeScript / node:test

**Spec:** 事故现场为执行详情 #20（workflow #3，2026-10-01 19:49:02–19:54:20）。失败信息 `Playwright error: Page.screenshot: Timeout 30000ms exceeded`；`MAX_RETRY=3` 触发的三次整跑（`logs\d66fec97`、`logs\0d179dcc`、`logs\e678dd68`）全部卡在同一个位置——保存文件后的 `await screenshot('step_4_save_file')`，异常分支里的 `error_screenshot` 又超时 30 秒。业务产物 `B站+2026-10-01.csv` 已生成并投递，但整条执行被判失败。

**Non-goals（另行开计划）:** 本次 CSV 只有表头、0 行数据的问题源于 B站消息页选择器未匹配（计划里第 2、3 步的 selector 是 `needs_user_input` 占位值），属于数据抽取子系统的独立问题，不在本计划范围内。修完本计划后，它会以「成功但空数据」的形式继续暴露，不会被本次改动掩盖。

## Global Constraints

- 不新增任何第三方依赖（Python 与前端都不加）。
- `ExecutionLog.step_logs` 是 JSON 字段，新增键必须向后兼容：历史记录没有 `screenshot_error`，前端必须容忍缺省值。
- 默认行为保持「整页优先」：只有整页截图失败时才降级为视口截图。
- 默认值：`SCREENSHOT_ENABLED=true`、`SCREENSHOT_TIMEOUT_MS=15000`、`SCREENSHOT_FULL_PAGE=true`。
- 新增配置必须通过 `getattr(config, ...)` 读取，保证 `tests/test_runner.py` 中 `PlaywrightRunner(SimpleNamespace(EXECUTION_TIMEOUT=5))` 的既有写法继续可用。
- 本仓库不是 git 仓库（无 `.git`），因此用「测试通过 + 手工验收」替代提交检查点，不执行 `git commit`。
- 代码标识符用英文、缩进 4 空格、行宽约 79 列，沿用 `app/engine/runner.py` 现有风格。

---

## 文件结构

| 文件 | 职责 | 本次改动 |
| --- | --- | --- |
| `app/config.py` | 运行配置 | 新增 3 个截图配置项 |
| `.env.example` | 环境变量模板 | 补文档与默认值 |
| `app/engine/runner.py` | Playwright 执行器与辅助函数 | 截图采集硬化、截图失败降级为非致命、`StepLog` 增加 `screenshot_error` |
| `tests/test_runner.py` | 执行器单元测试 | 新增 5 个测试 |
| `frontend/src/types/workflow.ts` | 前端类型 | `warning` 步骤状态、`screenshotError` 字段 |
| `frontend/src/lib/execution.ts` | 步骤日志归一化 | 识别 `warning` 与 `screenshot_error` |
| `frontend/src/pages/ExecutionDetail.tsx` | 执行详情页 | 渲染琥珀色警告状态与截图错误文案 |
| `frontend/tests/execution.test.mjs` | 前端单元测试 | 新增 1 个归一化测试 |

`desktop/` 的界面直接 import `frontend/src`（见 `desktop/src/App.tsx`），因此前端改动只需在 `frontend/` 改一次，最后重建 `desktop/dist` 即可。

---

### Task 1: 截图采集硬化（超时 / 动画 / 整页回退 / 开关）

**Files:**
- Modify: `app/config.py:19-21`
- Modify: `.env.example`
- Modify: `app/engine/runner.py:1-30`（新增日志器）、`app/engine/runner.py:93`（新增实例属性）、`app/engine/runner.py:757-766`（重写 `_capture_screenshot`）
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Settings`（`app/config.py`）
- Produces: `PlaywrightRunner.screenshot_enabled: bool`、`PlaywrightRunner.screenshot_timeout_ms: int`、`PlaywrightRunner.screenshot_full_page: bool`、`PlaywrightRunner._capture_screenshot(page, execution_id, filename) -> tuple[str | None, str | None]`（第一个元素是路径，失败或禁用时为 `None`；第二个元素是错误文案，成功或禁用时为 `None`）

- [ ] **Step 1: 写失败测试**

在 `tests/test_runner.py` 的 `FakePage` 类之后追加：

```python
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
        if (full_page and self.failing_full_page) or (
            not full_page and self.failing_viewport
        ):
            raise RuntimeError(
                "Page.screenshot: Timeout 15000ms exceeded."
            )
        self.calls.append(("screenshot", path))
        return b""
```

在文件末尾 `test_run_with_retry` 之前追加三个测试：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py -k capture_screenshot -v`

Expected: 3 个测试 FAIL，报 `TypeError` 或断言失败（`_capture_screenshot` 仍返回 `str`，且遇到异常直接抛出）。

- [ ] **Step 3: 实现配置项**

`app/config.py` 的 `MAX_RETRY: int = 3` 之后插入：

```python
    SCREENSHOT_ENABLED: bool = True
    SCREENSHOT_TIMEOUT_MS: int = 15000
    SCREENSHOT_FULL_PAGE: bool = True
```

`.env.example` 在 `MAX_RETRY=3` 之后插入：

```
# 诊断截图：单张截图的超时（毫秒）。整页截图超时后会自动降级为视口截图。
SCREENSHOT_ENABLED=true
SCREENSHOT_TIMEOUT_MS=15000
SCREENSHOT_FULL_PAGE=true
```

- [ ] **Step 4: 实现采集逻辑**

`app/engine/runner.py` 顶部 import 区加 `import logging`，并在 `if TYPE_CHECKING:` 块之后加：

```python
logger = logging.getLogger(__name__)
```

`PlaywrightRunner.__init__` 中 `self.timeout = config.EXECUTION_TIMEOUT` 之后插入：

```python
        self.screenshot_enabled = bool(
            getattr(config, "SCREENSHOT_ENABLED", True)
        )
        self.screenshot_timeout_ms = int(
            getattr(config, "SCREENSHOT_TIMEOUT_MS", 15000) or 15000
        )
        self.screenshot_full_page = bool(
            getattr(config, "SCREENSHOT_FULL_PAGE", True)
        )
```

用下面的实现整体替换现有 `_capture_screenshot`（原 `app/engine/runner.py:757-766`）：

```python
    async def _capture_screenshot(
        self,
        page: Any,
        execution_id: str,
        filename: str,
    ) -> tuple[str | None, str | None]:
        """采集一张截图，永不抛出异常。

        返回 ``(path, error)``：成功时 ``error`` 为 ``None``；失败时
        ``path`` 为 ``None`` 并附带错误文案；截图被禁用时两者都是
        ``None``。
        """

        if not self.screenshot_enabled:
            return None, None

        path = self.logs_dir / execution_id / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        attempts = (
            [True, False] if self.screenshot_full_page else [False]
        )
        last_error: str | None = None

        for full_page in attempts:
            try:
                await page.screenshot(
                    path=str(path),
                    full_page=full_page,
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
                    full_page,
                    filename,
                    last_error,
                )

        return None, last_error
```

注意：Playwright 超时可能在磁盘上留下 0 字节的半成品，因此每次失败都要 `unlink(missing_ok=True)`，避免执行详情页读到空图片。

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py -k capture_screenshot -v`

Expected: 3 passed。

同时跑一次执行器全量，确认旧的 `test_auto_screenshot` 未被打断：

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py -v`

Expected: 仍然全部 PASS（此时 `_capture_screenshot` 的调用方还没改，若出现 TypeError 属正常，进入 Task 2 修复）。

- [ ] **Step 6: 检查点**

记录 `pytest tests/test_runner.py -v` 的结果；本仓库无 git，不提交。

---

### Task 2: 截图失败降级为非致命（helper 与自动截图包装器）

**Files:**
- Modify: `app/engine/runner.py:40-50`（`StepLog` 增加字段）、`app/engine/runner.py:543-604`（自动截图包装器）、`app/engine/runner.py:617-658`（`screenshot()` helper）
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: Task 1 的 `_capture_screenshot(...) -> tuple[str | None, str | None]`
- Produces: `StepLog.screenshot_error: str | None`；步骤日志新增可选键 `screenshot_error`；`screenshot(step_name) -> str | None`（失败时返回 `None`，不再抛异常）；`warning` 步骤状态

- [ ] **Step 1: 写失败测试**

在 `tests/test_runner.py` 中追加公共执行辅助函数与两个测试：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py -k "screenshot_failure or auto_screenshot_failure" -v`

Expected: FAIL —— `result.success` 为 `False`（`screenshot failed: ...` 被抛出），或 `KeyError: 'screenshot_error'`。

- [ ] **Step 3: 扩展 StepLog**

`app/engine/runner.py` 的 `StepLog` 数据类末尾追加带默认值的字段（`asdict` 会自动把它写进日志 JSON）：

```python
    started_at: float
    finished_at: float
    screenshot_error: str | None = None
```

- [ ] **Step 4: 改写自动截图包装器**

用下面的实现整体替换 `_patch_page_auto_screenshot` 中的 `wrapper` 函数体（`app/engine/runner.py:537-605`）：

```python
                async def wrapper(*args: Any, **kwargs: Any) -> Any:
                    step_index = step_counter[0]
                    step_counter[0] += 1
                    started_at = time.perf_counter()
                    target = self._get_step_target(
                        action_name,
                        args,
                        kwargs,
                    )
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
                    active_step_logs = getattr(
                        page,
                        "_rpa_step_logs",
                        None,
                    )
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
```

关键差异：动作本身成功时，截图失败只写入 `screenshot_error`，步骤状态保持 `success`，不再 `raise`；动作本身失败时，仍然抛出原始异常，整单失败与重试语义不变（`asyncio.CancelledError` 继续保持抛出）。

- [ ] **Step 5: 改写 screenshot() helper**

用下面的实现整体替换 `_build_helper_functions` 内的 `screenshot` 函数（`app/engine/runner.py:617-656`）：

```python
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
```

`run()` 收集截图列表时已经用 `isinstance(screenshot_path, str)` 过滤，`None` 不会进入 `ExecutionResult.screenshots`，无需额外改动。

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py -v`

Expected: 全部 PASS（含新增 5 个测试与既有 `test_auto_screenshot`、`test_helper_functions`）。

- [ ] **Step 7: 运行执行器相关回归**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runner.py tests/test_engine.py tests/test_execution_manager.py tests/test_sandbox.py -q`

Expected: 全部 PASS。

- [ ] **Step 8: 检查点**

记录测试结果；本仓库无 git，不提交。

---

### Task 3: 前端展示 warning 状态与截图错误

**Files:**
- Modify: `frontend/src/types/workflow.ts:122-141`
- Modify: `frontend/src/lib/execution.ts:34-92`
- Modify: `frontend/src/pages/ExecutionDetail.tsx:73-146`、`frontend/src/pages/ExecutionDetail.tsx:900-945`
- Test: `frontend/tests/execution.test.mjs`

**Interfaces:**
- Consumes: 后端步骤日志的可选键 `screenshot_error`，以及 `status: "warning"`
- Produces: `ExecutionStepStatus` 新增 `'warning'`；`ExecutionStepLog.screenshotError: string | null`

- [ ] **Step 1: 写失败测试**

在 `frontend/tests/execution.test.mjs` 的 `mergeExecutionStepLogs` 测试之后追加：

```js
test('normalizeExecutionStepLog keeps screenshot warnings', () => {
  const step = normalizeExecutionStepLog(
    {
      step_index: 8,
      action: 'screenshot',
      target: 'step_4_save_file',
      status: 'warning',
      screenshot_error: 'Page.screenshot: Timeout 15000ms exceeded.',
      error: 'Page.screenshot: Timeout 15000ms exceeded.',
    },
    0,
  );

  assert.equal(step.status, 'warning');
  assert.equal(step.hasScreenshot, false);
  assert.equal(
    step.screenshotError,
    'Page.screenshot: Timeout 15000ms exceeded.',
  );
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend; npm test`

Expected: FAIL —— `step.status` 为 `'pending'`（未知状态回退），`step.screenshotError` 为 `undefined`。

- [ ] **Step 3: 扩展类型**

`frontend/src/types/workflow.ts`：

```ts
export type ExecutionStepStatus =
  | 'success'
  | 'warning'
  | 'failed'
  | 'running'
  | 'pending'
  | 'skipped';
```

`ExecutionStepLog` 接口在 `error` 之后加：

```ts
  screenshotError: string | null;
```

- [ ] **Step 4: 归一化新字段**

`frontend/src/lib/execution.ts` 的 `getStepStatus` 白名单加入 `warning`：

```ts
  if (
    status === 'success' ||
    status === 'warning' ||
    status === 'failed' ||
    status === 'running' ||
    status === 'pending' ||
    status === 'skipped'
  ) {
    return status;
  }
```

`normalizeExecutionStepLog` 的返回对象在 `error` 之前加：

```ts
    screenshotError: asString(record.screenshot_error),
```

- [ ] **Step 5: 渲染警告**

`frontend/src/pages/ExecutionDetail.tsx`：

`STATUS_LABELS` 增加一行：

```ts
  warning: '截图失败',
```

`stepStatusVariant` 在 `running` 分支之前插入：

```ts
  if (status === 'warning') {
    return 'warning';
  }
```

`StepStatusIcon` 在 `running` 分支之后插入（`TriangleAlert` 已在该文件 import）：

```tsx
  if (status === 'warning') {
    return <TriangleAlert className="h-4 w-4 text-amber-600" />;
  }
```

步骤卡片的错误段落改为一红一琥珀两段：

```tsx
                          {step.error ? (
                            <p className="mt-2 break-all text-xs leading-5 text-red-600">
                              {step.error}
                            </p>
                          ) : null}
                          {step.screenshotError ? (
                            <p className="mt-2 break-all text-xs leading-5 text-amber-600">
                              {step.screenshotError}
                            </p>
                          ) : null}
```

注意：`warning` 步骤的 `error` 与 `screenshotError` 可能同时为空（历史数据），页面必须像现在一样容忍缺省。

- [ ] **Step 6: 运行前端测试与构建**

Run: `cd frontend; npm test`

Expected: 新增测试与既有 7 个测试全部 PASS。

Run: `cd frontend; npm run build`

Expected: `tsc --noEmit` 与 `vite build` 成功，无类型错误。

- [ ] **Step 7: 重建桌面端产物**

Run: `cd desktop; npm run build`

Expected: 构建成功，`desktop/dist/assets/index-*.js` 被刷新（桌面端直接 import `frontend/src`，必须重建才会生效）。

- [ ] **Step 8: 检查点**

记录 `npm test`、`npm run build` 与桌面构建结果；本仓库无 git，不提交。

---

### Task 4: 端到端回归与真实工作流验收

**Files:**
- 无代码改动（只做验证与记录）

**Interfaces:**
- Consumes: Task 1–3 的全部改动
- Produces: 一份可复核的验收记录

- [ ] **Step 1: 后端全量测试**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: 全部 PASS。若出现与本次改动无关的既有失败，逐条记录失败用例名与原因，不要为了通过而修改无关代码。

- [ ] **Step 2: 复现原始场景**

打开桌面应用，进入「流程管理」→ 流程 #3（B站消息抓取）→ 运行；或等待 `0 18 * * *` 定时触发。

在「执行监控」打开新的执行详情，确认：

1. 整条执行状态为「执行成功」（或至少不再因为截图失败而变为「执行失败」）。
2. 时间线中出现琥珀色的 `截图失败`（`warning`）步骤，展开后能看到 `Page.screenshot: Timeout ...` 文案。
3. 前面的动作步骤（`goto` / `screenshot`）仍带截图预览。
4. 输出文件仍然投递成功，`D:\Administrator\Documents\` 下有对应的 `B站+YYYY-MM-DD.csv`。

如果 B站消息页仍然截不了图，第 2 条出现而第 1 条仍为成功，说明降级行为符合预期。

- [ ] **Step 3: 直接读库核对持久化结果**

Run:

```powershell
$env:PYTHONUTF8='1'; @'
import sqlite3, json
con = sqlite3.connect(r"C:\Users\Administrator\.nl-rpa\data\rpa.db")
row = con.execute(
    "select id,status,error_message,step_logs from execution_log "
    "order by id desc limit 1"
).fetchone()
print(row[0], row[1], row[2])
for step in json.loads(row[3] or "[]"):
    print(
        step.get("step_index"),
        step.get("status"),
        step.get("screenshot_error"),
    )
'@ | .venv\Scripts\python.exe -
```

Expected: 最新一条执行 `status=success`，`error_message` 为空，且步骤日志里能看到 `status=warning` 的截图步骤。

- [ ] **Step 4: 记录回滚方式**

如需临时关闭诊断截图，在 `.nl-rpa\.env`（桌面端数据目录）新增：

```
SCREENSHOT_ENABLED=false
```

如需缩短单张截图的等待，新增：

```
SCREENSHOT_TIMEOUT_MS=5000
```

两处都不需要改代码，改完重启后端服务即可。

---

## 风险与取舍

- **整页截图仍可能超时**：默认仍是整页优先，只是超时从 30 秒降到 15 秒并回退到视口截图；若某页面连视口截图也失败，会得到 `warning` 步骤而不是失败执行。
- **执行详情的信息变多**：截图失败会新增一条 `warning` 步骤或一个 `screenshot_error` 字段；历史执行不受影响，前端对缺省值已做兼容。
- **重试语义不变**：`Engine.execute_with_debug` 仍按 `MAX_RETRY=3` 重试真正的业务失败；截图失败不再进入这条路径，因此不会再把 5 分钟耗在重复截图上。
- **空数据问题未修**：本计划只解决「截图失败拖垮整条执行」，B站消息列表 0 条数据需要单独的选择器/接口方案。

## Self-Review

- 覆盖检查：截图超时（Task 1）、失败致命性（Task 2）、前端可见性（Task 3）、真实场景验收（Task 4），与事故的两个直接成因一一对应。
- 类型一致性：`_capture_screenshot` 在 Task 1 定义为 `tuple[str | None, str | None]`，Task 2 的两个调用点都按该签名解包；`screenshot()` 返回 `str | None`，生成代码忽略返回值，兼容。
- 缺省兼容：`screenshot_error` 是 JSON 可选键，前端 `asString` 对 `undefined` 返回 `null`。
- 无占位符：所有步骤都给出了可直接落地的代码或命令。
