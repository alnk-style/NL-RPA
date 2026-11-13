# NL-RPA 项目解析（简历与面试版）

## 1. 一句话定位

NL-RPA 是一个“自然语言驱动的 RPA 流程生成与执行平台”：用户用自然语言描述自动化需求，系统将其转换为结构化工作流，并分别通过 Playwright 网页执行器或 Windows 桌面 Agent 执行，同时提供流程管理、定时调度、实时日志、截图、通知和桌面客户端能力。

更准确的技术定位可以写成：

> 基于大语言模型、Playwright、FastAPI、WebSocket、Docker 和 Windows UI/COM 自动化构建的双执行器 RPA 平台。

## 2. 项目解决的问题

传统 RPA 工具通常要求用户手工拖拽流程、配置选择器或编写脚本，学习和维护成本较高。项目希望解决四类问题：

1. **降低流程创建门槛**
   - 用户直接输入“每天 9 点打开某网站并截图”这类自然语言。
   - 系统负责解析意图、规划步骤、生成可执行代码或桌面动作。

2. **同时覆盖网页和 Windows 桌面**
   - 网页任务使用 Playwright。
   - WPS 等 Windows 应用通过本机 Desktop Agent、COM、Win32 和 UI Automation 操作。

3. **控制 LLM 生成代码的安全风险**
   - 网页代码在执行前经过 AST 安全校验、凭据泄漏检测。
   - 优先在 Docker 沙箱中运行，并限制 CPU、内存、网络域名和执行时间。
   - 桌面端不执行任意 Python/PowerShell/CMD，只执行注册过的结构化动作。

4. **让自动化流程可管理、可观察、可调度**
   - 流程 CRUD、发布、定时执行。
   - 执行日志、步骤日志、截图、邮件/Webhook 通知。
   - 页面实时查看执行状态和步骤结果。

## 3. 总体架构

```text
React Web / Tauri Desktop
        |
        | HTTP + WebSocket
        v
FastAPI Backend
  |-- Chat Agent Pipeline
  |     |-- Intent Parser
  |     |-- Step Planner
  |     `-- Code Generator
  |
  |-- Execution Router
  |     |-- Web Execution Engine -> AST Validator -> Docker Sandbox / Local Playwright
  |     `-- Desktop Dispatcher -> WebSocket -> Windows Desktop Agent
  |
  |-- Workflow / Execution / Chat Session APIs
  |-- APScheduler
  |-- Notification Service
  `-- SQLite + SQLAlchemy

Windows Desktop Agent
  |-- Pairing and Heartbeat
  |-- Action Validation / Allowlist
  |-- PyWin32 / UIAutomation / WPS COM
  |-- Screenshot / Read Text / Window Control
  `-- Local JSONL Audit Log
```

核心设计不是“让 LLM 直接控制电脑”，而是把 LLM 当作“理解与规划组件”，最终执行必须落到受约束的工作流、代码沙箱或动作白名单上。

## 4. 核心原理

### 4.1 自然语言到结构化工作流

主要代码：

- `app/agents/intent_parser.py`
- `app/agents/step_planner.py`
- `app/agents/code_generator.py`
- `app/agents/desktop_plan.py`
- `app/api/chat.py`

处理链路：

1. 用户消息进入 `/api/chat/message`。
2. `IntentParserAgent` 调用 DeepSeek，把自然语言解析为 JSON：
   - `trigger`
   - `steps`
   - `variables`
   - 必要时返回 `needs_clarification`
3. 如果模型没有返回合法 JSON，系统会追加纠错指令并重试一次。
4. `StepPlannerAgent` 继续把粗粒度任务细化成步骤，包括选择器、参数和依赖信息。
5. 对于 WPS 文档创建类需求，`build_desktop_document_plan()` 使用确定性规则生成 `create_document` 动作，避免让 LLM 虚构不存在的桌面代码；其余桌面需求由 `DesktopPlannerAgent` 在动作目录和白名单约束下规划，并在目标窗口已打开时先通过 `desktop.inspect` 读取 UI Automation 控件摘要。
6. 对于普通网页流程，`CodeGeneratorAgent` 生成异步 Playwright Python 代码。
7. 生成的代码会检查是否硬编码了凭据；发现泄漏时将问题作为反馈重新生成一次。
8. 前端将结构化规划和生成代码保存为 Workflow。

