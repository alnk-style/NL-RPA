# 个人通用 RPA 引擎 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前以代码生成和分散执行器为主的 NL-RPA 升级为个人使用的确定性通用 RPA 引擎，并保留旧工作流的可执行迁移路径。

**Architecture:** 以不可变 `WorkflowVersion` 和单次执行策略快照为边界，把 Web、Data、Files、Email、Windows、MCP 统一到 `ActionDefinition -> invocation -> StepResult` 协议。执行主链先完成版本固定、动作校验、变量/条件/循环、独立验证和可恢复状态，再建设 Workflow Studio、视觉兜底和按需 VM。

**Tech Stack:** Windows 10/11、Python 3.11+、FastAPI、SQLAlchemy、SQLite/PostgreSQL、Pydantic、jsonschema、Playwright、pywinauto、aiosmtplib、React 18、TypeScript、Vite、Tailwind、TanStack Query、Zustand。

**Spec:** `docs/superpowers/specs/2026-10-28-personal-generic-rpa-design.md`

## Global Constraints

- 目标是个人使用、单用户、Windows 10/11 本地运行的通用 RPA 引擎。
- 不为酷狗、WPS、微信、网银、订单后台或任何特定网站/软件新增专用业务模块。
- 特定场景只能由通用动作、通用工作流、通用数据和通用邮件能力组合完成。
- LLM 只用于工作流创建、解释、规划、修复和草稿差异；发布后的运行时不得调用 LLM 生成或选择任意代码。
- 发布后的结构化工作流只执行动作目录中的类型化动作，不执行任意 Python、PowerShell、CMD 或页面内任意脚本。
- `legacy_code` 只用于历史工作流运行和迁移，不是新工作流的合法创建模式。
- 运行时必须遵循“确定性主路径 + 视觉兜底 + 人工接管 + 按需 VM”。
- 登录、验证码、扫码、视觉歧义和业务异常必须可暂停、可恢复、可取消。
- 文件、邮件和业务结果必须独立验证；动作返回不等于成功。
- 通用网页任务优先无头 Playwright；只有必须占用交互桌面的步骤可路由到本机 Agent 或隔离 VM Agent。
- 同一执行只使用一个不可变工作流版本和一个执行策略快照。
- 动作目录外动作、未知参数、未知变量、非法路径和未授权主机必须 fail closed。
- 所有测试都不能假设 Docker、真实 Windows 交互桌面、外部 SMTP、真实 VM 或外网可用。
- 当前工作区没有 `.git`。本计划不包含提交步骤；没有 Git 时以明确测试命令通过作为任务检查点。

---

## 当前基线（2026-10-28 实测）

### 仓库与环境

- 工作目录：`E:\项目2`
- Python：`3.14.6`，项目虚拟环境：`.venv`
- Node.js：`24.18.0`
- npm：`11.16.0`
- 数据库：默认 `sqlite+aiosqlite:///./rpa.db`
- `git status` 无法执行：仓库根目录没有 `.git`，因此无法判断未提交改动是否存在。
- 根目录已有 `$null`、`$null)`、`build/`、`desktop/src-tauri/target/` 等生成或异常文件；本计划不清理这些无关内容。

### 当前测试基线

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

结果：`738 passed, 23 skipped`，耗时 `46.79s`。

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

结果：前端 `63 passed`；TypeScript 检查和 Vite 生产构建成功。

当前 23 项跳过均为环境能力缺失，不是测试失败：

- 12 项交互式 Windows 桌面不可用（`EnumWindows` 访问被拒绝）。
- 若干项 `notepad.exe` 不存在。
- WPS COM `kwps.Application` 未注册。
- PostgreSQL 测试地址/嵌入实例未配置。
- 3 项 Playwright headless Chromium 因 `spawn EPERM` 不可用。
- 1 项无可见窗口可供检查。
- 1 项模板占位测试按设计跳过。

后续计划必须保留这种降级方式：单元/契约测试不依赖上述环境；需要真实环境的测试使用明确 marker，并在不可用时给出原因后跳过。

### 当前关键实现

- `app/engine/task_runtime.py` 已有变量插值、前后置条件、有限循环和运行时状态。
- `app/engine/dag.py` 已支持 DAG、条件、执行器别名和分支调度。
- `app/engine/hybrid_executor.py` 已支持 Web/Windows/MCP/Code 路由、部分并行和检查点续跑。
- `app/engine/web_executor.py` 已支持多数网页动作、抓取、下载、登录检测和步骤修复。
- `app/engine/desktop_dispatcher.py`、`app/api/agent_ws.py`、`desktop_agent/` 已具备 WebSocket 配对、任务分发和 UIA 操作。
- `app/engine/execution_manager.py` 已支持后台执行、暂停、恢复、登录后继续和部分检查点。
- `app/notifications/notifier.py` 已有最佳努力 SMTP 通知，但没有投递记录和必需投递语义。
- 前端已有工作流列表、React Flow 编辑器、执行详情、设置和聊天页，但没有 TanStack Query、工作台、待处理中心或统一动态动作表单。

### 现有历史计划

`docs/superpowers/plans/2026-10-22-general-task-engine.md` 中的变量、循环、条件、网页抓取、桌面动作和基础文件能力已经落地，应尽量复用。

该旧计划同时引入了酷狗歌词动作、酷狗应用配方和 WPS/Office COM 文档动作。这与本次已确认的“不得新增专用业务模块”冲突。本次计划不删除历史执行能力，但执行以下隔离：

- 新结构化动作目录不包含 `fetch_kugou_lyrics`、`create_document`、`create_spreadsheet`、`insert_image`。
- 现有工作流引用这些动作时，只能以 `legacy_code` 只读历史版本继续运行。
- 新工作流发布校验必须拒绝这些动作。
- 相关 handler 和测试保留为兼容层，并标注 `legacy_only`，直到用户明确批准兼容期结束。

## 设计文档与代码冲突

| 严重度 | 设计期望 | 当前代码 | 原因与处理 |
|---|---|---|---|
| 高 | 正式结构化步骤只含类型化动作 | `app/protocols/structured.py` 的 `StepExecutor.CODE`；`app/engine/dag.py` 的 `code/python` 别名；`app/engine/hybrid_executor.py::_execute_code_step` | `code` 会执行生成代码，违反新主链边界。新目录不注册 `code`；旧计划按 `legacy_code` 运行；结构化发布拒绝。 |
| 高 | 运行时不得执行任意页面脚本 | `app/agents/web_catalog.py` 暴露 `code_step`，使用 `page.evaluate` | 设计动作清单中没有 `code_step`。从新目录删除；仅旧版计划兼容执行。 |
| 高 | 不新增酷狗/WPS 专用模块 | `desktop_agent/actions/fetch_kugou_lyrics.py`、`desktop_agent/profiles/kugou.json`、`create_document.py`、`create_spreadsheet.py`、`insert_image.py`，以及对应目录和测试 | 现有实现是专用业务模块。冻结为 legacy-only，不进入新动作目录；不再扩展。 |
| 高 | 新工作流默认 `structured` | `WEB_STRUCTURED_ACTIONS` 默认 `False`，`ExecutionEngine` 可回退到 `generated_code` | 新流程可能仍生成代码。迁移后新工作流固定 `structured`，新增紧急 kill switch 只能阻断执行，不能静默改走代码。 |
| 高 | 执行固定不可变版本和策略快照 | `Workflow` 直接保存可编辑 `structured_plan/generated_code`；`ExecutionLog` 没有版本/策略快照字段 | 运行中编辑会改变下一次运行语义。新增 `WorkflowVersion`，执行只读版本。 |
| 高 | 登录暂停必须可恢复且重新验证 | 当前仅有 `awaiting_user` 状态和 step log 检查点，没有 `HumanTask` | 用户点击“继续”无法形成独立待办、过期、审计和验证状态。新增 HumanTask/manager/API。 |
| 高 | 邮件必须记录真实投递结果 | `NotificationService` 捕获异常后仅写日志，最终执行仍可能成功 | 会产生假成功。新增通用 Email action、DeliveryRecord、必需投递语义。 |
| 高 | 结构化模式不能静默回退代码 | `ExecutionEngine.uses_structured_web()` 在非显式计划校验失败时返回 `False` 并走代码 | 计划损坏会降级为旧代码。版本化后显式 `structured` 校验失败必须执行失败。 |
| 中 | 统一动作目录同时驱动前后端 | `WEB_ACTIONS`、`DESKTOP_ACTIONS`、`TRANSFORM_ACTIONS` 分散，MCP 与 Email 没有统一 catalog | 前端无法用同一 schema 生成全部表单，且发布校验不一致。新增统一 registry。 |
| 中 | `StepResult` 有五种状态和 `human_action` | `app/engine/task_runtime.py::StepResult` 只有 `success: bool`、text/value/items | `warning/awaiting_user/cancelled` 无法稳定表达。新增协议模型并提供旧格式适配器。 |
| 中 | Web/Data/Files/Email/Windows/MCP 统一分派 | `HybridExecutor._execute_task_runtime()` 把 `files` 与 `windows` 一起送桌面；普通调度分支没有 transform/files | 文件动作可能被错误发给 Agent，数据流路径不一致。改为统一 `ActionRegistry`。 |
| 中 | 动作执行结果独立验证 | 现有网页步骤有部分验证；邮件和多数文件动作没有统一验证 | 需要统一 `verification`、空列表策略、文件归档检查和投递检查。 |
| 中 | 主机在一次运行内固定 | `DesktopDispatcher._task_agents` 只在内存；每步可重新选择 Agent | 断点恢复可能换宿主，破坏窗口、Cookie 和剪贴板状态。新增 host binding/checkpoint。 |
| 中 | Agent 声明 `environment` 和能力 | `DesktopAgent` 无 `environment`；Agent 配对发送 `window/uia/wps/screenshot` | 无法表达 local/vm 或 `interactive_desktop`。新增字段和旧能力映射。 |
| 中 | 前端以工作台为主，TanStack Query + Zustand | 根路由跳 `/chat`；列表使用卡片；没有 QueryClient；编辑器状态全部在单页组件 | 与个人工作台定位和状态职责冲突。新增路由、Query 和 Studio store。 |
| 低 | 工作流列表为紧凑列表 | `frontend/src/pages/WorkflowsPage.tsx` 当前是卡片网格 | 属于体验差距，不是数据错误；Stage C 替换为列表。 |

## 核心设计决定

### 1. 不可变版本与单一行程

发布事务的顺序固定为：

1. 读取工作流草稿和 `draft_revision`。
2. 用当前 `ActionCatalog` 校验计划、变量、依赖、域名、风险、路径和策略。
3. 取 `max(WorkflowVersion.version) + 1`。
4. 插入 `WorkflowVersion`，保存 plan、validation、`action_catalog_version` 和创建时间。
5. 仅更新 `Workflow.active_version_id`、`status=published` 和 `updated_at`。
6. 同一事务提交。

`Workflow.structured_plan`、`generated_code` 和 `dag_definition` 继续作为可编辑草稿字段，以兼容现有 API；执行绝不直接读取草稿。

版本 `plan` 的规范形态：

```json
{
  "schema_version": 1,
  "execution_mode": "structured",
  "executor_type": "hybrid",
  "schedule_timezone": "Asia/Shanghai",
  "variables": [],
  "steps": []
}
```

历史代码版本的规范形态：

```json
{
  "schema_version": 1,
  "execution_mode": "legacy_code",
  "generated_code": "async def main(page): ...",
  "migrated_from": "workflow_draft"
}
```

新建工作流永远写入 `execution_mode="structured"`；`legacy_code` 只能由迁移脚本或历史数据回填。

### 2. legacy_code 兼容

迁移回填规则：

- `generated_code` 非空的既有工作流：`legacy_code`，因为旧默认执行路径实际是代码。
- 仅结构化 plan 且无代码的既有工作流：`structured`。
- 既无代码也无可执行步骤：保留 `structured` 草稿，但不创建可运行版本。
- 对每个已有可执行 payload 的历史工作流创建一次 `version=1` 快照，并设置 `active_version_id`，避免迁移后突然无法运行。
- 历史版本 `validation` 写入 `{"migrated": true, "legacy_autosnapshot": true}`。

发布规则：

- `legacy_code` 版本可运行、可停用、可查看。
- 修改 `legacy_code` 草稿后不能直接覆盖发布；必须先转为结构化计划，或另存为只读历史版本。
- 新 API 的 `POST /api/workflows` 不接受 `execution_mode=legacy_code`。
- 只保留一个紧急配置 `LEGACY_CODE_EXECUTION_ENABLED`；关闭时阻断 legacy 运行，但不把 legacy 工作流静默改走结构化路径。

### 3. ExecutionPolicy

默认策略：

```json
{
  "mode": "interactive_preferred",
  "login_policy": "pause_and_resume",
  "visual_fallback": "on_locator_failure",
  "host_policy": "local_then_isolated",
  "failure_policy": "stop",
  "required_delivery": true,
  "max_duration_seconds": 1800,
  "human_handoff": true
}
```

约束：

- `mode` 只能是 `interactive_preferred | background_if_safe | isolated_preferred`。
- `login_policy` 固定为 `pause_and_resume`；本轮不实现自动登录。
- `visual_fallback` 只能是 `disabled | on_locator_failure | always`，默认 `on_locator_failure`，实际还受全局 provider 配置控制。
- `host_policy` 只能是 `local_only | local_then_isolated | isolated_only`。
- `mode` 控制运行安全策略，`host_policy` 控制本机/VM 选择，二者不得合并。
- `host_policy` 只作用于需要桌面主机或 host-bound 状态的步骤；服务器侧 Web/Data/Files/Email 不受影响。
- 步骤 `host_requirement`、`background_if_safe` 的桌面安全要求、`local_only`/`isolated_only` 是硬约束；`isolated_preferred` 和 `local_then_isolated` 的本机优先只是软偏好。
- `failure_policy` 只能是 `stop | pause`；高风险动作不受 `stop` 以外策略绕过。
- `required_delivery=true` 时，任何声明为必需的交付失败都令执行失败。
- `max_duration_seconds` 上限为 86400。
- 策略在创建 `ExecutionLog` 的同一事务中做 JSON 快照；之后修改工作流不影响当前执行和恢复。

### 4. 统一动作协议

规范模型放在 `app/actions/protocol.py`：

```python
class StepStatus(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_USER = "awaiting_user"


class ActionDefinition(BaseModel):
    id: str
    executor: str
    title: str
    description: str
    category: str
    risk_level: str
    params_schema: dict[str, Any]
    output_schema: dict[str, Any]
    requires: dict[str, bool]
    catalog_version: str
    legacy_only: bool = False


class StepResult(BaseModel):
    status: StepStatus
    message: str = ""
    value: Any = None
    variables: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    error: dict[str, Any] | None = None
    human_action: dict[str, Any] | None = None
```

兼容规则：

- 旧 `StepResult(success=...)` 由 `coerce_legacy_step_result()` 转为新协议；`success=True` 对应 `SUCCESS`，否则 `FAILED`。
- `warning` 在依赖判断上视为可继续，但必需交付或明确 postcondition 失败时提升为 `failed`。
- 任一未知状态按 `failed` 处理。
- 所有动作参数使用 JSON Schema Draft 2020-12；默认 `additionalProperties=false`。
- 发布阶段静态校验动作、必填、未知字段和类型；运行时模板解析后再次校验实际类型。
- `${var}` 在静态校验中允许作为待解析占位符；如果步骤声明依赖但没有生产者，发布失败。

