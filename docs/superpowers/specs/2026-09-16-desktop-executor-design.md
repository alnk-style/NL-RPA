# 桌面执行器与桌面客户端设计

## 1. 背景

NL-RPA 当前是浏览器自动化平台：

- 前端使用 React、TypeScript、Vite。
- 后端使用 FastAPI、SQLAlchemy、SQLite。
- 工作流由 DeepSeek Agent 生成 Python/Playwright 代码。
- 执行由 DockerSandbox 或本地 PlaywrightRunner 完成。
- 调度由 APScheduler 完成。

该架构可以稳定处理网页流程，但不能启动或控制 Windows 桌面应用。

## 2. 当前问题

### 2.1 Playwright 只能控制浏览器

Playwright 的 `page`、`BrowserContext` 和选择器都针对浏览器 DOM，无法定位 WPS、微信等 Win32 或自绘桌面控件。

### 2.2 Docker 沙箱无法访问 Windows 桌面

当前沙箱是隔离容器，没有 Windows 用户桌面会话，也不能访问宿主机安装的 `WeChat.exe`、`wps.exe` 或 COM 自动化接口。

### 2.3 安全校验禁止宿主命令

代码验证器禁止 `os`、`subprocess`、`shutil` 等模块。直接放宽限制会让 LLM 生成代码获得主机控制能力，安全风险过高。

### 2.4 工作流没有执行器概念

Workflow 目前只有 `trigger_type`，没有 `executor_type`。后端默认所有流程都通过 Playwright 执行。

### 2.5 桌面步骤会生成伪代码

规划器可能把 `WeChat.exe` 或 WPS 路径转换成 Playwright 步骤。代码最终只能截图或等待，不能真正操作桌面程序。

### 2.6 本地使用和远程调度没有统一模型

单机使用时需要一键桌面客户端；未来多机器使用时需要远程 Agent。若直接做单体应用，后续会难以拆分。

## 3. 目标

1. 保留现有网页自动化能力。
2. 增加 Windows 桌面执行器。
3. 支持 WPS 等桌面应用的可控自动化。
4. 同时支持本地模式和远程模式。
5. 提供桌面客户端、托盘运行和定时执行。
6. 桌面任务必须具备日志、截图、超时、重试和审计。
7. 不向 LLM 开放任意宿主命令执行能力。

## 4. 非目标

- 不保证微信桌面版所有界面都能自动识别。
- 不绕过 Windows UAC、权限隔离或应用登录机制。
- 不允许工作流直接运行任意 PowerShell、CMD 或 Python 脚本。
- 不在第一阶段实现完整计算机视觉平台。
- 不在第一阶段支持 macOS 或 Linux 桌面应用。

## 5. 核心设计原则

### 5.1 能力动作，而非任意代码

桌面 Agent 只执行预定义动作：

- `launch_app`
- `activate_window`
- `open_document`
- `click_element`
- `type_text`
- `press_hotkey`
- `wait`
- `screenshot`
- `read_text`
- `save_document`
- `export_pdf`
- `close_app`

LLM 只能生成这些动作，不能生成 Python 或 PowerShell 脚本。

### 5.2 Agent 运行在用户桌面会话

Windows UI 自动化必须运行在用户已登录且桌面可用的会话中。不能把 Agent 做成普通 Windows Service 后直接控制交互桌面。

### 5.3 网页与桌面统一调度

Workflow 增加 `executor_type`：

- `web`：PlaywrightRunner 或 DockerSandbox。
- `windows`：DesktopDispatcher 加 Windows Desktop Agent。

### 5.4 当地优先，可远程扩展

桌面客户端默认连接本机后端。后续可以只切换服务地址，将同一个 Agent 连接到远程后端。

## 6. 总体架构

