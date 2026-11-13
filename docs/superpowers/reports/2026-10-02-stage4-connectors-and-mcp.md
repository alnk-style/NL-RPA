# 阶段 4：连接器与 MCP

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 阶段 4（任务 1–5）、
4.6 连接器与 MCP、6.1 后端接口、6.2 前端界面、第 8/9/10/11 节。

## 1. 交付内容

### 1.1 统一 Connector 接口

`app/engine/connectors/` 提供统一接口
`capabilities / observe / act / verify / auth_status`：

- `base.py`：`Connector` 抽象类、`ConnectorRegistry`、统一返回信封
  `connector_result()`；
- `mcp_connector.py`：MCP Server 注册表适配器；
- `desktop_bridge.py`：把既有 `DesktopDispatcher` / Agent WebSocket
  协议包装成 Connector；不改变原协议，仍由 Dispatcher 生成
  `desktop.task`、沿用任务、进度与审计行为；
- `permissions.py`：风险分级与权限门；
- `audit.py`：工具调用审计写入；
- `untrusted.py`：远程结果的不可信数据包装。

`GET /api/mcp/connectors` 返回连接器能力与认证状态；启动时
`app.state.connectors` 注册 `mcp` 与 `desktop` 两个连接器。

### 1.2 MCP Client 与连接池

`app/engine/mcp_client.py`：

- 优先使用官方 `mcp` Python SDK（`streamablehttp_client` +
  `ClientSession`）；SDK 不可用时退化为内置的 JSON-RPC/HTTP 实现，
  两者都支持 Streamable HTTP 的 `application/json` 与
  `text/event-stream` 响应；
- stdio 传输需要官方 SDK 且必须显式打开 `MCP_STDIO_ENABLED`（默认
  关闭，视为高权限插件）；
- `MCPClientPool` 按 Server 复用客户端与 `httpx.AsyncClient`，用
  `MCP_MAX_CONCURRENCY` 控制全局并发；每次调用建立一次会话，避免
  跨 asyncio 任务复用 anyio task group；
- 单次调用超时来自 `MCP_CALL_TIMEOUT`，步骤可以用
  `params.timeout_seconds` 覆盖；错误映射到
  `FailureCategory`（timeout / auth_required / permission_denied /
  business_error）；
- 协议版本跟随 SDK 支持的 `2025-03-26`，内置传输使用同一版本。

新增依赖：`mcp==1.9.4`（requirements.txt）。该版本与项目现有
FastAPI 0.115 / Starlette 0.46 兼容；更新的 2.x 会把 Starlette 升到
1.x 造成冲突，因此固定在 1.9.4。

### 1.3 工具发现与注册

`app/engine/mcp_registry.py` + `app/api/mcp.py`：

| 接口 | 说明 |
| --- | --- |
| `GET/POST /api/mcp/servers` | 列表 / 新增（密钥 Fernet 加密，只回显 `has_secret`） |
| `PATCH/DELETE /api/mcp/servers/{id}` | 启停、修改、删除 |
| `POST /api/mcp/servers/{id}/test` | 健康检查 + 工具发现，写回工具清单 |
| `POST /api/mcp/servers/{id}/tools/{tool}/authorize` | 工具授权与确认 |
| `GET /api/mcp/audits` | 审计查询（按用户过滤） |
| `GET /api/mcp/connectors` | 连接器能力与认证状态 |

数据模型新增 `MCPServer` 与 `ToolCallAudit`（`app/models/mcp.py`）：
配置、密钥引用、工具清单、风险级别与授权状态都存在 JSON 字段里，
Schema 由现有 `Base.metadata.create_all` 兼容创建，无需破坏性迁移。

工具风险推断规则：`annotations.readOnlyHint=true` → `read_only`，
`annotations.destructiveHint=true` → `destructive`，其余用 Server 的
`default_risk`（默认 `mutating`）。重新测试连接时保留已有授权状态，
暂时消失的工具保留记录并标记 `available=false`。

### 1.4 权限与安全

