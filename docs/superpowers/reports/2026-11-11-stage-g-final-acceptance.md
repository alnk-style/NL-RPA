# Stage G 最终环境验收报告

日期：2026-11-11

结论：**本地自动化、真实 Windows 交互桌面、本机 Desktop Agent 和
Playwright Chromium 候选通过；完整个人使用发布候选尚未完成。**

未完成的原因是当前机器没有可用的标准 Microsoft Edge、真实 Windows VM、
远程 WSS 环境、可用真实 SMTP 和远程视觉 provider，并且计划中的 Stage G.1
OpenAPI/共享类型生成尚未实现。这些项目均未计入通过。

## 1. 自动化和构建基线

| 检查 | 实际结果 |
|---|---|
| 后端全量 | `997 passed, 23 skipped`，68.04 秒 |
| 前端测试 | `111 passed, 0 failed`，594.56 ms |
| 前端生产构建 | 通过，2424 modules transformed |
| 桌面生产构建 | 通过，3909 modules transformed |
| `compileall app desktop_agent scripts` | 通过 |
| Replay | 5 fixtures，5 passed，`pass_rate=100%` |
| 临时 SQLite 首次迁移 | schema 10，执行迁移 1-10 |
| 同一 SQLite 重复迁移 | schema 10，`executed=[]` |
| 后端 sidecar | 构建通过 |
| Agent sidecar | 构建通过 |
| Tauri NSIS | 构建通过 |

NSIS 安装包：

```text
desktop/src-tauri/target/release/bundle/nsis/NL-RPA Desktop_0.1.0_x64-setup.exe
```

修复 Query Provider 后最终构建大小：370,373,769 bytes，时间
2026-11-11 21:58:56，SHA256
`39881B4A756B92F593B01A8895A5F468D3736B49D66D1B1D9260B95FA7EB6914`。

## 2. 验收中发现并修复的问题

以下问题均由真实桌面、浏览器或发布启动验收暴露，并已按失败测试后修复：

1. `desktop_agent/actions/read_window_text.py`
   - 字符串窗口句柄传给 pywinauto 时未读到控件。
   - 现在使用整数句柄，并同时读取控件名称和实际 value。
2. `desktop_agent/actions/activate_window.py`
   - 同 PID 的隐藏窗口可能被错误优先选择。
   - 现在只选择可见窗口。
3. `app/engine/web_executor.py`
   - Data 动作返回 dict 时被当作 `_StepOutcome` 使用。
   - 现在包装为 `_StepOutcome(value=items)`，保持执行链契约。
4. `app/engine/visual_fallback.py`
   - action registry 初始化失败可能导致视觉白名单静默失效。
   - 增加显式安全白名单回退，并规范化 `StepExecutor.WEB`。
5. `app/regression/replay.py`
   - `visual_ambiguous` 夹具没有可回放片段，Replay 实际只有 80%。
   - 增加离线 fake provider 视觉回放、决策快照和候选稳定顺序校验。
6. `desktop/src/App.tsx`
   - 桌面路由缺少 Dashboard、待处理、连接和高级工具页面。
   - 补齐侧边栏已经暴露但路由缺失的页面。
7. `desktop/src/main.tsx`
   - 首次安装启动后，共享页面调用 React Query 时抛出
     `No QueryClient set, use QueryClientProvider to set one`。
   - 桌面入口现在与 Web 入口一样使用 `QueryProvider`。
   - 新增入口契约和路由回归测试；桌面测试 14 passed。

修复后新增/更新了对应回归测试，最终全量通过。

## 3. 真实本机 Windows 桌面

验收脚本：

```text
scripts/acceptance/windows_desktop_check.py
```

报告：

```text
.tmp/stage-g/desktop/windows-desktop-acceptance.json
```

实际结果：通过。

- `launch_app` 找到可见 `charmap.exe` 窗口。
- 当前 DPI 为 96，缩放 100%。
- 目标窗口矩形为 `[18, 85, 588, 615]`。
- 截图尺寸为 `570x530`，与目标窗口矩形一致，没有截取完整桌面。
- UIA 点击、文本输入、`read_window_text` 和相对窗口受控点击通过。
- 最小化后恢复，句柄保持正确且窗口回到前台。
- 不存在的窗口返回明确失败和候选窗口列表。
- 脱敏截图实际为全黑，验证了受控图片脱敏路径。
- 关闭动作终止了测试进程。

未覆盖：

- 125% 和 150% Windows DPI。
- 多显示器、负坐标显示器和不同缩放组合。
- 真实 WPS COM；当前机器未注册 `kwps.Application`。
- 沙箱内的 Win32/UIA 访问会被拒绝，真实桌面验收必须在交互桌面执行。

