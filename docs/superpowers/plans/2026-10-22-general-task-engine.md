# 通用复杂任务引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有网页、桌面、MCP 和 Office 执行能力升级为可组合、可验证、可恢复的通用任务运行时。

**Architecture:** 以兼容现有 `structured_plan` 的 TaskGraph v2 为核心，增加变量插值、循环、条件验证、统一步骤结果和领域适配器。网页抓取、桌面操作和文件生成都通过同一运行时调度，并共享检查点与失败恢复。

**Tech Stack:** Python 3.11+、Pydantic、Playwright、pywinauto、pywin32、Pillow、WPS/Office COM、FastAPI、React/TypeScript。

**Spec:** `docs/superpowers/specs/2026-10-22-general-task-engine-design.md`

## Global Constraints

- 不执行模型生成的任意 Python、PowerShell 或批处理代码。
- 所有文件写入必须位于 `allowed_doc_dirs`。
- 所有网页抓取任务必须声明允许域名、最大页数和最大条目数。
- 所有新动作必须同时加入动作目录、ActionValidator、计划摘要和测试。
- 旧版 `generated_code` 路径默认行为不变。
- 新增可选字段不得破坏已有 `structured_plan`。
- 后端测试命令：`.venv\\Scripts\\python.exe -m pytest -q`
- 前端测试命令：`npm.cmd test`
- 前端构建命令：`npm.cmd run build`
- 当前工作区没有 `.git`。各任务末尾的 Git 步骤仅在后续初始化或导入 Git 仓库后执行；没有 Git 时将测试通过作为检查点，不执行 `git add` 或 `git commit`。

---

### Task 1: TaskGraph v2 变量与结果契约

**Files:**
- Modify: `app/protocols/structured.py`
- Create: `app/engine/task_runtime.py`
- Test: `tests/test_task_runtime.py`

**Interfaces:**
- Consumes: 现有 `StructuredStep`、`parse_structured_steps()`。
- Produces: `StepResult(success, message, text, value, items, artifacts, data, verification)`；`TaskRunResult(success, message, variables, results, artifacts, error)`；`resolve_template(value, variables)`；`TaskRuntime.execute(workflow, context)`。

- [x] **Step 1: Write the failing tests**

```python
from app.engine.task_runtime import resolve_template


def test_resolve_template_preserves_nested_values() -> None:
    variables = {
        "lyrics_lines": ["第一句", "第二句"],
        "paths": {"lyrics": r"D:\\Administrator\\Documents\\lyrics.docx"},
    }

    assert resolve_template("${lyrics_lines}", variables) == [
        "第一句",
        "第二句",
    ]
    assert resolve_template("${paths.lyrics}", variables).endswith(".docx")


def test_resolve_template_rejects_unknown_variable() -> None:
    import pytest

    with pytest.raises(ValueError, match="missing"):
        resolve_template("${missing}", {})
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_task_runtime.py -v`

Expected: FAIL because `app.engine.task_runtime` does not exist.

- [x] **Step 3: Implement the minimal contract**

```python
import re
from typing import Any


_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\\.[A-Za-z_][A-Za-z0-9_]*)*$")


def _lookup(path: str, variables: dict[str, Any]) -> Any:
    current: Any = variables
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Unknown variable: {path}")
        current = current[part]
    return current


def resolve_template(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\\$\\{([^}]+)\\}", value.strip())
        if match and _PATH.fullmatch(match.group(1).strip()):
            return _lookup(match.group(1).strip(), variables)
        return re.sub(
            r"\\$\\{([^}]+)\\}",
            lambda item: str(_lookup(item.group(1).strip(), variables)),
            value,
        )
    if isinstance(value, list):
        return [resolve_template(item, variables) for item in value]
    if isinstance(value, dict):
        return {
            key: resolve_template(item, variables)
            for key, item in value.items()
        }
    return value
```

在 `StructuredStep` 增加：

```python
result_var: str | None = None
foreach: dict[str, Any] | None = None
output_var: str | None = None
condition: dict[str, Any] | None = None
verify: list[dict[str, Any]] = Field(default_factory=list)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_task_runtime.py -v`

Expected: PASS

- [x] **Step 5: Commit**

按用户约束跳过：工作区没有 `.git`，以
`tests/test_task_runtime.py` 4 项测试通过作为 Task 1 检查点。