### 5. 动作目录

新结构化目录只包含以下动作：

| Executor | Actions |
|---|---|
| `web` | `goto`、`click`、`fill`、`press`、`select_option`、`check`、`uncheck`、`wait_for`、`wait_for_response`、`upload`、`download`、`extract_text`、`extract_table`、`extract_fields`、`collect_links`、`paginate`、`screenshot`、`assert_text`、`assert_logged_in` |
| `transform` | `filter`、`map`、`cast`、`sort`、`deduplicate`、`aggregate`、`join`、`flatten`、`limit`、`validate` |
| `files` | `write_csv`、`write_xlsx`、`save_artifact`、`copy_file`、`zip` |
| `email` | `send` |
| `windows` | `launch_app`、`activate_window`、`click_element`、`click_relative`、`type_text`、`press_hotkey`、`wait`、`screenshot`、`read_text`、`read_window_text`、`close_app` |
| `mcp` | `call_tool` |

兼容别名只对旧版本可见：

- `navigate -> web.goto`
- `save_file -> files.save_artifact`
- 旧 transform 名称 `split_lines/trim_lines/remove_blank_lines/filter_contains/map_fields/merge_rows/count_chars/aggregate_sum/sort_by` 映射到新 Data action 或 legacy handler。
- `code_step`、`fetch_kugou_lyrics`、`create_document`、`create_spreadsheet`、`insert_image` 不进入新目录。

目录版本：`2026-10-28.1`。旧版本执行时使用其保存的 `action_catalog_version`；动作被移除时预检返回 `blocked`，提示迁移，不静默执行替代代码。

### 6. 变量、条件、循环和验证

- 变量名只允许 `[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*`。
- 完整占位符 `${transactions.rows}` 保留原类型；嵌入字符串时转成文本。
- 未知变量、循环引用、前向引用和没有生产者的引用在发布阶段失败。
- 条件使用声明式对象，不使用 `eval`、Python `exec` 或 JavaScript 表达式。
- `all/any/not` 可嵌套；空 `all` 为 false，空 `any` 为 false，`not` 必须恰有一个子条件。
- 循环必须有 `max_iterations`、`max_steps` 或执行策略上限；默认 `max_iterations=100`、`max_steps=1000`。
- 列表类动作默认 `allow_empty=false`；空结果返回 `failed` 且分类 `data_empty`。
- 明确 `allow_empty=true` 时才允许 `SUCCESS`；`empty_policy=warning` 可返回 `WARNING`，界面必须显示。
- `result_var` 在步骤成功或 warning 后写入；随后执行 `postconditions/verify`。
- 文件产物必须返回 `path/name/size/sha256/delivered/delivered_path`。

### 7. 前端状态分工

- TanStack Query 只管理服务端状态：catalog、workflow、versions、executions、human tasks、settings、hosts。
- Zustand `useWorkflowStudioStore` 只管理编辑器状态：当前 workflow id、base version、draft、dirty、selection、undo/redo、底部面板和检查器布局。
- 保存成功后用服务端草稿覆盖 base，不覆盖之后产生的本地编辑；保存请求携带 `draft_revision`，冲突返回 `409`。
- WebSocket 事件只用于使 Query key 失效或增量写入 execution cache，不直接修改 Studio 草稿。
- 动态动作表单完全由 `ActionDefinition.params_schema` 驱动。只有通用控件适配器可以手写，每个具体动作不得手写表单分支。

---

## 分阶段实施顺序

依赖关系：

```text
Stage 0 基线保护
  -> Stage A 统一执行主链
  -> Stage B Web/Data/Files/Email
  -> Stage C Workflow Studio 与个人工作台
  -> Stage D 人工接管与恢复
  -> Stage E 视觉兜底
  -> Stage F 本机/VM 主机路由
  -> Stage G 总体验收与打包验证
```

Stage C 的基础版本可以先使用现有 `awaiting_user` 状态展示；待处理中心的完整交互依赖 Stage D。视觉和 VM 不得提前侵入统一执行主链。

## Stage 0：基线与兼容保护

### Task 0.1：固化当前基线和 legacy 样例

**Goal:** 在改动数据模型和行为前，把旧代码、旧结构化计划、旧桌面计划和旧 transform 的当前语义固化为回归测试。

**Dependencies:** 无。

**Files:**

- Create: `tests/fixtures/legacy/code_workflow.json`
- Create: `tests/fixtures/legacy/structured_web_workflow.json`
- Create: `tests/fixtures/legacy/desktop_workflow.json`
- Create: `tests/fixtures/legacy/transform_workflow.json`
- Create: `tests/test_legacy_compatibility.py`
- Create: `pytest.ini`
- Modify: `docs/superpowers/plans/2026-10-28-personal-generic-rpa-implementation.md` only if baseline evidence changes before implementation starts

**Interfaces:**

- Consumes: 当前 `Workflow.generated_code`、`Workflow.structured_plan`、`ExecutionManager.start_workflow()`。
- Produces: `test_legacy_code_workflow_still_runs_from_snapshot()`、`test_legacy_structured_plan_keeps_aliases()`、`test_legacy_desktop_action_is_not_in_new_catalog()`。

- [ ] **Step 1: 写失败测试。** 在 `tests/test_legacy_compatibility.py` 断言：旧 code workflow 的 mock engine 被调用；`navigate` 仍可解析；旧 transform 名称仍可运行；新 catalog 不包含 `code_step/fetch_kugou_lyrics/create_document/create_spreadsheet/insert_image`。

```python
def test_new_catalog_excludes_specialized_and_script_actions() -> None:
    catalog = get_action_catalog(include_legacy=False)
    ids = {(item.executor, item.id) for item in catalog.actions}
    for forbidden in (
        ("web", "code_step"),
        ("windows", "fetch_kugou_lyrics"),
        ("windows", "create_document"),
        ("windows", "create_spreadsheet"),
        ("windows", "insert_image"),
    ):
        assert forbidden not in ids
```

- [ ] **Step 2: 运行并确认失败。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_legacy_compatibility.py -q
```

Expected: FAIL，因为统一 catalog 尚不存在。

- [ ] **Step 3: 添加 pytest marker 和基线夹具。** 在 `pytest.ini` 注册 `desktop`、`browser`、`smtp`、`visual`、`vm`、`e2e`、`postgres`；夹具只保存最小 payload，不保存凭据。

- [ ] **Step 4: 再次运行基线。**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: 后端原有 `738 passed` 不减少；新增用例按当前阶段预期失败；前端 63 项仍通过，构建成功。

**Acceptance:** 旧行为有独立夹具；后续删除或隔离专用 action 时，能明确指出旧兼容测试和通用性测试的边界。

## Stage A：统一执行主链

### Task A.1：新增版本、策略、待办、投递和连接数据模型

**Goal:** 先建立执行所需的持久化边界，再改动执行器。

**Dependencies:** Task 0.1。

**Files:**

- Create: `app/models/workflow_version.py`
- Create: `app/models/human_task.py`
- Create: `app/models/delivery_record.py`
- Create: `app/models/app_setting.py`
- Create: `app/models/browser_profile.py`
- Create: `app/schemas/workflow_version.py`
- Create: `app/schemas/execution_policy.py`
- Create: `app/schemas/human_task.py`
- Create: `app/schemas/delivery.py`
- Create: `app/schemas/settings.py`
- Create: `tests/test_workflow_versions.py`
- Create: `tests/test_execution_policy.py`
- Modify: `app/models/workflow.py`
- Modify: `app/models/execution.py`
- Modify: `app/models/agent.py`
- Modify: `app/models/credential.py`
- Modify: `app/models/__init__.py`
- Modify: `app/schemas/workflow.py`
- Modify: `app/migrations.py`
- Modify: `app/database.py`
- Modify: `scripts/migrate_db.py`
- Modify: `tests/test_migrations.py`

**Interfaces:**

- Consumes: 现有 `Base.metadata.create_all`、增量 `add_column/add_index` 迁移机制。
- Produces:
  - `WorkflowVersion(id, workflow_id, version, plan, validation, action_catalog_version, created_at)`
  - `HumanTask(id, execution_id, step_id, kind, instruction, payload, screenshot_path, status, created_at, resolved_at, expires_at)`
  - `DeliveryRecord(id, execution_id, step_id, channel, status, recipient, attachment_paths, provider_message_id, error, created_at, finished_at)`
  - `ExecutionPolicy` Pydantic 模型及 `default_execution_policy()`。
  - `SCHEMA_VERSION = 8`。

数据字段：

- `Workflow` 新增 `execution_mode`、`execution_policy`、`active_version_id`、`schedule_timezone`、`draft_revision`。
- `ExecutionLog` 新增 `workflow_version_id`、`execution_policy_snapshot`、`host_id`、`human_task_id`、`trigger_type`、`cancel_requested_at`。
- `DesktopAgent` 新增 `environment`、`last_capabilities_at`。
- `Credential` 扩展 `user_id`、`scope`、`name`，并把 `workflow_id` 改为可空；旧工作流凭据回填 `user_id` 和 `scope=workflow`。
- `AppSetting` 保存非敏感设置：SMTP 主机/端口/TLS/发件人、导出目录、视觉 provider、默认收件人。
- `BrowserProfile` 保存自动化 Profile 名称、用户数据目录、Profile 目录、登录状态和最近验证时间。

- [ ] **Step 1: 写迁移测试。** 测试从当前 v5 schema 升级到 v8 后：

```python
assert "execution_mode" in workflow_columns
assert "active_version_id" in workflow_columns
assert "workflow_version_id" in execution_columns
assert "environment" in agent_columns
assert "user_id" in credential_columns
```

并断言二次运行迁移返回 `[]`。

- [ ] **Step 2: 写回填测试。** 构造 `generated_code` 非空、仅 plan、空草稿三类旧工作流；断言前两类各生成一个 `version=1`，第一类为 `legacy_code`，第二类为 `structured`，空草稿不产生可运行版本。

- [ ] **Step 3: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py tests/test_workflow_versions.py tests/test_execution_policy.py -q
```

Expected: FAIL，因为模型和迁移尚未实现。

- [ ] **Step 4: 实现迁移 v6-v8。**

```text
v6: workflow execution_mode/execution_policy/active_version_id/schedule_timezone/draft_revision；创建 workflow_version。
v7: execution_log 版本/策略/host/human/trigger 字段；创建 human_task、delivery_record。
v8: agent environment/capabilities 时间；credential 用户/范围；创建 app_setting、browser_profile。
```

所有新增列必须先可空或有服务端默认值；SQLite 和 PostgreSQL 共用现有迁移机制。迁移前 CLI 使用现有备份能力创建数据库备份；失败自动终止。

- [ ] **Step 5: 实现回填。** 用 SQL 读取旧行，在 Python 中构造版本 plan，使用参数化插入；不得把凭据或生成代码写入日志。

- [ ] **Step 6: 运行迁移和测试。**

```powershell
.\.venv\Scripts\python.exe scripts\migrate_db.py --status
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py tests/test_workflow_versions.py tests/test_execution_policy.py -q
```

Expected: schema version 8；迁移测试全部通过；重复执行不产生新版本。

**Migration/rollback:** 新增表/列保持向前加法兼容。应用回滚到旧版本时，旧代码可忽略新增列；数据回滚只能从迁移前备份恢复。计划实施时不得对生产 `rpa.db` 做无备份迁移。

### Task A.2：WorkflowVersion 服务和发布/回滚 API

**Goal:** 让所有执行都有不可变版本，并让发布、版本列表、差异和回滚不依赖草稿。

**Dependencies:** Task A.1。

**Files:**

- Create: `app/services/__init__.py`
- Create: `app/services/workflow_versions.py`
- Create: `tests/test_workflow_version_api.py`
- Modify: `app/api/workflows.py`
- Modify: `app/schemas/workflow.py`
- Modify: `app/models/workflow.py`
- Modify: `tests/test_api_4a.py`
- Modify: `tests/test_api_4b.py`

**Interfaces:**

- Consumes: `WorkflowVersion`、`ExecutionPolicy`、统一校验器 `validate_workflow_draft()`（Task A.3 完成后注入）。
- Produces:
  - `WorkflowVersionService.publish(workflow_id, user_id) -> WorkflowVersion`
  - `WorkflowVersionService.rollback(workflow_id, user_id, version_id) -> WorkflowVersion`
  - `WorkflowVersionService.list(workflow_id, user_id) -> list[WorkflowVersion]`
  - `WorkflowVersionService.diff(from_version_id, to_version_id) -> WorkflowVersionDiff`

API：

```text
POST /api/workflows/{id}/publish
GET  /api/workflows/{id}/versions
POST /api/workflows/{id}/rollback   body: {"version_id": 123}
```

发布响应：

```json
{
  "workflow_id": 12,
  "active_version_id": 101,
  "version": 3,
  "action_catalog_version": "2026-10-28.1"
}
```

- [ ] **Step 1: 写 API 测试。** 断言发布两次生成 version 1/2；修改草稿不改变 version 1 的 plan；rollback 只改变 `active_version_id`；其他用户的 workflow 返回 404。

- [ ] **Step 2: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_version_api.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现事务发布。** 在同一 `AsyncSession` 内完成校验、版本号计算、插入版本、更新 active pointer；任何异常 rollback。

- [ ] **Step 4: 实现回滚和差异。** 差异只比较版本 plan 的步骤增删改、变量和策略字段；API 返回结构化数组，不返回 HTML。

- [ ] **Step 5: 兼容旧响应。** 当前前端只依赖 `status/workflow_id`；新响应保留这些字段，新增版本信息，避免旧页面立即断裂。

- [ ] **Step 6: 运行测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_version_api.py tests/test_api_4a.py tests/test_api_4b.py -q
```

Expected: PASS。

**Acceptance:** 版本不可变、发布幂等边界明确、回滚不复制/改写历史数据。

### Task A.3：统一 ActionDefinition、catalog、params_schema 和 StepResult

**Goal:** 建立前后端、后端 registry、桌面 Agent 和执行器共享的动作协议。

**Dependencies:** 无数据结构依赖；可在 A.1 同时并行开发，但合并前需与 A.2 的发布校验对接。

**Files:**

- Create: `app/actions/__init__.py`
- Create: `app/actions/protocol.py`
- Create: `app/actions/catalog.py`
- Create: `app/actions/registry.py`
- Create: `app/actions/validation.py`
- Create: `app/actions/adapters.py`
- Create: `app/actions/legacy.py`
- Create: `tests/test_action_protocol.py`
- Create: `tests/test_action_registry.py`
- Create: `tests/test_action_catalog_contract.py`
- Modify: `app/protocols/structured.py`
- Modify: `app/agents/web_catalog.py`
- Modify: `app/agents/desktop_catalog.py`
- Modify: `app/agents/plan_summary.py`
- Modify: `app/engine/task_runtime.py`
- Modify: `app/engine/hybrid_executor.py`

**Interfaces:**

- Consumes: 现有 Web/Desktop/Transform/MCP handler 名称和失败分类。
- Produces:
  - `ActionCatalog.version: str`
  - `ActionCatalog.actions: list[ActionDefinition]`
  - `ActionRegistry.register(definition, handler, *, aliases=(), legacy_only=False)`
  - `ActionRegistry.validate(executor, action, params, *, resolve_variables=False) -> ValidationResult`
  - `ActionRegistry.catalog(include_legacy=False)`
  - `StepResult`、`StepStatus`、`StepError`、`HumanActionRequest`、`ArtifactRecord`。
  - `coerce_legacy_step_result(value) -> StepResult`

`ActionDefinition.params_schema` 示例：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["values", "field", "operator", "value"],
  "properties": {
    "values": {"type": ["array", "string"]},
    "field": {"type": "string", "minLength": 1},
    "operator": {
      "type": "string",
      "enum": ["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"]
    },
    "value": {},
    "cast": {
      "type": "string",
      "enum": ["string", "number", "integer", "boolean", "date"]
    },
    "allow_empty": {"type": "boolean", "default": false}
  }
}
```

