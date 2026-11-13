# 阶段 2 收尾：桌面步骤检查点与幂等标记

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 阶段 2 任务 5。

## 1. 交付内容

1. `app/engine/desktop_dispatcher.py`：`_convert_plan_to_actions()` 保留
   `step_id` 与 `idempotency_key`；`desktop.task` 载荷新增
   `resume_from_step` 与 `completed_idempotency_keys`；失败事件的
   `failed_step` 与完成键写入 `ExecutionResult.details`。
2. `desktop_agent/executor.py`：`TaskExecutor` 支持按断点索引与幂等键
   跳过步骤，跳过步骤写入 `skipped` 步骤日志并发送
   `desktop.progress`；完成与失败事件回报本次
   `completed_idempotency_keys`，失败步骤携带
   `step_id`/`idempotency_key`/`params`。
3. `app/engine/execution_manager.py`：桌面失败时把
   `next_step_index` 与 `completed_idempotency_keys` 检查点写入
   `ExecutionLog.step_logs`；暂停时根据已落库的步骤日志补写检查点，
   二次暂停会合并推进断点；`resume()` 允许带检查点的失败/暂停执行
   续跑，并把断点注入桌面任务上下文。
4. `app/engine/desktop_repair.py`：自动修复复跑时清掉断点键，
   避免修复切片被二次跳过。
5. `app/api/agent_ws.py`：真实 WebSocket 通道复用 dispatcher 的事件
   解析，`failed_step` 与 `completed_idempotency_keys` 不再丢失。
6. 测试：`tests/test_dispatcher.py`、`tests/test_task_executor.py`、
   `tests/test_execution_manager.py`、`tests/test_desktop_repair.py`、
   `tests/integration/test_desktop_pilot.py`。

## 2. 协议约定

桌面任务载荷新增两个字段：

```json
{
  "type": "desktop.task",
  "step_offset": 0,
  "resume_from_step": 1,
  "completed_idempotency_keys": ["wps-doc-created"],
  "actions": [
    {
      "type": "create_document",
      "step_id": 1,
      "idempotency_key": "wps-doc-created",
      "params": {}
    }
  ]
}
```

跳过规则：

- `step_index < resume_from_step`：断点之前的步骤，日志消息为
  “断点前已完成，续跑跳过”；
- 命中 `completed_idempotency_keys`：日志消息为
  “幂等标记已完成：<key>”；
- 跳过步骤不执行处理器，只写 `skipped` 步骤日志并发送进度事件。

回报与检查点：

- `desktop.completed` 与 `desktop.failed` 都带
  `completed_idempotency_keys`（含传入的已完成键）；
- 检查点条目 `type=checkpoint` 保存 `next_step_index` 与
  `completed_idempotency_keys`，恢复时映射为 `resume_from_step`
  注入下一次桌面任务；
- 续跑没有产生新日志时（例如 Agent 不在线）保留原检查点，
  避免丢失断点。

## 3. 试点回归

WPS 文档创建试点（`create_document` → `insert_image`）走完整链路：

```text
ExecutionManager -> DesktopRepairCoordinator -> DesktopDispatcher
  -> TaskExecutor（真实桌面 Agent 执行器） -> create_document / insert_image
```

回归场景：

1. 第一次执行在第 2 步失败，`ExecutionLog` 写入检查点
   `next_step_index=1`、`completed_idempotency_keys=["wps-doc-created"]`；
2. `resume()` 后第 1 步记为 `skipped`，第 2 步重跑成功；
3. `create_document` 只执行一次，`insert_image` 执行两次
   （失败一次、成功一次），最终文档内容与写入桩一致。

本机没有安装 WPS（注册表没有 `kwps.Application`，仅存在
`wpscloudsvr.exe`），因此试点用 COM 写入桩验证同一链路；测试另留
一条真实 WPS COM 冒烟用例，装有 WPS 的机器会自动执行。

## 4. 验证

| 项目 | 结果 |
| --- | --- |
| 检查点与断点注入（`test_execution_manager.py`） | 4 项新增通过 |
| 幂等跳过与失败回报（`test_task_executor.py`） | 2 项新增通过 |
| 标记透传与结果详情（`test_dispatcher.py`） | 4 项新增通过 |
| 修复复跑去除断点键（`test_desktop_repair.py`） | 1 项新增通过 |
| 桌面试点整链路（`tests/integration/test_desktop_pilot.py`） | 1 通过、1 跳过（本机无 WPS COM） |
| 全量套件 | 375 通过、21 跳过、0 失败 |

全量命令：`python -m pytest -q --basetemp=.tmp/pytest`。
跳过项与阶段 2 前一致（交互式桌面/浏览器沙箱限制），新增跳过项为
真实 WPS COM 冒烟。

## 5. 下一步

阶段 3：录制式生成与混合执行。桌面侧已有检查点、幂等标记与
巡检字段，可在录制事件转步骤草稿时直接产出 `idempotency_key`
和重试策略，并复用本阶段的 `skipped` 续跑协议。
