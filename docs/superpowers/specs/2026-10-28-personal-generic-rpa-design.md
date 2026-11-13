# 个人通用 RPA 引擎设计

日期：2026-10-28

## 1. 背景与目标

本项目是一个个人使用的通用 RPA 工具，不是面向客户交付的 SaaS 产品，也不以适配某个固定网站或桌面软件为目标。

目标是建立一套通用执行能力：

- 用户用自然语言描述任务，系统协助创建可编辑的工作流。
- 工作流发布后，以确定性步骤重复执行，不在每次运行时重新生成代码。
- 网页、数据、文件、邮件、桌面和视觉能力都通过统一动作协议提供。
- 新网站或新软件优先通过创建新工作流解决，不通过修改后端业务代码解决。
- 登录、验证码、扫码、视觉歧义和业务异常可以暂停并交由用户处理。
- 普通网页任务无头运行；必须占用交互桌面的任务按需进入本机 Agent 或 VM Agent。

典型任务包括：

- 定时打开网站，登录或等待人工登录，下载数据并生成文件。
- 按业务字段过滤、去重、排序、汇总和校验数据。
- 把结果发送到指定邮箱。
- 定时重复执行，保留历史版本、运行记录和产物。
- 页面结构变化时，通过页面观察和视觉定位生成修复候选。

这些任务只是验收通用能力的使用方式，不进入产品代码作为专用业务模块。

## 2. 范围

### 2.1 本期范围

- Windows 10/11 本地运行。
- 单用户、本地 SQLite 数据库。
- 本地浏览器、无头 Playwright、桌面 Agent。
- 可手动注册的 VM Agent。
- 手动和 Cron 触发。
- 通用动作 DSL、工作流版本、试运行和执行历史。
- 通用 SMTP 邮件动作和附件发送。
- 通用登录检查、人工接管和检查点恢复。
- 可选视觉定位兜底。
- 面向个人使用的前端工作台。

### 2.2 非目标

- 多租户 SaaS、计费、组织管理和商业化交付。
- 为特定网站、网银、ERP、酷狗、WPS、微信编写业务适配器。
- 自动绕过 CAPTCHA、短信验证、人脸识别、UKey 或反自动化风控。
- 自动创建和配置虚拟机。
- 移动端完整流程编辑。
- 无人确认地执行支付、转账、删除和覆盖等高风险操作。
- 运行时由 LLM 任意生成或执行 Python、PowerShell、CMD。

## 3. 核心原则

1. **确定性优先**：发布后的工作流执行类型化动作，不用 LLM 代替执行器。
2. **通用动作优先**：新增一个网站不需要新增后端业务代码。
3. **模型只做辅助**：LLM 参与创建、解释和修复，不直接控制最终执行。
4. **所有暂停可恢复**：登录、验证码、视觉歧义和人工确认都是一等状态。
5. **验证先于成功**：动作完成不等于业务成功，必须验证结果、产物和投递。
6. **按需隔离**：只有必须占用交互桌面的步骤进入 VM。
7. **可观察可回滚**：每次发布形成版本，每次执行保存步骤、候选和产物。
8. **安全默认值**：域名白名单、动作白名单、凭据隔离和高风险确认默认启用。

## 4. 总体架构

```text
桌面前端 / REST API / Cron
                  |
                  v
           Execution Orchestrator
                  |
      +-----------+-----------+
      |           |           |
 Preflight    Workflow      Human Task
              Runtime        Manager
                  |
      +-----------+-----------+-----------+
      |           |           |           |
    Web        Data/File    Email      Desktop
  Executor      Executor   Executor    Dispatcher
      |                                   |
 Playwright                         Local/VM Agent
                                          |
                                    UI Automation
                                          |
                                    Visual Fallback
                  |
                  v
       Verification / Artifacts / Delivery
                  |
                  v
       Execution Log / Trace / Metrics
```

### 4.1 工作流创建面

负责把自然语言目标和页面事实转换为工作流：

- 打开目标页面或应用。
- 等待用户完成必要登录。
- 观察 DOM、可访问性树、UIA 树和当前窗口。
- 生成类型化步骤草案。
- 允许用户编辑参数和定位策略。
- 单步试运行和整体试运行。
- 校验通过后发布为不可变版本。

### 4.2 工作流执行面

负责重复运行已发布版本：