```bash
git add app/protocols/structured.py app/engine/task_runtime.py tests/test_task_runtime.py
git commit -m "feat: add task runtime variable contracts"
```

### Task 2: 共享运行时状态、条件验证和检查点

**Files:**
- Modify: `app/engine/task_runtime.py`
- Modify: `app/protocols/structured.py`
- Test: `tests/test_task_runtime.py`

**Interfaces:**
- Consumes: `resolve_template()`、`StepResult`。
- Produces: `RuntimeState(variables, results, completed_steps, loop_cursors, artifacts)`；`RuntimeState.checkpoint()` / `RuntimeState.from_checkpoint(payload)`；`VerificationResult(passed, checks)`；`evaluate_postconditions(checks, state)`。

- [x] **Step 1: Write failing tests**

```python
from app.engine.task_runtime import evaluate_postconditions


def test_text_and_list_postconditions() -> None:
    state = {"variables": {"lyrics": "第一句\\n第二句", "rows": [1, 2]}}
    assert evaluate_postconditions(
        [
            {"type": "text_non_empty", "value": "${lyrics}"},
            {"type": "list_min_length", "value": "${rows}", "min": 2},
        ],
        state,
    ).passed


def test_file_postcondition_rejects_empty_file(tmp_path) -> None:
    target = tmp_path / "empty.xlsx"
    target.write_text("", encoding="utf-8")
    checked = evaluate_postconditions(
        [{"type": "file_non_empty", "path": str(target)}],
        {"variables": {}},
    )
    assert checked.passed is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_task_runtime.py -v`

Expected: FAIL because `evaluate_postconditions` is missing.

- [x] **Step 3: Implement checks**

```python
@dataclass
class VerificationResult:
    passed: bool
    checks: list[dict[str, Any]]


def evaluate_postconditions(
    checks: list[dict[str, Any]],
    state: dict[str, Any],
) -> VerificationResult:
    results: list[dict[str, Any]] = []
    variables = state.get("variables", {})
    for check in checks:
        check_type = str(check.get("type") or "")
        if check_type == "text_non_empty":
            value = resolve_template(check.get("value"), variables)
            passed = bool(str(value or "").strip())
        elif check_type == "list_min_length":
            value = resolve_template(check.get("value"), variables)
            minimum = int(check.get("min") or 1)
            passed = isinstance(value, list) and len(value) >= minimum
        elif check_type == "file_non_empty":
            path = Path(str(resolve_template(check.get("path"), variables)))
            passed = path.is_file() and path.stat().st_size > 0
        else:
            passed = False
        results.append({"type": check_type, "passed": passed})
    return VerificationResult(
        passed=all(item["passed"] for item in results),
        checks=results,
    )
```

- [x] **Step 4: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_task_runtime.py -v`

Expected: PASS

- [x] **Step 5: Commit**

按用户约束跳过 Git；检查点为 `tests/test_task_runtime.py` 与
`tests/test_structured_protocol.py` 共 29 项测试通过。

```bash
git add app/engine/task_runtime.py app/protocols/structured.py tests/test_task_runtime.py
git commit -m "feat: add runtime verification and state"
```

### Task 3: 循环节点和预算

**Files:**
- Modify: `app/engine/task_runtime.py`
- Test: `tests/test_task_runtime.py`

**Interfaces:**
- Consumes: `RuntimeState`、`resolve_template()`。
- Produces: `execute_foreach(step, state, execute_step)`，返回聚合列表并写入 `output_var`；支持 `max_iterations`、`max_steps`、`max_seconds`，失败时保留已聚合结果和 `loop_cursors` 游标。

- [x] **Step 1: Write failing test**

```python
import asyncio

from app.engine.task_runtime import execute_foreach


