# 阶段 3 第一部分：录制式生成（事件 → 可编辑草稿）

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 阶段 3 任务 1–3。

## 1. 交付内容

1. `app/engine/recorder.py`：录制事件模型与编译器。
   - 网页事件：navigate、click、fill、press、select_option、check、
     uncheck、upload、download、wait；
   - 桌面事件：desktop_launch、desktop_window、desktop_click、
     desktop_type、desktop_hotkey、desktop_wait；
   - 编译规则：连续输入合并、输入框聚焦点击去重、定位候选按语义
     优先级排序、自动生成 `idempotency_key` 与重试策略；
   - 复用 `validate_web_plan()` / `validate_desktop_plan()` 校验草稿，
     录制产物可以直接发布；
   - `BROWSER_RECORDER_SCRIPT`：注入浏览器的采集脚本（点击、输入、
     下拉、勾选、按键、上传、跨页面导航，sessionStorage 暂存，
     Ctrl+Enter 结束）。
2. `app/api/recordings.py`：录制会话接口；`app/api/__init__.py` 与
   `app/main.py` 完成路由注册。开启 `RECORDER_MODE` 后可用：

   | 接口 | 作用 |
   | --- | --- |
   | `POST /api/recordings` | 创建 web/windows 录制会话 |
   | `POST /api/recordings/{id}/events` | 追加录制事件 |
   | `GET /api/recordings/{id}` | 查看状态与草稿预览 |
   | `POST /api/recordings/{id}/pick` | 桌面录制：拾取鼠标下的控件 |
   | `POST /api/recordings/{id}/finish` | 生成草稿、写日志并落库为 draft 流程 |
   | `DELETE /api/recordings/{id}` | 丢弃会话 |

   结束时事件原文与草稿写入 `logs/recordings/`，Workflow 状态为
   `draft`，可直接进入编辑器继续修改。
3. 桌面元素拾取协议：`desktop_agent/pick.py` 读取光标下 UIA 控件与
   所属窗口；`desktop_agent/client.py` 应答 `desktop.pick`；
   `DesktopDispatcher.pick()` 与 `handle_pick_result()` 负责请求/应答；
   `app/api/agent_ws.py` 路由 `desktop.pick.result`。拾取一次即可得到
   `activate_window + click_element` 草稿步骤。
4. `scripts/record_browser.py`：交互式录制脚本。默认本地生成草稿
   JSON，`--api/--token` 可直接写入后端录制会话并落库。
5. `README.md`：补充录制模块、开关与使用方式。

## 2. 编译规则

事件字段（从宽接收，编译阶段收敛）：

| 字段 | 说明 |
| --- | --- |
| `kind` | 事件类型 |
| `url` / `title` | 页面地址与标题 |
| `locator` / `locators` | 主定位与候选定位 |
| `value` | 输入文本、下拉选项 |
| `key` | 按键或快捷键 |
| `path` | 桌面程序路径 |
| `window` | 桌面窗口 `{title, class_name, pid, index}` |
| `params` | 事件级附加参数（受动作目录白名单约束） |

编译约定：

1. 连续 `fill` 事件合并为最后一次取值；`click` 紧跟同一元素的
   `fill`/`select_option` 时丢弃点击（只是聚焦）。
2. 定位候选按 role → label → testid → text → css → xpath 排序，
   主定位写 `locator`，其余作为 `fallbacks`。
3. 每个步骤生成确定性幂等键
   `rec-<序号>-<动作>-<内容摘要>`，与桌面续跑协议共用。
4. 有副作用的动作（click、press、upload、launch_app、
   create_document、click_element、type_text 等）重试次数为 1，
   其余默认 2 次、退避 0.5 秒。
5. 桌面流程在输入类步骤前自动补 `activate_window`；中文文本自动
   选择 clipboard 输入方式。
6. 无法映射的事件写入 `warnings` 并丢弃；草稿为空或校验失败时
   返回 422 并给出原因。

## 3. 验证

| 项目 | 结果 |
| --- | --- |
| `tests/test_recorder.py` | 9 项通过（网页/桌面编译、合并去重、白名单、确定性） |
| `tests/test_recordings_api.py` | 6 项通过（开关、会话生命周期、草稿落库、拾取、权限） |
| `tests/test_desktop_pick.py` | 6 项通过（UIA 拾取、窗口识别、Agent 与派发协议） |
| 全量套件 | 396 通过、21 跳过、0 失败 |

全量命令：`python -m pytest -q --basetemp=.tmp/pytest`。

本机沙箱无法启动浏览器（`spawn EPERM`），浏览器录制脚本与真实页面
联调需要在桌面环境执行：

```bash
python scripts/record_browser.py --url https://example.com \
    --out logs/recordings/demo.json
```

真实 UIA 拾取同样需要在有交互桌面的 Windows 会话里点击一次验证。

## 4. 下一步（阶段 3 第二部分：混合执行与 DAG）

1. **步骤级执行器路由**：新增按 `step.executor` 分发的混合执行器，
   让同一个 Workflow 里的 `web`、`windows`（以及后续 `mcp`、`code`）
   步骤共用同一条 ExecutionLog 与检查点。当前
   `ExecutionManager._executor_for()` 仍按 `workflow.executor_type`
   整体路由，需要替换为混合执行器。
2. **DAG 执行**：按 `depends_on` 做拓扑排序，支持并行安全分支与
   条件步骤；失败时沿用本阶段的 `skipped`/`idempotency_key` 语义
   从失败步骤续跑。
3. **前端**：流程编辑器加入“开始录制/结束录制/拾取控件”面板，
   展示草稿来源、定位候选与警告；录制产生的 draft 流程支持逐条
   试跑。
4. **试点验收**：用 `scripts/record_browser.py` 录制一条真实网页
   操作并发布执行；桌面侧用 `POST /api/recordings/{id}/pick`
   拾取 WPS 控件，生成文档创建流程。
