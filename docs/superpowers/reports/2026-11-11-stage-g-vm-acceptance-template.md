# Stage G VM/WSS 人工验收记录

日期：
执行人：
VM 类型与版本：
Windows 版本：
Agent 版本：

## 环境准备

- [ ] 已存在一台可访问的 Windows VM；本流程不自动创建或启动 VM。
- [ ] VM 中已手动安装并启动 Desktop Agent。
- [ ] 后端具有受信 TLS 证书和 WSS 入口。
- [ ] 已在客户端“连接与凭据 > 执行主机”生成配对码。

## Agent 配置

```json
{
  "server_url": "wss://your-backend.example",
  "agent_id": 0,
  "token": "",
  "environment": "vm",
  "capabilities": [
    "interactive_desktop",
    "isolated_desktop"
  ],
  "heartbeat_interval": 30
}
```

敏感字段只填写在本机配置中，不粘贴到验收报告。

## 自动检查

```powershell
.\.venv\Scripts\python.exe scripts\acceptance\vm_agent_check.py `
  --base-url https://your-backend.example `
  --host-id 12 `
  --api-token $env:NL_RPA_ACCEPTANCE_TOKEN `
  --agent-config C:\path\to\config.json
```

通过条件：

- `environment=vm`
- capability 包含 `isolated_desktop`
- 状态为 `online` 或 `busy`
- `last_seen_at` 在心跳窗口内变化
- Agent 配置为 `wss`
- Agent Token 仅报告已配置，不输出值

## 人工场景

- [ ] `background_if_safe` 的桌面步骤只选择该 VM。
- [ ] VM 离线时预检阻断，不降级到本机桌面。
- [ ] 同一执行后续桌面步骤保持同一 `host_id`。
- [ ] 旧连接被新连接替换后只有一个活动 Agent。
- [ ] 心跳超时后主机状态变为离线。
- [ ] 能力变化后主机能力列表和事件更新。
- [ ] VM 会话丢失时恢复返回 `host_session_lost`。
- [ ] 有副作用的步骤不会因 VM 丢失而自动从头重跑。
- [ ] VM 控制台链接或人工说明仅在当前用户下可见。

## 结果

自动检查结果：

人工场景结果：

未覆盖项与原因：

最终判定：通过 / 不通过