这一链路涉及的知识：

- Prompt Engineering
- System Prompt 设计
- 结构化 JSON 输出与容错解析
- LLM 重试与反馈闭环
- OpenAI SDK 自定义 `base_url`
- 将模型输出约束到业务 Schema
- 用确定性规则兜底关键业务

### 4.2 Workflow 数据模型与 DAG 编辑

主要代码：

- `app/models/workflow.py`
- `app/schemas/workflow.py`
- `frontend/src/lib/workflow-graph.ts`
- `frontend/src/lib/workflow-editor.ts`
- `frontend/src/pages/WorkflowEditor.tsx`

Workflow 保存以下关键信息：

- 自然语言原始输入
- `structured_plan`
- `generated_code`
- `dag_definition`
- `trigger_type` 和 `cron_expression`
- `executor_type`，值为 `web` 或 `windows`
- `required_capabilities`
- 发布状态和通知配置

前端使用 React Flow 展示和编辑流程：

- 触发器节点和步骤节点
- 通过连线修改 `depends_on`
- 自动补充默认顺序依赖
- 建立依赖前检测循环
- 拖动节点、编辑节点参数和代码预览

需要注意：当前 DAG 主要承担“流程建模、可视化和依赖保存”，后端实际执行仍以生成的代码顺序或桌面动作数组为主，不应描述成“后端并行 DAG 调度引擎”。

### 4.3 双执行器路由

主要代码：

- `app/engine/router.py`
- `app/engine/engine.py`
- `app/engine/desktop_dispatcher.py`

`ExecutionRouter` 根据 `workflow.executor_type` 选择执行器：

- `web`：进入 `ExecutionEngine`。
- `windows`：进入 `DesktopDispatcher`。

这样网页自动化和 Windows 桌面自动化共享同一套 Workflow、ExecutionLog 和前端监控模型，但底层执行机制完全隔离。

### 4.4 Web 执行：Playwright + Docker 沙箱

主要代码：

- `app/engine/code_validator.py`
- `app/engine/runner.py`
- `app/engine/sandbox.py`
- `docker/entrypoint_template.py`

执行前校验：

1. 使用 Python `ast` 解析生成代码。
2. 禁止危险导入，例如 `os`、`subprocess`、`shutil`、`ctypes`、`socket` 等。
3. 禁止 `eval`、`exec`、`compile`、`open`、`__import__` 等危险调用。
4. 使用正则识别危险模式。
5. 移除顶层 `if __name__ == "__main__"`。
6. 检查代码是否包含明文凭据。

Docker 沙箱：

- Chromium 和无头 Playwright 运行在容器中。
- 默认 `network_mode="none"`。
- 如果流程包含 HTTP/HTTPS 目标，则切换为 bridge 网络。
- 从结构化计划中提取域名，注入 `ALLOWED_DOMAINS`。
- 容器内的 `page.goto()` 会拒绝对非白名单域名的访问。
- 限制 512 MB 内存和单核 CPU。
- 执行超时后自动 kill 容器。
- 结果通过 `result.json` 返回，截图复制回宿主机。
- Docker 不可用时，系统会降级到本地 `PlaywrightRunner`。

Playwright 执行器：

- 使用 `async_playwright()` 和 Chromium。
- 动态执行 LLM 生成的异步 Python 代码。
- 对 `goto`、`click`、`fill`、`press` 做包装。
- 每一步自动记录耗时、状态、错误和截图。
- 提供 `screenshot()`、`log()`、`extract_table()`、`wait_for()` 辅助函数。

涉及知识：

