# 阶段 5：规模化与生产加固

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 阶段 5（任务 1–6）、
第 8/9/10/11/13 节。基线：阶段 0–4 后端 445 通过 / 21 跳过 / 0 失败，
前端 38 通过 + 构建通过。

## 1. 交付内容

### 1.1 批次 A：指标、告警、失败分类与回归回放

- `app/metrics/service.py` 聚合方案第 8 节的 7 个指标：

| 指标 | 口径 |
| --- | --- |
| 首次生成成功率 | 时间窗内新建流程的首个执行成功率 |
| 定位失败率 | 定位类 `FailureCategory` 步骤数 / 步骤总数（含修复成功的原始定位失败） |
| 自动修复成功率 | 有修复尝试的步骤中最终成功的比例 |
| 每百次执行人工介入次数 | 执行级 `metrics.manual_interventions`（含 awaiting_user 回退计数） |
| 流程创建耗时 | 流程创建到首次执行的秒数均值 |
| 整条流程重跑次数 | 失败后新建执行（非检查点续跑）的次数 |
| 执行耗时增量 | 当前窗口均值 / 等长前序窗口均值 |

- 失败分类统一按 `protocols.FailureCategory` 聚合；旧数据缺少
  `failure_category` 时用 `classify_failure()` 从错误文本回退推断。
- 接口（`METRICS_API=true`，默认关闭）：
  `GET /api/metrics/overview`、`GET /api/metrics/failure-categories`、
  `POST /api/metrics/alerts/check`；支持 `range=1h|24h|7d|30d|all` 或
  `start/end`，维度支持 `workflow_id`、`executor`、`user_id`（越权查询
  返回 403）。
- 阈值告警：`app/metrics/alerts.py` + `app/metrics/monitor.py`，
  `METRICS_ALERTS_ENABLED=true` 时后台轮询，按 cooldown 去重；复用
  `NotificationService.send_alert()` 的 SMTP / Webhook 通道
  （新增 `SMTP_START_TLS` 便于本地假 SMTP 验证）。
- 前端：`MetricsPanel`（执行监控页，7 个 KPI + 失败分类条 + 时间范围
  /执行器/流程筛选）与 `ExecutionMetricsPanel`（执行详情页只读面板）；
  接口关闭（403）时面板自动隐藏。
- 回归回放：`app/regression/replay.py` + `scripts/run_regression.py`，
  把录制草稿、观察快照、step_logs 固化为
  `tests/regression/fixtures/` 下的夹具，输出通过率、失败分类与快照
  路径（`.tmp/regression/summary.json`）；回放失败只影响脚本退出码。

### 1.2 批次 B：工作区隔离、PostgreSQL 与任务队列

- `Workspace` 模型（owner、slug、默认标记、quota JSON）与
  Workflow / ExecutionLog / Credential / DesktopAgent / MCPServer /
  ToolCallAudit 的 `workspace_id`（可空、可逆迁移）。
- `app/security/workspaces.py` + `app/api/workspace_deps.py`：
  `WORKSPACE_ISOLATION=true` 时用 `X-Workspace-Id`（或查询参数）选择
  工作区，缺省默认工作区；跨租户流程/执行/凭据/MCP Server 与审计/
  Agent/产物与截图下载统一 404。启动时对历史数据执行一次默认工作区
  回填（`backfill_workspace_ids`）。
- 配额：`Workspace.quota.max_concurrent_executions` 在
  `ExecutionManager.start_workflow` 前检查；`max_concurrent_agent_tasks`
  在 `DesktopDispatcher.dispatch` 前检查；Agent 选择增加 workspace
  过滤（同工作区才可派发）。
- `DATABASE_URL` 支持 `postgresql+asyncpg://`；`app/database.py` 的
  SQLite `PRAGMA` 迁移替换为 `app/migrations.py` 的可移植
  `inspector + ADD COLUMN/INDEX`，并用 `schema_migration` 表记录
  1–5 号版本（Alembic 等价最小实现）；`scripts/migrate_db.py` 提供
  迁移/状态查询 CLI。
- 队列：`app/engine/queue_backends.py` 提供 `ExecutionQueue` 契约、
  默认 in-process 与 Redis 最小实现（LPUSH/BRPOPLPUSH/LREM + 重试后
  失败列表）及 `QueueWorker`；`EXTERNAL_QUEUE=false` 时保持阶段 1–4
  的 asyncio task 调度。评估结论：单机/桌面用 in-process；多进程或
  需要崩溃恢复时用 Redis；Celery/RQ 能力更全但依赖与 Windows 负担
  更重，暂不引入。