- [ ] **Step 1: 写协议测试。** 断言五种 StepStatus 可序列化；未知状态 coerce 为 failed；legacy success bool 可转换；`human_action`、`verification` 和 artifacts 保留。

- [ ] **Step 2: 写 registry 契约测试。** 每个 catalog entry 必须恰有运行时 handler；handler ID 不得重复；每个 schema 必须是 object 且 `additionalProperties=false`；新 catalog 不含 legacy-only action。

- [ ] **Step 3: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_action_protocol.py tests/test_action_registry.py tests/test_action_catalog_contract.py -q
```

Expected: FAIL。

- [ ] **Step 4: 实现 protocol、registry 和 JSON Schema 校验。** 静态校验允许 `${...}` 占位；运行时解析后使用完整 schema 检查，未知参数一律失败。

- [ ] **Step 5: 迁移现有 catalog facade。** `app/agents/web_catalog.py` 和 `desktop_catalog.py` 改为从统一 registry 读取新动作；旧 planner 文本由 `catalog_text(executor=...)` 生成。

- [ ] **Step 6: 适配 TaskRuntime。** `TaskRuntime` 接收 `ActionRegistry`，不再按 executor 内部分支直接执行；保留旧 dict result 适配。

- [ ] **Step 7: 运行 focused tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_action_protocol.py tests/test_action_registry.py tests/test_action_catalog_contract.py tests/test_task_runtime.py tests/test_web_catalog.py tests/test_desktop_catalog.py -q
```

Expected: PASS。

**Acceptance:** 任意新增动作只需注册定义和 handler，不需要新增后端 if/else 或前端专用表单。

### Task A.4：版本固定执行、预检和单工作流串行

**Goal:** 执行只读版本，不读草稿；预检在任何步骤运行前完成；同一工作流默认不并发。

**Dependencies:** Task A.2、A.3。

**Files:**

- Create: `app/engine/preflight.py`
- Create: `app/services/execution_versions.py`
- Create: `tests/test_preflight.py`
- Create: `tests/test_single_itinerary.py`
- Modify: `app/engine/execution_manager.py`
- Modify: `app/engine/engine.py`
- Modify: `app/engine/router.py`
- Modify: `app/api/executions.py`
- Modify: `app/scheduler/jobs.py`
- Modify: `app/schemas/workflow.py`

**Interfaces:**

- Consumes: `Workflow.active_version_id`、`WorkflowVersion`、`ExecutionPolicy`、`ActionRegistry`。
- Produces:
  - `ExecutionSnapshot(workflow_id, version_id, version, plan, policy, action_catalog_version)`
  - `PreflightService.check(snapshot, context) -> PreflightResult`
  - `PreflightResult.status in {"ready","awaiting_user","blocked"}`
  - `ExecutionManager.start_workflow(..., version_id: int | None = None)`；未传时解析 active version。

- [ ] **Step 1: 写单一行程测试。** 启动后编辑草稿，断言当前执行仍使用旧 version plan；再次启动只读最新 active version。

- [ ] **Step 2: 写预检测试。** 缺失凭据、不可写导出目录、离线 Agent、无效时区、版本 action 不存在均返回 `blocked`；登录态失效且 `human_handoff=true` 返回 `awaiting_user`。

- [ ] **Step 3: 写并发测试。** 同一 workflow 已有 `running/pending/awaiting_user` 时新运行返回 409；不同 workflow 可同时运行。

- [ ] **Step 4: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_preflight.py tests/test_single_itinerary.py tests/test_execution_manager.py -q
```

Expected: FAIL。

- [ ] **Step 5: 实现版本解析和策略快照。** 写入 `ExecutionLog.workflow_version_id`、`execution_policy_snapshot`；执行上下文只传 snapshot。

- [ ] **Step 6: 实现预检。** 输出逐项 checks 和稳定错误码；预检 blocked 不调用执行器；awaiting_user 在 Stage D 创建 HumanTask。

- [ ] **Step 7: 实现单工作流锁。** 在事务内查 active run 并创建 ExecutionLog，避免两个请求同时通过；Cron 冲突记录 skipped/coalesced，不创建重复运行。

- [ ] **Step 8: 运行测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_preflight.py tests/test_single_itinerary.py tests/test_execution_manager.py tests/test_scheduler.py -q
```

Expected: PASS。

**Acceptance:** 一次运行只有一个版本、一个策略快照和一个主执行实例。

### Task A.5：legacy_code 隔离和结构化 fail-closed 切换

**Goal:** 保留旧代码执行，同时阻止任何新结构化流程静默回退代码。

**Dependencies:** Task A.4。

**Files:**

- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `app/engine/engine.py`
- Modify: `app/engine/hybrid_executor.py`
- Modify: `app/agents/web_catalog.py`
- Modify: `app/api/workflows.py`
- Modify: `tests/test_engine.py`
- Modify: `tests/test_legacy_compatibility.py`
- Modify: `tests/test_api_4b.py`

**Interfaces:**

- Consumes: snapshot `execution_mode` 和 version plan。
- Produces:
  - `ExecutionEngine.execute_snapshot(snapshot, context)`
  - `LEGACY_CODE_EXECUTION_ENABLED=true` 默认值。
  - 结构化发布/执行对 `code`、`code_step`、未知动作返回 `unsupported_action`，不得降级。

- [ ] **Step 1: 写 fail-closed 测试。** `execution_mode=structured` 且包含 `code_step`、未知参数或未知 executor 时，运行失败且 mock code engine 未调用。

- [ ] **Step 2: 写 legacy 测试。** `execution_mode=legacy_code` 且开关开启时运行快照中的代码；开关关闭时返回 blocked。

- [ ] **Step 3: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_legacy_compatibility.py tests/test_api_4b.py -q
```

Expected: FAIL。

- [ ] **Step 4: 删除结构化 fallback 分支。** `uses_structured_web()` 不再以“计划不合法”为由返回 False；由 execution_mode 和版本校验决定路径。

- [ ] **Step 5: 冻结 legacy action。** 旧版本仍可查 handler；发布新版本时 registry `include_legacy=False`。

- [ ] **Step 6: 运行测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_legacy_compatibility.py tests/test_api_4b.py tests/test_hybrid_executor.py -q
```

Expected: PASS。

**Stage A Acceptance:**

- 新 workflow 创建后 `execution_mode=structured`。
- 发布生成不可变版本，执行记录引用该版本。
- 发布后修改草稿不影响运行。
- 旧工作流可通过 legacy 快照继续运行。
- 任何新结构化计划含脚本或未知动作时 fail closed。

## Stage B：通用 Web、Data、Files、Email 能力

### Task B.1：变量、条件、循环和空结果语义

**Goal:** 将现有 runtime 能力搬入统一 action context，并统一空数组、结果变量和验证语义。

**Dependencies:** Task A.3、A.4。

**Files:**

- Create: `app/actions/variables.py`
- Create: `app/actions/conditions.py`
- Create: `app/actions/runtime.py`
- Create: `tests/test_action_variables.py`
- Create: `tests/test_empty_results.py`
- Modify: `app/engine/task_runtime.py`
- Modify: `app/engine/dag.py`
- Modify: `app/actions/protocol.py`
- Modify: `tests/test_task_runtime.py`
- Modify: `tests/test_dag.py`

**Interfaces:**

- Consumes: 现有 `resolve_template`、`evaluate_condition`、`RuntimeState`。
- Produces:
  - `VariableScope.get/set/resolve`
  - `ConditionSpec` 支持 `all/any/not` 和全部比较操作。
  - `apply_empty_policy(value, allow_empty, empty_policy)`。
  - `ActionContext(workflow, version, execution_id, variables, artifacts, registry, services)`。

- [x] **Step 1: 写测试。** 覆盖完整占位符保留数组、嵌套路径、未知变量失败、`gt/cast=number`、`in/not_in/matches`、嵌套 `all/any/not`、空数组默认失败、`allow_empty=true` 成功。

- [x] **Step 2: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_action_variables.py tests/test_empty_results.py tests/test_task_runtime.py tests/test_dag.py -q
```

Expected: 新测试 FAIL，旧能力测试继续 PASS。

- [x] **Step 3: 抽离无副作用变量和条件模块。** 不改变现有 JSON 字段；旧 dict 条件通过适配器转换。

- [x] **Step 4: 统一循环预算。** 循环中的每一步仍经过 registry 校验；超预算返回 `business_validation_failed` 或 `timeout`，保留已完成聚合值。

- [x] **Step 5: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_action_variables.py tests/test_empty_results.py tests/test_task_runtime.py tests/test_dag.py tests/test_structured_protocol.py -q
```

Expected: PASS。

### Task B.2：Data 动作

**Goal:** 完成设计中的十个纯数据动作，并保留现有 transform 名称的 legacy alias。

**Dependencies:** Task B.1。

**Files:**

- Create: `app/actions/data.py`
- Create: `tests/test_data_actions.py`
- Modify: `app/actions/catalog.py`
- Modify: `app/actions/registry.py`
- Modify: `app/engine/transforms.py`
- Modify: `tests/test_transforms.py`

**Interfaces:**

- Consumes: `VariableScope`、`ActionContext`。
- Produces handlers：`filter`、`map`、`cast`、`sort`、`deduplicate`、`aggregate`、`join`、`flatten`、`limit`、`validate`。

参数最低契约：

```text
filter: values, field?, operator, value, cast?, allow_empty
map: values, mapping|fields, strict
cast: values, target, field?, on_error=error|skip|null
sort: values, field?, order=asc|desc, missing=last|first
deduplicate: values, keys?, keep=first|last
aggregate: values, operation=count|sum|min|max|avg, field?, group_by?
join: left, right, left_key, right_key, how=inner|left
flatten: values, field?, depth
limit: values, count, offset
validate: values, required_fields?, min_rows?, unique_by?, allow_empty
```

- [x] **Step 1: 写测试。** 为每个 action 至少覆盖正常、空数组、类型错误、字段缺失和 `allow_empty`；断言输出顺序稳定。

- [x] **Step 2: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_data_actions.py -q
```

Expected: FAIL。

- [x] **Step 3: 实现纯函数 handler。** 不执行表达式、不导入 pandas、不修改输入列表。

- [x] **Step 4: 接入 registry。** 每个 action 返回 `StepResult.value`；`result_var` 由 runtime 写入；`validate` 失败返回 `business_validation_failed`。

- [x] **Step 5: 加入 legacy alias。** 旧 `split_lines/count_chars` 等继续可运行，但不出现在新 catalog。

- [x] **Step 6: 运行测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_data_actions.py tests/test_transforms.py tests/test_task_runtime.py -q
```

Expected: PASS。

### Task B.3：Files 动作和独立产物验证

**Goal:** 在服务器侧生成、复制和归档文件，不把 Files 步骤错误路由给桌面 Agent。

**Dependencies:** Task B.1。

**Files:**

- Create: `app/actions/files.py`
- Create: `app/actions/xlsx_minimal.py`
- Create: `tests/test_file_actions.py`
- Create: `tests/test_xlsx_compat.py`
- Modify: `app/actions/catalog.py`
- Modify: `app/engine/artifacts.py`
- Modify: `app/engine/hybrid_executor.py`
- Modify: `app/config.py`
- Modify: `.env.example`

**Interfaces:**

- Consumes: 沙箱内 artifact 目录、`WEB_EXPORT_DIRS`、Agent `allowed_doc_dirs`。
- Produces handlers：
  - `write_csv(path, rows, headers?, encoding="utf-8-sig")`
  - `write_xlsx(path, rows, sheet_name, headers?)`
  - `save_artifact(path, content|source_path, variable?)`
  - `copy_file(source, destination)`
  - `zip(destination, sources, base_dir?)`
- 所有 handler 返回 `ArtifactRecord(path,name,size,sha256,delivered=false,delivered_path=null)`。

最低可行 XLSX 取舍：

- 使用标准库 `zipfile` 和 OOXML XML 生成单 sheet、值型单元格。
- 不实现公式、样式、合并单元格、图片和宏。
- 必须能被 Excel/WPS 打开；测试用 ZIP/XML 结构和行数验证。
- 若未来确有公式需求，另行评估 `openpyxl`，本轮不增加第三方依赖。

- [x] **Step 1: 写测试。** CSV 含 BOM 并可解析；XLSX 是合法 ZIP 且含 `[Content_Types].xml`、`xl/workbook.xml`、sheet 数据；空文件、0 行、缺列和无权限路径失败。

- [x] **Step 2: 写路由测试。** `executor=files` 不得调用 desktop dispatcher；local artifact 目录内允许写；白名单外路径返回 `permission_denied`。

- [x] **Step 3: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_file_actions.py tests/test_xlsx_compat.py tests/test_hybrid_executor.py -q
```

Expected: FAIL。

- [x] **Step 4: 实现原子写。** 先写同目录临时文件，flush + fsync，再 `os.replace`；异常清理临时文件。

- [x] **Step 5: 实现验证。** 检查 exists、size>0、sha256 可重算、CSV/XLSX 行列数与输入一致；失败分类 `artifact_missing`。

- [x] **Step 6: 运行测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_file_actions.py tests/test_xlsx_compat.py tests/test_artifacts.py tests/test_hybrid_executor.py -q
```

Expected: PASS。

### Task B.4：Web 动作统一和旧别名兼容

**Goal:** 把现有 WebExecutor 完整接入 ActionRegistry，并阻止新 catalog 暴露 `code_step`。

**Dependencies:** Task A.3、B.1、B.3。

**Files:**

- Modify: `app/actions/catalog.py`
- Modify: `app/actions/adapters.py`
- Modify: `app/actions/registry.py`
- Modify: `app/engine/web_executor.py`
- Modify: `app/engine/browser_observer.py`
- Modify: `app/agents/web_catalog.py`
- Modify: `app/agents/web_planner.py`
- Modify: `tests/test_web_executor.py`
- Modify: `tests/test_web_catalog.py`
- Modify: `tests/test_web_structured_chat.py`

**Interfaces:**

- Consumes: 现有 `WEB_ACTIONS`、`WebExecutor.execute_steps()`、`LocatorSpec`。
- Produces: `WebActionAdapter`，把 `ActionContext` 转成单步 WebExecutor 调用，并返回新 `StepResult`。

- [x] **Step 1: 写兼容测试。** 所有设计 Web action 存在且参数 schema 可校验；`navigate` 仍解析为 `goto`；`code_step` 只在 include_legacy 时存在。

- [x] **Step 2: 写结果测试。** 提取成功返回 variables；空表默认 failed；`allow_empty=true` 可 success/warning；下载 action 返回 artifact metadata；`assert_logged_in` 返回 `awaiting_user`，不是普通 failed。