- Playwright 自动等待、选择器和浏览器上下文
- 浏览器自动化
- Docker SDK
- 容器资源限制和网络隔离
- Python AST 静态分析
- 动态执行代码的安全边界
- 异步超时控制

### 4.5 Windows Desktop Agent

主要代码：

- `desktop_agent/client.py`
- `desktop_agent/executor.py`
- `desktop_agent/action_validator.py`
- `desktop_agent/actions/`
- `app/api/agent_ws.py`
- `app/engine/agent_connection_manager.py`
- `app/engine/desktop_dispatcher.py`

Agent 接入流程：

1. 用户在平台生成短期配对码。
2. 本机 Agent 调用配对接口，换取 `agent_id` 和随机 Token。
3. 数据库只保存 Token 的 SHA-256 哈希，不保存明文 Token。
4. Agent 通过 `/api/agents/ws` 建立 WebSocket 长连接。
5. 服务端每 30 秒发送心跳，60 秒未收到有效活动则断开。
6. Agent 断线后使用指数退避重连，最大等待 60 秒。

任务下发：

1. 后端把 Workflow 的 `structured_plan.steps` 转换为动作数组。
2. 根据 `required_capabilities` 和在线状态选择 Agent。
3. 通过 WebSocket 下发 `desktop.task`。
4. Agent 按顺序执行动作，并上报：
   - `desktop.progress`
   - `desktop.completed`
   - `desktop.failed`
5. 后端将结果写入 `DesktopTask` 和 `ExecutionLog`。
6. 支持超时、取消和 Agent 断线失败处理。

当前动作能力：

- `launch_app`
- `activate_window`
- `create_document`
- `click_element`
- `type_text`
- `press_hotkey`
- `wait`
- `screenshot`
- `read_text`
- `close_app`

Windows 自动化技术：

- `pywin32` 枚举窗口、获取 PID、激活窗口、终止进程。
- `pywinauto` UIA backend 查找控件、点击控件、读取文本和模拟键盘输入。
- `win32com` 调用 WPS Writer 的 `kwps.Application`，创建文档并保存。
- `Pillow.ImageGrab` 完成全屏或区域截图。
- 每个动作写入本地 JSONL 审计日志。

安全机制：

- Agent 只接受已注册动作，不接受任意 Shell 或脚本。
- `launch_app` 必须匹配应用白名单。
- 文档动作路径必须位于允许目录中。
- 前端支持 Agent 配置热更新。
- 一个 Agent 同一时间只执行一个任务。

涉及知识：

- WebSocket 长连接和心跳
- 消息协议设计
- 指数退避重连
- Agent 注册、配对和 Token 认证
- Windows Win32 API
- UI Automation
- WPS COM 自动化
- 应用和目录白名单
- 本地审计日志

### 4.6 调度、执行记录和通知

主要代码：

- `app/scheduler/jobs.py`
- `app/api/scheduler.py`
- `app/notifications/notifier.py`
- `app/api/executions.py`

调度：

- 使用 APScheduler `AsyncIOScheduler`。
- 使用 SQLAlchemy JobStore 持久化任务。
- 支持 5 段 Cron 表达式。
- `coalesce=True`、`max_instances=1`、`misfire_grace_time=60`。
- 调度控制接口仅允许本机访问。

执行记录：

- 每次运行创建 `ExecutionLog`。
- 保存状态、开始/结束时间、错误、截图和步骤日志。
- 前端通过 WebSocket 观察状态变化，并在完成后加载最终详情。

通知：

- 支持 SMTP 邮件。
- 支持 Webhook。
- 支持始终通知或仅失败通知。
- 邮件可附带最新截图。

### 4.7 Desktop 客户端与交付

主要代码：

- `desktop/src-tauri/`
- `desktop/src/`
- `scripts/build_backend.py`
- `scripts/build_agent.py`
- `scripts/build_all.py`

Tauri 客户端负责：