```text
┌────────────────────────────────────────────┐
│ Tauri / Electron 桌面客户端                 │
│  ├── React 界面                             │
│  ├── 本地 FastAPI 服务（可选）              │
│  ├── 本地 SQLite                            │
│  ├── 托盘与自动调度                         │
│  └── Windows Desktop Agent Sidecar          │
└───────────────────┬────────────────────────┘
                    │ HTTP / WebSocket
┌───────────────────▼────────────────────────┐
│ NL-RPA 后端                                 │
│  ├── Workflow API                           │
│  ├── Execution Router                       │
│  ├── Agent Registry                         │
│  ├── Desktop Dispatcher                     │
│  ├── Scheduler                              │
│  └── Execution / Audit Logs                 │
└───────────────────┬────────────────────────┘
                    │ WebSocket
┌───────────────────▼────────────────────────┐
│ Windows Desktop Agent                       │
│  ├── App Launcher                           │
│  ├── Window Controller                      │
│  ├── UI Automation Handler                  │
│  ├── WPS COM Adapter                        │
│  ├── Screenshot / Artifact Uploader         │
│  └── Allowlist / Policy Engine              │
└────────────────────────────────────────────┘
```

## 7. 组件职责

### 7.1 Execution Router

根据 `workflow.executor_type` 选择执行器：

```python
if workflow.executor_type == "windows":
    return await desktop_executor.execute(workflow)
return await web_executor.execute(workflow)
```

### 7.2 Agent Registry

维护在线 Agent：

- Agent 名称、平台、版本。
- 能力列表，例如 `wps`, `uia`, `window`, `screenshot`。
- 在线状态和最后心跳时间。
- 当前绑定用户。
- 配对令牌哈希，不保存明文。

### 7.3 Desktop Dispatcher

负责：

- 创建 DesktopTask。
- 选择在线 Agent。
- 通过 WebSocket 下发任务。
- 接收 accepted、progress、artifact、completed、failed 事件。
- 超时、断线、重试和取消。

### 7.4 Windows Desktop Agent

负责：

- 与后端建立 WebSocket。
- 解析并验证动作。
- 检查应用路径和文档目录白名单。
- 启动应用、等待窗口、执行 UI 操作。
- 执行 WPS COM 操作。
- 上传截图和日志。
- 记录本机审计日志。

### 7.5 Desktop Shell

推荐 Tauri，复用现有 React 前端：

- 管理应用窗口。
- 启动和停止 Python Sidecar。
- 启动桌面 Agent。
- 提供托盘菜单。
- 支持开机启动。
- 支持连接本地或远程后端。

## 8. 数据模型

### 8.1 Workflow 新增字段

```text
executor_type: str, default "web"
required_capabilities: JSON, default []
```

### 8.2 DesktopAgent

```text
id: int
user_id: int
name: str
platform: str
version: str
capabilities: JSON
status: str
last_seen_at: datetime
token_hash: str
created_at: datetime
```

### 8.3 DesktopTask

```text
id: int
execution_log_id: int
workflow_id: int
agent_id: int | null
status: str
actions: JSON
result: JSON | null
error_message: text | null
created_at: datetime
started_at: datetime | null
finished_at: datetime | null
```

## 9. Agent 协议

### 9.1 配对

```http
POST /api/agents/pair
Content-Type: application/json

{
  "pairing_code": "123456",
  "name": "DESKTOP-01",
  "platform": "windows",
  "version": "0.1.0",
  "capabilities": ["window", "uia", "wps", "screenshot"]
}
```

响应：

```json
{
  "agent_id": 1,
  "token": "one-time-visible-token"
}
```

### 9.2 WebSocket 连接

```text
ws://host:8000/api/agents/ws?agent_id=1&token=...
```

### 9.3 服务端下发任务

```json
{
  "type": "desktop.task",
  "task_id": 17,
  "execution_log_id": 42,
  "workflow_id": 3,
  "timeout_seconds": 300,
  "actions": [
    {
      "type": "launch_app",
      "params": {
        "path": "C:\\Program Files\\WPS Office\\wps.exe",
        "args": []
      }
    },
    {
      "type": "wait",
      "params": {
        "seconds": 3
      }
    },
    {
      "type": "screenshot",
      "params": {
        "name": "wps-opened"
      }
    }
  ]
}
```

### 9.4 Agent 事件

