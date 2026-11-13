# 阶段 2 第二部分：桌面巡检增强与修复范围扩展

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 阶段 2 任务 3、4

## 1. 交付内容

1. `desktop_agent/uia_observation.py`：巡检与失败候选共用同一套控件字段
   提取和定位评分，避免规划器看到的字段、分数与执行现场漂移。
2. `desktop_agent/inspector.py`：巡检元素新增类名、父路径、边界框、
   可见/可用状态、值、索引、UI Automation Patterns 和候选定位评分。
3. `desktop_agent/actions/_uia.py`：失败候选改为结构化详情，按失败时的
   定位条件排序；找到控件时记录 `last_locator`（最后可用定位）。
4. `click_element`、`read_text`、`wait`、`activate_window`：失败详情统一
   回报结构化候选、候选标签和最后可用定位；窗口类失败回报可见窗口候选。
5. `app/engine/desktop_repair.py`：可自动修复的动作从 2 个扩展到 4 个，
   结构化候选和最后定位透传给修复规划器并写入修复记录。
6. `app/agents/desktop_planner.py`：规划与修复提示词消费 `best_locator`、
   候选详情和最后可用定位。

## 2. 巡检字段与定位评分

每个控件元素在原有 `depth/name/automation_id/control_type` 基础上新增：

| 字段 | 说明 |
| --- | --- |
| `index` | 控件在本次巡检结果中的序号 |
| `class_name` | 窗口类名或控件类名 |
| `value` | `get_value` / `window_text` / legacy Value 的首个非空值 |
| `visible` / `enabled` | 实时可见与可用状态 |
| `rectangle` | `{left, top, right, bottom}` 边界框 |
| `parent_path` | 从窗口到直接父级的路径，最多 6 层 |
| `patterns` | 尽力读取的 UI Automation Patterns |
| `locators` | 按评分降序的候选定位 |
| `best_locator` | 评分最高的候选定位 |

候选定位策略与基础分：

| 策略 | 基础分 | 说明 |
| --- | --- | --- |
| automation_id | 100 | 唯一时最可靠 |
| name + control_type | 92 | 名称配合控件类型 |
| name | 80 | 仅控件名称 |
| control_type | 45 | 名称与 AutomationId 都缺失时兜底 |

匹配数量为 1 时保留基础分；匹配多个降 45 分；读取不到降 35 分。
评分只用于排序，执行器仍会逐个验证候选并记录实际命中项。

## 3. 候选详情与最后可用定位

`describe_candidates()` 现在返回结构化候选，字段示例：

```json
{
  "index": 3,
  "depth": 2,
  "name": "保存",
  "automation_id": "saveBtn",
  "control_type": "Button",
  "class_name": "Button",
  "enabled": true,
  "visible": true,
  "score": 124,
  "locators": [{"strategy": "automation_id", "value": "saveBtn", "count": 1, "unique": true, "score": 100}],
  "best_locator": {"strategy": "automation_id", "value": "saveBtn", "count": 1, "unique": true, "score": 100}
}
```

候选按失败时的定位条件打分排序：AutomationId 精确命中加 100，名称精确
命中加 80、包含加 55，其余按相似度折算；控件类型命中加 20，可见和可用
各加 4。同分时优先 `best_locator` 评分更高的候选。

失败详情约定：

| 字段 | 说明 |
| --- | --- |
| `candidates` | 结构化候选（字符串候选仍兼容） |
| `candidate_labels` | 人类可读标签，如 `保存(Button)` |
| `last_locator` | 本任务内最后一次成功定位的条件 |

`find_element()` 命中控件时把 `automation_id/name/control_type/window_handle`
写入任务上下文；`click_element` 与 `read_text` 的成功、失败结果都会带回。
`wait` 等待 `element_appears` 超时回报控件候选，等待 `window_appears`
超时回报可见窗口候选；`activate_window` 找不到窗口时回报可见窗口候选
（title、class_name、pid、process_name）。

失败步骤事件新增 `params`，修复规划器无需只依赖完整原流程推断条件。

## 4. 修复范围扩展

可进入自动修复的动作：

| 动作 | 进入修复的条件 |
| --- | --- |
| click_element | 有候选，或错误包含 UI Automation / no matching / not found |
| read_text | 同上 |
| wait | `element_appears` 超时且有候选或错误匹配；`window_appears` 超时 |
| activate_window | 找不到窗口且回报了窗口候选 |

以下失败仍不进入修复，避免掩盖需要人工处理的问题：

- `wait window_disappears` 超时（目标窗口仍存在，修复无法消除）。
- 白名单、权限、业务断言类失败。
- 没有候选且不匹配定位失败特征的执行异常。

修复流程：

```text
失败步骤（params + 结构化候选 + last_locator）
  -> 可修复判定（动作 + 错误特征 + 候选）
  -> DesktopPlannerAgent.repair()（候选详情与最后定位进 Prompt）
  -> validate_desktop_plan() 重新校验动作、参数与白名单
  -> 从失败步骤续跑，已成功前缀不重跑
  -> details.repairs 记录候选、候选详情与最后定位
```

## 5. 验证

| 项目 | 结果 |
| --- | --- |
| 巡检字段与评分 | `tests/test_desktop_inspector.py` 新增 1 项 |
| 候选排序与最后定位 | `tests/test_uia_candidates.py`（2 项） |
| 动作失败候选详情 | `tests/test_actions.py` 新增 4 项 |
| 可修复判定与透传 | `tests/test_desktop_repair.py` 新增 4 项 |
| 规划与修复提示词 | `tests/test_desktop_planner.py` 新增 2 项 |
| 桌面相关聚焦套件 | 52 通过、14 跳过、0 失败 |
| 后端全量套件 | 363 通过、20 跳过、0 失败 |

覆盖的关键行为：巡检富字段与唯一性评分、候选按匹配度排序、最后可用
定位落上下文、等待元素与激活窗口失败进入修复、结构化候选与最后定位
进入修复 Prompt 和修复记录、非定位失败仍被拒绝。前端没有改动，
`npm run build` / `npm test` 本轮未重跑。

## 6. 下一步

阶段 2 收尾：桌面步骤检查点与幂等标记，并完成桌面试点回归。

当前缺口：

1. `DesktopDispatcher._convert_plan_to_actions()` 会丢弃
   `idempotency_key` 和 `step_id`，桌面 Agent 拿不到幂等标记。
2. `TaskExecutor` 只支持 `step_offset` 续跑，不会跳过
   `completed_idempotency_keys` 中已完成的步骤，也不回报本次完成的键。
3. `ExecutionManager` 的检查点逻辑目前由网页执行器驱动；桌面执行
   失败或暂停时需要写入 `next_step_index` 与 `completed_idempotency_keys`，
   恢复时把它们注入桌面任务。

验收目标：

- 桌面流程失败或暂停后，从失败步骤继续，已成功且带幂等标记的步骤
  记录为 `skipped` 而不是重跑。
- 桌面试点（WPS 文档创建）整链路成功，失败重跑不从头开始。

预估改动文件：`app/engine/desktop_dispatcher.py`、
`desktop_agent/executor.py`、`app/engine/execution_manager.py`、
`tests/test_dispatcher.py`、`tests/test_task_executor.py`、
`tests/test_execution_manager.py`。