- 复用现有 React 页面。
- 启动 `backend-server` 和 `desktop-agent` 两个 Sidecar。
- 健康检查后启动本地调度。
- 支持本地模式和远程模式。
- 系统托盘菜单。
- 关闭主窗口时隐藏到托盘。
- 开机自启动注册。
- 打开日志目录。
- 通过 PyInstaller 打包 Python Sidecar。
- 后端包内携带 Playwright Chromium，降低目标机器安装成本。
- 最终生成 NSIS Windows 安装包。

涉及知识：

- Tauri 2 和 Rust
- Sidecar 进程管理
- IPC / invoke
- Windows 托盘
- Windows 注册表启动项
- 应用数据目录管理
- PyInstaller 打包
- NSIS 安装包

## 5. 技术知识地图

| 分类 | 技术 | 项目中的具体应用 |
| --- | --- | --- |
| 后端框架 | FastAPI | REST API、WebSocket、依赖注入、生命周期 |
| 异步编程 | Python asyncio | Agent、执行引擎、调度器、WebSocket |
| 数据校验 | Pydantic | API 请求/响应模型、配置加载 |
| 数据库 | SQLAlchemy 2.0 Async | ORM、关系、JSON 字段、异步 Session |
| 数据库 | SQLite + aiosqlite | 本地开发、桌面单机数据存储 |
| AI | DeepSeek + OpenAI SDK | 意图解析、步骤规划、代码生成、调试 Agent |
| AI 工程 | Prompt / JSON Schema | System Prompt、JSON 提取、重试、校验 |
| Web 自动化 | Playwright | 浏览器控制、选择器、截图、表格提取 |
| 沙箱 | Docker SDK | 资源限制、网络隔离、容器生命周期 |
| 安全 | Python AST | 禁止危险导入、调用和代码模式 |
| 安全 | Fernet | 对称加密存储凭据 |
| 安全 | JWT / SHA-256 | 用户身份、Agent Token 哈希 |
| 实时通信 | WebSocket | Agent 长连接、执行状态监控 |
| 调度 | APScheduler | Cron 调度、持久化 JobStore |
| 通知 | SMTP / HTTPX | 邮件和 Webhook |
| Windows | pywin32 | 窗口、进程、剪贴板和底层 API |
| Windows | pywinauto | UI Automation 和键盘输入 |
| Windows | WPS COM | 自动创建并保存 WPS 文档 |
| 图像 | Pillow | 桌面截图 |
| 前端 | React + TypeScript | 对话、流程、监控、设置页面 |
| 前端 | Vite + Tailwind CSS | 构建和界面样式 |
| 前端 | Zustand | 用户、会话和工作流状态 |
| 前端 | React Flow | DAG 流程预览与编辑 |
| 前端 | Axios | API 客户端和拦截器 |
| 桌面 | Tauri 2 + Rust | 桌面壳、Sidecar、托盘、自启动 |
| 打包 | PyInstaller + NSIS | Python 服务和 Windows 安装包 |
| 测试 | pytest / pytest-asyncio / httpx | 后端单元、API 和集成测试 |
| 测试 | Node test / Vitest | 前端逻辑和桌面逻辑测试 |

## 6. 最值得写进简历的技术亮点

1. **完成了从自然语言到可执行工作流的完整闭环**
   - 不只是调用 LLM，而是实现了意图解析、步骤规划、代码生成、人工确认、持久化和执行。

2. **设计了网页与桌面双执行器架构**
   - 通过 `executor_type` 和 Execution Router 解耦两种执行环境。
   - 共用 Workflow、日志、调度和前端监控模型。

3. **解决了 LLM 代码执行的安全问题**
   - AST 校验、凭据泄漏检查、Docker 资源限制、网络域名白名单。

4. **设计了面向 Windows 的受控 Agent 协议**
   - WebSocket 长连接、配对 Token、能力匹配、动作白名单、超时取消、断线处理和审计。

5. **落地了 Windows UI Automation 和 WPS COM**
   - 支持启动应用、窗口控制、UIA 查找、点击、文本输入、读取文本、截图和 WPS 文档创建。

6. **实现了工作流全生命周期管理**
   - 对话生成、DAG 编辑、发布、手动运行、Cron 调度、执行监控和通知。