## 4. 真实浏览器、登录和视觉 fake provider

验收脚本：

```text
scripts/acceptance/browser_web_check.py
```

报告：

```text
.tmp/stage-g/browser/browser-web-acceptance.json
```

实际结果：通过，浏览器引擎为 Playwright Chromium。

- site_a、site_b、site_c 三个结构不同的本地站点使用同一套通用动作。
- 每个站点提取 2 行，字段为 `amount`、`name`，并生成 1 张截图。
- 登录前返回 `auth_required` / `awaiting_user`。
- 完成登录后服务端重新检查成功，没有只相信前端“已完成”。
- 视觉 fake provider 在 DPR 1.0、1.25、1.5 下各调用 1 次。
- 三个 DPR 均自动选择高置信唯一候选，并通过 `dom_changed` 验证。
- 多候选和低置信度均进入 `visual_ambiguous` / `awaiting_user`。
- provider disabled 时调用次数为 0，失败保持 `locator_not_found`。

说明：这里的 DPR 是 Chromium 页面比例，不等同于 Windows 125%/150%
系统缩放和多显示器验收。

标准 Edge 验收：

```text
scripts/acceptance/edge_login_check.py
.tmp/stage-g/edge/edge-login-acceptance.json
```

实际结果：未通过，状态 `skipped`。

- 标准 Edge 安装路径不存在。
- `C:\CheeseEdge\App\msedge.exe` 存在，但 Playwright `channel=msedge`
  仍不可用，因此不能冒充标准 Microsoft Edge 通过。
- 真实 Edge Profile、登录失效、验证码/MFA 人工接管仍未覆盖。

## 5. 本机 Agent 与主机亲和

真实验收使用了本机 FastAPI 后端和真实 Desktop Agent：

```text
.tmp/stage-g/agent-online/local-agent-acceptance.json
```

实际结果：通过。

- 主机注册为 `environment=local`。
- capability 为 `interactive_desktop`。
- 初始状态 online，session ready。
- heartbeat 后 `last_seen_at` 变化，状态保持 online。
- busy 状态被观察到，任务结束后 active task count 回到 0。
- 真实 7 步桌面工作流成功。
- 执行记录写入 `host_id=1`、`host_environment=local`、`host_bound=true`
  和 host affinity 信息。
- 执行产物中包含本机窗口截图。

发布版应用启动后还验证了：

- `/health` 返回 200。
- Agent 在 Tauri sidecar 启动后自动上线。
- 关闭主窗口后应用进程和 sidecar 保持存活。
- 通过 Windows 托盘菜单“显示主窗口”恢复 `NL-RPA Desktop`。
- 完整应用停止并重启后，同一 SQLite 数据库、Agent 配置、在线状态和窗口
  均可恢复。

## 6. 真实 VM、WSS 和跨主机

当前机器没有可用 Hyper-V 管理、WSL VM、VM 管理器或可访问 Windows VM。

因此以下项目全部未验收：

- `environment=vm` 的真实 Agent。
- `isolated_desktop` capability 与 VM 主机位置的独立组合。
- 远程 WSS、受信 TLS 证书和 Token 认证。
- 断线、重连、旧连接替换、能力变化和忙碌状态。
- `background_if_safe` 只选择真实具备 `isolated_desktop` 的 VM。
- VM 会话丢失后的 `host_session_lost` 和人工恢复入口。

自动化路由、host pinning、不可静默回退和检查点保护测试已在全量测试中
通过，但不能替代真实 VM 环境验收。

可执行验收资产：

```text
scripts/acceptance/vm_agent_check.py
docs/superpowers/reports/2026-11-11-stage-g-vm-acceptance-template.md
```

## 7. 通用端到端工作流

本地真实 Chromium 已验证：

1. site_a：登录、表格提取和截图。
2. site_b：不同 DOM 结构，使用同一动作集合。
3. site_c：第三种结构，使用同一动作集合。
4. 登录失效后暂停，登录完成并通过服务端再验证后继续。
5. 多候选和低置信度视觉歧义进入 HumanTask。
6. provider disabled 时不调用远程或本地视觉。

本机真实 Agent 已验证桌面窗口流程。真实 VM 路由仍由未覆盖的 VM 环境
阻断。

当前没有单独创建计划中列出的 pytest 风格 `tests/e2e` 目录；现有验收由
可执行 acceptance 脚本、真实 Chromium/桌面脚本和全量 focused 测试覆盖。
这是 Stage G.2 的计划缺口，不应标记为完整完成。

