# NL-RPA Desktop

桌面客户端基于 Tauri 2，复用 `desktop/` 下的 React 界面，并通过
sidecar 在本机运行 FastAPI 后端和 Windows Desktop Agent。

## 环境要求

- Windows 10/11
- Python 3.11+
- Node.js 18+
- Rust stable
- Visual Studio Build Tools，需安装 MSVC 和 Windows SDK

## 开发模式

首次开发前先安装依赖：

```bash
python -m pip install -r requirements.txt
python -m pip install -r desktop_agent/requirements.txt
cd desktop
npm install
```

启动 Tauri 开发模式：

```bash
npm run tauri dev
```

Vite 开发服务器默认运行在 `http://localhost:5173`。Tauri 开发进程会启动
桌面界面；首次运行前需要先执行两个 sidecar 构建脚本，或直接执行
`python scripts/build_all.py`，因为开发模式也会通过同一组 externalBin
启动本地服务。

首次启动后，桌面应用会在 Tauri 应用数据目录中维护：

- `.env`：自动生成加密密钥、JWT 密钥和数据库配置。
- `desktop-settings.json`：运行模式、远程地址和 DeepSeek Key 配置。
- `config.json`：Desktop Agent 配对信息。
- `rpa.db` 和 `logs/`：本地数据库与运行日志。

这些文件不依赖仓库根目录，因此安装到 `Program Files` 后仍可正常读写。

## 构建安装包

在仓库根目录执行：

```bash
python scripts/build_all.py
```

也可以按顺序手动执行：

```bash
python scripts/build_backend.py
python scripts/build_agent.py
cd desktop
npm install
npm run tauri build
```

构建结果位于：

```text
desktop/src-tauri/target/release/bundle/nsis/
```

## 目录结构

```text
desktop/
├── src/                              # React 桌面界面
├── src-tauri/
│   ├── binaries/                     # PyInstaller sidecar
│   ├── capabilities/                 # Tauri 权限配置
│   ├── icons/                        # Windows 应用图标
│   ├── src/
│   │   ├── main.rs                   # Tauri 应用入口
│   │   ├── sidecar.rs                # sidecar 启停与健康检查
│   │   ├── scheduler.rs              # 本地调度状态
│   │   ├── tray.rs                   # 托盘菜单
│   │   ├── window_manager.rs         # 窗口显示与隐藏
│   │   └── autostart.rs              # 开机启动
│   ├── Cargo.toml
│   └── tauri.conf.json
├── package.json
└── vite.config.ts
```

## Sidecar 通信流程

1. 本地模式下，Tauri 先启动 `backend-server` 并等待 `/health` 通过；
   随后启动 `desktop-agent`。
2. `backend-server` 在 `127.0.0.1:8000` 提供 FastAPI HTTP/WebSocket 服务。
3. React 界面通过 `http://127.0.0.1:8000` 调用后端 API。
4. 本地模式首次启动时，Agent 自动调用本机配对接口并保存 agent id/token；
   远程模式使用设置页提供的配对码。
5. `desktop-agent` 通过 WebSocket 连接后端的
   `/api/agents/ws?agent_id=...&token=...`。
6. 后端将桌面任务下发到 Agent，Agent 执行动作并将进度、截图和结果回传。
7. Tauri 通过 sidecar 事件记录子进程输出；退出桌面进程时负责停止
   sidecar。

Tauri 2 的 `externalBin` 会查找带目标三元组后缀的文件。构建脚本因此同时
保留任务要求的 `backend-server.exe`/`desktop-agent.exe`，并复制为
`backend-server-x86_64-pc-windows-msvc.exe` 和
`desktop-agent-x86_64-pc-windows-msvc.exe`。

## 本地模式

本地模式适合单机使用：

- 后端和 SQLite 数据库运行在当前电脑。
- Agent 连接 `http://127.0.0.1:8000`。
- 首次启动自动完成本地 Agent 配对。
- 关闭主窗口后，托盘进程和本地调度仍可继续运行。
- 页面、后端和 Agent 共用同一台机器，不需要远程服务。

## 远程模式

远程模式适合多台 Windows 设备：

- 桌面客户端连接远程 HTTPS/WSS 后端。
- Agent 通过 `wss://` 连接远程后端，本地模式可使用 `ws://127.0.0.1`。
- 设置页填写远程地址和由远程管理页生成的 Agent 配对码。
- 后端根据 Agent 能力、在线状态和工作流偏好选择执行设备。
- 远程部署需要配置 TLS、设备配对、令牌轮换和访问控制。

两种模式使用同一套 Agent Action Schema；切换模式时只改变服务地址和认证
配置，不改变工作流动作定义。设置页中的 DeepSeek Key 仅写入本机应用数据
目录，不保存在浏览器 `localStorage`。