- 接收手动或 Cron 触发。
- 执行预检。
- 为每个步骤选择执行器和执行主机。
- 执行类型化动作。
- 验证步骤结果。
- 处理重试、修复、人工接管和取消。
- 保存产物、邮件投递和审计记录。

### 4.3 控制面

负责配置和状态：

- 工作流列表、版本和计划。
- 执行记录和实时状态。
- 待处理中心。
- 浏览器配置、凭据、SMTP 和执行主机。
- 动作目录和高级调试功能。

## 5. 执行模式与兼容策略

### 5.1 正式执行模式

新工作流默认使用：

```json
{
  "execution_mode": "structured"
}
```

正式运行只接受经过动作目录校验的结构化步骤。

### 5.2 旧代码兼容模式

已有工作流可以保留：

```json
{
  "execution_mode": "legacy_code"
}
```

兼容规则：

- `legacy_code` 仅用于运行和迁移历史工作流。
- 新建工作流不得进入 `legacy_code`。
- 在界面中明确标记“旧代码模式”，并提示迁移。
- 旧流程第一次编辑并发布时，必须转换为结构化工作流，或继续作为只读旧版本保留。

### 5.3 单一行程

一次执行使用一个不可变工作流版本和一个执行策略快照。执行期间修改工作流不影响当前运行的流程。

## 6. 工作流模型

### 6.1 Workflow

保留可编辑草稿和当前发布版本指针。

新增字段：

```text
execution_mode: structured | legacy_code
execution_policy: JSON
active_version_id: int | null
schedule_timezone: string
```

### 6.2 WorkflowVersion

每次发布创建不可变版本：

```json
{
  "id": 101,
  "workflow_id": 12,
  "version": 3,
  "plan": {},
  "action_catalog_version": "2026-10-28",
  "validation": {},
  "created_at": "2026-10-28T09:00:00+08:00"
}
```

规则：

- 草稿可以随时修改。
- 发布生成新版本。
- 执行始终引用具体版本。
- 回滚通过切换 `active_version_id` 完成，不改写历史版本。
- 修复生成新版本或草稿补丁，不覆盖已发布历史。

### 6.3 ExecutionPolicy

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

`mode` 可选值：

- `interactive_preferred`：允许交互桌面步骤使用本机 Agent，但最终主机仍受 `host_policy` 和步骤能力约束。
- `background_if_safe`：不得抢占本机交互桌面；需要交互桌面的步骤必须使用具备 `isolated_desktop` 能力的 VM Agent，否则阻断。
- `isolated_preferred`：在满足其他硬约束时优先选择 VM Agent，但不表示只能使用 VM。

`host_policy` 可选值，只作用于需要桌面主机或 host-bound 状态的步骤：

- `local_only`：只能使用本机 Agent；没有满足能力且可用的本机主机时阻断。
- `local_then_isolated`：允许本机或 VM Agent；当两种主机都满足硬约束时可优先本机。
- `isolated_only`：只能使用 VM Agent；没有满足能力且可用的 VM 主机时阻断。

`mode`、`host_policy` 和步骤 `host_requirement` 是三个独立契约。步骤
`host_requirement`、`background_if_safe` 的桌面安全要求、
`local_only`/`isolated_only` 都是硬约束；`isolated_preferred` 和
`local_then_isolated` 中的本机优先只是软偏好。软偏好不能覆盖任何硬约束，
无法满足时必须产生 `policy_conflict` 或 `host_capability_missing`，不能静默降级。
普通服务器侧 Web/Data/Files/Email 步骤不受 `host_policy` 影响。

### 6.4 Step

```json
{
  "step_id": 4,
  "title": "筛选大额交易",
  "executor": "transform",
  "action": "filter",
  "params": {},
  "depends_on": [3],
  "host_requirement": "any",
  "visual_fallback": false,
  "preconditions": [],
  "postconditions": [],
  "verification": [],
  "retry_policy": {
    "max_attempts": 2,
    "backoff_seconds": 1
  },
  "timeout_seconds": 60,
  "on_failure": "stop",
  "idempotency_key": null
}
```

执行器类型：

- `web`
- `transform`
- `files`
- `email`
- `windows`
- `mcp`

`host_requirement` 可选值：