- 权限门：`read_only` 默认允许；`mutating` 需要显式授权；
  `destructive` 需要授权且 `confirm=true`（或授权时
  `confirmed=true`）。未授权直接拒绝，不发起任何网络调用。
- 审计：每次调用（含成功、失败、超时、拒绝、Server 不存在）都会写
  `tool_call_audit`，记录 server、tool、风险级别、授权决策、脱敏后的
  参数/结果摘要、状态、耗时、尝试次数、错误、workflow_id、
  execution_id、user_id。数据库写入失败会退化为结构化日志。
- 脱敏：`app/security/redaction.py` 扩展 `redact_payload()`，按敏感键名
  （password/token/authorization/...）与运行时凭据明文替换为 `***`，
  并限制深度、条目数与字符串长度；MCP Server 的访问令牌走现有
  Fernet 加密存储。
- 不可信数据：`wrap_untrusted_result()` 给远程结果打上
  `trust=untrusted` 与来源标记，清理控制字符并截断；结果只进入执行
  日志与显式声明的 `params.result_var`，不直接拼进提示词，策略检查
  全部在模型之外执行。

### 1.5 执行链接入

`app/engine/hybrid_executor.py` 实现 `_execute_mcp_step`，替换原
“not implemented”占位：

- 步骤协议：`{executor:"mcp", action:"call_tool",
  params:{server, tool, arguments, timeout_seconds?, confirm?, result_var?},
  depends_on:[...]}`；
- 资源键沿用 `mcp:<server>`：同 Server 串行、不同 Server 可并行；
- 超时、重试（只有 `read_only` 会重试超时/瞬时错误）、
  `idempotency_key` 与检查点续跑沿用阶段 3 语义；
- 步骤日志新增 `server / tool / risk_level / decision / duration_ms /
  attempts / audit_id / untrusted / result_summary`；
- `ExecutionManager._needs_hybrid_execution()` 已把含 `mcp` 的流程交给
  混合执行器，失败时写失败检查点，授权后 `resume` 从失败步骤继续。
- `app/api/workflows.py` 的发布校验支持 `mcp` 步骤：`MCP_CLIENT`
  打开且 `call_tool + server + tool` 字段齐全时才允许发布混排流程，
  关闭开关时仍然拒绝发布。

### 1.6 MCP Server 导出（次级目标）

`app/engine/mcp_server.py` + `app/api/mcp_export.py`：

- `MCP_SERVER_EXPORT=true` 时开放 `POST /mcp`，实现最小 JSON-RPC 子集
  （`initialize`、`notifications/initialized`、`ping`、`tools/list`、
  `tools/call`）；
- 白名单：`platform_health`、`platform_list_workflows`、
  `platform_get_execution` 为只读，`platform_run_workflow` 需要
  `MCP_SERVER_ALLOW_MUTATING=true` 且调用参数 `confirm=true`；
- `MCP_SERVER_TOKEN` 配置后校验 `X-MCP-Token`；
- 工具结果始终限定在当前登录用户（JWT / DEV_AUTH_BYPASS 用户）范围内。

### 1.7 前端

- 新增 `/settings` 页（`frontend/src/pages/SettingsPage.tsx`，「连接器」
  导航项）：MCP Server 列表、新增表单、连接测试、工具清单与风险/授权
  状态、停用/删除、最近调用审计；
- `frontend/src/api/mcp.ts` + `frontend/src/types/mcp.ts` +
  `frontend/src/lib/mcp-settings.ts`（纯函数，供 Node 测试）；
- `MCP_CLIENT` 关闭时页面提示未启用，不报错。

### 1.8 演示与验证脚本

`scripts/run_mcp_demo_server.py`：标准库实现的本地 MCP
Streamable HTTP Server，暴露 `echo`（read_only）、`write_note`
（mutating）、`purge_notes`（destructive），用于联调权限门与审计。

## 2. 测试