- [x] **Step 3: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_catalog.py tests/test_web_executor.py tests/test_web_structured_chat.py -q
```

Expected: 新 contract tests FAIL；真实浏览器项若环境不可用应 skip 并给原因。

- [x] **Step 4: 实现 adapter。** 单步失败分类通过统一 `StepError`；截图等诊断失败作为 warning，不掩盖业务失败。

- [x] **Step 5: 实现域名和动作风险预检。** 有抓取动作的计划必须有 `crawl_policy.allowed_domains/max_pages/max_items`；裸 `goto` 也必须在域名白名单内或显式允许。

- [x] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_catalog.py tests/test_web_executor.py tests/test_web_structured_chat.py tests/test_browser_observer.py -q
```

Expected: PASS；无浏览器环境相关项 skip。

### Task B.5：Windows 与 MCP 通用动作注册

**Goal:** 新目录只暴露设计中的通用 Windows 动作和 MCP `call_tool`，专用历史动作保持冻结。

**Dependencies:** Task A.3。

**Files:**

- Create: `app/actions/windows.py`
- Create: `app/actions/mcp.py`
- Modify: `app/actions/catalog.py`
- Modify: `app/actions/adapters.py`
- Modify: `app/agents/desktop_catalog.py`
- Modify: `app/engine/hybrid_executor.py`
- Modify: `desktop_agent/action_validator.py`
- Modify: `desktop_agent/actions/__init__.py`
- Modify: `tests/test_desktop_catalog.py`
- Modify: `tests/test_agent_validator.py`
- Modify: `tests/test_mcp_integration.py`

**Interfaces:**

- Consumes: `DesktopDispatcher.dispatch()`、`MCPRegistry.call_tool()`。
- Produces:
  - `WindowsActionAdapter.execute(context, invocation) -> StepResult`
  - `MCPActionAdapter.execute(context, invocation) -> StepResult`
  - 新 catalog Windows 白名单严格等于设计清单。

- [x] **Step 1: 写 test。** 新 catalog 只含通用 Windows 动作；legacy-only 专用动作不在新 catalog；MCP 只允许 `call_tool` 且 server/tool 必填。

- [x] **Step 2: 写参数校验 test。** Windows 动作未知参数、越权路径和未知能力失败；MCP 风险确认和审计仍生效。

- [x] **Step 3: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_catalog.py tests/test_agent_validator.py tests/test_mcp_integration.py -q
```

Expected: FAIL。

- [x] **Step 4: 实现 adapters。** 把 Agent `ActionResult` 转为统一 StepResult；保留 pid/window handle/screenshot/result data。

- [x] **Step 5: 标记 legacy-only。** 不删除专用 handler，只在 registry metadata 标记并从新 catalog 过滤。

- [x] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_desktop_catalog.py tests/test_agent_validator.py tests/test_mcp_integration.py tests/test_mcp_results.py -q
```

Expected: PASS。

### Task B.6：SMTP 设置、Email send、DeliveryRecord 和测试发送

**Goal:** 建立独立可验证的通用邮件能力，不再使用最佳努力通知冒充业务投递。

**Dependencies:** Task A.1、A.3、B.3。

**Files:**

- Create: `app/services/email.py`
- Create: `app/api/settings.py`
- Create: `app/actions/email.py`
- Create: `tests/support/fake_smtp.py`
- Create: `tests/test_email_service.py`
- Create: `tests/test_email_api.py`
- Create: `tests/test_delivery_records.py`
- Modify: `app/actions/catalog.py`
- Modify: `app/notifications/notifier.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`
- Modify: `app/models/delivery_record.py`

**Interfaces:**

- Consumes: `aiosmtplib`、`Credential`、`AppSetting`、`DeliveryRecord`。
- Produces:
  - `EmailService.send(message: EmailMessage, credential_key: str) -> DeliveryResult`
  - `EmailService.test_connection() -> DeliveryResult`
  - `EmailService.send_test(recipient: str) -> DeliveryResult`
  - `EmailActionAdapter`
  - Delivery status：`pending | success | failed | warning | cancelled`。

API：

```text
GET  /api/settings/email
PUT  /api/settings/email
POST /api/settings/email/test
POST /api/settings/email/test-send
```

`PUT` 不返回明文密码；读取时返回 `password_configured: true|false`。

- [x] **Step 1: 写本地 SMTP contract test。** fake SMTP 记录 envelope、MIME、Message-ID 和附件 sha256；断言成功返回 provider_message_id。

- [x] **Step 2: 写失败测试。** 无配置、认证失败、收件人非法、附件缺失、附件为空、连接超时分别写入 failed DeliveryRecord；`required_delivery=true` 时执行最终 status=failed。

- [x] **Step 3: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_email_service.py tests/test_email_api.py tests/test_delivery_records.py -q
```

Expected: FAIL。

- [x] **Step 4: 实现 MIME 构造。** 支持 To/Cc/Bcc、plain/HTML 二选一、附件列表、中文主题；禁止路径越界和缺失附件。

- [x] **Step 5: 实现投递记录状态机。** 发送前 pending，成功后 success 和 message-id，失败后 finished_at/error；重试复用一条记录的 attempt count（保存在 payload/validation JSON 或扩展列，按 A.1 最终 schema）。

- [x] **Step 6: 重构 NotificationService。** 内部复用 EmailService；通知仍是可选 warning，不创建 workflow 的 required delivery，除非 workflow 显式配置。

- [x] **Step 7: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_email_service.py tests/test_email_api.py tests/test_delivery_records.py tests/test_notifier.py tests/test_metrics_alerts.py -q
```

Expected: PASS；不访问外网。

**Stage B status: COMPLETE (2026-10-28).**

| Task | Status | Verification |
|---|---|---|
| B.1 variables, conditions, loops, empty results | Complete | Focused runtime/DAG tests passed |
| B.2 Data actions | Complete | 42 focused tests passed; catalog and dynamic form wired |
| B.3 Files actions | Complete | 29 focused tests passed; CSV/XLSX/ZIP, overwrite, and path guards verified |
| B.4 Web action unification | Complete | 58 passed, 3 environment skips |
| B.5 Windows/MCP registration | Complete | 51 focused tests passed; legacy-only actions isolated |
| B.6 SMTP and Email send | Complete | 114 consolidated Stage B focused tests passed; fake SMTP, API, DeliveryRecord, retry, HybridExecutor, and frontend verified |

**Stage B final verification:** backend `832 passed, 23 skipped`; frontend
`66 passed`; production build passed; temporary SQLite migration reached
schema 8 and the repeat run executed no migrations.

**Stage B Acceptance:**

- 不修改前后端业务分支即可创建包含提取、filter、write_xlsx、send email 的工作流。
- 空数据、缺失文件、附件不存在、SMTP 失败都不会标记 success。
- 每封 required email 有 DeliveryRecord 和独立验证。

## Stage C：个人工作台与 Workflow Studio

### Task C.0：工作流创建、观察和统一校验 API

**Goal:** 为自然语言、页面观察、录制导入和空白工作流提供统一草稿入口；LLM 只在这里生成或修复草稿，不接触运行时。

**Dependencies:** Task A.3、B.1；现有 `app/agents`、`app/engine/browser_observer.py`、`app/engine/recorder.py`。

**Files:**

- Create: `app/services/workflow_authoring.py`
- Create: `tests/test_workflow_authoring_api.py`
- Create: `tests/test_workflow_validation_api.py`
- Modify: `app/api/workflows.py`
- Modify: `app/api/chat.py`
- Modify: `app/api/observations.py`
- Modify: `app/api/recordings.py`
- Modify: `app/agents/web_planner.py`
- Modify: `app/agents/desktop_planner.py`
- Modify: `app/agents/plan_summary.py`
- Modify: `app/schemas/workflow.py`

**Interfaces:**

- Consumes: 现有 intent parser、Web/Desktop planner、BrowserObserver、Recorder、`ActionRegistry`。
- Produces:
  - `WorkflowAuthoringService.draft_from_goal(goal, start_url, session_id) -> DraftResult`
  - `WorkflowAuthoringService.observe(workflow_id, url, mode) -> ObservationSnapshot`
  - `WorkflowAuthoringService.validate(plan, policy) -> WorkflowValidationResult`
  - `DraftResult(plan, observations, warnings, needs_confirmation)`，不创建版本、不发布。

API：

```text
POST /api/workflows/draft-from-goal
POST /api/workflows/{id}/observe
POST /api/workflows/{id}/validate
```

创建入口：

```text
natural_language：调用 LLM 只生成结构化草稿
observe：浏览器观察后由 planner 生成有限候选
recording：导入现有 recording draft 并转换为 catalog 动作
blank：创建空 structured plan
```

- [ ] **Step 1: 写 API 测试。** 自然语言入口返回结构化草稿但不创建 WorkflowVersion；recording 入口保留 locator 和 dependencies；observe 返回真实候选；未知 action/参数由 validate 返回语义化的 `valid=false`。

- [ ] **Step 2: 写边界测试。** draft service 可以导入 `app.agents`，但该模块不被 `app/actions`、`app/engine` 或 `desktop_agent` 导入；任何返回 code/code_step 的 LLM 输出都被拒绝。

- [ ] **Step 3: 运行失败测试。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_authoring_api.py tests/test_workflow_validation_api.py -q
```

Expected: FAIL。

- [ ] **Step 4: 实现服务。** 结束前统一走 A.3 registry；观察元素只能引用实际候选；录制导入不能生成任意脚本。

- [ ] **Step 5: 接入现有 chat/observation/recording。** 保留旧响应字段；新服务是唯一校验入口。

- [ ] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_workflow_authoring_api.py tests/test_workflow_validation_api.py tests/test_web_structured_chat.py tests/test_recordings_api.py tests/test_observations_api.py -q
```

Expected: PASS。

### Task C.1：TanStack Query、路由和导航骨架

**Goal:** 把前端从聊天优先页面切成个人自动化工作台，并把服务器状态统一交给 Query。

**Dependencies:** Stage A API 稳定；可先用 mock API 开发。

**Files:**

- Modify: `frontend/package.json`
- Create: `frontend/src/app/queryClient.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/api/queryKeys.ts`
- Create: `frontend/src/api/actionCatalog.ts`
- Create: `frontend/src/lib/lazy-page.tsx`
- Create: `frontend/src/pages/DashboardPage.tsx`
- Create: `frontend/src/pages/PendingPage.tsx`
- Create: `frontend/src/pages/ConnectionsPage.tsx`
- Create: `frontend/src/pages/AdvancedToolsPage.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/tests/workflow-delete-confirmation.test.mjs`

**Interfaces:**

- Consumes: `axios` instance、现有 auth token/workspace interceptor。
- Produces: QueryClientProvider、lazy route map、导航 items。

路由：

```text
/                         工作台
/workflows                工作流列表
/workflows/:id/studio     Workflow Studio
/executions               执行记录
/executions/:id           执行详情
/pending                  待处理
/connections              连接与凭据
/settings                 设置
/advanced/tools           高级工具
/advanced/chat            旧工作流助手
/apps                     现有应用页
```

- [ ] **Step 1: 写纯函数 tests。** 导航 active 判定、路由 fallback、Query key 稳定性。

- [ ] **Step 2: 运行失败 tests。**

```powershell
cd frontend
npm.cmd test
```

Expected: 新测试 FAIL。

- [ ] **Step 3: 安装并接入依赖。**

```powershell
cd frontend
npm.cmd install @tanstack/react-query
```

锁文件 `frontend/package-lock.json` 必须同步；不得手工编辑生成内容。

- [ ] **Step 4: 实现 QueryClient 与 lazy routes。** default retry=1、refetchOnWindowFocus=false、staleTime 按资源配置；Studio、ExecutionDetail、Chat 按需加载。

- [ ] **Step 5: 运行 tests/build。**

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS；build 输出按路由拆出 chunk。

### Task C.2：工作台和工作流列表

**Goal:** 用紧凑、工作型界面展示运行、等待、计划、失败和产物。

**Dependencies:** Task C.1；Stage A/B API。

**Files:**

- Create: `frontend/src/pages/DashboardPage.tsx`（若在 C.1 仅占位，则此处完善）
- Create: `frontend/src/components/dashboard/ActiveRunList.tsx`
- Create: `frontend/src/components/dashboard/PendingTaskList.tsx`
- Create: `frontend/src/components/dashboard/ArtifactList.tsx`
- Create: `frontend/src/components/workflows/WorkflowTable.tsx`
- Create: `frontend/src/components/workflows/WorkflowRow.tsx`
- Create: `frontend/src/lib/workflow-mode.ts`
- Create: `frontend/tests/workflow-mode.test.mjs`
- Modify: `frontend/src/pages/WorkflowsPage.tsx`
- Modify: `frontend/src/pages/ExecutionsPage.tsx`
- Delete: `frontend/src/components/workflows/WorkflowCard.tsx` only after all imports removed

**Interfaces:**

- Consumes: `/api/workflows`、`/api/executions`、`/api/human-tasks`（Stage D 完成后有数据）。
- Produces: 状态标签 `structured | legacy_code`；“旧代码模式”标记和迁移入口；行操作 Run/Edit/Duplicate/Disable。

- [ ] **Step 1: 写纯逻辑 test。** legacy workflow 显示“旧代码模式”，不得显示“结构化”；structured 显示 active version；unknown mode 标记 warning。

- [ ] **Step 2: 运行失败 tests。**

```powershell
cd frontend
npm.cmd test
```

Expected: 新测试 FAIL。

- [ ] **Step 3: 用 Query 替换组件内 fetch。** `useQuery(queryKeys.workflows)` 和 mutation 后 invalidate；去掉 `window.prompt`，复制使用内联表单/对话框。

- [ ] **Step 4: 实现紧凑列表。** 桌面 1280×800/1440×900 优先，表格列不换行；移动端退化为纵向行，但不隐藏关键状态。

- [ ] **Step 5: 运行 tests/build。**

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS。

### Task C.3：Studio Zustand、步骤大纲和动态属性检查器

**Goal:** 把现有单文件编辑器拆成服务端状态 + 编辑草稿 + 通用 schema form。

**Dependencies:** Task A.3 action catalog、Task C.1。

**Files:**

- Create: `frontend/src/store/useWorkflowStudioStore.ts`
- Create: `frontend/src/components/studio/StudioLayout.tsx`
- Create: `frontend/src/components/studio/StudioTopBar.tsx`
- Create: `frontend/src/components/studio/StepOutline.tsx`
- Create: `frontend/src/components/studio/VariableList.tsx`
- Create: `frontend/src/components/studio/StepSequenceView.tsx`
- Create: `frontend/src/components/studio/ActionInspector.tsx`
- Create: `frontend/src/components/studio/ActionSchemaForm.tsx`
- Create: `frontend/src/components/studio/VariablePicker.tsx`
- Create: `frontend/src/components/studio/ConditionBuilder.tsx`
- Create: `frontend/src/components/studio/JsonAdvancedEditor.tsx`
- Create: `frontend/src/lib/action-schema.ts`
- Create: `frontend/tests/action-schema.test.mjs`
- Create: `frontend/tests/studio-store.test.mjs`
- Create: `frontend/tests/condition-builder.test.mjs`
- Modify: `frontend/src/pages/WorkflowEditor.tsx`
- Modify: `frontend/src/components/workflows/WorkflowConfigPanel.tsx`
- Modify: `frontend/src/components/workflows/WorkflowEditorNode.tsx`
- Modify: `frontend/src/components/WorkflowDagPreview.tsx`
- Modify: `frontend/src/lib/workflow-editor.ts`
- Modify: `frontend/src/types/workflow.ts`