- `any`：可在服务器或满足所需能力的 Agent 上执行。
- `headless_web`：由服务器侧 Web Executor 执行。
- `interactive_desktop`：由具备 `interactive_desktop` 能力的本机或 VM Agent 执行；`background_if_safe` 下必须使用具备 `isolated_desktop` 能力的 VM Agent。
- `isolated_desktop`：只能由 `environment=vm` 且具备 `isolated_desktop` 能力的 Agent 执行。

## 7. 通用动作协议

### 7.1 ActionDefinition

动作目录是前后端共享的能力来源：

```json
{
  "id": "filter",
  "executor": "transform",
  "title": "筛选数据",
  "description": "按字段条件筛选数组",
  "category": "data",
  "risk_level": "read_only",
  "params_schema": {},
  "output_schema": {},
  "requires": {
    "interactive_desktop": false,
    "visual": false,
    "network": false
  }
}
```

前端根据 `params_schema` 动态生成表单。后端根据同一目录校验参数。新增通用动作时不要求手工新增一套前端表单。

### 7.2 ActionInvocation

```json
{
  "executor": "web",
  "action": "extract_table",
  "params": {
    "selector": "table",
    "variable": "rows"
  }
}
```

### 7.3 StepResult

```json
{
  "status": "success",
  "message": "提取 128 行",
  "value": [],
  "variables": {},
  "artifacts": [],
  "verification": {},
  "duration_seconds": 1.2,
  "error": null,
  "human_action": null
}
```

状态集合：

- `success`
- `warning`
- `failed`
- `cancelled`
- `awaiting_user`

## 8. 基础动作清单

### 8.1 Web

- `goto`
- `click`
- `fill`
- `press`
- `select_option`
- `check`
- `uncheck`
- `wait_for`
- `wait_for_response`
- `upload`
- `download`
- `extract_text`
- `extract_table`
- `extract_fields`
- `collect_links`
- `paginate`
- `screenshot`
- `assert_text`
- `assert_logged_in`

### 8.2 Data

- `filter`
- `map`
- `cast`
- `sort`
- `deduplicate`
- `aggregate`
- `join`
- `flatten`
- `limit`
- `validate`

### 8.3 Files

- `write_csv`
- `write_xlsx`
- `save_artifact`
- `copy_file`
- `zip`

### 8.4 Email

- `send`

### 8.5 Windows

保留现有类型化桌面动作：

- `launch_app`
- `activate_window`
- `click_element`
- `click_relative`
- `type_text`
- `press_hotkey`
- `wait`
- `screenshot`
- `read_text`
- `read_window_text`
- `close_app`

### 8.6 MCP

- `call_tool`

MCP 是可选扩展能力，不是通用主链的必需依赖。

## 9. 变量和表达式

### 9.1 变量命名

变量使用简单标识符或点路径：

```text
${transactions}
${transactions.rows}
${large_transactions.count}
${report_file}
```

### 9.2 条件

条件使用声明式对象，不使用 `eval`：

```json
{
  "left": {"var": "transactions.amount"},
  "operator": "gt",
  "right": {"literal": 1000},
  "cast": "number"
}
```

支持运算：

- `eq`
- `ne`
- `gt`
- `gte`
- `lt`
- `lte`
- `contains`
- `not_contains`
- `startswith`
- `endswith`
- `matches`
- `exists`
- `not_exists`
- `in`
- `not_in`

支持组合：

- `all`
- `any`
- `not`

### 9.3 筛选

```json
{
  "executor": "transform",
  "action": "filter",
  "params": {
    "values": "${transactions.rows}",
    "field": "amount",
    "operator": "gt",
    "cast": "number",
    "value": 1000,
    "result_var": "large_transactions"
  }
}
```

结果变量必须可被后续文件、邮件和验证步骤引用。

## 10. 工作流创建

### 10.1 创建入口

前端提供三种入口：

- 自然语言描述。
- 录制网页或桌面操作。
- 从空白工作流开始。

### 10.2 创建流程

1. 用户输入目标和起始地址。
2. 系统启动可见或无头浏览器。
3. 登录失效时创建人工接管任务。
4. 观察当前页面并生成快照。
5. 规划器生成结构化草稿。
6. 前端展示步骤、变量、定位策略和预期结果。
7. 用户单步试运行或整体试运行。
8. 校验器检查动作、依赖、变量、域名和风险。
9. 用户发布，生成不可变版本。

### 10.3 页面观察

观察结果至少包括：

