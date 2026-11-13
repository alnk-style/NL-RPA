# NL-RPA 可靠性与 MCP 工具层落地方案

日期：2026-10-02

状态：方案稿，待确认后进入实施

## 1. 目标

本方案要解决的核心问题不是“接入 MCP”，而是让流程生成和执行从“靠模型一次猜对”升级为“基于真实环境观察、逐步验证、失败可修复、返回可复用”的工程化链路。

首要目标：

1. 提高流程首次生成成功率。
2. 降低选择器、控件定位和登录态导致的执行失败率。
3. 失败后只修复当前步骤，避免整条流程重跑。
4. 让网页和桌面的执行模型统一，支持后续混合流程。
5. 为 MCP、官方 API、WPS COM、UIA 等能力预留统一接入层。

非目标：

1. 不承诺绕过扫码、验证码、MFA 或系统权限控制。
2. 不把 MCP 当作万能 UI 驱动。
3. 不替换现有桌面 Agent 的配对、心跳、任务进度和审计协议。
4. 不在第一阶段支持任意本地脚本执行。

## 2. 现状判断

### 2.1 已有基础

- 桌面侧已有 `desktop.inspect`、结构化动作目录、白名单校验、候选控件回填和自动修复。
- 网页侧已有 Playwright 执行器、持久 Edge Profile、截图、产物投递和 AST 安全校验。
- Workflow、ExecutionLog、Scheduler、通知、Tauri 桌面壳和 Agent WebSocket 已具备产品闭环。
- 后端现有 253 个测试通过、17 个跳过；前端逻辑测试和桌面测试均通过。

### 2.2 主要缺口

1. 网页流程生成前没有真实 DOM、Accessibility Tree 或页面状态观察，选择器靠模型推断。
2. `ExecutionEngine.execute_with_debug()` 仍是 TODO，DebugAgent 没有进入主失败链。
3. `CredentialStore` 已实现，但主执行链没有把凭据注入运行上下文。
4. 网页执行以整段生成代码为主，没有步骤级验证、重试、检查点和断点续跑。
5. 桌面巡检和修复能力存在，但观察字段、定位评分和可修复动作范围有限。
6. Workflow 只有 `web` 和 `windows` 两类单执行器，缺少步骤级路由和混合执行。
7. DAG 当前主要用于可视化和依赖建模，实际执行仍是顺序流程。
8. MCP、官方 API 等结构化能力没有统一的接入协议和执行模型。

## 3. 目标架构

```text
用户需求 / 录制演示
        |
        v
意图解析与规划器
        |
        v
观察层
  - 浏览器 DOM / A11y / Network
  - 桌面窗口 / UIA / COM
  - API / MCP 工具目录
        |
        v
流程编译器
  - 生成结构化步骤
  - 绑定候选定位
  - 生成前置条件和后置断言
        |
        v
验证与修复
  - 单步试跑
  - 失败分类
  - 只修复失败步骤
        |
        v
步骤执行器
  - Web Executor
  - Desktop Executor
  - MCP / API Executor
  - Code Step 兜底
        |
        v
检查点 / 日志 / 截图 / 产物 / 指标
```

设计原则：

1. 先生成、后执行，生成阶段允许模型参与，执行阶段优先确定性。
2. 观察结果是事实，模型不能凭空编造不存在的定位和控件。
3. 默认使用结构化步骤，复杂场景保留代码生成作为兜底。
4. 每一个步骤都必须可记录、可验证、可重试、可定位失败原因。
5. MCP 作为连接器实现之一，不替代现有执行层和安全边界。

## 4. 工作流设计

### 4.1 网页结构化动作层

新增网页动作目录 `WEB_ACTIONS`，与 `app/agents/desktop_catalog.py` 保持同一设计思路。

第一版动作：

| 动作 | 用途 |
| --- | --- |
| `goto` | 打开页面并等待稳定状态 |
| `click` | 点击元素 |
| `fill` | 输入文本 |
| `press` | 键盘按键 |
| `select_option` | 下拉选择 |
| `check` / `uncheck` | 勾选状态 |
| `wait_for` | 等待元素、文本、网络或 URL 条件 |
| `extract_text` | 提取文本 |
| `extract_table` | 提取表格 |
| `screenshot` | 截图 |
| `save_file` | 保存产物 |
| `assert_logged_in` | 登录态断言 |
| `assert_text` | 文本断言 |
| `download` | 文件下载 |
| `upload` | 文件上传 |
| `code_step` | 复杂场景的受控代码兜底 |

定位描述统一为 `LocatorSpec`：

