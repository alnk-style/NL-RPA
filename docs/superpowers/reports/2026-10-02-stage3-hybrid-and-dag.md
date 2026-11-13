# 阶段 3 第二部分：混合执行与 DAG

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 阶段 3 任务 4–5。

## 1. 交付内容

### 1.1 步骤级执行器路由

- 新增 `app/engine/hybrid_executor.py`：`HybridExecutor` 读取
  `structured_plan.steps[].executor`（`web` / `windows` / `code` /
  `mcp`），按步骤把调用分发给网页执行器或桌面执行器。
- `ExecutionManager._executor_for()` 不再按 `workflow.executor_type`
  整体路由：混排、条件步骤或非顺序依赖的流程走 `HybridExecutor`；
  纯网页/纯桌面且为顺序链的流程继续走原有执行路径，保留既有修复
  与兼容行为。
- `HybridExecutor` 通过 `WebExecutor.browser_session()` 复用同一个
  浏览器页：`web → windows → web` 的流程不会因为中间插入桌面步骤
  而丢失网页会话；桌面步骤通过单步派发复用 `DesktopRepairCoordinator`
  的自动修复，但修复结果只回写临时视图，不会覆盖完整混合计划。

### 1.2 DAG 执行

- 新增 `app/engine/dag.py`：
  - `normalize_execution_plan()` 校验 step_id、执行器、依赖
    存在性与无环性，并把 `executor` 别名（`desktop` 等）归一化。
  - `merge_dag_definition()` 在步骤没有声明 `depends_on` 时用
    `Workflow.dag_definition` 的节点/连线补齐依赖与节点条件；
    步骤自身声明了依赖时以步骤声明为准，避免前端删除连线后旧 DAG
    定义把依赖“复活”。
  - `evaluate_condition()` 支持变量比较、步骤状态（`step_success` /
    `step_failed` / `step_status`）以及 `all` / `any` / `not` 组合。
- 调度器遵守拓扑顺序：依赖未完成的步骤不启动；依赖失败的步骤连同
  下游记为 `skipped`（`skip_reason=dependency_failed`）；条件不满足的
  步骤记为 `skipped`（`skip_reason=condition_false`）；互不依赖的
  分支可以并行，共享资源（同一个浏览器页、同一个桌面 Agent）的
  步骤自动串行，保证并行分支安全。
- 步骤日志按声明顺序聚合，失败步骤写入 `details.failed_step`，
  已完成幂等键写入 `details.completed_idempotency_keys`。

### 1.3 检查点与续跑

- `ExecutionManager` 的检查点从“仅桌面流程”扩展到所有结构化流程
  （纯网页、混排、桌面），复用阶段 2 的 `idempotency_key` + `skipped`
  语义：命中已完成幂等键的步骤直接记录为 `skipped`，从失败步骤继续。
- `HybridExecutor` 在步骤结束后把进度写回 `ExecutionLog.step_logs`，
  暂停时 `_write_pause_checkpoint()` 能从已有进度生成检查点；服务
  重启导致的 `paused` 也能从步骤日志合成检查点后继续。

### 1.4 发布口子

- `app/api/workflows.py` 的发布校验改为 `_workflow_publishable()`：
  - 有生成代码仍可发布（旧路径不回归）；
  - 纯网页结构化流程仍按 `WEB_STRUCTURED_ACTIONS` 校验；
  - 混排流程按步骤执行器分别校验：网页步骤走网页动作目录与参数
    校验，桌面步骤必须在桌面动作目录中，依赖图必须无环。

### 1.5 前端

- 流程编辑器新增“录制”面板
  （`frontend/src/components/workflows/WorkflowRecordingPanel.tsx`）：
  - 网页/桌面两种录制模式；
  - “开始录制 / 结束录制 / 刷新 / 丢弃”入口；
  - 桌面录制提供“拾取控件”，直接调用 `POST /api/recordings/{id}/pick`；
  - 网页录制显示复用同一会话的 `record_browser.py --recording-id`
    命令，可一键复制；
  - 展示录制草稿的步骤、事件数、丢弃数与全部警告，并可一键把草稿
    合并（重映射 step_id 与依赖）到当前编辑器计划。