```json
{
  "type": "desktop.progress",
  "task_id": 17,
  "step_index": 1,
  "status": "success",
  "message": "WPS 已启动",
  "screenshot_path": "logs/task-17/step-1.png"
}
```

```json
{
  "type": "desktop.completed",
  "task_id": 17,
  "success": true,
  "output": "WPS opened",
  "error": null
}
```

## 10. 安全设计

1. Agent 配对使用短期配对码，令牌明文只显示一次。
2. 服务端只保存令牌哈希。
3. 所有任务使用 TLS；本地模式允许显式开启 `ws://127.0.0.1`。
4. Agent 使用应用路径白名单，不允许任意程序。
5. 文档路径限制在白名单目录。
6. 动作参数必须经过 Pydantic 和 Agent 双重校验。
7. 禁止任意 shell、脚本和动态代码执行。
8. 每个动作写入审计日志。
9. 高风险动作，例如关闭应用或覆盖文件，需要工作流显式确认。
10. 远程多机器模式必须增加设备撤销和令牌轮换。

## 11. WPS 自动化策略

优先级从高到低：

1. COM 自动化。
2. UI Automation。
3. 图像识别或 OCR。

WPS 可能提供的 COM ProgID：

- 文字：`kwps.Application`
- 表格：`ket.Application`
- 演示：`kwpp.Application`

Agent 启动 WPS 后，优先调用 COM 完成打开、编辑、保存和导出操作。只有 COM 不支持的菜单、弹窗或对话框，才使用 UIAutomation。

## 12. 微信自动化边界

微信桌面版存在以下风险：

- 大量界面为自绘控件，UIAutomation 覆盖不完整。
- 版本升级可能改变控件结构。
- 自动化可能触发安全风控。
- 无人值守登录依赖用户会话状态。

因此微信应作为独立适配器，不能假设通用 UIA 步骤即可稳定工作。

## 13. 运行模式

### 13.1 本地模式

- Tauri 客户端启动本地后端。
- 本地后端使用 SQLite。
- Agent Sidecar 自动注册。
- 客户端关闭时，托盘进程继续运行。
- Windows 计划任务负责在用户登录后启动托盘。

### 13.2 远程模式

- Web 前端连接远程后端。
- Windows Agent 通过 WebSocket 连接远程后端。
- 用户选择可用 Agent。
- 远程模式支持多台电脑。

## 14. 分阶段交付

### P0：后端执行器抽象

- 增加 `executor_type`。
- 增加 Execution Router。
- 保持现有网页流程行为不变。

### P1：Windows Agent MVP

- 配对和在线状态。
- 启动应用、等待、截图、热键。
- Agent 任务协议。
- ExecutionLog 集成。

### P2：WPS 连接器

- COM 打开文档。
- 保存和导出 PDF。
- UIAutomation 回退。

### P3：桌面客户端

- Tauri 包装现有 React。
- Python Sidecar。
- 托盘、自动启动和本地调度。

### P4：多机器与远程 Agent

- Agent 选择。
- 设备撤销。
- 任务队列和断线重试。
- 审计和权限策略。

## 15. 验收标准

1. 现有网页流程测试全部通过。
2. 新 Workflow 默认仍是 `executor_type=web`。
3. Windows Agent 可以完成配对并在页面显示在线状态。
4. Agent 可以启动 Notepad 并截图。
5. Agent 可以启动 WPS、打开文档、保存并截图。
6. 桌面任务失败时，ExecutionLog 记录错误和已完成步骤。
7. Agent 离线时任务返回可理解的错误，不进入无限等待。
8. 未经白名单允许的程序无法启动。
9. Tauri 客户端关闭主窗口后，托盘进程仍能保持调度。
10. 本地模式和远程模式共用同一套 Action Schema。

## 16. 主要风险

- WPS 不同版本 COM 接口存在差异。
- Windows 锁屏或切换用户会导致 UI 自动化失败。
- UAC 弹窗通常无法自动处理。
- 打包 Python 和 Playwright 会显著增加安装包体积。
- 微信风控和界面变化会影响稳定性。

