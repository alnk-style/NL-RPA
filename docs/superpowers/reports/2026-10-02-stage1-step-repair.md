# 阶段 1 第三部分：单步修复与步骤级接口

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 第 4.3、6.1 节

## 1. 交付内容

1. `DebugAgent.repair_steps()`：结构化步骤修复提示词与解析，
   只输出失败步骤及其后续步骤。
2. `WebExecutor` 执行内修复回路：失败后重新观察当前页面，调用
   修复 Agent，校验并合并计划，从失败步骤原地续跑。
3. `BrowserObserver.observe_page()`：对已打开的页面做一次观察，
   供修复阶段复用。
4. 步骤级接口：`POST /api/workflows/{id}/steps/{step_id}/validate`
   与 `POST /api/workflows/{id}/steps/{step_id}/repair`。
5. 修复结果持久化：`ExecutionManager` 把 `repaired_plan` 写回
   Workflow，修复历史写入 `step_logs` 与执行明细。
6. 新配置：`WEB_REPAIR_ATTEMPTS`（默认 2），沿用
   `STEP_LEVEL_REPAIR` 作为总开关。

## 2. 执行内修复流程

```text
步骤失败
  -> 失败分类是否可修复？
     否 -> 直接失败（auth_required / permission_denied / business_error）
     是 -> 是否开启 STEP_LEVEL_REPAIR 且未超过修复轮数？
         否 -> 直接失败
         是 -> 观察当前页面（observe_page）
             -> DebugAgent.repair_steps(plan, failure, observation)
             -> 合并「已成功前缀 + 修复后的剩余步骤」
             -> validate_web_plan 重新校验动作、参数与依赖
             -> 从失败步骤继续执行，前缀不重跑
```

关键规则：

1. 修复前先观察当前页面，候选定位只能来自真实观察结果。
2. 修复结果必须重新通过 `validate_web_plan()`；未知动作、未知参数、
   重复 `step_id`、悬空依赖都会被拒绝，原失败照常上报。
3. 只替换失败步骤及其后续步骤，已成功的前缀不重新执行。
4. 单次执行最多修复 `WEB_REPAIR_ATTEMPTS` 轮；修复后再次失败会
   继续观察、继续修复，直到上限。
5. 登录、权限、业务断言失败不进入自动修复，避免掩盖需要人工
   处理的问题。

## 3. 修复记录

每个修复轮次写入三类记录：

| 位置 | 内容 |
| --- | --- |
| 失败步骤日志 | 状态 `repaired`，保留原失败分类、错误与截图 |
| `auto_repair` 日志 | 修复轮次、原始错误、动作与消息 |
| `details.repairs` / `details.repaired_plan` | 修复历史与最终计划 |

`ExecutionManager` 在持久化执行结果时，把 `details.repaired_plan`
写回 `workflow.structured_plan`，所以后续执行直接使用修复后的计划。
执行被暂停或取消时不会写回。

## 4. 步骤级接口

### 4.1 校验

```http
POST /api/workflows/{id}/steps/{step_id}/validate
```

返回规范化后的步骤、执行器、定位候选列表与步骤序号；计划不合法
返回 400，步骤不存在返回 404。

### 4.2 修复

```http
POST /api/workflows/{id}/steps/{step_id}/repair
{
  "url": "https://example.com",
  "error": "未找到元素：css=#old",
  "failure_category": "locator_not_found",
  "observation": null
}
```

行为：

1. 由 `STEP_LEVEL_REPAIR` 开关保护，关闭时返回 403。
2. 提供 `observation` 时直接使用；否则用 `url` 或失败步骤之前
   最近的 `goto` 地址重新观察页面。重新观察要求
   `OBSERVE_BEFORE_PLAN` 已开启。
3. 调用 `DebugAgent.repair_steps()`，合并并校验计划后写回
   Workflow，返回修复后的完整步骤列表。
4. 观察失败返回 502（带失败分类），修复结果校验失败返回 400。

## 5. 验证

| 项目 | 结果 |
| --- | --- |
| DebugAgent 修复提示与解析 | `tests/test_agents_3c.py` 新增 2 项 |
| 执行内修复回路 | `tests/test_web_executor.py` 新增 5 项 |
| 步骤级接口 | `tests/test_step_repair_api.py`（5 项） |
| 修复计划持久化 | `tests/test_execution_manager.py` 新增 1 项 |
| 全量套件 | 321 通过、19 跳过、0 失败 |

覆盖的关键行为：修复后只重跑失败步骤、前缀只执行一次、修复轮数
上限、未知动作修复被拒绝、登录失败不触发修复、接口开关保护、
页面重观察与计划写回。

下一步：阶段 1 收尾。把观察摘要与网页动作目录接入结构化规划器，
让对话生成直接产出 `WEB_ACTIONS` 步骤，并按 B 站消息抓取试点
完成端到端验收。