- URL 和标题。
- 页面加载状态。
- 可见文本和登录标志。
- 表单、按钮、下拉框和表格。
- 可访问性角色、名称、标签和测试 ID。
- 可交互元素的多个定位候选。
- 当前页面截图。
- 帧（iframe）信息。

规划器只能使用观察结果中真实存在的候选，或用户明确给出的文字定位。

### 10.4 版本与差异

自然语言修改、自动修复和录制导入都先形成草稿差异：

```text
步骤 4
- amount > 1000
+ amount > 2000
```

用户确认后保存草稿。发布时才创建新版本。

## 11. 执行引擎

### 11.1 执行流程

```text
创建 ExecutionLog
-> 加载不可变版本
-> 预检
-> 执行步骤
-> 验证步骤
-> 处理失败、重试或人工接管
-> 保存产物
-> 执行交付动作
-> 验证交付
-> 写入最终状态
```

### 11.2 预检

预检失败时不启动主流程。

检查项：

- 工作流版本存在且已通过校验。
- 执行策略和主机要求可满足。
- 所需 Agent 在线。
- 浏览器配置存在。
- 登录态是否可用，是否允许进入人工登录。
- 导出目录可写。
- SMTP 配置可用。
- 必填凭据存在。
- Cron 表达式和时区有效。
- 当前工作流没有超过并发限制。

预检结果分为：

- `ready`
- `awaiting_user`
- `blocked`

### 11.3 主机固定

一次运行中的桌面步骤固定到同一个 Agent：

- 同一运行不会把桌面步骤拆分到不同 VM。
- 窗口句柄、剪贴板、Cookie 和客户端状态保持在同一次会话内。
- Web 和 Data 步骤可以在服务端执行。
- 运行记录保存实际使用的 Agent 和环境类型。
- 检查点保存 `host_id` 和 host-bound 状态。原主机丢失时不得自动切换到另一台主机恢复
  有窗口、Cookie、剪贴板、登录态或其他副作用状态的步骤。
- 原主机丢失时保留已完成步骤、产物、变量和检查点，执行进入明确失败或人工处理状态。
- 只有明确没有 host-bound 状态的服务器侧步骤可以在原主机丢失后继续。
- 用户可以在新主机上显式重新开始整个运行，但界面必须提示可能产生重复副作用；
  新主机重新开始不得伪装成原执行的自动恢复。

### 11.4 并发

- 默认同一工作流只允许一个运行实例。
- 不同工作流可以并行。
- 同一浏览器 Profile 默认只允许一个活动会话。
- 同一桌面 Agent 默认只执行一个桌面任务。
- Cron 触发时如果上一实例仍在运行，按 `coalesce` 规则跳过并记录原因。

## 12. 登录和人工接管

### 12.1 登录状态

登录状态：

- `ready`
- `login_required`
- `mfa_required`
- `captcha_required`
- `expired`

### 12.2 浏览器登录

- 使用独立自动化 Profile，不占用日常浏览器 Profile。
- 需要登录时打开可见窗口。
- 创建 `HumanTask`，保存截图和登录说明。
- 用户完成后点击“继续执行”。
- 系统重新验证登录标志，再从检查点继续。
- 登录失效不自动重复尝试，避免触发风控。

### 12.3 桌面登录

桌面应用无法自动判断登录时，通过人工接管任务处理。

### 12.4 HumanTask

```json
{
  "execution_id": 88,
  "step_id": 2,
  "kind": "login_required",
  "instruction": "请在打开的窗口中完成登录",
  "screenshot_path": "",
  "status": "pending",
  "expires_at": "2026-10-28T11:00:00+08:00"
}
```

状态：

- `pending`
- `resolved`
- `cancelled`
- `expired`

处理接口必须重新校验页面或窗口状态，不能只根据用户点击就继续。

## 13. 视觉兜底

### 13.1 触发条件

- 所有结构化定位失败。
- UIA 控件不可交互。
- 页面出现未知弹窗。
- 用户为步骤显式启用 `visual_fallback`。

### 13.2 请求

视觉定位请求包含：

- 目标窗口的截图。
- 当前动作和目标描述。
- 页面或窗口尺寸。
- UIA/DOM 候选摘要。
- 脱敏后的可见文本。
- 允许的动作类型。

### 13.3 响应

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

### 13.4 执行规则

