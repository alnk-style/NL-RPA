# 通用桌面执行器设计（第一阶段）

日期：2026-09-24
状态：已确认，进入实现

## 背景

执行侧已经具备通用桌面能力：桌面 Agent 注册了受控动作、按
`allowed_apps` 白名单校验、由 `DesktopDispatcher` 通过 WebSocket 下发任务，
`ExecutionRouter` 按 `workflow.executor_type` 在网页与桌面之间路由。

缺口在生成侧：`app/api/chat.py` 只在
`build_desktop_document_plan()` 命中 WPS/Word 文档标记时生成桌面流程，
其余桌面需求会被 `desktop_automation_reason()` 判定后直接回绝。因此除
WPS 新建文档以外的桌面应用都无法进入规划流程。

## 目标

- 用户可以用自然语言规划任意白名单程序的操作流程。
- 规划结果只能是受控动作目录内的动作，且启动路径必须命中白名单。
- 桌面流程不经过 Playwright 代码生成，直接保存为
  `executor_type="windows"` 的工作流。
- 缺少桌面 Agent 或白名单为空时，给出可执行的下一步提示，而不是
  “平台不支持桌面”这类过期文案。

## 第二阶段（已实现）

1. **`desktop.inspect` 协议**
   - 后端 → Agent：`{"type": "desktop.inspect", "request_id", "window"?,
     "max_windows", "max_elements", "max_depth"}`。
   - Agent → 后端：`{"type": "desktop.inspect.result", "request_id",
     "success", "windows", "target", "elements", "error"}`。
   - 不带 `window` 时只回报可见窗口列表；带 `window`（handle / title /
     class_name / pid）时额外回报该窗口的控件摘要。
   - 窗口摘要包含 `title`、`class_name`、`pid` 和进程可执行文件名；
     控件摘要包含 `name`、`automation_id`、`control_type`、`depth`。
2. **规划前巡检**
   `app/api/chat.py` 在调用桌面规划器前先取窗口清单，用 `app_hint` 与
   步骤目标匹配目标窗口，命中后再取控件摘要。摘要与窗口清单一起进入
   规划器提示词，模型必须使用摘要中真实存在的控件标识。
3. **失败时给出候选控件**
   `click_element`、`read_text` 找不到控件时，会返回当前窗口内可用控件的
  名称与类型，写入失败信息与 `data.candidates`，便于用户据此修正流程。

## 第三阶段（已实现）

1. **失败现场带候选控件**
   Agent 把候选控件写入
   `desktop.failed.failed_step.details.candidates`；Dispatcher 将其保留在
   `ExecutionResult.details` 中，执行管理器据此判断是否可自动修复。
2. **自动修复循环**（`app/engine/desktop_repair.py`）
   - 只有“找不到控件”这类失败会触发修复，白名单或权限类失败直接返回，
     不做无意义重试。
   - `DesktopPlannerAgent.repair()` 用候选控件重写失败步骤，输出从该步骤
     开始的步骤列表，并再次经过动作目录与白名单校验。
   - 修复后的完整流程会写回 Workflow，下次运行直接使用修正后的步骤。
   - 续跑只下发“失败步骤及其后续步骤”，通过 `step_offset` 把步骤编号还原
     到完整流程中的位置，已经执行成功的步骤不会重复执行。
   - 修复次数由 `DESKTOP_REPAIR_ATTEMPTS` 控制（默认 2）。每次修复都会以
     `auto_repair` 步骤写入执行日志，修复记录同时写入执行结果 details。

## 控制与限流

- 巡检只读：不修改窗口状态，不执行输入。
- 摘要数量受限：窗口默认 40 个，控件默认 60 个、深度默认 4 层。
- 巡检超时默认 10 秒；超时或 Agent 离线时返回失败结果，规划器退回仅
  依赖白名单的模式，不阻塞流程生成。
- 自动修复默认最多 2 轮，只针对找不到控件这类失败；每轮都会重新校验
  动作目录与白名单。

## 组件

1. `app/agents/desktop_catalog.py`
   动作目录单一来源：动作名、参数说明、所需能力。目录只包含 Agent 真正
   注册并有处理函数的动作，避免出现“校验通过但执行时报未知动作”。
2. `app/agents/desktop_planner.py`
   `DesktopPlannerAgent` 依据意图、动作目录与白名单上下文生成桌面计划，
   并在返回前做确定性校验：动作必须属于目录、`launch_app` 路径必须命中
   白名单、步骤必须非空。校验失败抛出 `DesktopPlanError`。
3. `app/engine/desktop_targets.py`
   从连接管理器取出在线 Agent，读取其白名单与能力，形成规划上下文；
   没有在线 Agent 时回退到已配对 Agent，便于离线编排。
4. `app/api/chat.py`
   保留 WPS 确定性快捷路径；其余桌面意图改走桌面规划器；回复中携带
   `executor_type="windows"`、`generated_code=None`。`/modify` 按
   `workflow.executor_type` 分流。
5. `app/agents/intent_parser.py`
   意图 JSON 增加 `executor_type` 与 `app_hint`，用于区分网页与本地程序。
6. `desktop_agent/actions/launch_app.py`
   支持白名单常见写法：完整路径、`notepad.exe` 这类可执行名，以及不带
   后缀的程序名。解析顺序为原值、`shutil.which`、Windows App Paths 注册表。

## 数据流

```
自然语言
  -> IntentParser（executor_type / app_hint）
  -> StepPlanner（保留 WPS 快捷路径）
  -> 桌面意图判定
  -> DesktopExecutionContext（在线 Agent 白名单 + 能力）
  -> DesktopPlannerAgent（动作目录约束）
  -> validate_desktop_plan（动作白名单 + 应用白名单）
  -> Workflow(executor_type=windows)
  -> ExecutionRouter -> DesktopDispatcher -> 桌面 Agent
```

## 错误处理

| 情况 | 行为 |
| --- | --- |
| 没有已配对 Agent | 提示先在桌面客户端完成配对 |
| 有 Agent 但白名单为空 | 提示在设置页添加允许的程序 |
| 规划结果含目录外动作 | 拒绝并说明动作名 |
| 启动路径不在白名单 | 拒绝并说明程序名与白名单入口 |
| Agent 离线 | 允许保存流程，执行时由 Dispatcher 报无可用 Agent |

## 安全边界

- 规划层与执行层两次校验白名单，任何一层不通过都不能执行。
- 桌面计划不会进入 Playwright 代码生成器，也不会生成任意脚本。
- 动作参数在 Agent 侧仍由 `ActionValidator` 与动作处理函数复核。

## 测试

- 动作目录与 Agent 注册表保持同步。
- 桌面计划校验：目录外动作、白名单外应用、白名单内程序名。
- 能力推导：`uia` / `window` / `screenshot` / `wps`。
- chat 路由：桌面意图返回 windows 计划且不调用代码生成器；无 Agent
  时返回配对提示。
- `launch_app` 能解析不带路径的可执行名。