def test_foreach_preserves_order_and_limit() -> None:
    async def run():
        async def execute_step(step, state):
            return {"value": state["variables"]["item"] * 2}

        state = {
            "variables": {"items": [1, 2, 3], "result": []},
            "results": {},
        }
        await execute_foreach(
            {
                "output_var": "result",
                "params": {
                    "items": "${items}",
                    "item_var": "item",
                    "max_iterations": 2,
                    "steps": [{"action": "double"}],
                },
            },
            state,
            execute_step,
        )
        return state["variables"]["result"]

    assert asyncio.run(run()) == [2, 4]
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_task_runtime.py::test_foreach_preserves_order_and_limit -v`

Expected: FAIL

- [x] **Step 3: Implement foreach**

```python
async def execute_foreach(step, state, execute_step):
    params = step.get("params", {})
    items = resolve_template(params.get("items"), state["variables"])
    if not isinstance(items, list):
        raise ValueError("foreach items must resolve to a list")
    limit = min(int(params.get("max_iterations", 100)), len(items))
    item_var = str(params.get("item_var") or "item")
    results = []
    for item in items[:limit]:
        state["variables"][item_var] = item
        for child in params.get("steps", []):
            result = await execute_step(child, state)
            results.append(result.get("value", result.get("text")))
    output_var = step.get("output_var") or "output"
    state["variables"][output_var] = results
    return results
```

- [x] **Step 4: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_task_runtime.py -v`

Expected: PASS

- [x] **Step 5: Commit**

按用户约束跳过 Git；检查点为 `tests/test_task_runtime.py` 13 项测试通过。

```bash
git add app/engine/task_runtime.py tests/test_task_runtime.py
git commit -m "feat: add bounded foreach execution"
```

### Task 4: 桌面选区复制动作

**Files:**
- Create: `desktop_agent/actions/copy_selected_text.py`
- Modify: `desktop_agent/actions/__init__.py`
- Modify: `desktop_agent/action_validator.py`
- Modify: `app/agents/desktop_catalog.py`
- Modify: `app/agents/plan_summary.py`
- Test: `tests/test_actions.py`
- Test: `tests/test_desktop_catalog.py`

**Interfaces:**
- Consumes: pywinauto `send_keys`、`win32clipboard`。
- Produces: `handle_copy_selected_text(params, context) -> ActionResult(text=...)`；支持 `keys`、`min_chars`、`settle_seconds`、`timeout_seconds`，空内容自动重发一次后报错。

- [x] **Step 1: Write failing test**

```python
def test_copy_selected_text_reads_clipboard(tmp_path, monkeypatch) -> None:
    from desktop_agent.actions import copy_selected_text as module

    monkeypatch.setattr(module, "_send_keys", lambda keys: None)
    monkeypatch.setattr(module, "_read_clipboard_text", lambda: "第一句\\n第二句")
    result = asyncio.run(
        module.handle_copy_selected_text({"keys": "^a^c"}, {})
    )
    assert result.success is True
    assert result.text == "第一句\\n第二句"
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_actions.py::test_copy_selected_text_reads_clipboard -v`

Expected: FAIL

- [x] **Step 3: Implement action**

```python
async def handle_copy_selected_text(params, context):
    keys = str(params.get("keys") or "^a^c").strip()
    min_chars = int(params.get("min_chars", 1))
    await asyncio.to_thread(_send_keys, keys)
    await asyncio.sleep(float(params.get("settle_seconds", 0.4)))
    text = await asyncio.to_thread(_read_clipboard_text)
    if len(text.strip()) < min_chars:
        raise ValueError("Clipboard text is empty after copy")
    return ActionResult(success=True, message="Text copied", text=text)
```

- [x] **Step 4: Register action and validation**

Add `copy_selected_text` to:

- `REGISTERED_ACTION_TYPES`
- `ACTION_TYPES`
- action registry imports
- `_ACTION_LABELS`

No file or network permission is granted by this action.