- 只接受动作目录内的动作。
- 只接受高置信且唯一的候选。
- 低置信度或多候选时创建人工确认任务。
- 点击后必须验证页面状态或业务结果。
- 高风险动作必须人工确认。
- 视觉修复可以形成定位候选缓存，但不能跳过后续验证。

### 13.5 视觉提供方

定义 `VisualProvider` 接口：

- `local_ocr`：本地 OCR，不把截图发出本机。
- `remote_vlm`：远程视觉模型。
- `disabled`：默认关闭视觉兜底。

截图、脱敏策略、供应商和凭据由设置页配置。

## 14. 执行主机与按需 VM

### 14.1 Agent 能力

Agent 注册信息增加：

```json
{
  "environment": "vm",
  "capabilities": [
    "headless_web",
    "interactive_desktop",
    "isolated_desktop",
    "visual_fallback"
  ]
}
```

`environment` 只表示主机位置，规范值为：

- `local`
- `vm`

`environment` 不表示能力。特别是 `environment=vm` 不自动代表具备
`isolated_desktop` 能力；路由必须同时检查 `environment` 和父能力集合。
历史数据中的 `environment=isolated` 迁移为 `vm`；API 兼容输入可以临时接受
`isolated` 并归一化为 `vm`，但响应和前端只使用 `vm`。未知 environment 必须拒绝，
不得猜测为 VM 或赋予隔离能力。

规范 capability 至少包括：

- `headless_web`
- `interactive_desktop`
- `isolated_desktop`
- `visual_fallback`

旧 Agent 的 `window`、`uia`、`screenshot` 能力兼容映射为
`interactive_desktop`；`wps` 只保留给 legacy plan，不用于新发布。
没有 capabilities 的历史 Agent 按最小能力处理，不自动视为具备
`interactive_desktop` 或 `isolated_desktop`。

### 14.2 路由

- `headless_web`：由服务器侧 Web Executor 执行。
- `interactive_desktop`：选择具备 `interactive_desktop` 能力且满足
  `mode`、`host_policy` 的主机；`background_if_safe` 下必须是具备
  `isolated_desktop` 能力的 VM Agent。
- `isolated_desktop`：只选择 `environment=vm` 且具备
  `isolated_desktop` 能力的 Agent。
- `any`：按成本最低且满足全部硬约束的服务器/本机/VM 主机选择。

路由检查顺序不改变硬约束含义：先检查步骤 `host_requirement`，再检查
`background_if_safe` 的桌面安全要求，最后检查 `host_policy`。任一硬约束无法满足
时阻断；只有全部硬约束都满足后，才应用 `isolated_preferred` 等软偏好。

### 14.3 VM 范围

本期只支持手动注册已存在的 VM Agent：

- 安装桌面 Agent。
- 通过现有 WebSocket 配对。
- 配置 `environment=vm`，并显式声明 `isolated_desktop` 能力。
- 在主机列表中可见。

自动创建、启动、快照和销毁 VM 不在本期范围。

### 14.4 人工访问

VM 人工接管至少提供：

- VM 当前状态。
- 截图或可视化预览。
- 打开 VM 控制台的链接或操作说明。
- 处理完成后的继续按钮。

## 15. 文件、数据和邮件

### 15.1 数据校验

提取和整理后的数据必须支持：

- 字段类型转换。
- 必填字段检查。
- 行数检查。
- 主键去重。
- 条件筛选。
- 排序和汇总。

不得默认把空数组视为成功。步骤需要显式声明 `allow_empty`。

### 15.2 文件输出

文件动作必须返回：

```json
{
  "path": "",
  "name": "",
  "size": 0,
  "sha256": "",
  "delivered": false,
  "delivered_path": null
}
```

生成后检查：

- 文件存在。
- 文件大小大于 0。
- 必要列存在。
- 行数与数据一致。
- 文件可被目标目录接收。

### 15.3 邮件

邮件是通用动作：

```json
{
  "executor": "email",
  "action": "send",
  "params": {
    "to": ["${recipient}"],
    "cc": [],
    "bcc": [],
    "subject": "${subject}",
    "body": "${body}",
    "attachments": ["${report_file}"],
    "credential_key": "smtp_default"
  },
  "retry_policy": {
    "max_attempts": 3,
    "backoff_seconds": 2
  }
}
```

设置页支持：

- SMTP 主机、端口、用户名、密码、发件人和 TLS。
- 测试连接。
- 发送测试邮件。
- 默认收件人。