**Interfaces:**

- Consumes: `ActionDefinition.params_schema`、`ActionDefinition.output_schema`、`WorkflowVersion`、当前 draft。
- Produces:

```ts
interface WorkflowStudioState {
  workflowId: number | null;
  baseVersionId: number | null;
  baseRevision: number;
  draft: StructuredPlan;
  dirty: boolean;
  selectedStepId: number | null;
  panel: 'variables' | 'steps';
  bottomTab: 'logs' | 'screenshot' | 'variables' | 'data';
  load(workflow: WorkflowRecord, version?: WorkflowVersion): void;
  patchStep(stepId: number, patch: Partial<WorkflowStep>): void;
  addStep(step: WorkflowStep): void;
  moveStep(from: number, to: number): void;
  undo(): void;
  redo(): void;
  markSaved(revision: number, plan: StructuredPlan): void;
}
```

控件映射：

```text
string -> Input
multiline -> Textarea
number/integer -> number Input
boolean -> Switch
enum -> Select
array -> repeatable field list
object -> nested fieldset
credential_key -> credential picker
variable -> VariablePicker
condition -> ConditionBuilder
unsupported schema -> scoped JSON editor with validation error
```

- [ ] **Step 1: 写 failing tests。** schemaToFields 支持 string/number/boolean/enum/array/object；未知 schema 不崩溃并进入 JSON fallback；patchStep 标记 dirty；undo/redo 恢复完全相同 plan；条件 builder 输出设计规定的 `{left,operator,right,cast}`。

- [ ] **Step 2: 运行失败 tests。**

```powershell
cd frontend
npm.cmd test
```

Expected: FAIL。

- [ ] **Step 3: 实现 store。** 按 workflow id 重置编辑状态；不把 execution/task/catalog 服务端状态复制到 Zustand。

- [ ] **Step 4: 拆分 WorkflowEditor。** 顶部显示名称、状态、active version、save/test/publish；新建入口提供自然语言、录制导入和空白计划；左侧 outline+variables；中间顺序视图，React Flow 仅复杂依赖切换；右侧动态 inspector；底部抽屉。

- [ ] **Step 5: 保持旧 JSON 编辑。** 放在高级 drawer，不默认暴露；保存前用同一 schema 校验。

- [ ] **Step 6: 运行 tests/build。**

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS。

### Task C.4：Studio 保存、试运行、数据预览、版本和发布

**Goal:** 在 UI 中完整完成“编辑草稿 -> 单步/整体试运行 -> 看数据 -> 发布/回滚”。

**Dependencies:** Task A.2、B.1-B.6、C.3。

**Files:**

- Create: `frontend/src/components/studio/RunDrawer.tsx`
- Create: `frontend/src/components/studio/DataPreview.tsx`
- Create: `frontend/src/components/studio/StepResultPanel.tsx`
- Create: `frontend/src/components/studio/WorkflowVersionDiff.tsx`
- Create: `frontend/src/api/versions.ts`
- Create: `frontend/src/api/dryRun.ts`
- Create: `frontend/tests/data-preview.test.mjs`
- Create: `frontend/tests/version-diff.test.mjs`
- Modify: `frontend/src/api/workflows.ts`
- Modify: `frontend/src/types/workflow.ts`
- Modify: `frontend/src/pages/WorkflowEditor.tsx`
- Modify: `frontend/src/store/useWorkflowStudioStore.ts`
- Modify: `frontend/src/lib/workflow-draft.ts`

**Interfaces:**

API：

```text
POST /api/workflows/{id}/validate
POST /api/workflows/{id}/test-step
POST /api/workflows/{id}/dry-run
POST /api/workflows/{id}/publish
GET  /api/workflows/{id}/versions
POST /api/workflows/{id}/rollback
```

Save 请求必须包含 `draft_revision`；冲突返回 409，不覆盖用户编辑。

- [ ] **Step 1: 写 tests。** dry-run 结果映射为表格头/类型/行数/空值/重复值；filter 显示 before/after；版本 diff 区分 add/remove/change；保存 409 保留本地 dirty。

- [ ] **Step 2: 运行失败 tests。**

```powershell
cd frontend
npm.cmd test
```

Expected: FAIL。

- [ ] **Step 3: 实现 mutation。** save/test/publish 使用 `useMutation`；成功后 invalidate workflow/version query；失败显示后端稳定错误码。

- [ ] **Step 4: 实现 DataPreview。** 只显示后端试运行结果，不在前端推断业务成功；空数组和 warning 使用明确状态。

- [ ] **Step 5: 实现版本 diff 与 rollback。** rollback 二次确认，保留旧版本和审计。

- [ ] **Step 6: 运行 tests/build。**

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS。

### Task C.5：执行详情、时间线、产物和投递状态

**Goal:** 重构执行详情，展示版本、主机、策略、步骤、变量、截图、文件和邮件投递。

**Dependencies:** Task A.4、B.3、B.6、C.1。

**Files:**

- Create: `frontend/src/components/executions/RunTimeline.tsx`
- Create: `frontend/src/components/executions/ArtifactPanel.tsx`
- Create: `frontend/src/components/executions/DeliveryPanel.tsx`
- Create: `frontend/src/components/executions/VariablePanel.tsx`
- Create: `frontend/tests/execution-timeline.test.mjs`
- Modify: `frontend/src/pages/ExecutionDetail.tsx`
- Modify: `frontend/src/pages/ExecutionsPage.tsx`
- Modify: `frontend/src/lib/execution.ts`
- Modify: `frontend/src/api/executions.ts`
- Modify: `frontend/src/types/workflow.ts`

**Interfaces:**

- Consumes: `GET /api/executions/{id}`、`GET /api/executions/{id}/events`、现有 WebSocket live。
- Produces: 稳定 `RunTimelineItem` 和 delivery badge，不把 unknown 字段自动视为 success。

- [ ] **Step 1: 写 tests。** warning 显示 warning；awaiting_user 不计成功；failed delivery 显红；cancelled 终态；变量值脱敏；步骤 duration 稳定。

- [ ] **Step 2: 运行失败 tests。**

```powershell
cd frontend
npm.cmd test
```

Expected: FAIL。

- [ ] **Step 3: 实现 Query+WebSocket 更新。** WebSocket 事件 invalidate execution detail/events；断线不影响已加载历史。

- [ ] **Step 4: 实现 artifact/delivery panels。** 只允许白名单路径下载；附件和 message-id 只读展示。

- [ ] **Step 5: 运行 tests/build。**

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS。

### Task C.6：连接、凭据和设置页面

**Goal:** 统一管理 Browser Profile、SMTP、本机/VM Agent、导出目录和视觉设置。

**Dependencies:** Task A.1、B.6、Stage F 可先占位。

**Files:**

- Create: `frontend/src/api/connections.ts`
- Create: `frontend/src/api/settings.ts`
- Create: `frontend/src/components/connections/ProfileList.tsx`
- Create: `frontend/src/components/connections/SmtpSettingsForm.tsx`
- Create: `frontend/src/components/connections/HostList.tsx`
- Create: `frontend/src/components/connections/ExportDirectoryForm.tsx`
- Create: `frontend/src/components/connections/CredentialEditor.tsx`
- Create: `frontend/tests/connections.test.mjs`
- Modify: `frontend/src/pages/ConnectionsPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/types/workflow.ts`
- Modify: `app/api/settings.py`
- Modify: `app/api/agents.py`

**Interfaces:**

- Consumes: `/api/settings/email`、`/api/hosts`、`/api/browser-profiles`、`/api/credentials`。
- Produces: 密码字段永不回显；保存后 invalidate settings/credentials；测试邮件显示 DeliveryRecord 状态。

- [ ] **Step 1: 写 tests。** `password_configured` 正确渲染；空密码保存不覆盖已有值；agent environment 标签；credentials 值不进入 DOM。

- [ ] **Step 2: 运行失败 tests。**

```powershell
cd frontend
npm.cmd test
```

Expected: FAIL。

- [ ] **Step 3: 实现表单和 API。** SMTP 测试连接与测试发送分开；Profile 登录状态只显示服务端结果。

- [ ] **Step 4: 运行 tests/build。**

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS。

**Stage C status: COMPLETE (2026-10-30).**

| Task | Status | Verification |
|---|---|---|
| C.0 authoring, observe, validation and draft execution APIs | Complete | Draft/test-step/dry-run do not create formal executions or versions; observation locator and draft revision conflicts are guarded |
| C.1 Query, routing and personal workbench shell | Complete | TanStack Query provider, lazy Stage C routes, dashboard-first navigation |
| C.2 workflow list and structured/legacy states | Complete | Compact workflow table, active version labels, in-app dialogs, formal run gating |
| C.3 Studio state, catalog forms and inspector | Complete | Catalog-driven schema form, variable picker, declarative conditions, sequence/React Flow views |
| C.4 save, test/dry-run, data preview, versions and rollback | Complete | Save-conflict dirty preservation, filter counts, version diff, confirm-and-rollback UI |
| C.5 execution detail, timeline, variables, artifacts and delivery | Complete | Actual workflow version, Query invalidation from WebSocket, warning/awaiting/failed/cancelled semantics |
| C.6 connections and credentials | Complete | SMTP test/send, browser profiles, persisted export directories, workflow credentials with no value echo |

**Stage C scope note:** Per the Stage C execution boundary, visual-provider settings
from Task C.6 remain in Stage E, and VM host management/routing remains in Stage F.
The Stage C connection page therefore covers SMTP, browser profiles, export
directories and workflow credentials, while reusing the existing read-only local
Agent support where already present.

**Stage C final verification (2026-10-30):**

- Backend: `859 passed, 23 skipped`.
- Frontend: `95 passed`.
- Frontend production build: passed.
- Temporary SQLite: schema 8; first run executed migrations 1-8; repeat run
  returned `executed=[]`.
- Focused migration/version/history/detail/connection/artifact tests: `20 passed`.

**Stage C Acceptance:** 普通网页工作流可在界面中创建、观察、编辑参数、单步/试运行、查看数据和发布/回滚；旧代码工作流清楚标记并只读迁移。

## Stage D：人工接管、检查点和恢复

### Task D.1：HumanTask 服务、状态机和 API

**Goal:** 将登录、验证码、扫码、视觉歧义和高风险确认变成独立可审计待办。

**Dependencies:** Task A.1、A.4。

**Files:**

- Create: `app/services/human_tasks.py`
- Create: `app/api/human_tasks.py`
- Create: `tests/test_human_tasks.py`
- Create: `tests/test_human_task_api.py`
- Modify: `app/models/human_task.py`
- Modify: `app/engine/execution_manager.py`
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`
- Modify: `app/schemas/human_task.py`

**Interfaces:**

- Consumes: `ExecutionLog`、`HumanTask`、`BrowserObserver`/Agent inspect。
- Produces:
  - `HumanTaskManager.create(...)`
  - `HumanTaskManager.resume(task_id, decision, verification_payload)`
  - `HumanTaskManager.cancel(task_id, reason)`
  - `HumanTaskManager.expire_stale(now)`

API：

```text
GET  /api/human-tasks
GET  /api/human-tasks/{id}
POST /api/human-tasks/{id}/resume
POST /api/human-tasks/{id}/cancel
```

Resume body：

```json
{
  "decision": "resume",
  "verification": {
    "type": "web_logged_in",
    "success_selector": "[data-user]"
  }
}
```

- [ ] **Step 1: 写状态机 tests。** pending -> resolved/cancelled/expired；resolved 不可再次 resolve；expired 不能 resume；执行与 HumanTask 状态在同一事务更新。

- [ ] **Step 2: 写验证 tests。** 仅点击 resume 不通过；web selector/URL、desktop window title/process、risk confirmation 任一失败时任务保持 pending 且返回 409。

- [ ] **Step 3: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_human_tasks.py tests/test_human_task_api.py -q
```

Expected: FAIL。

- [ ] **Step 4: 实现强校验。** `resume_check` 必须来自允许的动作/检查清单，不得执行任意表达式。无法自动验证的桌面登录必须通过 Agent inspect 确认目标窗口存在且前台，并明确记录“人工已确认”证据。

- [ ] **Step 5: 接入 ExecutionManager。** `auth_required` 和 `human_action` 结果创建 HumanTask；只允许通过 manager/resume 入口恢复。

- [ ] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_human_tasks.py tests/test_human_task_api.py tests/test_execution_manager.py -q
```

Expected: PASS。

### Task D.2：完整检查点、暂停/恢复和主机绑定

**Goal:** 检查点保存恢复所需全部状态，恢复前重新预检和验证当前步骤。

**Dependencies:** Task D.1、A.4。

**Files:**

- Create: `app/services/checkpoint_service.py`
- Create: `tests/test_checkpoint_service.py`
- Modify: `app/engine/checkpoints.py`
- Modify: `app/engine/execution_manager.py`
- Modify: `app/engine/task_runtime.py`
- Modify: `app/engine/desktop_dispatcher.py`
- Modify: `tests/regression/fixtures/checkpoint_resume_step_logs/fixture.json`
- Modify: `tests/test_execution_manager.py`

**Interfaces:**

- Consumes: `RuntimeState`、ExecutionLog、HumanTask。
- Produces checkpoint payload：

```json
{
  "type": "checkpoint",
  "schema_version": 2,
  "execution_id": 88,
  "workflow_version_id": 101,
  "next_step_id": 4,
  "completed_step_ids": [1, 2, 3],
  "completed_idempotency_keys": ["login-check"],
  "variables": {},
  "artifacts": [],
  "login_state": "ready",
  "host_id": 7,
  "human_task_id": 31,
  "created_at": "2026-10-28T10:00:00+08:00"
}
```

- [ ] **Step 1: 写 checkpoint round trip tests。** 变量、循环游标、产物、host 和登录状态保持；敏感值脱敏；旧 v1 checkpoint 可读。

- [ ] **Step 2: 写恢复 tests。** 恢复时重新预检；当前步骤 precondition 失败不放行；Agent 离线返回 blocked/awaiting_user，不丢 checkpoint。

- [ ] **Step 3: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_checkpoint_service.py tests/test_execution_manager.py -q
```

Expected: FAIL。

- [ ] **Step 4: 实现 checkpoint service。** 每步成功后原子更新 ExecutionLog.step_logs；失败或 awaiting_user 写入失败步骤和分类；不把凭据秘密放进 JSON。

- [ ] **Step 5: 实现暂停/恢复。** pause 请求记录 cancel_requested_at 并等待当前安全边界；resume 清除 finished_at，但保留 checkpoint；终态拒绝恢复。

- [ ] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_checkpoint_service.py tests/test_execution_manager.py tests/test_execution_control_api.py -q
```

Expected: PASS。

### Task D.3：待处理中心和登录/验证码 E2E

**Goal:** 用户可以从待处理中心处理登录、验证码、扫码和视觉确认，并在服务端验证后继续。

**Dependencies:** Task C.1、D.1、D.2。

**Files:**

- Create: `frontend/src/components/pending/HumanTaskPanel.tsx`
- Create: `frontend/src/components/pending/HumanTaskList.tsx`
- Create: `frontend/src/api/humanTasks.ts`
- Create: `frontend/tests/human-tasks.test.mjs`
- Create: `tests/e2e/test_human_login_resume.py`
- Create: `tests/fixtures/sites/login/index.html`
- Modify: `frontend/src/pages/PendingPage.tsx`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/pages/ExecutionDetail.tsx`