- [x] **Step 5: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_actions.py tests/test_desktop_catalog.py -q`

Expected: PASS

- [x] **Step 6: Commit**

按用户约束跳过 Git；检查点为动作、桌面目录和计划摘要共 17 项测试通过。

```bash
git add desktop_agent/actions/copy_selected_text.py desktop_agent/actions/__init__.py desktop_agent/action_validator.py app/agents/desktop_catalog.py app/agents/plan_summary.py tests/test_actions.py tests/test_desktop_catalog.py
git commit -m "feat: add desktop selection text capture"
```

### Task 5: Word 与 Excel 文件动作

**Files:**
- Modify: `desktop_agent/actions/create_document.py`
- Create: `desktop_agent/actions/create_spreadsheet.py`
- Modify: `desktop_agent/actions/__init__.py`
- Modify: `desktop_agent/action_validator.py`
- Modify: `app/agents/desktop_catalog.py`
- Modify: `app/agents/plan_summary.py`
- Test: `tests/test_desktop_document_action.py`

**Interfaces:**
- Consumes: WPS/Office COM ProgID。
- Produces: `handle_create_spreadsheet(params, context) -> ActionResult`；内部写入器保持 `_create_spreadsheet(path, sheet_name, columns, widths, overwrite)` 五参数接口，可选 COM ProgID 通过 `ContextVar` 传递。

- [x] **Step 1: Write failing spreadsheet test**

```python
def test_create_spreadsheet_writes_rows_and_formulas(
    tmp_path,
    monkeypatch,
) -> None:
    from desktop_agent.actions import create_spreadsheet as module

    captured = {}

    def fake_write(path, sheet_name, columns, widths, overwrite):
        captured.update(
            {
                "path": path,
                "sheet_name": sheet_name,
                "columns": columns,
                "widths": widths,
                "overwrite": overwrite,
            }
        )

    monkeypatch.setattr(module, "_create_spreadsheet", fake_write)
    result = asyncio.run(
        module.handle_create_spreadsheet(
            {
                "path": str(tmp_path / "歌词.xlsx"),
                "sheet_name": "歌词",
                "columns": [
                    {"header": "歌词", "values": ["第一句", "第二句"]},
                    {"header": "字数", "formula": "=LEN(A{row})"},
                ],
                "column_widths": [80, 12],
            },
            {},
        )
    )
    assert result.success is True
    assert captured["sheet_name"] == "歌词"
    assert captured["columns"][1]["formula"] == "=LEN(A{row})"
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_desktop_document_action.py::test_create_spreadsheet_writes_rows_and_formulas -v`

Expected: FAIL

- [x] **Step 3: Implement package-friendly COM writer**

```python
def _create_spreadsheet(path, sheet_name, columns, widths, overwrite):
    import win32com.client

    if overwrite and path.exists():
        path.unlink()
    app = win32com.client.Dispatch("ket.Application")
    app.Visible = False
    app.DisplayAlerts = 0
    workbook = app.Workbooks.Add()
    sheet = workbook.Worksheets.Item(1)
    sheet.Name = sheet_name
    max_rows = max(len(column.get("values", [])) for column in columns)
    for column_index, column in enumerate(columns, start=1):
        sheet.Cells(1, column_index).Value = column["header"]
        values = column.get("values") or []
        for row_index, value in enumerate(values, start=2):
            sheet.Cells(row_index, column_index).Value = value
        formula = column.get("formula")
        if formula:
            for row_index in range(2, max_rows + 2):
                sheet.Cells(row_index, column_index).Formula = (
                    formula.format(row=row_index)
                )
    for index, width in enumerate(widths, start=1):
        sheet.Columns(index).ColumnWidth = width
    workbook.SaveAs(str(path))
    workbook.Close(False)
    app.Quit()
```

For environments without WPS Spreadsheet, fall back to
`Excel.Application` only if `PROGID` is explicitly configured.

- [x] **Step 4: Add validation and catalog entry**

`create_spreadsheet` must:

- require `.xlsx` or `.xls` suffix,
- resolve the path inside `allowed_doc_dirs`,
- reject empty columns,
- require a non-empty sheet name.

- [x] **Step 5: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_desktop_document_action.py tests/test_desktop_catalog.py -q`

Expected: PASS

- [x] **Step 6: Commit**

按用户约束跳过 Git；检查点为文档动作、桌面目录和计划摘要共 29 项测试通过。

```bash
git add desktop_agent/actions/create_spreadsheet.py desktop_agent/actions/create_document.py desktop_agent/actions/__init__.py desktop_agent/action_validator.py app/agents/desktop_catalog.py app/agents/plan_summary.py tests/test_desktop_document_action.py tests/test_desktop_catalog.py
git commit -m "feat: add spreadsheet generation actions"
```

### Task 6: TransformEngine

**Files:**
- Create: `app/engine/transforms.py`
- Modify: `app/engine/task_runtime.py`
- Test: `tests/test_transforms.py`

**Interfaces:**
- Consumes: list、字符串和变量路径。
- Produces: `TRANSFORM_ACTIONS`、`split_lines(text)`、`count_chars(text)`、`apply_transform(action, params, variables)`；实现 `trim_lines`、`remove_blank_lines`、`deduplicate`、`filter_contains`、`map_fields`、`merge_rows`、`aggregate_sum`、`sort_by`。