投递记录：

- 开始时间。
- 结束时间。
- SMTP message-id。
- 附件列表和校验和。
- 成功或失败状态。
- 错误原因。

`required_delivery=true` 时，邮件投递失败导致工作流失败。可选通知可以记为 `warning`，但必须在界面明确显示。

## 16. 错误、重试和恢复

### 16.1 失败分类

- `auth_required`
- `locator_not_found`
- `visual_ambiguous`
- `business_validation_failed`
- `data_empty`
- `artifact_missing`
- `delivery_failed`
- `environment_unavailable`
- `timeout`
- `cancelled`
- `unknown`

### 16.2 重试

- 网络抖动、临时超时和下载失败可以自动重试。
- 登录失败、验证码、业务校验失败和人工取消不自动重试。
- 重试次数、退避时间和最大总时长由步骤或执行策略控制。

### 16.3 检查点

检查点保存：

- 下一个步骤。
- 已完成步骤。
- 幂等键。
- 当前变量。
- 产物。
- 当前登录状态。
- 当前 Agent、`host_id`、环境和资源绑定。
- host-bound 状态及恢复所需的验证条件。

恢复时重新执行预检和当前步骤验证。

## 17. 安全与隐私

- 凭据只进入加密凭据存储，不进入 Prompt、日志和截图。
- 工作流默认使用域名白名单。
- 动作目录外动作不能执行。
- 运行时不执行任意脚本。
- 视觉请求默认脱敏，可以完全关闭远程视觉。
- 删除、支付、转账、覆盖和发送外部消息等动作需要风险等级和确认策略。
- 执行日志对密码、Token、Cookie 和银行卡信息进行脱敏。
- 产物保存路径受允许目录限制。
- 每个步骤保留审计记录。

## 18. 数据模型

保留现有模型：

- `Workflow`
- `ExecutionLog`
- `DesktopAgent`
- `Credential`
- `ChatSession`

新增或扩展：

### 18.1 Workflow

- `execution_mode`
- `execution_policy`
- `active_version_id`
- `schedule_timezone`

### 18.2 WorkflowVersion

- `id`
- `workflow_id`
- `version`
- `plan`
- `validation`
- `action_catalog_version`
- `created_at`

### 18.3 HumanTask

- `id`
- `execution_id`
- `step_id`
- `kind`
- `instruction`
- `payload`
- `screenshot_path`
- `status`
- `created_at`
- `resolved_at`
- `expires_at`

### 18.4 DeliveryRecord

- `id`
- `execution_id`
- `step_id`
- `channel`
- `status`
- `recipient`
- `attachment_paths`
- `provider_message_id`
- `error`
- `created_at`
- `finished_at`

### 18.5 ExecutionLog 扩展

- `workflow_version_id`
- `execution_policy_snapshot`
- `host_id`
- `human_task_id`

## 19. API 设计

### 19.1 动作与工作流

- `GET /api/action-catalog`
- `POST /api/workflows/draft-from-goal`
- `POST /api/workflows/{id}/observe`
- `POST /api/workflows/{id}/validate`
- `POST /api/workflows/{id}/test-step`
- `POST /api/workflows/{id}/dry-run`
- `POST /api/workflows/{id}/publish`
- `GET /api/workflows/{id}/versions`
- `POST /api/workflows/{id}/rollback`

### 19.2 执行

- `POST /api/workflows/{id}/run`
- `GET /api/executions`
- `GET /api/executions/{id}`
- `POST /api/executions/{id}/pause`
- `POST /api/executions/{id}/resume`
- `POST /api/executions/{id}/cancel`
- `GET /api/executions/{id}/events`

### 19.3 人工接管

- `GET /api/human-tasks`
- `GET /api/human-tasks/{id}`
- `POST /api/human-tasks/{id}/resume`
- `POST /api/human-tasks/{id}/cancel`

### 19.4 配置

- `GET /api/settings/email`
- `PUT /api/settings/email`
- `POST /api/settings/email/test`
- `GET /api/hosts`
- `GET /api/browser-profiles`
- `POST /api/browser-profiles/{id}/login`

## 20. 前端设计

### 20.1 产品形态

前端定位为个人自动化工作台，不采用 SaaS 后台和聊天机器人作为主界面。

主导航：

