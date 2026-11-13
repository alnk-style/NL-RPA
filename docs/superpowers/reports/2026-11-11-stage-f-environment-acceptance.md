# Stage F 真实环境验收清单

日期：2026-11-11

本清单只覆盖 Stage F 的环境验收。自动化测试使用 fake Agent 和模拟主机，
以下项目必须在真实 Windows/VM 环境执行后才能标记通过。

## 1. 本机 Windows Agent

1. 在 Windows 交互桌面启动桌面 Agent。
2. 配置：
   - `environment=local`
   - `capabilities` 包含 `interactive_desktop`
   - 本机调试可使用 `ws://127.0.0.1:...`
3. 在“连接与凭据 > 执行主机”确认主机显示：
   - 环境：本机
   - 状态：在线
   - 能力：`interactive_desktop`
   - 最近心跳持续更新
4. 发布一个包含 `host_requirement=interactive_desktop` 的工作流并运行。
5. 预期：
   - 执行详情显示实际本机 Agent、环境和 host-bound 状态。
   - 同一执行后续桌面步骤继续使用同一 Agent。
   - 不出现窗口句柄、剪贴板或会话无提示换机。

## 2. VM Agent 手动注册

1. 在已存在的 VM 中手动安装并启动桌面 Agent；不要求系统自动创建 VM。
2. 配置：
   - `server_url` 使用 `https://` 或 `wss://`
   - `environment=vm`
   - `capabilities` 显式包含 `interactive_desktop` 和
     `isolated_desktop`
3. 使用现有配对码手动完成注册。
4. 在主机页面填写受信控制台 URL 和人工访问说明。
5. 预期：
   - 主机环境显示 VM。
   - VM 环境本身不被视为 `isolated_desktop`；缺少该能力时隔离步骤预检阻断。
   - 控制台 URL、说明和状态不会暴露 Agent Token。

## 3. 远程 WSS

1. 使用受信 TLS 证书部署后端 WSS 入口。
2. 将 VM Agent 的 `server_url` 配置为 `https://` 或 `wss://`。
3. 断开并重新连接 Agent。
4. 预期：
   - VM Agent 使用 WSS 成功连接。
   - VM Agent 配置为明文 `ws/http` 时注册或连接被拒绝。
   - 重连后主机状态从离线恢复为在线，旧连接不再接收任务。

## 4. 运行策略矩阵

在真实本机与 VM 同时在线时分别验证：

| `host_requirement` | `mode` | `host_policy` | 预期 |
|---|---|---|---|
| `interactive_desktop` | `interactive_preferred` | `local_then_isolated` | 优先本机 |
| `interactive_desktop` | `background_if_safe` | `local_then_isolated` | 只选具备 `isolated_desktop` 的 VM |
| `interactive_desktop` | `background_if_safe` | `local_only` | 预检 `policy_conflict`，不启动桌面步骤 |
| `isolated_desktop` | 任意 | `isolated_only` | 只选 VM |
| `web/transform/files/email` | 任意 | 任意 | 服务器执行，不依赖桌面主机 |

额外验证：

- VM 离线或忙碌时，有 host-bound 状态的执行不自动切到本机。
- 原 Agent 重连但会话指纹变化时，恢复返回 `host_session_lost`。
- 原主机丢失后，独立且无 host-bound 状态的服务器步骤仍可继续；后续桌面步骤
  在重新满足原主机或明确人工处理前不得运行。

## 5. Edge 登录与人工接管

1. 使用独立自动化 Edge Profile 启动需登录的工作流。
2. 触发 `login_required`、MFA 或验证码人工任务。
3. 在待处理中心确认截图、说明和当前 URL 已显示。
4. 不完成登录直接继续，预期服务端验证失败且任务保持 pending。
5. 完成登录后继续，预期服务端重新验证通过后从检查点恢复。
6. 预期：
   - 不重复执行已完成的下载、发送或其他副作用步骤。
   - 密码、Cookie、Token、验证码不进入日志、事件或前端。

## 6. DPI、窗口截图和视觉坐标

分别验证 100%、125%、150% Windows 缩放：

1. 捕获目标窗口，不捕获完整桌面。
2. 记录窗口矩形、PID、句柄和截图尺寸。
3. 运行 `click_relative` 或视觉候选确认。
4. 预期：
   - 点击坐标相对目标窗口且换算正确。
   - 截图边界、候选框和实际点击位置一致。
   - 执行后通过 UIA、窗口状态或后续业务结果验证。
   - 验证失败不会显示成功，也不会无预算重试。

## 7. 远程视觉隐私

1. 保持视觉 provider 默认 `disabled`，确认结构化定位失败时不会上传截图。
2. 启用远程 VLM 前确认隐私提示明确说明截图可能离开本机。
3. 使用不含真实敏感数据的测试页面验证：
   - 密码、Token、Cookie、银行卡和路径已遮罩。
   - 只上传目标窗口或页面视口。
   - API key 不出现在响应和日志。
4. 预期：
   - 脱敏失败时中止视觉流程。
   - Provider 只返回候选，不能执行动作。

## 8. 环境跳过记录

若本机缺少交互桌面、VM、WSS、Edge 或远程视觉 provider，以上项目必须
明确记录为“未覆盖”，不能计入自动化测试通过项。自动化测试中的 fake Agent、
模拟窗口和 fake provider 只能证明控制流与契约。