**Interfaces:**

- Consumes: HumanTask API。
- Produces: pending list、截图、说明、等待时长、resume/skip/cancel 操作；resume 失败显示后端验证原因。

- [ ] **Step 1: 写 frontend logic tests。** 过期/取消/等待状态标签；resume disabled while pending mutation；错误不会本地标记 resolved。

- [ ] **Step 2: 写 local browser E2E。** 本地登录页先显示登录表单；测试模拟用户完成登录并调用 resume；服务端 selector 不存在时拒绝，selector 存在时从下一检查点继续。

- [ ] **Step 3: 运行 tests。**

```powershell
cd frontend
npm.cmd test
cd ..
.\.venv\Scripts\python.exe -m pytest tests/e2e/test_human_login_resume.py -q
```

Expected: PASS；无浏览器时 skip 并注明 `browser` marker。

**Stage D Acceptance:** 登录失效和人工确认不会重复失败；待办有独立记录、过期和验证；恢复不会重跑已完成步骤。

**Stage D status: COMPLETE (2026-11-11).**

| Task | Status | Verification |
|---|---|---|
| D.1 HumanTask service, state machine and API | Complete | Dedupe, pending/resolved/cancelled/expired transitions, audit events, user-scoped list/detail/resume/cancel, structured verification failures, idempotent and cross-manager concurrent resume |
| D.2 checkpoint, pause/resume and host binding | Complete | v2 checkpoint round trip and v1 compatibility, preflight revalidation, original host check when Agents are online, checkpoint resume without repeating completed side effects, browser Profile persistence and reuse |
| D.3 pending center and login/captcha handoff | Complete | Pending center, execution-detail banner, screenshot/URL/window details, visible Profile login session, server-side verification, verify/cancel flows and legacy awaiting_user display |

**Stage D final verification (2026-11-11):**

- Backend: `901 passed, 23 skipped`.
- Frontend: `100 passed`.
- Frontend production build: passed.
- Temporary SQLite: schema 9; first run executed migrations 1-9; repeat run
  returned `executed=[]`.
- Real headful Edge login, real Windows Agent inspection and WPS COM remain
  environment-gated and are not counted as passed.

**Stage D scope note:** `visual_ambiguous` is reserved as a HumanTask kind for
Stage E; no visual provider, visual fallback or VM host routing was added here.
Legacy `awaiting_user` rows are migrated to a pending HumanTask whose retry is
guarded by checkpoint presence and the normal resume preflight.

## Stage E：视觉兜底

### Task E.1：VisualProvider、候选协议、脱敏和设置

**Goal:** 定义受控的视觉请求/响应边界，默认不发送截图到本机外。

**Dependencies:** Task A.3、C.6、D.1。

**Files:**

- Create: `app/visual/__init__.py`
- Create: `app/visual/provider.py`
- Create: `app/visual/redaction.py`
- Create: `app/visual/local_ocr.py`
- Create: `app/visual/remote_vlm.py`
- Create: `app/api/visual.py`
- Create: `tests/test_visual_protocol.py`
- Create: `tests/test_visual_redaction.py`
- Create: `tests/test_visual_settings.py`
- Create: `frontend/src/components/connections/VisualSettingsForm.tsx`
- Modify: `app/actions/catalog.py`
- Modify: `app/main.py`
- Modify: `app/api/__init__.py`
- Modify: `frontend/src/pages/ConnectionsPage.tsx`

**Interfaces:**

- Consumes: screenshot、locator/UIA summary、action target、safe visible text、credential。
- Produces:

```python
class VisualProvider(Protocol):
    name: str
    async def locate(self, request: VisualLocateRequest) -> VisualLocateResponse: ...
```

```json
{
  "candidates": [
    {
      "target": "导出",
      "bbox": [1180, 210, 1260, 238],
      "confidence": 0.91,
      "evidence": "右上角按钮，文字为导出",
      "expected_after": "出现下载文件"
    }
  ]
}
```

- [ ] **Step 1: 写 protocol tests。** bbox 必须是 4 个非负整数且 width/height>0；confidence 0..1；空候选合法；provider name 只允许 `disabled/local_ocr/remote_vlm`。

- [ ] **Step 2: 写 redaction tests。** 密码、Token、Cookie、银行卡、显式 secret values、路径和用户配置矩形被遮罩；远程 provider 收到的是脱敏图。

- [ ] **Step 3: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_visual_protocol.py tests/test_visual_redaction.py tests/test_visual_settings.py -q
```

Expected: FAIL。

- [ ] **Step 4: 实现 disabled 和 remote_vlm。** `disabled` 永远返回 `environment_unavailable`；remote 端点必须 HTTPS、显式开启、有凭据和超时。返回内容只解析为候选 JSON，不执行任何代码。

- [ ] **Step 5: 实现 local_ocr 最小替代。** 如果可选的本地 OCR 依赖不存在，provider 声明不可用并在设置页明确显示；不得偷偷改用远程。此取舍只影响无网络/隐私模式，不影响 disabled/remote 主路径。

- [ ] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_visual_protocol.py tests/test_visual_redaction.py tests/test_visual_settings.py -q
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS。

### Task E.2：视觉定位执行、置信度、验证和人工确认

**Goal:** 只在结构化定位失败且策略允许时调用视觉，永不盲点，执行后必须验证。

**Dependencies:** Task E.1、D.1、B.4。

**Files:**

- Create: `app/engine/visual_fallback.py`
- Create: `tests/test_visual_fallback.py`
- Create: `tests/regression/fixtures/visual_ambiguous/fixture.json`
- Create: `tests/e2e/test_visual_fallback.py`
- Modify: `app/engine/web_executor.py`
- Modify: `app/engine/desktop_dispatcher.py`
- Modify: `app/engine/hybrid_executor.py`
- Modify: `app/actions/registry.py`
- Modify: `app/regression/replay.py`

**Interfaces:**

- Consumes: `VisualProvider`、ActionContext、step `verification`。
- Produces:
  - `VisualFallback.try_locate(step, capture) -> VisualDecision`
  - `VisualDecision.action in {"execute","confirm","disabled","failed"}`
  - 候选缓存 key：`workflow_version_id + step_id + url/window_title + viewport/rect + target`。

判定规则：

- 默认 `accept_threshold=0.90`，第二候选领先至少 `0.10`。
- 高风险动作永不自动执行，必须 HumanTask。
- 只允许 action catalog 中已声明的 action；视觉只提供定位/目标，不改变 executor/action/params。
- 执行后必须运行 step postconditions/verify；验证失败清理缓存并创建 visual_ambiguous HumanTask。

- [ ] **Step 1: 写 unit tests。** 单高置信候选执行；低置信、双高候选、高风险和 provider disabled 进入人工/失败；bbox 越界失败；缓存命中仍需验证。

- [ ] **Step 2: 写 Replay test。** 固定截图/候选夹具重放得到相同 decision；人工确认后记录 resolved candidate。

- [ ] **Step 3: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_visual_fallback.py tests/regression/test_replay.py -q
```

Expected: FAIL。

- [ ] **Step 4: 实现 web/desktop wrapper。** 结构化 locator/UIA 全部失败后触发；不要替换动作参数。Web 使用比例坐标点击，Desktop 使用 `click_relative` 或经确认的目标点击。

- [ ] **Step 5: 实现人工确认。** HumanTask payload 保存脱敏截图、候选和预期结果；resume 必须指定 candidate_id 并以验证通过作为 resolved 条件。

- [ ] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_visual_fallback.py tests/test_visual_protocol.py tests/regression/test_replay.py -q
```

Expected: PASS。

**Stage E Acceptance:** 页面定位失败不会无候选重启；视觉只在允许条件触发；高置信唯一候选仍执行后验证；歧义进入人工确认。

**Stage E status: COMPLETE (2026-11-11).**

| Task | Status | Verification |
|---|---|---|
| E.1 VisualProvider, candidates, redaction and settings | Complete | Disabled/local_ocr/remote_vlm contracts, HTTPS/key validation, normalized candidate models, image/text redaction, encrypted key storage, settings API and frontend form |
| E.2 visual fallback execution and confirmation | Complete | Explicit-step trigger, attempt budget/cache, Web viewport and Desktop window captures, catalog action allowlist, risk gating, post-action verification, visual_ambiguous HumanTask, candidate_id resume and replay fixture |

**Stage E final verification (2026-11-11):**

- Backend: `939 passed, 23 skipped`.
- Stage E focused: `132 passed, 2 skipped`.
- Frontend: `103 passed`.
- Frontend production build: passed.
- `compileall app desktop_agent scripts`: passed.
- Temporary SQLite: schema 9; first run executed migrations 1-9; repeat run
  returned `executed=[]`.
- Real remote visual provider, real interactive desktop capture/click and
  external browser Playwright remain environment-gated and are not counted as
  passed.

**Stage E scope note:** Provider output is candidate-only. Server-generated
`candidate_id` values are persisted in HumanTask payloads and checkpoint
selection state. No VM routing, host migration or automatic VM management was
added.

## Stage F：本机 Agent 与按需 VM

### Task F.1：Agent environment、能力声明和主机注册

**Goal:** 区分本机与隔离 VM，并让能力声明驱动路由，而不是前端手选固定 Agent。

**Dependencies:** Task A.1、B.5。

**Files:**

- Create: `app/models/agent_capability.py`（仅当需要规范化历史能力时；否则保留 JSON）
- Create: `desktop_agent/capabilities.py`
- Create: `tests/test_host_capabilities.py`
- Create: `tests/test_agent_environment_api.py`
- Modify: `desktop_agent/config.py`
- Modify: `desktop_agent/client.py`
- Modify: `desktop_agent/main.py`
- Modify: `desktop_agent/config.json.example`
- Modify: `app/models/agent.py`
- Modify: `app/schemas/agent.py`
- Modify: `app/api/agents.py`
- Modify: `app/api/agent_ws.py`
- Modify: `tests/test_agents_api.py`

**Interfaces:**

- Consumes: PairRequest、AgentConfig、WebSocket registration。
- Produces:
  - `DesktopAgent.environment in {"local","vm"}`；environment 只表示主机位置，不表示能力。
  - capabilities 至少 `headless_web`、`interactive_desktop`、`isolated_desktop`、`visual_fallback`；`environment=vm` 不自动具备 `isolated_desktop`。
  - `normalize_capabilities()` 将历史 `window/uia/screenshot` 映射为 `interactive_desktop`；历史 `wps` 映射仅用于 legacy plan，不用于新发布。
  - 历史 `environment=isolated` 迁移为 `vm`；API 兼容输入可接受 `isolated` 并归一化为 `vm`，输出只使用 `vm`。
  - 未知 environment 拒绝；旧 Agent 没有 capabilities 时按最小能力处理。

- [x] **Step 1: 写 tests。** 本地 Agent 发 `interactive_desktop`；VM 配置发 `environment=vm` 且显式声明 `isolated_desktop`；`vm` 无该能力时路由不得视为隔离；旧能力可迁移；unknown capability 被忽略并告警，不让注册失败；`isolated -> vm` 迁移和 API 归一化通过。

- [x] **Step 2: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_host_capabilities.py tests/test_agent_environment_api.py tests/test_agents_api.py -q
```

Expected: FAIL。

- [x] **Step 3: 扩展配对协议和配置。** Agent name、token 不变；environment 由 Agent 配置声明，不从前端伪造。

- [x] **Step 4: 显示主机能力。** `GET /api/hosts` 与现有 `/api/agents` 兼容；VM 手动配对后才能出现。

- [x] **Step 5: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_host_capabilities.py tests/test_agent_environment_api.py tests/test_agents_api.py tests/test_agent_ws_events.py -q
```

Expected: PASS。

### Task F.2：主机路由和运行内固定

**Goal:** `headless_web` 在服务器执行；交互桌面固定到同一 local/vm Agent；断点不换宿。

**Dependencies:** Task F.1、A.4、D.2。

**Files:**

- Create: `app/engine/host_router.py`
- Create: `tests/test_host_router.py`
- Create: `tests/test_host_pinning.py`
- Modify: `app/engine/hybrid_executor.py`
- Modify: `app/engine/desktop_dispatcher.py`
- Modify: `app/engine/execution_manager.py`
- Modify: `app/api/executions.py`
- Modify: `app/models/execution.py`
- Modify: `frontend/src/api/executions.ts`
- Modify: `frontend/src/types/workflow.ts`

**Interfaces:**

- Consumes: execution policy `mode` 与 `host_policy`、step `host_requirement`、Agent environment/capabilities/status。
- Produces:
  - `HostRouter.select(step, policy, bound_host_id) -> HostDecision`
  - HostDecision 包含 `host_id`、`environment`、`capabilities`、`reason`、`fallback_allowed`。

路由矩阵：

```text
headless_web       -> server web executor
interactive_desktop-> 满足 mode + host_policy 的 local/vm Agent
                       background_if_safe 时必须为 vm + isolated_desktop
isolated_desktop   -> 仅 environment=vm + isolated_desktop 的 Agent
any                -> 最低成本满足全部硬约束的 server/local/vm
```

- [x] **Step 1: 写 route matrix tests。** 覆盖 `host_requirement`、`mode`、`host_policy`、environment/capabilities 组合；硬约束不能被子偏好覆盖；没有满足能力时返回 `policy_conflict`/`host_capability_missing` 并阻断，不跨主机偷跑。

- [x] **Step 2: 写 pinning tests。** 一次执行第一次桌面步骤选择 Agent 后，后续桌面步骤使用同一 id；Agent 离线时进入 awaiting_user/blocked，不自动换到另一台；host-bound 状态缺失原 host 时保留检查点并失败/待处理。

- [x] **Step 3: 运行失败 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_host_router.py tests/test_host_pinning.py tests/test_hybrid_executor.py -q
```

Expected: FAIL。

- [x] **Step 4: 保存 host binding。** `ExecutionLog.host_id`、environment 和 host-bound 标记与 checkpoint 同时保存；有 host-bound 状态时恢复只能使用同一 host，不能自动迁移。用户在新主机显式重新开始时必须提示重复副作用，不能伪装成原执行恢复。

- [x] **Step 5: 实现按需启动语义。** 本轮只接受已在线 VM；不创建 VM。VM 不在线时 HumanTask/blocked，不把桌面步骤降级到当前桌面。