```text
工作台
工作流
执行记录
待处理
连接与凭据
设置
高级工具
```

聊天、MCP、旧代码预览和原始 JSON 放入工作流助手或高级工具。

### 20.2 工作台

显示：

- 正在运行。
- 等待处理。
- 今日计划。
- 最近失败。
- 最近产物。

信息采用紧凑列表，不使用营销式大卡片。

### 20.3 工作流列表

每个工作流显示：

- 名称和描述。
- 当前版本。
- 启停状态。
- 下次运行时间。
- 最近一次结果。
- 运行、编辑、复制和停用操作。

### 20.4 Workflow Studio

布局：

```text
顶部：名称、状态、版本、保存、试运行、发布
左侧：步骤大纲、变量列表
中间：顺序视图，可选切换图视图
右侧：动作属性检查器
底部抽屉：日志、截图、变量、数据预览
```

默认展示顺序视图。React Flow 图视图只用于分支、并行和复杂依赖。

动作属性表单由 `ActionDefinition.params_schema` 动态生成。

高级 JSON 编辑保留，但不作为默认填写方式。

### 20.5 数据预览

数据步骤支持：

- 表头和类型。
- 行数。
- 字段预览。
- 过滤前后数量。
- 空值和重复值提示。
- 试运行结果。

### 20.6 执行详情

显示：

- 状态、触发方式、版本、执行主机和耗时。
- 步骤时间线。
- 当前页面截图或实时预览。
- 变量和输出。
- 文件和邮件投递状态。
- 重试、修复、取消和人工接管操作。

### 20.7 待处理中心

每条任务显示：

- 工作流和执行。
- 暂停原因。
- 等待时长。
- 截图和说明。
- 继续、跳过、终止操作。

### 20.8 连接与凭据

统一管理：

- 自动化浏览器 Profile。
- 登录状态。
- 账号和 Token 凭据。
- SMTP。
- 本机和 VM Agent。
- 导出目录。

### 20.9 前端技术

- React + TypeScript + Tailwind。
- `TanStack Query` 管理服务器状态和缓存。
- Zustand 管理编辑器草稿、选择状态和面板布局。
- WebSocket 事件更新 Query 缓存。
- 路由级代码拆分。
- 工作流 Studio、执行详情和代码预览按需加载。

关键组件：

- `ActionSchemaForm`
- `VariablePicker`
- `ConditionBuilder`
- `StepOutline`
- `DataPreview`
- `RunTimeline`
- `HumanTaskPanel`
- `WorkflowVersionDiff`

### 20.10 视觉规范

- 桌面优先，重点适配 1280×800 和 1440×900。
- 首页、列表和执行详情使用紧凑、安静、工作型界面。
- 卡片只用于重复项和独立工具面板。
- 圆角不超过 8px。
- 不使用装饰性渐变、光晕或大标题。
- 状态色只用于表达运行、警告、失败和等待。

## 21. 测试策略

### 21.1 单元测试

- 动作目录和参数校验。
- 条件和表达式。
- 数据转换。
- 文件生成。
- 邮件消息构造。
- 工作流版本和执行策略。

### 21.2 契约测试

- 前端动作表单与后端 `params_schema` 一致。
- `ActionDefinition` 与运行时动作注册一致。
- StepResult 和事件格式一致。
- HumanTask 状态机一致。

### 21.3 Replay 测试

保存页面快照、UIA 树、选择器、视觉候选和预期结果，用于重复验证定位与修复逻辑。

### 21.4 端到端测试

- 本地示范站点创建、试运行和发布。
- 登录失效后人工接管。
- 表格提取、过滤、XLSX 和邮件附件。
- 视觉兜底候选歧义进入人工确认。
- 本机 Agent 和 VM Agent 主机路由。

### 21.5 通用性验收

- 不修改后端业务代码，能在三个不同结构的网页上创建工作流。
- 不修改前端代码，新增一个动作后能通过动作目录生成表单。
- 同一个动作可以被任意工作流复用。
- 旧工作流可以通过兼容模式继续运行。

## 22. 实施阶段

### 阶段 A：统一基础

- 引入 `execution_mode` 和执行策略。
- 建立工作流版本。
- 统一动作目录。
- 结构化执行成为新工作流默认。
- 修复成功的定义和错误状态。

完成标准：

- 新工作流不再要求生成整段代码。
- 旧工作流仍可运行。
- 动作目录可以同时驱动后端校验和前端表单。