## 8. 文件、邮件和交付

自动化 focused 验收覆盖：

- CSV、XLSX、ZIP、多附件构造。
- 文件非空、行数和字段校验。
- 附件 sha256、DeliveryRecord 和 message-id。
- SMTP 未配置、连接失败、收件人无效和附件缺失。
- `required_delivery=true` 时邮件失败导致流程失败。
- 可选通知失败产生 warning，不静默忽略。
- SMTP 密码、Cookie、Token 和验证码不进入 API 响应、日志或事件。

当前配置的 SMTP 主机无法连接，环境中出现名称解析失败，因此没有发送真实
邮件。真实 SMTP、STARTTLS 证书和实际收件箱投递均未计入通过。

## 9. 恢复、并发、重启和事件

全量测试中的恢复和事件 focused 套件通过，覆盖：

- 重复 resume 幂等。
- 并发 resume 不重复执行已完成步骤。
- 过期、已取消和已解决 HumanTask 不能恢复。
- 服务重启后执行进入明确暂停状态。
- HumanTask 和检查点重启后可读。
- 同一工作流重复调度不产生并发重复实例。
- WebSocket 断开后，页面轮询仍可更新当前数据库状态。

主机事件总线是进程内实时流，并有数据库状态轮询兜底。设计只要求当前状态
正确，没有要求后端重启后重放全部历史主机事件，因此本阶段接受该语义；
已通过测试确认当前 Agent/主机状态来自数据库，而不是旧内存事件。

## 10. 安全和兼容

自动化测试覆盖并通过：

- Agent Token、SMTP 密码和视觉密钥不回显。
- 日志、事件和敏感字段脱敏。
- HumanTask 用户归属、状态和过期校验。
- candidate_id 只能由服务端解释，前端不能提交任意 bbox 执行。
- `environment=isolated` 历史迁移为 `vm`，新输出只写 `vm`。
- 旧 awaiting_user、旧检查点和旧执行记录兼容。
- 不存在新的站点专用 action；legacy 酷狗动作仍只在隔离 legacy catalog。

## 11. 发布候选检查

已通过：

- Tauri 应用可启动并显示窗口。
- 本地模式启动 backend-server 和 desktop-agent sidecar。
- `/health`、Agent 在线、心跳、busy 和 7 步桌面流程。
- 关闭窗口隐藏到托盘，进程继续运行。
- 托盘菜单恢复主窗口。
- 应用重启后配置和数据库状态可读。
- NSIS 安装包构建成功。
- 前端生产资源已进入桌面 release 构建。

未完成：

- 安装包实际安装到干净机器后的首次运行。
- 真实远程后端模式。
- 设置页到真实浏览器、真实 SMTP、真实视觉和真实 VM 的端到端配置流程。
- Stage G.1 OpenAPI 导出、前后端共享类型和 drift check。

## 12. 未覆盖和跳过项

后端全量 23 个 skip 的实际原因包括：

- 沙箱内交互 Windows 桌面不可用。
- 沙箱内 Playwright Chromium `spawn EPERM`。
- `notepad.exe` 不可用。
- WPS COM `kwps.Application` 未注册。
- PostgreSQL 未配置。
- MCP 模板占位测试按设计跳过。

环境未覆盖：

- 标准 Microsoft Edge、真实 Profile 和验证码/MFA 人工接管。
- 真实 Windows VM、远程 WSS、证书和连接替换。
- 真实远程 SMTP。
- 真实远程视觉 provider、网络白名单和截图离开本机的策略验收。
- 125%/150% Windows DPI 和多显示器。
- 沙箱外的人工托盘复杂交互之外的完整安装包升级/卸载。

## 13. 发布结论

当前版本达到：

**本地自动化与真实 Windows/Chromium 候选通过。**

当前版本尚未达到：

**完整个人使用发布候选全部通过。**

在获得真实 Edge、VM/WSS、SMTP 和视觉 provider 环境前，应使用以下表述：

> 代码测试和本地真实环境验收完成，真实外部环境验收未完成。

进入下一轮发布验收前必须完成：

1. 在真实标准 Edge 中完成登录失效、验证码/MFA 和 HumanTask 恢复。
2. 在真实 Windows VM 中完成 VM Agent、WSS、断线重连和 host affinity。
3. 使用专用测试 SMTP 完成真实投递、TLS 和附件校验。
4. 使用脱敏测试截图完成真实视觉 provider 和网络隐私验收。
5. 完成 Stage G.1 OpenAPI/共享类型生成与 drift check。
6. 在安装包安装后的干净用户环境重跑启动、托盘、升级和卸载。