- [x] **Step 6: 运行 tests。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_host_router.py tests/test_host_pinning.py tests/test_hybrid_executor.py tests/test_dispatcher.py -q
```

Expected: PASS。

### Task F.3：VM 人工访问和连接页面

**Goal:** 人工处理 VM 待办时能看状态、截图/说明和继续按钮，不自动管理 VM 生命周期。

**Dependencies:** F.2、C.6、D.1。

**Files:**

- Create: `frontend/src/components/connections/VmAccessPanel.tsx`
- Create: `frontend/src/components/pending/VmHumanTaskDetails.tsx`
- Create: `frontend/tests/vm-access.test.mjs`
- Modify: `frontend/src/pages/ConnectionsPage.tsx`
- Modify: `frontend/src/components/pending/HumanTaskPanel.tsx`
- Modify: `app/schemas/human_task.py`
- Modify: `app/api/human_tasks.py`

**Interfaces:**

- Consumes: host status、screenshot URL、HumanTask payload。
- Produces: VM state、preview、console link/instructions、resume validation reason。

- [x] **Step 1: 写 tests。** 离线 VM 禁用 resume；隔离 host 显示 isolated badge；没有 console link 时显示操作说明；截图加载失败不影响任务说明。

- [x] **Step 2: 运行失败 tests。**

```powershell
cd frontend
npm.cmd test
```

Expected: FAIL。

- [x] **Step 3: 实现 UI。** 不嵌入任意远程桌面协议；只提供用户配置的受信链接或文字说明。

- [x] **Step 4: 运行 tests/build。**

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: PASS。

**Stage F Acceptance:** 必须占用交互桌面的步骤可以明确路由到本机或已注册 VM；本机可继续做服务端 Web/Data；VM 不可用时不会假装成功或偷偷切换主机。

**Stage F status: COMPLETE (2026-11-11).**

| Task | Status | Verification |
|---|---|---|
| F.1 host environment and capabilities | Complete | `environment=local|vm`, `isolated -> vm` migration/API compatibility, canonical capability normalization, manual host registration/list/access API and WebSocket host events |
| F.2 host routing and pinning | Complete | Hard/soft policy matrix, background-safe VM requirement, immutable version capability snapshot, host-bound execution, session fingerprint, no automatic cross-host fallback |
| F.3 VM access and frontend | Complete | Host list/status/capabilities/access UI, Studio `host_requirement`, execution host banner, VM HumanTask details and offline resume gating |

**Stage F final verification (2026-11-11):**

- Backend: `990 passed, 23 skipped`, 68.03 seconds.
- Frontend: `111 passed`.
- Frontend production build: passed.
- `compileall app desktop_agent scripts`: passed.
- Temporary SQLite: schema 10; first run executed migrations 1-10;
  repeat run returned `executed=[]`.
- Real VM, WSS, interactive Windows desktop, DPI/window capture,
  Edge login takeover and remote visual provider remain environment-gated.
  The real-environment checklist is
  `docs/superpowers/reports/2026-11-11-stage-f-environment-acceptance.md`;
  these items are not counted as passed.

**Stage F scope note:** `environment` is host location only (`local|vm`);
`isolated_desktop` remains an explicit capability. This stage did not add
automatic VM creation, startup, software installation, host destruction,
cross-host migration of host-bound execution, or any site-specific module.

## Stage G status: CONDITIONALLY COMPLETE (2026-11-11)

本地自动化与可用的真实本机环境已经完成验收，但完整个人使用发布候选仍需
外部环境和 G.1 契约生成补齐。不能把以下未覆盖项计为通过。

| Task | Status | Evidence / Gap |
|---|---|---|
| G.1 OpenAPI and shared types | Deferred | `scripts/export_openapi.py`, generated TS types and contract tests are not present. |
| G.2 Generic E2E, Replay, local acceptance | Partial | Replay 5/5; real Windows desktop, local Agent, Playwright Chromium three-site/login/visual-fake checks passed. `tests/e2e` pytest suite was not created; real Edge/VM/SMTP/remote visual remain coverage gaps. |
| G.3 Docs, package, degradation notes | Partial | Latest sidecars and NSIS installer built; local app launch, restart, tray hide/restore and sidecar health verified. Delivery acceptance report added. Runtime-boundary G.3 files remain deferred. |

**Stage G final verification (2026-11-11):**

- Backend: `997 passed, 23 skipped`, 68.04 seconds.
- Frontend: `111 passed`; production build passed with 2424 modules.
- Desktop production build passed with 3908 modules.
- `compileall app desktop_agent scripts`: passed.
- Temporary SQLite: schema 10; first run executed 1-10; repeat run returned `executed=[]`.
- Replay: 5 fixtures, 5 passed, `pass_rate=100%`.
- NSIS installer: `desktop/src-tauri/target/release/bundle/nsis/NL-RPA Desktop_0.1.0_x64-setup.exe`.
- Real standard Edge, Windows VM Agent, remote WSS, real SMTP and remote visual provider were unavailable and remain unaccepted.

The authoritative environment record is
`docs/superpowers/reports/2026-11-11-stage-g-final-acceptance.md`.
Do not mark G.1 complete until OpenAPI export, generated TS types and drift
checks exist and pass.

## 阶段验证矩阵

每个阶段结束时必须执行以下命令；任何 failure 都阻断进入下一阶段。

| 阶段 | 命令 | 预期 |
|---|---|---|
| 0 | `.\.venv\Scripts\python.exe -m pytest -q`；在 `frontend` 执行 `npm.cmd test`、`npm.cmd run build` | 原基线和新增兼容测试全部通过；构建成功 |
| A | `.\.venv\Scripts\python.exe -m pytest tests/test_workflow_versions.py tests/test_execution_policy.py tests/test_action_protocol.py tests/test_action_registry.py tests/test_action_catalog_contract.py tests/test_preflight.py tests/test_single_itinerary.py tests/test_legacy_compatibility.py -q` | 全部 PASS；structured 非法动作 fail closed；legacy 快照可运行 |
| B | `.\.venv\Scripts\python.exe -m pytest tests/test_action_variables.py tests/test_data_actions.py tests/test_file_actions.py tests/test_xlsx_compat.py tests/test_web_catalog.py tests/test_web_executor.py tests/test_email_service.py tests/test_email_api.py tests/test_delivery_records.py -q` | 全部 PASS；无外网/无 Docker；浏览器缺失项明确 skip |
| C | 在 `frontend` 执行 `npm.cmd test`、`npm.cmd run build`；另跑 C.0-C.6 后端 focused tests | 前端 0 failure、build 成功；Studio 无硬编码具体动作表单 |
| D | `.\.venv\Scripts\python.exe -m pytest tests/test_human_tasks.py tests/test_human_task_api.py tests/test_checkpoint_service.py tests/test_execution_manager.py tests/test_execution_control_api.py -q` | 登录、人工任务和检查点恢复全部 PASS |
| E | `.\.venv\Scripts\python.exe -m pytest tests/test_visual_protocol.py tests/test_visual_redaction.py tests/test_visual_fallback.py tests/regression/test_replay.py -q` | provider disabled/remote 语义正确；歧义不自动点击 |
| F | `.\.venv\Scripts\python.exe -m pytest tests/test_host_capabilities.py tests/test_host_router.py tests/test_host_pinning.py tests/test_dispatcher.py -q` | 路由矩阵 PASS；真实 VM 缺失时 pytest skip |
| G | 最终验收命令、`scripts/run_regression.py` 和 OpenAPI check | 0 failure；Replay 100%；无类型漂移 |

阶段 A 额外做一次全量后端测试；阶段 B、D、E、F 结束各做一次全量后端测试，防止共享执行链回归。

## Stage G：契约、Replay、端到端和通用性验收

### Task G.1：OpenAPI 和前后端共享类型

**Goal:** 用可生成、可校验的 API 契约消除手写 TS/Pydantic 漂移。

**Dependencies:** Stage A-F API 基本稳定。

**Files:**

- Create: `scripts/export_openapi.py`
- Create: `frontend/src/types/api.generated.ts`
- Create: `frontend/src/types/api.ts`
- Create: `frontend/scripts/generate-types.mjs`
- Create: `frontend/tests/api-contract.test.mjs`
- Create: `tests/test_openapi_contract.py`
- Modify: `frontend/package.json`
- Modify: `app/main.py`
- Modify: `app/schemas/workflow.py`
- Modify: `app/schemas/agent.py`
- Modify: `app/schemas/human_task.py`
- Modify: `app/schemas/delivery.py`

**Interfaces:**

- Consumes: FastAPI `app.openapi()`。
- Produces:
  - `openapi.json` 稳定生成。
  - `api.generated.ts` 由 `openapi-typescript` 生成。
  - `frontend/src/types/api.ts` 只导出领域别名，不复制后端字段。

- [ ] **Step 1: 导出 schema。**

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py --output .tmp\openapi.json
```

- [ ] **Step 2: 安装代码生成依赖并生成类型。**

```powershell
cd frontend
npm.cmd install -D openapi-typescript
npm.cmd run types:generate
```

- [ ] **Step 3: 写 contract tests。** Action catalog、StepResult、ExecutionPolicy、WorkflowVersion、HumanTask、DeliveryRecord 都在 OpenAPI schema 中存在；前端生成文件包含对应 operation IDs。

- [ ] **Step 4: 验证生成无漂移。**

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py --check
cd frontend
npm.cmd run types:check
npm.cmd test
npm.cmd run build
```

Expected: 无差异、通过。

### Task G.2：通用性、Replay 和本地端到端验收

**Goal:** 用不依赖外网的本地示范站点证明“新增网站不改业务代码”，并覆盖文件/邮件/登录/视觉/主机路由。

**Dependencies:** Stage A-F。

**Files:**

- Create: `tests/fixtures/sites/site_a/index.html`
- Create: `tests/fixtures/sites/site_b/index.html`
- Create: `tests/fixtures/sites/site_c/index.html`
- Create: `tests/fixtures/sites/download/index.html`
- Create: `tests/e2e/conftest.py`
- Create: `tests/e2e/test_generic_three_sites.py`
- Create: `tests/e2e/test_file_email_chain.py`
- Create: `tests/e2e/test_visual_and_host_routing.py`
- Create: `tests/regression/fixtures/action_step_results/fixture.json`
- Create: `tests/regression/fixtures/visual_ambiguous/fixture.json`
- Modify: `scripts/run_regression.py`
- Modify: `tests/regression/test_replay.py`
- Modify: `frontend/tests/action-schema.test.mjs`
- Modify: `pytest.ini`

**Interfaces:**

- Consumes: local HTTP server、fake SMTP、mock WebSocket Agent、fake visual provider。
- Produces: 可重复的通用验收结果；所有需要真实浏览器/桌面的测试都有 marker 和 skip 原因。

- [ ] **Step 1: 写三站点测试。** 三个站点 DOM 结构不同；同一 Web+Data+Files workflow 动作集合通过参数/locator 差异完成提取、过滤和 CSV/XLSX；不新增 app/frontend 业务文件。

```python
@pytest.mark.e2e
def test_three_sites_use_same_action_catalog(local_site_server) -> None:
    for site in ("site_a", "site_b", "site_c"):
        plan = build_plan_for(site, local_site_server.url(site))
        assert validate_workflow_draft(plan).valid is True
        result = run_structured_plan(plan)
        assert result.status == "success"
        assert result.artifacts[0]["size"] > 0
```

- [ ] **Step 2: 写文件+邮件链。** local fake SMTP、真实本地文件、DeliveryRecord；断言附件 sha256 等于磁盘文件，message-id 非空，required delivery 成功。

- [ ] **Step 3: 写视觉+主机路由测试。** fake visual candidate 高置信通过；双候选转 HumanTask；VM/local 组合按 policy 路由且 host id 固定。

- [ ] **Step 4: 写通用 action 扩展测试。** 测试注册一个临时 `test.echo` action 后 catalog API 和 schema form 都能显示；不需要修改前端源文件。

- [ ] **Step 5: 运行 focused e2e。**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/e2e -q -m "e2e and not desktop and not vm"
```

Expected: PASS；无浏览器时相关用例 skip 并明确原因。

- [ ] **Step 6: 运行 Replay。**

```powershell
.\.venv\Scripts\python.exe scripts\run_regression.py
```

Expected: `passed == total`，`pass_rate=100%`。

### Task G.3：文档、打包和环境降级说明

**Goal:** 让最终产物可安装、可诊断、可回滚，不依赖开发机隐藏状态。

**Dependencies:** Stage A-F、G.1/G.2。

**Files:**

- Modify: `README.md`
- Modify: `desktop/README.md`
- Modify: `.env.example`
- Modify: `scripts/build_backend.py`
- Modify: `scripts/build_agent.py`
- Create: `docs/superpowers/reports/2026-10-28-personal-generic-rpa-acceptance.md`
- Create: `scripts/check_runtime_boundaries.py`
- Create: `tests/test_runtime_boundaries.py`

**Interfaces:**

- Consumes: 最终 action catalog、migrations、frontend build、packaging config。
- Produces: 面向个人用户的启动/备份/升级/恢复说明；运行时边界静态检查。

- [ ] **Step 1: 写边界测试。** 扫描 `app/actions`、`app/engine`、`desktop_agent`，禁止运行时导入 `app.agents.*`、OpenAI 或任意 LLM 客户端；禁止生成的 action handler 拼接执行 `powershell/cmd/python -c`。

- [ ] **Step 2: 更新 README。** 去除 SaaS/客户交付叙事，说明单用户、SQLite、无 Docker、旧流程模式和备份恢复。

- [ ] **Step 3: 验证打包不遗漏。** backend 包含 `app/actions` 与 xlsx 模块；desktop-agent 不包含 Playwright/FastAPI/SQLAlchemy，仍可启动并重连。

```powershell
.\.venv\Scripts\python.exe scripts\build_agent.py --dry-run
.\.venv\Scripts\python.exe scripts\build_backend.py --dry-run
```

Expected: 命令列出必要模块且排除项正确。真实 build 不在本轮自动执行，除非用户明确要求。