- 步骤节点显示“网页/桌面/MCP/代码”执行器标识，混排流程可直接
  在画布上区分。
- `scripts/record_browser.py` 支持 `--recording-id` 复用编辑器发起的
  录制会话，并支持 `--no-finish` 与 `--create-workflow`。

## 2. 测试

| 项目 | 结果 |
| --- | --- |
| `tests/test_dag.py` | 8 项通过（依赖解析、环、DAG 定义合并、条件求值） |
| `tests/test_hybrid_executor.py` | 9 项通过（步骤路由、页复用、并行分支、条件/依赖跳过、续跑、Manager 检查点） |
| `tests/test_web_executor.py` | 新增页面复用用例：传入 `page=` 时不再启动新浏览器 |
| 全量后端套件 | 416 通过，21 跳过，0 失败 |
| 前端逻辑测试 | 34 通过，0 失败（新增录制草稿合并与混排预览用例） |
| 前端构建 | `tsc --noEmit` + `vite build` 通过 |

全量后端命令：`.venv\Scripts\python -m pytest -q --basetemp=.tmp/pytest`

## 3. 真实环境验收

以下两步需要带浏览器和 WPS 的交互式 Windows 会话，本机沙箱无法
启动浏览器进程，因此未在本轮执行，命令与检查点如下。

### 3.1 网页录制 → 发布 → 执行

1. 启动后端，登录拿到 JWT。
2. 录制：

   ```bash
   python scripts/record_browser.py --url https://example.com \
       --api http://127.0.0.1:8000 --token <JWT>
   ```

   脚本结束时输出 `workflow_id`。
3. 也可以从流程编辑器“录制”面板开始会话，然后复制面板里的
   `--recording-id` 命令录制，结束后在面板里“应用到编辑器”。
4. 发布并执行：

   ```bash
   curl -X POST http://127.0.0.1:8000/api/workflows/<id>/publish \
       -H "Authorization: Bearer <JWT>"
   curl -X POST http://127.0.0.1:8000/api/executions/<id>/run \
       -H "Authorization: Bearer <JWT>"
   ```

### 3.2 桌面拾取 WPS 流程

1. 启动桌面 Agent，白名单加入 `wps.exe`（或对应启动方式）。
2. 创建桌面录制会话：

   ```bash
   curl -X POST http://127.0.0.1:8000/api/recordings \
       -H "Authorization: Bearer <JWT>" \
       -H "Content-Type: application/json" \
       -d '{"mode":"windows","name":"WPS 文档创建"}'
   ```
3. 依次对“新建文档”“插入图片”等控件调用
   `POST /api/recordings/{id}/pick`，再调用
   `POST /api/recordings/{id}/finish`（`create_workflow=true`）。
4. 发布并执行生成的流程，确认桌面步骤按拾取到的
   AutomationId/控件名命中。

### 3.3 混排与 DAG 验收

构造一个同时包含网页与桌面步骤、且带分支依赖的流程：

```json
{
  "steps": [
    {"step_id": 1, "executor": "web", "action": "goto",
     "params": {"url": "https://example.com"}, "depends_on": []},
    {"step_id": 2, "executor": "windows", "action": "launch_app",
     "params": {"path": "wps.exe"}, "depends_on": []},
    {"step_id": 3, "executor": "web", "action": "click",
     "params": {}, "locator": {"strategy": "role", "value": "button"},
     "depends_on": [1]},
    {"step_id": 4, "executor": "windows", "action": "type_text",
     "params": {"text": "季度总结"}, "depends_on": [2]},
    {"step_id": 5, "executor": "web", "action": "screenshot",
     "params": {}, "depends_on": [3, 4]}
  ]
}
```

预期：步骤 1/2 分支并行推进，3 只等待 1，4 只等待 2，5 必须等
3 与 4 都成功；执行日志里 `executor` 字段同时出现 `web` 与
`windows`。把步骤 4 改成失败或给步骤 5 加
`"condition": {"type": "variable", "name": "flag", "equals": true}`，
可验证依赖失败跳过与条件跳过；随后用 `resume` 验证检查点续跑。