7. **完成了桌面产品化交付**
   - Tauri Sidecar、本地/远程模式、托盘、自启动、日志目录、PyInstaller 和 NSIS。

8. **构建了可重复验证的测试体系**
   - 使用 Fake Docker、Fake Agent、API 集成测试和 Windows 交互式测试覆盖关键链路。

## 7. 简历表述模板

### 7.1 完整技术版

**NL-RPA：自然语言驱动的双执行器 RPA 平台**

技术栈：`Python`、`FastAPI`、`SQLAlchemy`、`AsyncIO`、`DeepSeek/OpenAI SDK`、`Playwright`、`Docker`、`WebSocket`、`APScheduler`、`React 18`、`TypeScript`、`React Flow`、`Tauri 2`、`Rust`、`PyWin32`、`pywinauto`

- 设计并实现“自然语言理解 -> 结构化规划 -> 工作流生成 -> 执行 -> 日志反馈”的完整 RPA 链路，支持对话式修改、确认、保存、发布和定时执行。
- 构建网页与 Windows 双执行器架构：网页任务由 Playwright 执行并优先运行在 Docker 沙箱中；桌面任务被编译为受控动作，通过 WebSocket 下发给本机 Agent。
- 为 LLM 生成代码设计 AST 安全校验、敏感信息泄漏检测、Docker 资源限制和域名白名单机制，阻断危险导入、系统调用和非授权网络访问。
- 实现 Windows Desktop Agent，包括设备配对、Token 哈希认证、心跳重连、能力匹配、应用/目录白名单、超时取消、截图回传和 JSONL 审计日志。
- 基于 PyWin32、UIAutomation 和 WPS COM 实现应用启动、窗口控制、控件操作、键盘输入、文本读取、截图和 WPS 文档创建。
- 完成 Tauri 桌面客户端与 PyInstaller Sidecar 交付，支持本地/远程模式、托盘、开机自启动和 Windows 安装包构建。

### 7.2 AI 应用 / AI Agent 方向

- 基于 DeepSeek 和 OpenAI SDK 设计多阶段 Agent 链，将自然语言需求依次转换为意图 JSON、细粒度步骤和可执行 Playwright 代码。
- 通过严格 System Prompt、JSON 容错解析和纠错重试，降低大模型输出不稳定对业务流程的影响。
- 将 LLM 定位为规划和生成组件，通过 Workflow DSL、AST 校验、沙箱和桌面动作白名单限制模型权限，避免“LLM 任意控制机器”。
- 实现对话历史、需求澄清、自然语言修改流程和人工确认机制，形成可交互、可修正的 Agentic Workflow。
- 支持将生成结果持久化为可编辑 DAG，并在后续通过对话继续调整，而不是一次性生成后丢弃。

### 7.3 后端 / 平台工程方向

- 使用 FastAPI 和异步 SQLAlchemy 构建 Workflow、Execution、Agent、ChatSession 等核心领域模块，覆盖 CRUD、发布、调度和执行链路。
- 设计 Execution Router，根据执行器类型、在线状态和所需能力选择 Web Engine 或 Desktop Agent。
- 使用 WebSocket 管理桌面 Agent 长连接，实现心跳检测、任务下发、进度回传、完成通知、超时和断线故障处理。
- 使用 APScheduler SQLAlchemy JobStore 实现 Cron 任务持久化和单实例、防抖、错过执行宽限策略。
- 建立统一 ExecutionLog 与步骤日志模型，支持截图归档、邮件/Webhook 通知和前端实时监控。

### 7.4 全栈 / 桌面方向

- 使用 React、TypeScript、Zustand 和 React Flow 实现对话生成、DAG 编辑、代码预览、执行监控和 Agent 配置界面。
- 实现 DAG 依赖增删、循环依赖检测、节点配置和实时保存状态提示。
- 使用 Tauri 2 复用 React 前端，并通过 Rust 管理 Python Sidecar、健康检查、系统托盘、窗口隐藏和开机启动。
- 使用 PyInstaller 打包后端与 Agent，携带 Playwright Chromium，最终生成 Windows NSIS 安装包。