- [ ] **Step 4: 最终验证。**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm.cmd test
npm.cmd run build
cd ..
.\.venv\Scripts\python.exe scripts\run_regression.py
.\.venv\Scripts\python.exe scripts\migrate_db.py --status
```

Expected:

- 后端 0 failures；旧 738 项不减少。
- 前端 0 failures，build 成功。
- Replay 100%。
- schema version 8，无 pending migration。

## 文件级改动总表

### 新增

```text
app/actions/__init__.py
app/actions/adapters.py
app/actions/catalog.py
app/actions/conditions.py
app/actions/data.py
app/actions/email.py
app/actions/files.py
app/actions/legacy.py
app/actions/mcp.py
app/actions/protocol.py
app/actions/registry.py
app/actions/runtime.py
app/actions/validation.py
app/actions/variables.py
app/actions/web.py
app/actions/windows.py
app/actions/xlsx_minimal.py
app/api/human_tasks.py
app/api/settings.py
app/api/visual.py
app/engine/host_router.py
app/engine/preflight.py
app/engine/visual_fallback.py
app/models/app_setting.py
app/models/browser_profile.py
app/models/delivery_record.py
app/models/human_task.py
app/models/workflow_version.py
app/schemas/delivery.py
app/schemas/execution_policy.py
app/schemas/human_task.py
app/schemas/settings.py
app/schemas/workflow_version.py
app/services/__init__.py
app/services/checkpoint_service.py
app/services/email.py
app/services/execution_versions.py
app/services/human_tasks.py
app/services/workflow_authoring.py
app/services/workflow_versions.py
app/visual/__init__.py
app/visual/local_ocr.py
app/visual/provider.py
app/visual/redaction.py
app/visual/remote_vlm.py
desktop_agent/capabilities.py
frontend/src/app/queryClient.tsx
frontend/src/app/router.tsx
frontend/src/api/actionCatalog.ts
frontend/src/api/connections.ts
frontend/src/api/dryRun.ts
frontend/src/api/humanTasks.ts
frontend/src/api/queryKeys.ts
frontend/src/api/settings.ts
frontend/src/api/versions.ts
frontend/src/components/connections/*
frontend/src/components/dashboard/*
frontend/src/components/executions/*
frontend/src/components/pending/*
frontend/src/components/studio/*
frontend/src/pages/AdvancedToolsPage.tsx
frontend/src/pages/ConnectionsPage.tsx
frontend/src/pages/DashboardPage.tsx
frontend/src/pages/PendingPage.tsx
frontend/src/store/useWorkflowStudioStore.ts
frontend/src/types/api.generated.ts
frontend/src/types/api.ts
scripts/check_runtime_boundaries.py
scripts/export_openapi.py
tests/test_action_protocol.py
tests/test_action_registry.py
tests/test_action_catalog_contract.py
tests/test_action_variables.py
tests/test_data_actions.py
tests/test_file_actions.py
tests/test_xlsx_compat.py
tests/test_workflow_versions.py
tests/test_workflow_authoring_api.py
tests/test_workflow_validation_api.py
tests/test_execution_policy.py
tests/test_preflight.py
tests/test_single_itinerary.py
tests/test_human_tasks.py
tests/test_human_task_api.py
tests/test_checkpoint_service.py
tests/test_visual_protocol.py
tests/test_visual_redaction.py
tests/test_visual_fallback.py
tests/test_email_service.py
tests/test_email_api.py
tests/test_delivery_records.py
tests/test_host_capabilities.py
tests/test_host_router.py
tests/test_host_pinning.py
tests/e2e/conftest.py
tests/e2e/test_generic_three_sites.py
tests/e2e/test_file_email_chain.py
tests/e2e/test_human_login_resume.py
tests/e2e/test_visual_and_host_routing.py
tests/support/fake_smtp.py
tests/fixtures/legacy/*
tests/fixtures/sites/*
tests/regression/fixtures/action_step_results/fixture.json
tests/regression/fixtures/visual_ambiguous/fixture.json
```

### 修改

```text
README.md
.env.example
pytest.ini
requirements.txt
requirements.lock
app/config.py
app/database.py
app/main.py
app/migrations.py
app/models/__init__.py
app/models/agent.py
app/models/credential.py
app/models/execution.py
app/models/workflow.py
app/protocols/structured.py
app/schemas/__init__.py
app/schemas/agent.py
app/schemas/workflow.py
app/api/__init__.py
app/api/agents.py
app/api/agent_ws.py
app/api/executions.py
app/api/workflows.py
app/agents/web_catalog.py
app/agents/desktop_catalog.py
app/agents/plan_summary.py
app/agents/web_planner.py
app/engine/artifacts.py
app/engine/checkpoints.py
app/engine/dag.py
app/engine/desktop_dispatcher.py
app/engine/engine.py
app/engine/execution_manager.py
app/engine/hybrid_executor.py
app/engine/router.py
app/engine/runner.py
app/engine/task_runtime.py
app/engine/transforms.py
app/engine/web_executor.py
app/notifications/notifier.py
app/regression/replay.py
app/scheduler/jobs.py
desktop_agent/action_validator.py
desktop_agent/actions/__init__.py
desktop_agent/client.py
desktop_agent/config.py
desktop_agent/config.json.example
desktop_agent/main.py
frontend/package.json
frontend/package-lock.json
frontend/src/main.tsx
frontend/src/App.tsx
frontend/src/index.css
frontend/src/components/Layout.tsx
frontend/src/components/workflows/WorkflowConfigPanel.tsx
frontend/src/components/workflows/WorkflowEditorNode.tsx
frontend/src/components/workflows/WorkflowRecordingPanel.tsx
frontend/src/components/WorkflowDagPreview.tsx
frontend/src/lib/execution.ts
frontend/src/lib/workflow-draft.ts
frontend/src/lib/workflow-editor.ts
frontend/src/pages/ExecutionDetail.tsx
frontend/src/pages/ExecutionsPage.tsx
frontend/src/pages/SettingsPage.tsx
frontend/src/pages/WorkflowEditor.tsx
frontend/src/pages/WorkflowsPage.tsx
frontend/src/types/workflow.ts
scripts/build_agent.py
scripts/build_backend.py
scripts/migrate_db.py
scripts/run_regression.py
tests/conftest.py
tests/test_actions.py
tests/test_agent_validator.py
tests/test_agents_api.py
tests/test_api_4a.py
tests/test_api_4b.py
tests/test_browser_observer.py
tests/test_desktop_catalog.py
tests/test_desktop_planner.py
tests/test_dag.py
tests/test_engine.py
tests/test_execution_control_api.py
tests/test_execution_manager.py
tests/test_hybrid_executor.py
tests/test_legacy_compatibility.py
tests/test_mcp_integration.py
tests/test_migrations.py
tests/test_task_runtime.py
tests/test_transforms.py
tests/test_web_catalog.py
tests/test_web_executor.py
tests/test_web_structured_chat.py
tests/test_workspace_isolation.py
tests/regression/test_replay.py
```

### 删除或退役

```text
frontend/src/components/workflows/WorkflowCard.tsx
```

仅当所有引用已移除后删除。

以下文件不删除，但标记 `legacy_only`，不得进入新 catalog：

```text
desktop_agent/actions/fetch_kugou_lyrics.py
desktop_agent/profiles/kugou.json
desktop_agent/actions/create_document.py
desktop_agent/actions/create_spreadsheet.py
desktop_agent/actions/insert_image.py
```

兼容期结束后如需物理删除，必须另立迁移计划并先确认没有历史版本引用。

## API 契约总表

| Method | Path | Request | Response | 兼容性 |
|---|---|---|---|---|
| GET | `/api/action-catalog` | optional `executor` | `{version, actions}` | 新增 |
| POST | `/api/workflows/draft-from-goal` | goal/start_url/session_id | draft plan + observations + warnings | 新增 |
| POST | `/api/workflows/{id}/observe` | url/mode | ObservationSnapshot | 新增 |
| POST | `/api/workflows/{id}/validate` | draft_revision optional | `{valid, errors, warnings}` | 新增 |
| POST | `/api/workflows/{id}/test-step` | step_id + draft | StepResult | 新增 |
| POST | `/api/workflows/{id}/dry-run` | draft | ExecutionResult，不写正式历史 | 新增 |
| POST | `/api/workflows/{id}/publish` | none | WorkflowVersionSummary | 兼容保留 workflow_id/status |
| GET | `/api/workflows/{id}/versions` | pagination | `{items}` | 新增 |
| POST | `/api/workflows/{id}/rollback` | version_id | active version summary | 新增 |
| POST | `/api/workflows/{id}/run` | optional version_id | ExecutionLog summary | 新增别名 |
| POST | `/api/executions/{workflow_id}/run` | none | ExecutionLog summary | 保留旧路径，内部同逻辑 |
| GET | `/api/executions` | filters | list | 保留，增加 version/host/delivery |
| GET | `/api/executions/{id}` | none | ExecutionDetail | 新增；保留 `/{id}/detail` alias |
| GET | `/api/executions/{id}/events` | after cursor | event list | 新增 |
| GET | `/api/executions/{id}/live` | WebSocket | events | 保留 |
| POST | `/api/executions/{id}/pause` | none | ExecutionLog | 保留 |
| POST | `/api/executions/{id}/resume` | none | ExecutionLog | 保留 |
| POST | `/api/executions/{id}/cancel` | reason optional | ExecutionLog | 新增 |
| GET | `/api/human-tasks` | status/kind | `{items}` | 新增 |
| GET | `/api/human-tasks/{id}` | none | HumanTask | 新增 |
| POST | `/api/human-tasks/{id}/resume` | decision/verification | HumanTask + execution status | 新增 |
| POST | `/api/human-tasks/{id}/cancel` | reason | HumanTask | 新增 |
| GET | `/api/settings/email` | none | SMTP 设置，不含明文密码 | 新增 |
| PUT | `/api/settings/email` | SMTP 设置 | 脱敏响应 | 新增 |
| POST | `/api/settings/email/test` | none | DeliveryResult | 新增 |
| POST | `/api/settings/email/test-send` | recipient | DeliveryResult | 新增 |
| GET | `/api/hosts` | none | hosts + capabilities + environment | 新增，兼容 `/api/agents` |
| GET | `/api/browser-profiles` | none | profiles + login_state | 新增 |
| POST | `/api/browser-profiles/{id}/login` | none | login session status | 新增 |
| GET | `/api/settings/visual` | none | provider + redaction settings | 新增 |
| PUT | `/api/settings/visual` | provider settings | 脱敏响应 | 新增 |

共享类型以 OpenAPI 为唯一来源。手写前端 `WorkflowRecord` 等类型在 G.1 后仅做别名或适配器，不再复制字段定义。

## 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| 历史工作流回填错误 | 发布后旧流程无法运行或行为改变 | 先夹具、后迁移；保留 legacy_action handler；迁移前备份；迁移后可回放旧快照 |
| 统一 registry 重构面过大 | Web/Desktop/MCP 回归 | 先 adapter 包装现有 executor，再逐步移内部 if/else；每步保持现有测试通过 |
| 版本固定与调度并发 | 两次 Cron 运行读到不同版本或抢占 Profile | 发布/启动事务化；active pointer 原子读取；同 workflow 单实例；Profile/Agent 单会话 |
| 邮件假成功 | 业务误判完成 | DeliveryRecord + provider message-id + 附件 sha256 + required_delivery 最终判定 |
| 文件格式不兼容 | XLSX 打不开 | 用标准 OOXML 结构测试；Excel/WPS 手动验收作为可选环境项 |
| 视觉误判 | 点错按钮或泄露截图 | 默认 disabled；脱敏；高置信唯一；高风险人工；执行后验证；Replay |
| VM 跨宿主恢复 | 窗口/Cookie 丢失 | 运行内 host pinning；恢复要求同一 host；显式迁移需用户确认 |
| 前端状态互相覆盖 | 草稿丢失或保存旧数据 | 服务端 Query 与 Studio Zustand 分离；draft_revision 409 |
| 动态表单不支持复杂 schema | 用户无法编辑动作 | 通用控件 + scoped JSON fallback；未知 schema 不静默丢失 |
| 无 Git、无真实环境 | 难以审计和验证交互功能 | 每任务 checklist + 测试命令；数据库备份；真实桌面/VM/SMTP 使用 marker 和 fake 环境 |

## 回滚方式

1. **应用代码回滚：** 新表和新列均为加法式，旧版本代码应可忽略；回归测试验证旧 API 仍可启动。
2. **数据库回滚：** 迁移前由 `migrate_db.py` 生成可验证备份；需要移除 schema 时恢复备份，不编写不可靠的 down migration。
3. **工作流回滚：** 只切换 `active_version_id`；不删除、不改写历史 `WorkflowVersion`。
4. **动作兼容回滚：** 新 registry 可读取旧 alias；如某个通用动作有缺陷，可在 catalog 中禁用该版本动作，预检阻断相关版本，而不是执行任意替代代码。
5. **前端回滚：** 保留 `/advanced/chat` 和现有 `/apps` 路由；如果 Studio 出问题，用户仍可查看旧工作流和旧代码预览。
6. **视觉回滚：** 设置 provider 为 `disabled`，立即停止远程截图和视觉执行；已有人工任务仍可取消。
7. **VM 回滚：** 将 `host_policy=local_only`，隔离步骤预检为 blocked；不修改历史 host 记录。

## 最终验收命令

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -m "not desktop and not vm"
.\.venv\Scripts\python.exe scripts\run_regression.py
.\.venv\Scripts\python.exe scripts\migrate_db.py --status
.\.venv\Scripts\python.exe scripts\export_openapi.py --check
cd frontend
npm.cmd test
npm.cmd run build
npm.cmd run types:check
```

预期：

- 后端无 failure；当前兼容基线不低于 997 passed。
- 需要真实 Windows/VM/外网 SMTP 的测试明确 skip，原因可读。
- Replay `pass_rate=100%`。
- schema version 10；重复迁移 `executed=[]`。
- OpenAPI 和生成 TS 无 drift；Stage G.1 未实现前此项保持未通过。
- 前端测试和构建成功。

## 通用性验收清单

- [ ] 三个结构不同的本地网页使用同一动作集合创建工作流，不修改后端专用业务代码。
- [ ] 新增一个通用 action 后，因 action catalog 自动出现，无需新增前端表单组件。
- [ ] 同一 `filter/write_xlsx/email.send` 可被任意工作流复用。
- [ ] `code_step`、酷狗、WPS、微信等专用动作不能出现在新 catalog 或新工作流发布校验中。
- [ ] 运行时模块不导入 LLM/agents 包。
- [ ] 旧 code workflow 仍可从历史版本运行。
- [ ] 空列表、空文件、缺失附件和 SMTP 失败都不会成功。

## 第一阶段范围（用户批准后首先执行）

第一阶段只做 Stage 0、Task A.1-A.5 和 Task B.1 的最小可运行闭环：

1. 迁移到 workflow version/policy 数据模型。
2. 建立统一 ActionDefinition/registry/StepResult。
3. 新 workflow 固定 structured；旧 workflow 固定 legacy snapshot。
4. 执行只读版本、预检、单实例和 fail-closed。
5. 保留变量/条件/循环能力并接到统一 runtime。
6. 不先做前端大改、视觉和 VM。

第一阶段的明确退出标准：

- 旧 738 项测试仍通过。
- 新发布 workflow 有不可变 version。
- 修改草稿不影响当前运行。
- 含 `code_step`/特殊动作的新 structured 计划无法发布。
- 旧的 code workflow 可从迁移版本继续运行。
- registry contract tests 覆盖每个已注册 action，且新 catalog 中不得出现没有 handler 的 action；Web/Data/Files/Email/Windows/MCP 六类完整性是 Stage B 退出条件。

## Self-Review

### Spec coverage

| 设计章节 | 覆盖任务 |
|---|---|
| 5 执行模式与单一行程 | A.1-A.5 |
| 6 Workflow/WorkflowVersion/ExecutionPolicy | A.1-A.4 |
| 7 通用动作协议 | A.3 |
| 8 基础动作清单 | B.2-B.6 |
| 9 变量、条件、过滤 | B.1-B.2 |
| 10 工作流创建 | C.0-C.4、G.2 |
| 11 执行引擎/预检/并发 | A.4、D.2、F.2 |
| 12 登录和人工接管 | D.1-D.3 |
| 13 视觉兜底 | E.1-E.2 |
| 14 主机与按需 VM | F.1-F.3 |
| 15 文件、数据和邮件 | B.2-B.6 |
| 16 错误、重试和检查点 | A.3-A.5、D.2 |
| 17 安全与隐私 | A.3、B.3-B.6、E.1、G.3 |
| 18 数据模型 | A.1 |
| 19 API | A.2、B.6、C.0-C.6、D.1、F.1、G.1 |
| 20 前端设计 | C.1-C.6、D.3、E.1-E.2、F.3 |
| 21 测试策略 | 0.1、A.3、B.2-B.6、D.3、E.2、G.1-G.3 |
| 22 实施阶段 A-F | 本计划 Stage A-F |
| 23 风险 | “风险与应对” |
| 24 完成定义 | “最终验收命令”和“通用性验收清单” |

### Placeholder scan

占位符扫描未发现未定义步骤。每个任务均有明确文件、接口、测试或验收结果。

### Type consistency

- `StepStatus` 五种状态与设计一致。
- `ExecutionPolicy.mode`、`visual_fallback`、`host_policy` 名称在 API、前端和 execution manager 中保持一致。
- `WorkflowVersion` 使用 `workflow_version_id` 作为执行引用。
- `HumanTask` 使用 `execution_id/step_id/kind/instruction/payload/screenshot_path/status/created_at/resolved_at/expires_at`。
- `DeliveryRecord` 使用设计字段，不新增并行命名。
- `ActionRegistry.catalog(include_legacy=False)` 是前后端新动作可见性的唯一开关。