### 阶段 B：通用 Web 与数据能力

- 完成 Web、Data、Files、Email 动作。
- 完成变量、条件、过滤、验证和文件校验。
- 完成 SMTP 配置和测试。
- 完成数据预览和试运行。

完成标准：

- 不修改代码可以创建工作流完成网页提取、数据整理、文件生成和邮件发送。

### 阶段 C：工作流创建 Studio

- 工作流列表、Studio、动态表单、变量选择和条件构建。
- 自然语言生成草稿。
- 页面观察和录制。
- 版本、差异、发布和回滚。

完成标准：

- 普通网页流程可以通过界面创建、测试和发布。

### 阶段 D：人工接管和恢复

- HumanTask 数据模型。
- 待处理中心。
- 登录、验证码和歧义确认。
- 检查点恢复。

完成标准：

- 登录失效和执行异常不会直接导致重复失败，用户可以恢复。

### 阶段 E：视觉兜底

- 视觉提供方接口。
- 截图脱敏。
- 候选生成和置信度策略。
- 已验证点击和人工确认。

完成标准：

- 结构化定位失败时可以使用视觉候选，但不确定时不会盲点。

### 阶段 F：按需 VM

- Agent 环境能力和主机注册。
- 主机路由和人工访问。
- 后台运行模式。

完成标准：

- 必须占用交互桌面的步骤可以进入 VM，本机可以继续其他工作。

## 23. 风险

| 风险 | 应对 |
|---|---|
| 网站结构变化 | 多定位策略、页面观察、版本、Replay 和视觉兜底 |
| 登录或风控限制 | 人工接管，不绕过验证和安全机制 |
| 视觉模型误判 | 高置信唯一候选、执行后验证、人工确认 |
| 邮件投递失败 | 独立 DeliveryRecord、重试和明确失败状态 |
| VM 资源占用 | 只路由必要步骤，手动注册和按需启动 |
| 模型调用成本 | 正式运行不使用 LLM，视觉只在失败时启用 |
| 旧流程兼容复杂 | 明确 legacy 模式，不阻塞新主链 |
| 前端状态复杂 | 服务器状态与编辑器草稿状态分离 |

## 24. 完成定义

本项目达到以下条件时，通用 RPA 主链视为完成：

- 用户可以通过自然语言、观察或录制创建任意网页工作流。
- 发布后的工作流以确定性类型化动作执行。
- 工作流支持变量、条件、循环、筛选、整理、文件和邮件。
- 登录、验证码和视觉歧义可以暂停并恢复。
- 结构化定位失败时可以使用视觉兜底。
- 必须占用桌面的步骤可以路由到按需 VM。
- 文件、邮件和业务结果都有独立验证。
- 所有失败都有明确分类，不会被标记为成功。
- 新网站和新软件不需要新增专用业务模块。
- 现有旧工作流不会被破坏，并有明确迁移路径。

## 25. 最终实现状态（2026-11-11）

Stage A-F 的通用工作流版本、结构化动作、HumanTask、视觉兜底、本机/VM
主机路由和桌面封装已经实现。Stage G 已在本机 Windows 环境完成以下验收：

- 真实 Windows 交互窗口、UIA、文本输入、受控点击、窗口截图、最小化恢复
  和托盘隐藏/恢复。
- 真实本机 Desktop Agent 配对、心跳、忙碌状态和 host-bound 桌面流程。
- Playwright Chromium 三个结构不同的本地站点、登录后服务端重新验证、
  文件/数据测试以及 fake visual provider 的视觉接管流程。
- 后端、前端、Replay、SQLite 迁移、compileall、Tauri sidecar 和 NSIS
  安装包构建。

以下环境尚未完成验收，不能计入通过：

- 标准 Microsoft Edge 的登录、验证码/MFA 人工接管。
- 真实 Windows VM Agent、远程 WSS、TLS 证书、断线重连和 VM 会话丢失。
- 真实 SMTP 服务、远程视觉 provider 及其网络隐私策略。
- 125%/150% Windows DPI 与多显示器组合。

Stage G.1 的 OpenAPI 导出和前后端共享类型生成仍未实现。因此当前状态是
“本地自动化与真实 Windows/Chromium 候选通过，完整个人使用发布候选未完成”，
而不是全面发布验收通过。