- [x] **Step 1: Write failing tests**

```python
from app.engine.transforms import apply_transform, split_lines


def test_split_lines_removes_blank_and_metadata() -> None:
    lines = split_lines(
        "[00:01.00]第一句\\n\\n[00:03.00]第二句\\n"
    )
    assert lines == ["第一句", "第二句"]


def test_count_chars_returns_per_line_counts() -> None:
    result = apply_transform(
        "count_chars",
        {"values": ["第一句", "第二句"]},
        {},
    )
    assert result == [3, 3]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_transforms.py -v`

Expected: FAIL

- [x] **Step 3: Implement first transforms**

```python
import re


_LRC_TAG = re.compile(r"^\\[[^\\]]+\\]")


def split_lines(text: str) -> list[str]:
    return [
        _LRC_TAG.sub("", line).strip()
        for line in str(text or "").splitlines()
        if _LRC_TAG.sub("", line).strip()
    ]


def count_chars(text: str) -> int:
    return len("".join(str(text or "").split()))
```

`apply_transform` dispatches `split_lines`、`count_chars`、
`deduplicate`、`filter_contains` 并返回新变量值。

- [x] **Step 4: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_transforms.py -v`

Expected: PASS

- [x] **Step 5: Commit**

按用户约束跳过 Git；检查点为 `tests/test_transforms.py` 与
`tests/test_task_runtime.py` 共 20 项测试通过。

```bash
git add app/engine/transforms.py app/engine/task_runtime.py tests/test_transforms.py
git commit -m "feat: add text transformation engine"
```

### Task 7: WebAdapter 抓取动作与预算

**Files:**
- Modify: `app/agents/web_catalog.py`
- Modify: `app/engine/web_executor.py`
- Modify: `app/agents/web_planner.py`
- Test: `tests/test_web_executor.py`
- Test: `tests/test_web_structured_chat.py`

**Interfaces:**
- Consumes: Playwright page、现有 `extract_text` / `extract_table`。
- Produces: `observe_page`、`collect_links`、`extract_fields`、`paginate`、`wait_for_response`、`assert_data`、`download_file`；`CRAWL_ACTIONS` 与 `crawl_policy` 校验。

- [x] **Step 1: Write failing test**

```python
def test_collect_links_respects_limit(fake_page) -> None:
    result = asyncio.run(
        executor._action_collect_links(
            fake_page,
            {
                "selector": "a.item",
                "attribute": "href",
                "limit": 2,
            },
            {},
            {},
        )
    )
    assert len(result["items"]) == 2
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_web_executor.py::test_collect_links_respects_limit -v`

Expected: FAIL

- [x] **Step 3: Implement deterministic collectors**

`collect_links` must:

- require a selector,
- limit result count,
- resolve relative URLs with `urljoin`,
- reject non-http(s) URLs,
- deduplicate in encounter order.

`paginate` must:

- accept `max_pages`,
- stop when the next button is missing or disabled,
- stop when no new items are found,
- emit progress for each page,
- preserve partial rows after failure.

- [x] **Step 4: Add domain and rate budgeting**

Add plan-level:

```json
{
  "crawl_policy": {
    "allowed_domains": ["example.com"],
    "requests_per_second": 1,
    "max_pages": 20,
    "max_items": 500
  }
}
```

Reject navigation to a host outside `allowed_domains`.

- [x] **Step 5: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_web_executor.py tests/test_web_structured_chat.py -q`

Expected: PASS

- [x] **Step 6: Commit**

按用户约束跳过 Git；检查点为网页执行器、结构化聊天、规划器和目录
共 49 项通过、2 项因本机浏览器环境跳过。

```bash
git add app/agents/web_catalog.py app/engine/web_executor.py app/agents/web_planner.py tests/test_web_executor.py tests/test_web_structured_chat.py
git commit -m "feat: add bounded web collection actions"
```

### Task 8: 应用配方加载与校验

**Files:**
- Create: `app/engine/app_profiles.py`
- Create: `desktop_agent/profiles/kugou.json`
- Modify: `desktop_agent/action_validator.py`
- Modify: `app/agents/desktop_planner.py`
- Test: `tests/test_app_profiles.py`