```json
{
  "strategy": "role",
  "value": "button",
  "name": "登录",
  "nth": 0,
  "exact": true,
  "frame": null,
  "fallbacks": [
    {"strategy": "testid", "value": "login-button"},
    {"strategy": "text", "value": "登录"},
    {"strategy": "css", "value": "#login"}
  ]
}
```

定位优先级：

1. `role` + `name`
2. `label` 或 `placeholder`
3. `testid`
4. 可见文本
5. CSS
6. XPath

### 4.2 观察层

网页观察：

- URL、标题、视口大小和页面加载状态。
- Accessibility Tree 摘要。
- 可交互元素、表单、按钮、链接和表格。
- 候选定位及其唯一性评分。
- 可见性、可用状态、是否被遮挡。
- iframe、Shadow DOM、弹窗和登录表单标识。

桌面观察：

- 窗口标题、类名、进程名、PID 和窗口句柄。
- 控件名称、AutomationId、ControlType、类名和父路径。
- 边界框、可见性、可用状态、索引和值。
- 支持的 UI Automation Patterns。
- 候选定位评分和最后可用定位。

观察结果处理：

- 规划阶段使用精简摘要，控制在模型上下文允许范围内。
- 完整快照作为执行产物保存，用于失败诊断。
- 成功定位组合写入步骤，后续优先复用。

### 4.3 验证与修复

每个步骤定义：

```json
{
  "step_id": 3,
  "executor": "web",
  "action": "click",
  "params": {},
  "locator": {},
  "preconditions": [],
  "postconditions": [],
  "retry_policy": {
    "max_attempts": 2,
    "backoff_seconds": 1
  },
  "timeout_seconds": 20,
  "on_failure": "repair",
  "idempotency_key": null,
  "depends_on": [2]
}
```

失败分类：

- `locator_not_found`
- `locator_not_unique`
- `element_not_visible`
- `element_disabled`
- `timeout`
- `auth_required`
- `permission_denied`
- `app_not_found`
- `window_not_found`
- `business_error`
- `unknown`

修复规则：

1. 只修复失败步骤及其后置条件，不重新执行已成功步骤。
2. 修复前重新观察当前页面或窗口。
3. 修复后重新校验动作目录、白名单和参数 Schema。
4. 修复结果写回 Workflow，并记录修复历史。
5. 默认最多修复 2 轮，可配置。

### 4.4 登录与会话

网页：

- 使用持久浏览器 Profile 或加密的 `storage_state`。
- 执行前运行 `assert_logged_in`。
- 检测到 `AUTH_REQUIRED` 后把 ExecutionLog 标记为 `awaiting_user`。
- 前端提示用户打开浏览器登录，登录完成后从检查点恢复。

桌面：

- Agent 继续运行在已登录的 Windows 用户会话。
- 检测到登录窗口时暂停任务并通知用户。
- 支持应用级登录适配器，例如 WPS、Office 和企业内部客户端。

凭据规则：

- 凭据只在运行时注入，不进入 Prompt、日志、截图和模型上下文。
- 每个 Workflow 或用户独立作用域。
- 凭据使用现有 Fernet 加密存储。
- MFA、扫码和验证码场景默认人工接管。

### 4.5 录制式生成

新增“录制一次”模式：

1. 用户浏览器端捕获点击、输入、选择、跳转和下载。
2. 桌面端捕获窗口切换、控件点击、文本输入和快捷键。
3. 平台把原始事件转换为结构化步骤草稿。
4. 模型补充参数、断言、重试策略和可读名称。
5. 用户在编辑器中逐步试跑并确认。

录制模式是降低流程创建成本的关键功能，但依赖前面的结构化动作层。

### 4.6 连接器与 MCP

统一连接器接口：

```python
class Connector:
    async def capabilities(self) -> dict: ...
    async def observe(self, context: dict) -> dict: ...
    async def act(self, action: dict, context: dict) -> dict: ...
    async def verify(self, assertion: dict, context: dict) -> dict: ...
    async def auth_status(self) -> dict: ...
```

能力优先级：

1. 官方 API 或官方 MCP。
2. 应用原生自动化接口，例如 WPS COM、Office COM。
3. 浏览器 DOM / A11y。
4. 桌面 UIA。
5. 图像匹配或 OCR。
6. 坐标点击，仅作为最后兜底。

MCP 接入规则：