### 1.3 批次 C：供应链、备份与升级

- 插件供应链：`app/security/plugin_integrity.py` 对 MCP Server 配置
  计算规范化 sha256 指纹，支持 HMAC-SHA256 可选签名；
  `PLUGIN_INTEGRITY=true` 时缺少版本/指纹、指纹不匹配或签名错误会
  在加载前拒绝（不发起网络调用）。`POST /api/mcp/servers/{id}/fingerprint`
  固定指纹（`sign=true` 时用 `PLUGIN_SIGNING_SECRET` 签名）；工具清单
  或 schema 变化时自动重置授权并标记 `needs_reconfirmation`。
- 依赖复现：`requirements.lock`（当前环境 92 个包精确版本），
  `app/security/dependency_check.py` 在启动时比对
  （`STARTUP_SELF_CHECK=true`，严格模式拒绝启动），
  `scripts/lock_dependencies.py` / `scripts/check_dependencies.py`。
- 备份/恢复：`app/ops/backup.py` 生成 SQLite `VACUUM INTO` 一致性
  快照（其他数据库走 JSONL 逻辑导出）+ logs/artifacts 文件副本 +
  逐项 sha256 + 密钥指纹（不落明文），并写入 `KEY_ROTATION.md`；
  密钥无效时拒绝备份并输出轮换说明。`scripts/backup.py` /
  `scripts/restore.py` / `scripts/restore_drill.py` 支持备份、校验、
  恢复与本地真实演练。
- 升级：`app/ops/upgrade.py` + `scripts/upgrade.py` = 版本兼容检查
  （数据库 schema 不得高于应用）→ 自动备份 → 迁移，返回回滚命令；
  `scripts/rotate_credentials.py` 用旧密钥解密、新密钥重加密
  Credential 与 MCP 密钥。
- 打包：`scripts/build_backend.py` 增加 `app.metrics/ops/security/
  migrations` 与 `asyncpg`、`redis`、`mcp` 隐藏导入，排除
  `app.testing`、`embedded_postgres`、`fakeredis` 测试依赖；桌面
  sidecar 与本地 MCP Server / 桌面 Agent 可共存（见第 3.6 节）。

## 2. 测试