**Interfaces:**
- Consumes: 配方 JSON、应用名称、窗口信息。
- Produces: `load_app_profiles(root)`、`resolve_profile(app_hint, profiles)`、`validate_profile(profile)`；`ActionValidator.validate_profile_recipe(profile, recipe_name, manual_confirmation=...)` 与 `click_relative` 桌面动作。

- [x] **Step 1: Write failing test**

```python
from app.engine.app_profiles import resolve_profile


def test_resolve_profile_by_display_name(tmp_path) -> None:
    profile = {
        "app_id": "kugou",
        "display_name": "酷狗音乐",
        "executable_path": r"D:\\KGMusic\\KuGou.exe",
        "recipes": {},
    }
    loaded = resolve_profile("打开酷狗音乐", [profile])
    assert loaded["app_id"] == "kugou"
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_app_profiles.py -v`

Expected: FAIL

- [x] **Step 3: Implement profile validation**

Required fields:

- `schema_version = 1`
- `app_id`
- `display_name`
- `executable_path`
- `window`
- `recipes`

Reject absolute coordinates outside `[0, 1]` for `click_relative`.

- [x] **Step 4: Build initial KuGou profile**

The first profile contains only verified recipes. Unverified recipes
must carry `"verified": false` and may not run without manual
confirmation.

- [x] **Step 5: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_app_profiles.py tests/test_desktop_planner.py -q`

Expected: PASS

- [x] **Step 6: Commit**

按用户约束跳过 Git；检查点为配方、相对点击、桌面目录和规划器
共 41 项测试通过。内置酷狗配方均标记为未验证，需人工确认后才可自动执行。

```bash
git add app/engine/app_profiles.py desktop_agent/profiles/kugou.json desktop_agent/action_validator.py app/agents/desktop_planner.py tests/test_app_profiles.py tests/test_desktop_planner.py
git commit -m "feat: add application automation profiles"
```

### Task 9: 规划器与任务编译器接入新能力

**Files:**
- Modify: `app/agents/intent_parser.py`
- Modify: `app/agents/desktop_planner.py`
- Modify: `app/agents/web_planner.py`
- Modify: `app/agents/plan_summary.py`
- Modify: `app/api/chat.py`
- Test: `tests/test_agents_3a.py`
- Test: `tests/test_desktop_planner.py`
- Test: `tests/test_api_4a.py`

**Interfaces:**
- Consumes: TaskGraph v2、动作目录、应用配方。
- Produces: 不再要求低层控件信息的可执行计划；新增 `validate_task_graph_plan(plan, allowed_apps, allowed_doc_dirs)`，混合计划经 `HybridExecutor` 自动路由到 `TaskRuntime`。

- [x] **Step 1: Write failing planner tests**

```python
def test_desktop_plan_uses_result_variable_for_document() -> None:
    plan = validate_desktop_plan(
        {
            "executor_type": "windows",
            "steps": [
                {
                    "step_id": 1,
                    "action": "copy_selected_text",
                    "result_var": "lyrics",
                    "params": {"keys": "^a^c"},
                },
                {
                    "step_id": 2,
                    "action": "create_document",
                    "params": {
                        "path": "D:\\\\Administrator\\\\Documents\\\\lyrics.docx",
                        "content": "${lyrics}",
                    },
                },
            ],
        },
        allowed_apps=(),
    )
    assert plan["steps"][0]["result_var"] == "lyrics"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_desktop_planner.py -v`

Expected: FAIL because `result_var` validation is missing.

- [x] **Step 3: Extend plan validation**

Validation must reject:

- duplicate `result_var` producers,
- forward references in `${...}`,
- foreach without an explicit limit,
- unknown `output_var`,
- file paths outside allowlists.

- [x] **Step 4: Update planning prompts**

The planner must prefer:

1. Application profile recipes.
2. Generic desktop primitives.
3. Clipboard text extraction for non-UIA controls.
4. File and Office actions for outputs.
5. Web collectors and loops for crawling.

The planner must not ask for:

- executable path,
- window class,
- UIA automation id,
- screen coordinates already stored in a profile.

- [x] **Step 5: Run tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/test_agents_3a.py tests/test_desktop_planner.py tests/test_api_4a.py -q`

Expected: PASS

- [x] **Step 6: Commit**

按用户约束跳过 Git；检查点为规划器、API、混合执行器、网页结构化聊天
和计划摘要共 80 项测试通过。