- 平台作为 MCP Client 接入外部工具。
- 平台作为 MCP Server 作为后续能力，用于外部 AI 客户端调用平台。
- 现有 Desktop Agent WebSocket 保持不变，需要时包装为 Desktop Bridge。
- 每个 MCP 工具映射为一个 `ToolStep`，记录服务、工具、版本和参数。
- 运行时优先调用确定性工具，不让模型每一步重新决策。
- 本地 MCP Server 视为高权限插件，必须显式授权。
- 远程 MCP 结果视为不可信输入，防止 Prompt Injection。

## 5. 数据模型调整

优先复用现有 JSON 字段，避免大规模数据库迁移。

Workflow 扩展：

- `compiler_version`
- `observations` 摘要
- `execution_mode`：`code`、`structured` 或 `hybrid`

Step 扩展：

- `executor`
- `locator`
- `preconditions`
- `postconditions`
- `retry_policy`
- `timeout_seconds`
- `on_failure`
- `idempotency_key`

ExecutionLog 扩展：

- `current_step`
- `checkpoint`
- `failure_category`
- `awaiting_user`
- `repair_history`
- `tool_calls`

新增配置：

- MCP Server 列表
- 每个 Server 的传输方式、版本和密钥引用
- 每个工具的权限、确认级别和超时

## 6. API 与界面调整

### 6.1 后端接口

- `POST /api/observations/browser`
- `POST /api/observations/desktop`
- `POST /api/workflows/{id}/steps/{step_id}/validate`
- `POST /api/workflows/{id}/steps/{step_id}/repair`
- `POST /api/executions/{id}/resume-after-login`
- `GET /api/mcp/servers`
- `POST /api/mcp/servers`
- `POST /api/mcp/servers/{id}/test`
- `POST /api/mcp/servers/{id}/tools/{tool}/authorize`

### 6.2 前端界面

对话生成：

- 展示观察来源和使用的工具。
- 生成后提供“逐步试跑”和“单步修改”。
- 登录失败时显示“打开登录并继续”。

流程编辑器：

- 区分网页、桌面、MCP、代码步骤。
- 展示候选定位和最后可用定位。
- 支持单步试跑、修复、禁用和替换。

设置页面：

- 新增 MCP Server 配置。
- 展示工具权限和风险级别。
- 提供连接测试和调用日志。

执行详情：

- 展示步骤级时间线。
- 展示失败分类、截图、DOM/UIA 快照和修复记录。
- 支持从失败步骤恢复。

## 7. 分阶段实施

按 2 到 3 人并行开发估算；单人开发时间约乘以 1.5 到 2 倍。

### 阶段 0：基线与试点准备

预计 2 到 3 天。

任务：

1. 选择 1 个网页试点，例如 B 站消息抓取。
2. 选择 1 个桌面试点，例如 WPS 文档创建或记事本自动化。
3. 建立成功率、失败率、人工介入次数和创建耗时的基线。
4. 定义 `Step`、`LocatorSpec`、`ObservationSnapshot` 和 `FailureCategory`。

交付：

- 基线指标报告。
- 数据 Schema 草案。
- 试点流程清单。

### 阶段 1：网页可靠性与结构化执行

预计 1 到 2 周。

任务：

1. 实现网页观察层。
2. 实现 `WEB_ACTIONS` 和结构化执行器。
3. 实现多定位、候选评分和失败截图。
4. 接上 DebugAgent，只修复失败步骤。
5. 保留旧代码生成路径，用开关控制。

验收：

- 试点网页流程以结构化步骤执行。
- 步骤日志包含定位策略和执行结果。
- 定位失败时可以自动修复并复跑当前步骤。
- 旧网页流程不回归。

### 阶段 2：登录、桌面增强与检查点

预计 2 周。

任务：

1. 打通凭据注入和持久会话。
2. 实现 `awaiting_user` 和登录后恢复。
3. 扩展桌面巡检字段和定位评分。
4. 扩展桌面修复动作范围。
5. 增加步骤检查点和幂等标记。

验收：

- 凭据不出现在 Prompt 和日志中。
- 登录失效后可以暂停、人工登录、继续执行。
- 桌面定位失败率相对基线下降。
- 失败流程不需要从头重跑。

### 阶段 3：录制式生成与混合执行

预计 3 到 4 周。

任务：

1. 实现浏览器录制。
2. 实现桌面元素拾取或辅助录制。
3. 录制事件转换为步骤草稿。
4. 步骤级执行器支持 `web`、`windows`、`mcp` 和 `code`。
5. DAG 执行支持依赖、并行安全分支和条件步骤。

验收：

- 用户演示一次可以生成可编辑草稿。
- 单个 Workflow 可以包含网页和桌面步骤。
- DAG 依赖在运行时真实生效。