## 8. 90 秒面试介绍

> 这个项目是一个自然语言驱动的 RPA 平台，目标是把用户的一句话需求转换成可执行、可管理、可调度的自动化流程。整体链路是：用户先输入自然语言，系统通过 DeepSeek 做意图解析和步骤规划，再根据任务类型选择网页或 Windows 执行路径。
>
> 网页流程会由模型生成 Playwright 异步代码，但不会直接执行。代码先经过 AST 安全校验，禁止 `os`、`subprocess`、`eval` 等危险能力，随后优先放进 Docker 沙箱运行，并限制 CPU、内存、超时和可访问域名。执行过程中会自动记录每个步骤、错误和截图。
>
> Windows 流程采用另一种思路：不让大模型直接生成任意脚本，而是把流程转换成 `launch_app`、`click_element`、`type_text`、`screenshot` 等受控动作。后端通过 WebSocket 把任务下发给本机 Agent，Agent 会做动作白名单、应用白名单和文档目录校验，再通过 pywin32、UI Automation 和 WPS COM 执行。
>
> 平台层还实现了工作流 CRUD、React Flow DAG 编辑、Cron 调度、执行监控、邮件/Webhook 通知，以及基于 Tauri 的桌面客户端、托盘、自启动和本地/远程模式。项目涉及的不只是模型调用，还包含完整的产品闭环、安全边界和桌面交付。

## 9. 高频面试追问

### 为什么先把自然语言转换成结构化 JSON，而不是直接生成代码？

结构化计划是业务中间层：

- 可以校验字段、步骤和依赖。
- 可以持久化、展示和人工修改。
- 可以在网页与桌面两种执行器之间做路由。
- 可以避免每轮修改都从原始自然语言重新开始。
- 可以把 LLM 的不确定性限制在规划阶段。

### 为什么桌面端不让 LLM 直接生成 Python 或 PowerShell？

桌面 Agent 运行在真实用户会话中，权限接近用户本人。直接执行任意脚本风险过高。项目采用固定动作协议，将模型输出限制到：

- 可枚举的动作类型
- 可校验的参数
- 应用白名单
- 文档目录白名单
- 审计日志

这体现的是“能力边界设计”，而不是单纯依赖 Prompt 约束。

### 如何避免执行模型生成的危险网页代码？

项目采用多层防御：

1. Prompt 禁止危险模块和硬编码密码。
2. AST 检查危险导入、调用和模式。
3. 检查敏感信息泄漏并触发重新生成。
4. Docker 限制资源、时间与网络。
5. 容器入口包装 `page.goto()`，只允许白名单域名。
6. 容器移除并清理临时执行目录。

### WebSocket 在这里承担什么角色？

- Desktop Agent 的在线状态维护
- 服务端心跳与 Agent 连接存活检查
- 桌面任务下发
- 执行进度、完成和失败事件回传
- 取消任务通知
- Agent 白名单配置热更新
- 前端执行详情页的实时状态展示

### 如何处理执行失败和超时？

- Playwright 每步记录失败截图和错误。
- Docker 超时会 kill 容器。
  - 桌面任务由 Dispatcher 设置超时事件，超时后将 Task 和 ExecutionLog 标记失败。
  - Agent 断线时，当前任务自动失败。
  - 支持主动取消，并向 Agent 发送 `desktop.cancel`。
  - 桌面任务因“找不到控件”失败时，`DesktopRepairCoordinator` 会用 Agent 回报的候选控件重写该步骤并从失败处续跑，次数由 `DESKTOP_REPAIR_ATTEMPTS` 控制。

## 10. 简历与面试边界

为了避免面试时被追问后出现“项目描述超过实现”的情况，以下边界要明确：