```bash
git add app/agents/intent_parser.py app/agents/desktop_planner.py app/agents/web_planner.py app/agents/plan_summary.py app/api/chat.py tests/test_agents_3a.py tests/test_desktop_planner.py tests/test_api_4a.py
git commit -m "feat: compile complex tasks with variables and profiles"
```

### Task 10: 两个端到端验收场景

**Files:**
- Create: `tests/integration/test_complex_desktop_task.py`
- Create: `tests/integration/test_web_collection_task.py`
- Modify: `tests/conftest.py` only if a shared fixture is required.

**Interfaces:**
- Consumes: 前九项任务的运行时和动作。
- Produces: 可重复验证的酷狗歌词导出场景和网页抓取场景；桌面夹具使用模拟剪贴板/Office 写入器，网页夹具使用本地静态 HTML 页池。

- [x] **Step 1: Add desktop fixture test**

The test uses fake clipboard and fake Office writer:

```python
def test_kugou_lyrics_exports_word_and_excel(tmp_path) -> None:
    lyrics = "第一句\\n第二句\\n第三句"
    plan = build_kugou_lyrics_plan(
        output_dir=tmp_path,
        lyrics="\\${lyrics}",
    )
    result = asyncio.run(run_fixture_plan(plan, {"lyrics": lyrics}))
    assert result.success is True
    assert (tmp_path / "第一推动力.docx").is_file()
    assert (tmp_path / "第一推动力歌词.xlsx").is_file()
```

- [x] **Step 2: Add web collection fixture test**

Use a local StaticPool HTML fixture with three pages and verify:

- 12 rows extracted,
- duplicate item removed,
- Excel output exists,
- the request count never exceeds the configured limit.

- [x] **Step 3: Run integration tests**

Run: `.venv\\Scripts\\python.exe -m pytest tests/integration/test_complex_desktop_task.py tests/integration/test_web_collection_task.py -q`

Expected: PASS

- [x] **Step 4: Run full backend suite**

Run: `.venv\\Scripts\\python.exe -m pytest -q`

Expected: all tests pass.

- [x] **Step 5: Run frontend tests and build**

Run in `frontend`:

```bash
npm.cmd test
npm.cmd run build
```

Expected: all tests pass and Vite build exits 0.

- [x] **Step 6: Commit**

按用户约束跳过 Git；两个集成验收测试已通过。

最终验证结果：

- 后端：`738 passed, 23 skipped`
- 前端：`63 passed`
- 前端构建：Vite 成功
- `backend-server.exe` 与 `desktop-agent.exe` 已通过 PyInstaller 重建；
  backend 打包进程通过 `/health`，desktop-agent 打包进程可启动并进入重连循环。
- backend CArchive 已确认包含
  `desktop_agent\profiles\kugou.json`。
- 未验证应用配方可展开为原子动作并标记 `requires_confirmation`；
  酷狗歌词导出的确定性编译兜底已覆盖 Word/Excel、歌词拆行和逐行字数。
- 带 `app_hint` 的 MCP 候选会路由到桌面规划器，并移除窗口类名/UIA
  等运行时澄清，避免在生成可执行计划前提前停止。
- 酷狗歌词导出优先使用确定性数据链，确保
  `fetch_kugou_lyrics -> split_lines -> count_chars -> Word/Excel`
  全部由真实步骤产生，不再接受缺少数据生产者的“伪成功”计划。
- 酷狗搜索入口优先使用 UIA 控件 `搜索(Edit)`；桌面歌词为自绘窗口，
  因此歌词正文通过只读 `fetch_kugou_lyrics` 从三个固定酷狗域名获取，
  并限制响应大小。获取结果必须不同于搜索词，以阻止把搜索关键词误当成
  歌词导出。
- `TaskRuntime` 在写入 `result_var` 后解析后置条件，保证
  `text_not_equals(${lyrics_text}, ${query})` 能校验真实动作结果。

```bash
git add tests/integration/test_complex_desktop_task.py tests/integration/test_web_collection_task.py
git commit -m "test: cover complex desktop and web tasks"
```

## Phase 2 Follow-Up Plans

The following require separate plans after Phase 1 is working:

1. OCR and visual-anchor provider.
2. Automatic recipe learning from successful runs.
3. Network-response-first crawler strategy.
4. Execution timeline data preview and verification UI.
5. Production hardening and resource quotas.