### 阶段 4：连接器与 MCP

预计 3 到 4 周。

任务：

1. 实现统一 Connector 接口。
2. 实现 MCP Client 和 Server 注册。
3. 实现工具发现、权限、密钥、审计和超时。
4. 将现有桌面 Agent 包装为 Desktop Bridge。
5. 将平台有限工具暴露为 MCP Server。

验收：

- MCP 工具可以作为 Workflow 步骤执行。
- 每个工具调用都有审计记录和权限记录。
- 高权限或破坏性操作需要确认。
- Desktop Agent 原有能力不回归。

### 阶段 5：规模化与生产加固

预计 4 到 6 周。

任务：

1. 指标、告警和失败分类面板。
2. 录制回放和回归测试集。
3. PostgreSQL 与任务队列评估。
4. 多用户、多 Agent 和工作区隔离。
5. 插件签名、版本固定和供应链安全。
6. 打包、升级、备份和灾难恢复。

## 8. 验收指标

初始目标，实施前先记录基线，再按实际情况调整：

| 指标 | 目标 |
| --- | --- |
| 首次生成成功率 | 相对基线提升 30% 以上 |
| 定位失败率 | 相对基线下降 50% 以上 |
| 自动修复成功率 | 可修复失败中达到 60% 以上 |
| 每百次执行人工介入次数 | 相对基线下降 50% 以上 |
| 流程创建耗时 | 相对基线下降 40% 以上 |
| 整条流程重跑次数 | 下降 70% 以上 |
| 执行时间增加 | 不超过 20% |

## 9. 测试策略

1. 单元测试：动作 Schema、定位评分、失败分类、修复合并。
2. 集成测试：浏览器测试页、桌面测试程序、登录过期模拟。
3. 契约测试：MCP 工具发现、参数校验、权限和超时。
4. 回放测试：录制真实流程后在固定环境回放。
5. 端到端测试：网页试点和桌面试点各一条完整链路。
6. 回归测试：旧 `generated_code` 流程必须继续运行。

## 10. 风险与控制

| 风险 | 控制措施 |
| --- | --- |
| 登录、扫码、MFA 无法全自动 | 明确人工接管流程，不承诺绕过 |
| 自绘控件 UIA 覆盖差 | 优先 COM 和官方 API，视觉方案只做兜底 |
| 录制事件噪声大 | 录制后必须经过模型整理和人工确认 |
| MCP 工具权限过高 | 插件安装授权、工具级权限、沙箱和审计 |
| MCP 结果包含提示注入 | 工具结果按不可信数据处理，策略在模型外执行 |
| 页面结构频繁变化 | 多定位、候选评分、DOM 快照和自愈 |
| 整段代码无法断点续跑 | 结构化步骤优先，代码步骤拆分为可检查点单元 |
| SQLite 并发写入受限 | 多用户阶段迁移 PostgreSQL |
| PyInstaller 与 MCP 进程复杂 | 先本地 HTTP，后 stdio，增加健康检查 |

## 11. 发布与回滚

新增功能开关：

- `OBSERVE_BEFORE_PLAN`
- `WEB_STRUCTURED_ACTIONS`
- `AUTH_REQUIRED_PAUSE`
- `STEP_LEVEL_REPAIR`
- `RECORDER_MODE`
- `MCP_CLIENT`

回滚策略：

1. 旧代码生成路径保留一个版本周期。
2. 新旧路径按 Workflow 维度灰度。
3. 新路径失败可以自动回退旧路径。
4. 数据模型优先使用 JSON 字段，避免不可逆迁移。
5. 每个阶段单独发布，不做一次性大切换。

## 12. 首个迭代建议

第一个迭代只做 5 件事：

1. 网页观察层。
2. `WEB_ACTIONS` 和结构化步骤执行。
3. 单步重试与 DebugAgent 修复。
4. 凭据注入和登录态暂停恢复。
5. 试点流程指标和失败快照。

MCP、录制和 DAG 并行执行放到后续阶段。先证明“定位失败率和人工介入次数明显下降”，再扩展协议和生态。

## 13. 决策清单

实施前需要确认：

1. 第一网页试点和第一桌面试点分别选什么。
2. 第一版是否保留代码生成作为默认兜底。
3. 是否需要支持多用户和远程 Agent。
4. 登录失败恢复采用人工登录还是应用专用适配器。
5. MCP 是否只做客户端，还是同时做服务端。
6. 目标环境是否允许常驻浏览器和常驻桌面 Agent。