1. **自动修复只覆盖桌面流程的控件失败**
     - 桌面侧已接入闭环：失败步骤带候选控件 → 修复规划器重写该步 → 从失败处续跑，默认最多 2 轮。
     - 网页侧仍未接入：`DebugAgent` 已有独立调用逻辑，但 `ExecutionEngine.execute_with_debug()` 中仍是 TODO。
     - 简历可以写“桌面流程支持基于真实控件的失败自动修复与续跑”，不要写“网页代码失败后由 LLM 自动修复并重试”。

2. **WPS 当前主要是创建文档**
   - `create_document` 已完成。
   - `open_document`、`save_document`、`export_pdf` 出现在动作类型和设计目标中，但当前没有对应注册处理器。
   - 简历可以写“基于 WPS COM 实现文档创建”，不要写“完整覆盖打开、编辑、另存和 PDF 导出”。

3. **凭据存储模块和主执行链路尚未完全打通**
   - Fernet 加密存储、读取、删除和 `inject_credentials()` 已实现。
   - 当前 Workflow 运行接口没有自动调用凭据注入。
   - 可以写“实现加密凭据存储组件与泄漏检测”，不要写“已完成端到端凭据注入闭环”。

4. **DAG 不是后端并行调度引擎**
   - 当前 DAG 主要用于可视化、依赖建模和编辑。
   - 实际执行仍是生成代码顺序或动作数组。
   - 可以写“DAG 可视化编辑与依赖建模”，不要写“支持后端并行 DAG 调度”。

5. **本地降级会削弱沙箱隔离**
   - Docker 不可用时，项目会退回当前进程中的 Playwright 执行。
   - 面试时应说明这是开发降级路径，生产环境应禁用。

6. **实时监控是近实时实现**
   - 前端通过 WebSocket 获取状态。
   - 后端实时接口会周期性读取 ExecutionLog，并将新增步骤推送给前端。
   - 可以写“WebSocket 实时执行监控”，但不宜夸大为高频事件流平台。

## 11. 面试前复习优先级

### P0：必须能讲清

- 自然语言到结构化 Workflow 的完整链路
- 为什么网页和桌面要使用不同执行器
- Execution Router 的选择逻辑
- AST 校验和 Docker 沙箱如何保护网页执行
- 桌面 Agent 的 WebSocket、配对、心跳和动作白名单
- ExecutionLog、步骤日志和截图如何工作

### P1：应能解释设计取舍

- JSON 输出容错和模型重试
- Workflow DAG 的建模作用与执行边界
- APScheduler 持久化 JobStore 和 Cron
- Agent 断线、超时和取消处理
- Tauri Sidecar 与本地/远程模式
- PyWin32、UIAutomation、COM 的适用场景

### P2：加分项

- PyInstaller 如何打包 Playwright
- NSIS 安装包和 Windows 自启动
- Fernet、JWT 和 SHA-256 Token 哈希
- React Flow 的循环依赖检测
- Docker 域名白名单的边界情况
- 如何把当前项目扩展为多租户、多 Agent 集群

## 12. 推荐简历关键词

可以放在技能或项目标签中的关键词：

`LLM Agent`、`Prompt Engineering`、`Structured Output`、`RPA`、`Playwright`、`Browser Automation`、`Windows Automation`、`UI Automation`、`WPS COM`、`WebSocket`、`FastAPI`、`AsyncIO`、`SQLAlchemy`、`SQLite`、`Docker Sandbox`、`AST Security`、`APScheduler`、`React`、`TypeScript`、`React Flow`、`Zustand`、`Tauri`、`Rust`、`PyInstaller`。

## 13. 最稳的项目总结

如果只能用三句话介绍，推荐这版：

1. NL-RPA 是一个把自然语言需求转换成可执行自动化流程的 RPA 平台。
2. 它通过 LLM 完成意图解析和步骤规划，再根据任务类型路由到 Playwright 沙箱或 Windows Desktop Agent。
3. 我重点解决了双执行器抽象、模型代码安全、桌面动作白名单、WebSocket 任务调度、执行可观测性和 Tauri 桌面交付问题。
