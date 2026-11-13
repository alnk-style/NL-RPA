# NL-RPA：自然语言驱动的 RPA 流程自动生成平台

## 当前状态（2026-11-11）

本项目按个人使用、单用户和本地 SQLite 设计。Docker 不是运行前提，正式执行
以发布后的类型化工作流版本为准，不依赖每次运行重新生成代码。

本地自动化测试、真实 Windows 交互桌面、本机 Desktop Agent 和 Playwright
Chromium 验收已通过。标准 Microsoft Edge、真实 Windows VM/WSS、远程 SMTP
和远程视觉 provider 尚未在当前环境完成验收，因此不能把“完整个人使用发布
候选”视为已经验收通过。详细记录见
[`docs/superpowers/reports/2026-11-11-stage-g-final-acceptance.md`](docs/superpowers/reports/2026-11-11-stage-g-final-acceptance.md)。

## 🚀 快速启动

项目提供一键启动脚本。首次运行会自动检查并安装 Python 依赖、Playwright Chromium 浏览器和前端依赖；后续运行会直接启动服务。

### Linux / Mac

```bash
chmod +x start.sh && ./start.sh
```

### Windows

双击项目根目录下的 `start.bat`。

启动完成后会自动打开 [http://localhost:5173](http://localhost:5173)：

- 前端地址：`http://localhost:5173`
- 后端地址：`http://localhost:8000`
- 接口文档：`http://localhost:8000/docs`

运行日志位于 `logs/backend.log` 和 `logs/frontend.log`。如果 `.env` 不存在，脚本会自动从 `.env.example` 创建，并提醒填写 API Key。

`.env.example` 默认关闭免登录模式（`DEV_AUTH_BYPASS=false`）。本地开发需要
免登录时，请在自己的 `.env` 中显式设为 `true`，流程管理和执行监控会自动使用
`local-dev` 用户。正式部署必须保持 `false`，并接入完整登录流程。

### 停止服务

- Linux / Mac：运行 `./stop.sh`
- Windows：双击 `stop.bat`，或在 `start.bat` 启动窗口中按任意键

使用 `start.sh` 时也可以直接按 `Ctrl+C` 优雅停止全部服务。

## 🖥️ 桌面客户端构建

### 环境要求

- Windows 10/11
- Python 3.11+
- Node.js 18+
- Rust（用于 Tauri 编译）
- Visual Studio Build Tools（含 MSVC、Windows SDK）

### 一键构建

```bash
python scripts/build_all.py
```

脚本会依次安装后端与 Agent 依赖，下载并嵌入 Playwright Chromium，
使用 PyInstaller 生成两个 sidecar，安装桌面端依赖，并执行 Tauri NSIS
打包。构建脚本默认使用可访问的 GitHub 镜像下载 NSIS 工具包，可通过
`TAURI_BUNDLER_TOOLS_GITHUB_MIRROR` 环境变量覆盖。

### 手动构建

1. 打包后端：

   ```bash
   python scripts/build_backend.py
   ```

2. 打包 Agent：

   ```bash
   python scripts/build_agent.py
   ```

3. 构建桌面应用：

   ```bash
   cd desktop
   npm install
   npm run tauri build
   ```

PyInstaller 会在 `desktop/src-tauri/binaries/` 生成
`backend-server.exe`、`desktop-agent.exe`，同时生成带
`x86_64-pc-windows-msvc` 后缀的 Tauri externalBin 文件。后端 exe 已包含
运行工作流所需的 Chromium，不需要在目标电脑上单独执行
`playwright install chromium`。

由于内置 Chromium，最终 NSIS 安装包约 300 MB 以上，这是保证目标电脑
无需额外下载浏览器运行时的代价。

### 安装包位置

```text
desktop/src-tauri/target/release/bundle/nsis/NL-RPA Desktop_0.1.0_x64-setup.exe
```

### 开发模式

```bash
cd desktop
npm run tauri dev
```

桌面客户端开发、sidecar 通信和本地/远程模式说明见
[`desktop/README.md`](desktop/README.md)。

## 项目概述

用户通过自然语言描述自动化需求，系统协助生成并发布不可变的类型化工作流
版本。正式运行只执行已发布版本，保留检查点、HumanTask、产物和投递记录。

## 技术栈

| 层级 | 技术选型 |
| --- | --- |
| 后端框架 | FastAPI (Python 3.11+) |
| 数据库 | SQLite + SQLAlchemy ORM |
| 浏览器自动化 | Playwright (async API) |
| AI 模型 | DeepSeek (deepseek-chat) via openai SDK |
| 定时调度 | APScheduler (AsyncIOScheduler) |
| 执行主机 | 本机 Desktop Agent / 手动注册的 VM Agent |
| 前端 | React 18 + TypeScript + Vite + TailwindCSS + React Flow |
| 实时通信 | WebSocket |

## 手动启动

1. 复制环境变量模板，并填写 `DEEPSEEK_API_KEY` 和
   `CREDENTIAL_ENCRYPTION_KEY`：

   ```bash
   cp .env.example .env
   ```

2. 生成 Fernet Key：

   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. 安装后端依赖：

   ```bash
   pip install -r requirements.txt
   ```

4. 安装 Playwright 浏览器：

   ```bash
   playwright install chromium
   ```

5. 启动后端服务：

   ```bash
   uvicorn app.main:app --reload
   ```

6. 在另一个终端安装前端依赖：

   ```bash
   cd frontend
   npm install
   ```

7. 启动前端开发服务器：

   ```bash
   npm run dev
   ```

8. 访问 [http://localhost:5173](http://localhost:5173)。

后端默认监听 `http://localhost:8000`，Vite 开发服务器默认监听
`http://localhost:5173`。CORS 默认只允许本地前端和 Tauri 桌面端来源，
可通过 `.env` 中的逗号分隔列表 `CORS_ALLOW_ORIGINS` 调整。

运行后端测试：

```bash
pip install pytest pytest-asyncio
python -m pytest -q
```

前端构建与逻辑测试：

```bash
cd frontend
npm run build
npm test
```

## 项目结构说明

```text
rpa_platform/
├── app/
│   ├── main.py                    # FastAPI 入口，注册路由，启动调度器
│   ├── config.py                  # 环境变量与运行配置
│   ├── database.py                # SQLAlchemy 引擎和 Session 管理
│   ├── models/                    # User、Workflow、ExecutionLog、Credential
│   ├── schemas/                   # Pydantic 请求与响应模型
│   ├── protocols/
│   │   └── structured.py          # 步骤、定位、观察与失败分类协议
│   ├── agents/
│   │   ├── intent_parser.py       # 自然语言 → 结构化 JSON
│   │   ├── step_planner.py        # 结构化计划 → 细化步骤
│   │   ├── code_generator.py      # 步骤 → Playwright 代码
│   │   ├── web_catalog.py         # 网页结构化动作目录与计划校验
│   │   ├── web_planner.py         # 观察结果 → 结构化网页步骤
│   │   ├── desktop_catalog.py     # 桌面结构化动作目录
│   │   ├── debug_agent.py         # 失败代码修复
│   │   └── template_agent.py      # 流程模板序列化与 CRUD
│   ├── engine/
│   │   ├── runner.py              # 本地 Playwright 执行引擎
│   │   ├── web_executor.py        # 结构化网页步骤执行与单步重试
│   │   ├── browser_observer.py    # 网页观察层（DOM/可访问性/定位候选）
│   │   ├── hybrid_executor.py     # web/windows/mcp/code 步骤路由与 DAG 调度
│   │   ├── mcp_client.py          # MCP 客户端（官方 SDK + 内置 HTTP 兜底）
│   │   ├── mcp_registry.py        # MCP Server 注册、工具发现、权限门与审计
│   │   ├── mcp_server.py          # 平台有限工具导出为 MCP Server
│   │   ├── connectors/            # 统一 Connector 接口与 Desktop Bridge
│   │   ├── checkpoints.py         # 步骤检查点与登录后恢复
│   │   ├── recorder.py            # 录制事件 → 结构化步骤草稿
│   │   ├── sandbox.py             # Docker 沙箱、资源与网络隔离
│   │   └── code_validator.py      # AST 安全预检与凭据泄漏检测
│   ├── api/
│   │   ├── chat.py                # 对话生成、修改与确认接口
│   │   ├── workflows.py           # 流程创建、编辑、发布接口
│   │   ├── credentials.py         # 流程凭据保存、列出与删除接口
│   │   ├── observations.py        # 网页观察接口
│   │   ├── recordings.py          # 录制会话、草稿生成与桌面拾取接口
│   │   ├── mcp.py                 # MCP Server 配置、测试、授权与审计接口
│   │   ├── mcp_export.py          # 平台工具导出为 MCP Server（POST /mcp）
│   │   └── executions.py          # 手动执行、日志与实时状态接口
│   ├── scheduler/                 # APScheduler 定时任务管理
│   ├── notifications/             # 邮件与 Webhook 通知
│   └── security/                  # Fernet 凭据加密存储与日志脱敏
├── frontend/
│   ├── src/                       # React 页面、组件、API 客户端与类型
│   └── dist/                      # 前端生产构建产物
├── docker/
│   ├── Dockerfile.sandbox         # 沙箱镜像
│   └── entrypoint_template.py     # 沙箱执行入口与域名白名单检查
├── templates/                     # 流程模板 JSON/YAML 存储
├── scripts/                       # 录制、演示 MCP Server 等辅助脚本
├── logs/                          # 执行日志、截图与运行产物
├── tests/
│   ├── conftest.py                # 全局 pytest 夹具
│   └── integration/               # 端到端与定时任务整合测试
├── .env.example                   # 环境变量模板
├── requirements.txt               # 后端 Python 依赖
├── Dockerfile                     # 后端服务镜像
├── docker-compose.yml             # app、frontend、redis 编排
└── README.md
```

主要模块职责：

- `app/api` 提供对话、流程管理、执行和日志接口。
- `app/agents` 负责自然语言解析、步骤规划、代码生成和调试修复。
- `app/engine` 负责代码安全预检、浏览器执行、Docker 沙箱和网络白名单。
- `app/security` 负责凭据加密存储和运行时凭据注入。
- `app/scheduler` 负责定时任务持久化和触发。
- `app/notifications` 负责邮件与 Webhook 通知。
- `frontend` 提供对话、流程编辑、执行状态和结果展示界面。
- `tests/integration` 覆盖核心 API、执行、日志、通知和定时任务流程。

可靠性与 MCP 功能开关默认关闭（见 `.env.example`）：
`OBSERVE_BEFORE_PLAN` 开启网页观察层，`WEB_STRUCTURED_ACTIONS`
启用结构化网页执行，`STEP_LEVEL_REPAIR` 允许失败后按步骤自动修复，
`WEB_REPAIR_ATTEMPTS` 控制单次执行内的最大修复轮数，
`AUTH_REQUIRED_PAUSE` 默认开启：登录失效会创建持久化 HumanTask，
由待处理中心完成服务端重新验证后从检查点继续。关闭后新执行会按旧语义
直接失败，旧的 `resume-after-login` 接口仍保留兼容。
`RECORDER_MODE` 开启录制接口：先创建录制会话，再追加事件，
结束后生成可编辑的结构化草稿并落库为 draft 流程。浏览器演示脚本：

```bash
python scripts/record_browser.py --url https://example.com
```

流程编辑器提供“录制”面板：网页录制会给出复用同一会话的录制命令，
桌面录制可以直接调用拾取控件；草稿与警告会显示在面板里，可一键导入。
同一个 Workflow 支持混排 `web` 与 `windows` 步骤：`step.executor` 决定
执行器，`depends_on` 在运行时按 DAG 拓扑调度，条件步骤用 `condition`
声明；失败后按幂等键写入检查点，可从失败步骤继续。

脚本会打开浏览器录制点击、输入、选择与下载等操作，按 Ctrl+Enter
结束后输出步骤草稿；配合 `--api` 与 `--token` 可直接写入后端录制
会话。桌面录制可以调用 `POST /api/recordings/{id}/pick` 拾取鼠标
指针下的控件，生成带 AutomationId 的 `click_element` 步骤。

### 连接器与 MCP

平台作为 MCP Client 接入外部工具时，直接在设置页（`/settings`）的
「连接器与 MCP」里打开开关即可，无需改 `.env` 或重启：开关写入数据库
并立即生效，重启后仍然保留。`.env` 里的 `MCP_CLIENT` 只作为默认值，
应用内开关优先；接口同样可以管理：

| 接口 | 用途 |
| --- | --- |
| `GET/PATCH /api/mcp/settings` | 读取或切换 MCP 客户端开关（关闭时也可访问） |
| `GET/POST /api/mcp/servers` | 列出或新增 MCP Server（密钥加密存储，只回显是否已配置） |
| `PATCH/DELETE /api/mcp/servers/{id}` | 启停/修改/删除 Server |
| `POST /api/mcp/servers/{id}/test` | 连接测试与工具发现，写入工具清单与风险级别 |
| `POST /api/mcp/servers/{id}/tools/{tool}/authorize` | 工具授权（destructive 可同时确认） |
| `GET /api/mcp/audits` | 调用审计（server、tool、参数摘要、状态、耗时、错误、workflow/execution） |
| `GET /api/mcp/connectors` | 统一 Connector 能力与认证状态 |

单机模式的 App MCP 运行管理使用：

| 接口 | 用途 |
| --- | --- |
| `GET /api/local-apps` | 列出 `mcp_apps` 中已校验的应用和运行状态 |
| `POST /api/local-apps/scan` | 重新扫描并返回新增、移除和 Manifest 校验错误 |
| `POST /api/local-apps/{app_id}/start` | 启动受管本机进程、健康检查并自动注册 MCP Server |
| `POST /api/local-apps/{app_id}/stop` | 停止当前后端启动的进程，保留 Server 和授权记录 |
| `POST /api/local-apps/{app_id}/restart` | 停止后重新启动并刷新工具清单 |
| `GET /api/local-apps/{app_id}/logs?lines=200` | 读取最近日志（最多 500 行，内容脱敏） |
| `GET/PATCH/DELETE /api/local-apps/{app_id}/binding` | 查看、设置自动启动或由 owner 解除持久化绑定 |
| `GET/POST /api/local-apps/{app_id}/mcp` | 同进程 App MCP 端点，要求每应用独立 Bearer token |

前端在 `/apps` 中以应用卡片提供启动、停止、重启、测试连接、详情和
日志查看。Adapter 模板与开发约束见
[`docs/app_mcp_adapter_guide.md`](docs/app_mcp_adapter_guide.md)。

Manifest 的 `local.host_mode` 支持 `subprocess` 和 `in_process`。前者启动
独立回环 HTTP 子进程；后者直接加载到后端，通过现有 ConnectorRegistry
共享 Desktop Connector。当前只有 Windows 路径经过验证，其他平台会明确
返回 unsupported。首次认证启动建立 owner/workspace 绑定，只有持久化绑定
且 `auto_start=true` 的应用会在后端启动后自动恢复。

工具按风险级别管控：`read_only` 默认允许，`mutating` 需要显式授权，
`destructive` 需要授权并在步骤参数里 `confirm=true`（或授权时
`confirmed=true`）；未授权、被拒绝和超时的调用都会写审计，且不会
发起真实调用。远程结果按不可信数据包装（`trust=untrusted`），只进入
执行日志与显式声明的 `result_var`，不会直接拼进提示词。

Workflow 里用步骤 `executor="mcp"`、`action="call_tool"` 调用：

```json
{
  "step_id": 2,
  "executor": "mcp",
  "action": "call_tool",
  "params": {
    "server": "local-tools",
    "tool": "echo",
    "arguments": {"text": "hello"},
    "timeout_seconds": 20,
    "confirm": false
  },
  "retry_policy": {"max_attempts": 2, "backoff_seconds": 0.5},
  "idempotency_key": "mcp-echo-1",
  "depends_on": [1]
}
```

同一个 Server 的调用按 `mcp:<server>` 资源键串行，不同 Server 可并行；
超时、只读工具的有限重试与幂等键续跑都沿用阶段 3 的检查点语义。
本机可用演示 Server 验证整条链路：

```bash
python scripts/run_mcp_demo_server.py --port 8765
```

需要把平台工具暴露给外部 MCP 客户端时打开 `MCP_SERVER_EXPORT=true`，
可选 `MCP_SERVER_TOKEN` 校验 `X-MCP-Token`，`POST /mcp` 提供
`initialize`、`tools/list`、`tools/call`；白名单只包含 `platform_health`、
`platform_list_workflows`、`platform_get_execution` 与需要
`confirm=true` 且 `MCP_SERVER_ALLOW_MUTATING=true` 的
`platform_run_workflow`。

### 指标、告警与回归回放（阶段 5 批次 A）

`METRICS_API=true` 后开放只读指标接口：

| 接口 | 用途 |
| --- | --- |
| `GET /api/metrics/overview` | 7 个验收指标 + 失败分类聚合 |
| `GET /api/metrics/failure-categories` | 按 `FailureCategory` 的失败明细 |
| `POST /api/metrics/alerts/check` | 立即执行阈值检查（需 `METRICS_ALERTS_ENABLED=true`） |

时间范围支持 `range=1h|24h|7d|30d|all` 或显式 `start/end`（ISO 8601），
维度支持 `workflow_id`、`executor`、`user_id`（只能查询当前用户）。
告警复用 `NotificationService` 的 SMTP / Webhook：默认阈值可用
`METRICS_ALERT_THRESHOLDS` JSON 覆盖，`METRICS_ALERTS_ENABLED=true`
时后台按 `METRICS_ALERT_INTERVAL_SECONDS` 轮询，按 cooldown 去重。
前端在执行监控页与执行详情页展示只读指标面板；开关关闭时接口返回
403，面板自动隐藏。

录制/观察/检查点回归夹具一键回放：

```powershell
.\.venv\Scripts\python.exe scripts/run_regression.py
.\.venv\Scripts\python.exe scripts/run_regression.py --json .tmp/regression.json
```

夹具位于 `tests/regression/fixtures/`（录制草稿、观察快照、step_logs），
输出通过率、失败分类与快照路径到 `.tmp/regression/`；回放失败只影响
该命令的退出码，不影响普通 pytest 用例。

### 工作区隔离、PostgreSQL 与任务队列（阶段 5 批次 B）

`WORKSPACE_ISOLATION=true` 后，Workflow / ExecutionLog / Credential /
DesktopAgent / MCPServer / ToolCallAudit 与产物下载都按
`workspace + user` 过滤：请求头 `X-Workspace-Id`（或查询参数
`workspace_id`）选择工作区，缺省使用当前用户的默认工作区；跨租户
读写与下载统一返回 404。历史数据在首次以该开关启动时自动回填到
默认工作区。每个工作区的 `quota.max_concurrent_executions` /
`max_concurrent_agent_tasks` 控制并发上限，Agent 只接受同工作区的
桌面任务。默认关闭时保持阶段 1–4 的单用户行为。

PostgreSQL 只需替换 `DATABASE_URL`：

```env
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/nlrpa
```

迁移入口 `scripts/migrate_db.py` 是 Alembic 等价的最小实现：所有
步骤为 `CREATE TABLE / ADD COLUMN / CREATE INDEX` 加法式 DDL，
SQLite 与 PostgreSQL 共用，版本记录在 `schema_migration` 表；
回滚方式是恢复升级前备份。启动时自动执行迁移。

任务队列默认 in-process（与阶段 1–4 一致）。需要多进程消费时：

```env
EXTERNAL_QUEUE=true
QUEUE_BACKEND=redis
REDIS_URL=redis://localhost:6379/0
```

Redis 后端用 `LPUSH/BRPOPLPUSH/LREM` 做至少一次投递，失败按
`max_attempts` 重试后进入失败列表。回滚：把 `EXTERNAL_QUEUE` 改回
`false` 重启即可，未消费的消息可在 Redis 列表中人工重放。本机
实测（SQLite vs 内嵌 PostgreSQL、200 行并发写入 / 20 次执行链路）
见阶段 5 报告。

### 供应链、备份与升级（阶段 5 批次 C）

MCP 插件可以固定版本 + sha256 指纹（可选 HMAC 签名）：

- `POST /api/mcp/servers/{id}/fingerprint` 计算/固定指纹，`sign=true`
  时用 `PLUGIN_SIGNING_SECRET` 生成签名；
- `PLUGIN_INTEGRITY=true` 后，缺少版本/指纹、指纹不匹配、签名缺失或
  错误的插件在加载前直接拒绝，不会发起网络调用；
- 工具清单变化（新增/删除工具或 schema 变化）会重置该 Server 的
  工具授权并要求重新确认。

依赖可复现：`requirements.lock` 记录当前环境精确版本，
`STARTUP_SELF_CHECK=true`（默认）在启动时比对，严格模式
`STARTUP_SELF_CHECK_STRICT=true` 下不一致则拒绝启动：

```powershell
.\.venv\Scripts\python.exe scripts/lock_dependencies.py
.\.venv\Scripts\python.exe scripts/check_dependencies.py
```

备份 / 恢复 / 升级 / 密钥轮换：

```powershell
# 备份（DB 一致性快照 + logs/artifacts + 密钥指纹；密钥无效时拒绝并输出轮换说明）
.\.venv\Scripts\python.exe scripts/backup.py --target logs/backups/manual-01 --include logs
# 校验 + 恢复
.\.venv\Scripts\python.exe scripts/restore.py --backup logs/backups/manual-01 --target .tmp/restore --verify-only
.\.venv\Scripts\python.exe scripts/restore.py --backup logs/backups/manual-01 --target .tmp/restore --force
# 恢复演练（真实 DB → 临时目录，逐表比对行数）
.\.venv\Scripts\python.exe scripts/restore_drill.py
# 升级：兼容检查 → 自动备份 → 迁移；--dry-run 只做兼容检查
.\.venv\Scripts\python.exe scripts/upgrade.py --dry-run
# 密钥轮换（旧/新密钥走环境变量）
$env:CREDENTIAL_OLD_KEY="..."; $env:CREDENTIAL_NEW_KEY="..."
.\.venv\Scripts\python.exe scripts/rotate_credentials.py
```

注意：仓库 `.env` 里的 `CREDENTIAL_ENCRYPTION_KEY` 是占位值，首次
备份/升级前必须先生成合法 Fernet 密钥并完成一次轮换，步骤见备份目录
自动生成的 `KEY_ROTATION.md`。本机 PyInstaller 打包（Tauri sidecar）
不包含 `embedded_postgres` / `fakeredis` 等测试依赖，但会打包
`asyncpg`、`redis` 与 MCP 运行时，桌面模式与本地 MCP Server / 桌面
Agent 可共存（见阶段 5 报告“打包共存说明”）。构建需要 MSVC
（VS Build Tools 的 VCTools 工作负载）+ Rust stable + Playwright
浏览器目录；完整打包命令为
`python scripts/build_all.py`，NSIS 安装包输出到
`desktop/src-tauri/target/release/bundle/nsis/`。

## 核心流程

```text
用户自然语言输入
↓
创建面 → 结构化草稿（trigger + steps + variables）
↓
观察 / 录制 / 自然语言辅助 → 结构化步骤
↓
校验 → 发布不可变 WorkflowVersion
↓
预检 → 固定 active version 与执行主机
↓
Web / Data / Files / Email / Desktop / MCP 类型化执行
↓
验证 / 检查点 / HumanTask / 视觉兜底（可选）
↓
ExecutionLog 记录结果、产物、投递、截图与审计
```

## Agent 说明

所有 Agent 通过 DeepSeek API 调用（使用 openai SDK，base_url 指向 DeepSeek），模型为 deepseek-chat。每个 Agent 有独立的 system prompt。

| Agent | 输入 | 输出 |
| --- | --- | --- |
| IntentParser | 自然语言文本 | StructuredPlan (JSON) |
| StepPlanner | StructuredPlan | list[StepDetail] |
| CodeGenerator | list[StepDetail] + context | Python 代码字符串 |
| DebugAgent | 失败代码 + 错误信息 + 截图 | 修复后的代码 |
| TemplateManager | Workflow 对象 | JSON/YAML 文件 |

## 数据库模型概要

- **User**: id, username, email, hashed_password, created_at
- **Workspace**: id, owner_id(FK), name, slug, is_default, quota(JSON), created_at, updated_at
- **Workflow**: id, user_id(FK), workspace_id(FK), name, description, natural_language_input, structured_plan(JSON), generated_code, dag_definition(JSON), trigger_type, cron_expression, status, notification_config(JSON), created_at, updated_at
- **ExecutionLog**: id, workflow_id(FK), workspace_id(FK), status, started_at, finished_at, error_message, screenshots(JSON), artifacts(JSON), step_logs(JSON), metrics(JSON)
- **MCPServer**: id, user_id(FK), workspace_id(FK), name, version, fingerprint, signature, toolset_fingerprint, transport, url/command/args/env, headers(JSON), secret_encrypted, enabled, default_risk, timeout_seconds, tools(JSON), last_error, last_tested_at
- **ToolCallAudit**: id, server_id/server_name, tool, risk_level, decision, status, arguments_summary(JSON), result_summary(JSON), error, duration_ms, attempts, workflow_id, execution_id, user_id, workspace_id, created_at

## 安全注意事项

- 凭据使用 Fernet 对称加密后写入 `Credential` 表；`list_credentials()` 只
  返回 key，解密后的值仅在执行前通过 `inject_credentials()` 注入到运行时
  context。
- 代码生成 Prompt 明确禁止硬编码密码。生成的代码会经过 AST 预检和凭据
  泄漏检查，发现明文凭据时最多自动重新生成一次。
- 网页流程生成代码不能直接调用 `open()` 写盘，只能调用运行时提供的
  `save_file(path, content)`。产物先写入执行记录目录，只有目标路径位于
  `WEB_EXPORT_DIRS` 或桌面代理已授权的文档目录内时才会复制到本机目录；
  未授权时文件保留在执行记录中，可在执行详情页下载。
- 本地模式不要求 Docker；Web 动作在受控浏览器 Profile 中执行，桌面动作
  只通过已配对的 Desktop Agent 执行。
- 旧 Docker 沙箱能力仅作为历史兼容路径保留，不是当前桌面版的发布验收
  前提，也不应被描述为默认执行路径。
- MCP 工具按 `read_only` / `mutating` / `destructive` 分级：只读默认
  允许，写入需要显式授权，破坏性操作需要授权加确认；权限判断与策略
  检查全部在模型之外执行，未授权的调用不会发往外部服务。
- MCP Server 的访问令牌与流程凭据一样用 Fernet 加密存储；审计里的
  参数与结果摘要会先脱敏（敏感键名与凭据明文都替换为 `***`），再裁剪
  长度。远程工具结果是不可信输入，不会直接进入模型上下文。
- `WORKSPACE_ISOLATION=true` 时所有流程/执行/凭据/MCP Server/审计/
  产物下载都带 workspace 谓词，跨租户访问返回 404；历史数据首次启动
  自动回填默认工作区。工作区并发配额在 `Workspace.quota` JSON 中配置。
- 插件供应链：`PLUGIN_INTEGRITY=true` 拒绝未固定版本/指纹或指纹不匹配
  的 MCP Server；`PLUGIN_REQUIRE_SIGNATURE=true` 时还需 HMAC 签名。
  工具清单变化会重置授权并要求重新确认。
- 备份包含数据库一致性快照、logs/artifacts 校验和与密钥指纹；恢复前
  会校验所有 sha256。`CREDENTIAL_ENCRYPTION_KEY` 无效时备份/升级会
  拒绝执行并输出轮换说明。

## Stage G 验收清单

- [x] 后端全量测试、前端测试和前端生产构建通过。
- [x] 临时 SQLite 首次迁移到 schema 10，重复迁移无重复执行。
- [x] 本机 Windows 窗口、UIA、文本输入、受控点击、截图和托盘恢复通过。
- [x] 本机真实 Desktop Agent 配对、心跳、忙碌状态和 7 步桌面流程通过。
- [x] Playwright Chromium 三站点通用动作、登录验证和视觉 fake provider 通过。
- [x] Replay 5/5，`pass_rate=100%`。
- [x] Tauri sidecar 与 NSIS 安装包构建通过。
- [ ] 标准 Microsoft Edge 登录 / 验证码人工接管。
- [ ] 真实 Windows VM Agent、远程 WSS、证书和断线重连。
- [ ] 真实远程 SMTP 投递。
- [ ] 真实远程视觉 provider 与隐私网络策略。
- [ ] OpenAPI 导出和前后端共享类型生成（计划 G.1）。