| 项目 | 结果 |
| --- | --- |
| `tests/test_mcp_client.py` | 11 项：配置解析、风险推断、权限门、脱敏、不可信包装、错误映射、审计、重试策略 |
| `tests/test_mcp_contract.py` | 6 项：测试内起真实假 MCP HTTP Server，覆盖 SDK 与内置传输的工具发现、调用、超时、未授权拒绝（校验请求数为 0） |
| `tests/test_mcp_integration.py` | 4 项：web/mcp/windows 混排经 ExecutionManager 执行、DAG 依赖、destructive 拒绝 + 审计、授权后续跑、幂等跳过 |
| `tests/test_mcp_api.py` | 4 项：开关 403、Server CRUD、连接测试与风险推断、授权、审计、MCP Server 导出契约 |
| `tests/test_connectors.py` | 2 项：Desktop Bridge 统一接口、MCP Connector 状态 |
| `tests/test_api_4b.py` | 新增 2 项：含 MCP 步骤的流程可发布、字段缺失时拒绝发布 |
| 全量后端套件 | 445 通过，21 跳过，0 失败（基线 416 + 新增 29） |
| 前端逻辑测试 | 38 通过，0 失败（新增 4 项 MCP 设置用例） |
| 前端构建 | `tsc --noEmit` + `vite build` 通过 |

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.tmp/pytest
cd frontend; npm.cmd test; npm.cmd run build
```

## 3. 真实环境验收

本轮在带网络的本机环境跑通了完整链路（后端 uvicorn + 演示 MCP
Server + SDK 传输）：

1. 启动演示 Server `python scripts/run_mcp_demo_server.py --port 8765`，
   后端以 `MCP_CLIENT=true`、独立 SQLite 与有效
   `CREDENTIAL_ENCRYPTION_KEY` 启动。
2. `POST /api/mcp/servers` 创建 `demo-local`（带 `auth_token`）：
   响应只显示 `has_secret=true`，令牌不回显。
3. `POST /api/mcp/servers/1/test`：`transport=sdk:http`，发现 3 个工具，
   风险级别自动推断为 `read_only` / `mutating` / `destructive`。
4. 创建含两个 MCP 步骤的流程（`echo` → 依赖 → `purge_notes`）并执行：
   第一步成功并写审计；第二步因未授权被拒绝，`mcp:calls=0`，执行记录
   写入失败检查点（`next_step_index=1`，已完成幂等键
   `["echo-once"]`）。
5. 授权 `purge_notes`（`authorized=true`、`confirmed=true`）后
   `POST /api/executions/1/resume`：第一步按幂等键跳过，第二步成功，
   最终执行 `success`；审计共 3 条（success / denied / success），
   均带 workflow_id、execution_id 与耗时。
6. `MCP_SERVER_EXPORT=true`、`MCP_SERVER_ALLOW_MUTATING=true` 重启后：
   `initialize` / `tools/list` 正常；`platform_health`、
   `platform_list_workflows`、`platform_get_execution` 可读；
   `platform_run_workflow` 不带 `confirm` 返回 `-32003`，带
   `confirm=true` 触发执行并成功；错误 `X-MCP-Token` 返回 401。

## 4. 未完成项与后续

- stdio 传输只做了配置与权限开关，没有真实子进程端到端验证；本机
  验证使用 HTTP（SDK 与内置传输都覆盖）。
- MCP Server 导出是最小 JSON-RPC 子集：不支持 SSE 流式推送、
  `resources/prompts`、`listChanged` 通知与 OAuth；作为次级目标收尾，
  外部客户端可用 POST JSON 方式接入。
- 桌面链路用测试内的 fake dispatcher 验证了 Connector 包装与单步派发，
  未在真机上跑 WPS/记事本验收；Desktop Agent 原协议与既有测试全部保持
  通过。
- 工具风险级别目前由 annotations 推断 + 人工在设置页调整；暂不支持
  按 Server 维度配置「禁止列表」与参数级策略（例如仅允许特定目录）。
- 审计查询默认返回最近 50 条（前端展示 30 条），暂无分页与导出；
  多用户/工作区隔离按方案留到阶段 5。
- 回滚：`MCP_CLIENT=false`（默认）时所有 MCP 接口返回 403，执行链不会
  构造注册表，`executor="mcp"` 的步骤会以确定性失败返回；新增两张表与
  JSON 字段不影响既有流程。