| 项目 | 结果 |
| --- | --- |
| 指标聚合/时间窗/失败分类（`test_metrics_service.py`） | 8 项 |
| 阈值告警 + 真实本地 SMTP/Webhook（`test_metrics_alerts.py`） | 5 项 |
| 指标 API 开关/维度/告警（`test_metrics_api.py`） | 6 项 |
| 执行指标落库与登录暂停（`test_execution_metrics.py`） | 2 项 |
| 回归回放引擎与夹具（`tests/regression/`） | 3 项 |
| 越权读写/下载（`test_workspace_isolation.py`） | 7 项 |
| 工作区配额/Agent 绑定/回填（`test_workspace_quota.py`） | 4 项 |
| 队列契约 + 外部队列执行链路（`test_queue_backends.py`） | 5 项 |
| 可移植迁移（`test_migrations.py`） | 1 项 |
| 插件指纹/签名/工具变更重确认（`test_plugin_integrity.py`） | 5 项 |
| 依赖锁定与启动自检（`test_dependency_check.py`） | 5 项 |
| 备份校验/恢复/升级/轮换（`test_backup_restore.py`） | 6 项 |
| SQLite + PostgreSQL 核心链路（`test_pg_core_chain.py`） | 2 项（PG 不可用时 1 项 skip） |
| 后端全量 | **504 通过 / 22 跳过 / 0 失败**（基线 445/21） |
| 前端逻辑测试 | **44 通过**（基线 38，新增 6 项指标面板用例） |
| 前端构建 | `tsc --noEmit` + `vite build` 通过 |

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/pytest
cd frontend; npm.cmd test; npm.cmd run build
.\.venv\Scripts\python.exe scripts/run_regression.py
.\.venv\Scripts\python.exe scripts/check_dependencies.py
```

回放输出：4 个夹具全部通过，失败分类
`locator_not_found: 1`，快照与汇总写入 `.tmp/regression/`。

## 3. 真实环境验收

### 3.1 PostgreSQL 双跑

本机无 Docker/psql，使用 `embedded_postgres`（内置 PG 18 二进制）在
系统临时目录启动真实 PostgreSQL，集簇编码用 EUC_CN（中文 Windows 下
initdb 的 UTF8 后处理会因代码页问题失败；服务端可正常服务 UTF-8
客户端并完成中文往返）。执行：

```powershell
$env:PYTHONUTF8='1'
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_pg_core_chain.py
```

结果：`2 passed`（SQLite 与 PostgreSQL 各自跑通
建表/迁移/流程执行/落库/中文往返）。未安装 `embedded_postgres` 且未
设置 `TEST_POSTGRES_URL` 时该用例 skip 并给出说明。

### 3.2 SQLite vs PostgreSQL 实测

`scripts/bench_database.py`（200 行并发写入 / 并发 10；20 次执行链路，
每次走 ExecutionManager + mock 引擎落库），结果写入
`logs/bench_database.json`：

| 后端 | 并发写入（行/秒） | 执行链路（次/秒） |
| --- | --- | --- |
| SQLite (aiosqlite) | 878.9 | 126.0 |
| PostgreSQL (asyncpg) | **5660.4（6.4×）** | **250.3（2.0×）** |

### 3.3 队列

- in-process 契约、worker 处理/失败、ExecutionManager 外部队列
  端到端链路共 5 项测试通过；
- Redis 后端用 fakeredis 验证 LPUSH/BRPOPLPUSH/ack/重试/失败列表；
  本机无 Redis 服务，未做真实 Redis 进程验证（见第 4 节）。

### 3.4 告警真实投递

`test_metrics_alerts.py` 在本机起真实 SMTP 会话与 HTTP Webhook：
`send_alert()` 成功投递并校验收件人与 payload；服务级测试验证阈值触发、
cooldown 去重与注入通知器的投递结果。

### 3.5 备份 → 恢复演练（真实数据）

对 .env 指向的真实 SQLite 库
`C:\Users\Administrator\.nl-rpa\data\rpa.db` 执行
`scripts/restore_drill.py --drill-key`（演练专用临时密钥，因为 .env 中的
`CREDENTIAL_ENCRYPTION_KEY` 仍是占位值）：备份 → sha256 校验 → 恢复到
临时目录 → 逐表行数比对，结果 `ok=true`：user 1、agent 1、workflow 4、
execution_log 24、desktop_task 26、apscheduler_jobs 2、chat_session 6，
`mismatched_tables={}`。无效密钥场景由单元测试验证会被拒绝并输出
轮换说明。

### 3.6 阶段 1–4 能力保持

- 全量套件覆盖 MCP 契约（测试内启动真实假 MCP HTTP Server，SDK 与
  内置传输）、混排/DAG/检查点续跑、登录暂停恢复、桌面派发与录制；
  全部保持通过。
- `scripts/run_mcp_demo_server.py` 冒烟：`initialize` 返回
  `protocolVersion=2025-03-26`、`serverInfo=nl-rpa-demo`，演示 Server
  与权限门/审计的验收入口不变。
- PyInstaller 桌面 sidecar 仍为 onefile，继续打包 Playwright 浏览器
  与 docker entrypoint；新增异步驱动（asyncpg/redis）与 MCP 运行时，
  测试依赖不进入产物。桌面模式下 `DATABASE_URL` 由
  `runtime_paths.stable_database_url()` 指向用户数据目录，本地 MCP
  Server（HTTP/stdio 开关）与桌面 Agent 各自独立进程，通过后端
  注册表与 WebSocket 协同，不共享 Python 运行时。
- 本轮已在带 MSVC/VS Build Tools 的环境完成实际打包验证：
  1. 重建 `backend-server.exe`（320,692,159 字节）并用临时数据库
     启动，`GET /health` 返回 `{"status":"ok"}`；
  2. 重建 `desktop-agent.exe`（22,973,886 字节）；
  3. Tauri release 编译 + NSIS 打包成功，产物
     `desktop/src-tauri/target/release/bundle/nsis/NL-RPA Desktop_0.1.0_x64-setup.exe`
     （346,271,887 字节）；
  4. 安装包静默安装验证：安装目录出现 `nl-rpa-desktop.exe`
     （12,454,400 字节）、`backend-server.exe`、`desktop-agent.exe`，
     退出码 0；随后静默卸载退出码 0，验证目录与进程均无残留。
  本机为此安装了 Visual Studio Build Tools 2022（VCTools 工作负载，
  安装路径 `C:\BuildTools`，优先使用本地包缓存）。
- 真机使用中修复的两个桌面端问题：
  1. `/settings` 路由冲突：桌面版设置页原先占用 `/settings`，导致侧边栏
     “连接器”入口打开的是桌面设置而不是 MCP 连接器页。现在 `/settings`
     指向共享连接器页，桌面设置移到 `/desktop-settings`。
  2. 桌面版每次启动会重写 `~/.nl-rpa/.env` 且只保留 5 个托管键，用户
     手写的 `MCP_CLIENT` / `METRICS_API` 等开关会被清掉；现在重写时
     保留未托管的自定义键，阶段 5 开关可以在桌面版持久启用。
  修复后重新打包并覆盖安装（安装包 346,273,293 字节），应用窗口与
  后端 `/health` 验证正常。

## 4. 未完成项与回滚

未完成/边界：

1. 真实 Redis 服务未在本机启动（无 redis-server / Docker），Redis
   队列用 fakeredis 做契约验证；生产接入前建议在目标环境再跑一次
   `tests/test_queue_backends.py` + 真实 Redis 冒烟。
2. PostgreSQL 只在本地内嵌实例（EUC_CN 集簇编码）验证；生产 Linux
   环境应按常规 UTF8 初始化，并执行 `scripts/migrate_db.py` 确认
   `schema_migration` 1–5 全部应用。
3. MCP stdio 传输仍只有配置与权限开关（沿用阶段 4 遗留项）；插件
   签名目前是 HMAC-SHA256（共享密钥），未实现非对称签名与吊销列表。
4. 备份/恢复对 PostgreSQL 走 JSONL 逻辑导出，未做 `pg_dump`/PITR；
   SQLite 恢复为整库快照替换。
5. 前端工作区切换仅提供 `localStorage.workspaceId` → 请求头通道，
   多工作区管理 UI 未做（默认工作区满足单用户路径）。
6. 观察快照（`/api/observations/browser`）与录制草稿仍按用户级
   隔离，落盘目录未按 workspace 分目录；它们不在批次 B 列出的
   资源清单内，多租户部署时建议再补一层路径分区。
7. MCP Server 导出（`/mcp` 的外部客户端通道）按用户过滤，没有
   workspace 维度：单用户多工作区时外部客户端会看到该用户全部工作区
   的流程。内网单工作区无影响，多租户对外导出前需要补充 workspace
   选择参数或按工作区分发令牌。
8. 定时任务（scheduler）创建的 ExecutionLog 会写入 workspace_id，
   但不经过 `start_workflow` 的工作区并发配额与外部队列，且调度器
   控制接口仅限 localhost；定时任务纳入配额/队列属于后续增强。
9. 指标告警后台轮询 `MetricsAlertMonitor` 只有服务级与冒烟覆盖，
   没有针对“应用启动后按 interval 自动触发”的端到端测试。
10. 安装包未做代码签名，Windows SmartScreen 可能提示；也未在“全新
    未安装 WebView2 的系统”上验证（配置为 embedBootstrapper，理论
    会自动安装）。发布前建议补签名并在干净虚机上做一次安装/升级/
    卸载回归。

RPO/RTO：

- RPO = 最近一次成功备份的时点。建议每日本地备份 + 升级前自动备份；
  当前演练为手动触发，SQLite `VACUUM INTO` 保证快照一致性。
- RTO：本次演练在本机约 2 秒完成“校验 + 恢复 + 行数比对”（小型库，
  1 用户/4 流程/24 执行）；实际 RTO 与 artifacts 体积和磁盘 IO 相关。

回滚方式（全部可逆）：

1. 指标/告警：`METRICS_API=false`、`METRICS_ALERTS_ENABLED=false`
   （前端面板自动隐藏）。
2. 工作区：`WORKSPACE_ISOLATION=false` 立即回到单用户语义；
   `workspace_id` 为可空列，不使用时不参与谓词。
3. 队列：`EXTERNAL_QUEUE=false` 回到 in-process。
4. 插件：`PLUGIN_INTEGRITY=false`、`PLUGIN_REQUIRE_SIGNATURE=false`。
5. 数据库：迁移均为加法式；回退用升级前自动备份执行
   `scripts/restore.py --backup <dir> --target <data_dir> --force`。
6. 密钥：备份与升级在 `CREDENTIAL_ENCRYPTION_KEY` 无效时直接拒绝；
   轮换失败时用备份 + `scripts/rotate_credentials.py` 回切旧密钥。

密钥轮换提示：仓库 `.env` 的 `CREDENTIAL_ENCRYPTION_KEY` 是占位值，
备份/升级前必须先生成合法 Fernet 密钥并执行一次
`scripts/rotate_credentials.py`；具体步骤随每次备份写入
`KEY_ROTATION.md`。
