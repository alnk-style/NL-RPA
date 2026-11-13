# 通用复杂任务引擎设计

日期：2026-10-22

状态：方案设计

关联代码：

- `app/protocols/structured.py`
- `app/engine/dag.py`
- `app/engine/hybrid_executor.py`
- `app/engine/web_executor.py`
- `app/agents/desktop_planner.py`
- `app/agents/web_planner.py`
- `desktop_agent/executor.py`
- `desktop_agent/actions/`

## 1. 目标

让系统能够处理跨网页、桌面应用、文件系统和 Office 文档的复杂任务，
并通过观察、数据流、循环、验证和恢复减少对用户补充底层参数的依赖。

典型目标：

1. 打开酷狗，搜索并播放“第一推动力”，读取歌词，生成 Word 和 Excel。
2. 打开网页，遍历分页和详情页，提取商品数据，生成 Excel。
3. 网页抓取后调用桌面微信，把结果文件发送给指定联系人。
4. 同类任务失败后自动观察现场、更换定位或恢复窗口，而不是整段重跑。

## 2. 非目标

1. 不执行模型生成的任意 Python、PowerShell 或批处理代码。
2. 不绕过验证码、风控、付费墙、登录权限或网站访问控制。
3. 不承诺所有软件首次运行全自动成功。
4. 不把所有能力做成酷狗或微信专用硬编码。
5. 不改变现有旧版 `generated_code` 路径的默认行为。

## 3. 总体架构

```text
用户自然语言
    |
    v
意图解析器
    |
    v
任务编译器（动作目录 + 应用配方 + 数据契约）
    |
    v
TaskGraph v2（DAG + 变量 + 循环 + 前后置条件）
    |
    v
TaskRuntime
  |- 变量与插值
  |- 循环与分支
  |- 重试、修复、人工接管
  |- 检查点与幂等
    |
    +--> WebAdapter      Playwright / DOM / Network
    +--> DesktopAdapter  UIA / Win32 / Clipboard / Vision
    +--> FilesAdapter    CSV / JSON / Word / Excel
    +--> TransformEngine split / filter / map / aggregate
    +--> MCPAdapter      external app tools
    |
    v
验证结果 + 步骤日志 + 产物
```

现有的 `HybridExecutor` 保留为兼容入口，但核心逐步迁移到
`TaskRuntime`。Web、桌面和文件动作都通过统一的步骤结果模型返回
文本、列表、对象、文件和验证信息。

## 4. TaskGraph v2

### 4.1 兼容原则

继续使用 `structured_plan.steps`，不破坏已有计划。新增字段均为可选：

- `result_var`
- `foreach`
- `output_var`
- `condition`
- `preconditions`
- `postconditions`
- `verify`
- `on_failure`
- `retry_policy`

旧计划只使用 `step_id`、`executor`、`action`、`params`、
`depends_on` 时保持原行为。

### 4.2 步骤执行器

`StepExecutor` 扩展为：

- `web`
- `windows`
- `files`
- `transform`
- `mcp`
- `code`（兼容旧流程，不用于新复杂任务）

### 4.3 步骤结果

每次动作返回统一结果：

```json
{
  "success": true,
  "message": "Text copied",
  "text": "第一句歌词\n第二句歌词",
  "value": null,
  "items": null,
  "artifacts": [],
  "verification": {
    "passed": true,
    "checks": []
  },
  "data": {}
}
```

`result_var` 把 `text`、`value` 或 `items` 写入共享变量空间。
运行时入口返回 `TaskRunResult(success, message, variables, results,
artifacts, error)`，步骤级结果仍统一为 `StepResult`。

### 4.4 变量插值

参数中支持只读路径解析：

```json
{
  "content": "${lyrics_text}",
  "rows": "${lyrics_lines}"
}
```

规则：

1. 只支持 `${name}` 和 `${name.path.to.value}`。
2. 不执行表达式、函数调用或 Python 代码。
3. 未解析变量直接使步骤失败，并在界面显示变量名。
4. 对象和数组保持原始类型，不做字符串化后再猜类型。

### 4.5 循环

循环节点示例：

```json
{
  "step_id": 8,
  "executor": "web",
  "action": "foreach",
  "params": {
    "items": "${detail_links}",
    "item_var": "item",
    "max_iterations": 100,
    "steps": [
      {
        "executor": "web",
        "action": "goto",
        "params": {"url": "${item.url}"}
      },
      {
        "executor": "web",
        "action": "extract_fields",
        "params": {"schema": "${item.schema}"},
        "result_var": "row"
      }
    ]
  },
  "output_var": "detail_rows"
}
```

限制：

- 最大循环次数必须显式配置，默认 100。
- 单次执行有总步骤预算和总时长预算。
- 中止循环后保留已完成结果和检查点。

实现接口为 `execute_foreach(step, state, execute_step)`，
`params.max_steps` 与 `params.max_seconds` 控制单次循环预算，
`RuntimeState.loop_cursors` 保存已完成迭代数。

### 4.6 前后置条件

条件使用声明式对象，不执行任意代码：

```json
{
  "type": "text_non_empty",
  "value": "${lyrics_text}"
}
```

首批支持：

- `window_visible`
- `element_visible`
- `text_non_empty`
- `list_min_length`
- `file_exists`
- `file_non_empty`
- `clipboard_format`
- `url_contains`
- `page_title_contains`
- `row_count_min`

验证失败归类为 `verification_failed`，进入步骤修复或人工接管。

## 5. 适配器

### 5.1 WebAdapter

职责：

- 启动或复用浏览器会话。
- DOM、可访问性树、页面文本和网络响应观察。
- 导航、点击、填写、滚动、分页、下载。
- 结构化字段提取和链接收集。
- 登录状态检测和人工登录恢复。

新增动作：

- `observe_page`
- `collect_links`
- `extract_fields`
- `paginate`
- `wait_for_response`
- `download_file`
- `assert_data`

抓取优先级：

1. 可识别且稳定的网络响应。
2. 语义定位和可访问性树。
3. CSS/XPath 回退。
4. 重新观察和修复。

### 5.2 DesktopAdapter

职责：

- 恢复、激活和定位窗口。
- UIA/Win32 控件操作。
- 键盘、鼠标、剪贴板和截图。
- OCR、模板匹配和视觉区域验证。
- 无 UIA 应用的相对坐标配方。

新增动作：

- `copy_selected_text`
- `paste_text`
- `click_relative`
- `wait_for_window_state`
- `verify_region`
- `locate_text_visual`
- `capture_window`

`copy_selected_text` 示例：

```json
{
  "executor": "windows",
  "action": "copy_selected_text",
  "params": {
    "keys": "^a^c",
    "timeout_seconds": 3,
    "min_chars": 1
  },
  "result_var": "lyrics_text"
}
```

### 5.3 FilesAdapter

职责：

- 文本、CSV、JSON 和 Markdown 文件读写。
- Word 文档创建。
- Excel 工作簿创建、逐行写入、列宽和公式。
- 文件存在性、大小和内容验证。

依赖策略：

1. Word 和 Excel 优先使用本机 WPS/Office COM。
2. 如果后续引入 `openpyxl`，只作为无 Office 环境的回退。
3. 所有写入路径必须位于桌面 Agent 的 `allowed_doc_dirs`。

`create_spreadsheet` 示例：

```json
{
  "executor": "files",
  "action": "create_spreadsheet",
  "params": {
    "path": "D:\\Administrator\\Documents\\第一推动力歌词.xlsx",
    "sheet_name": "歌词",
    "columns": [
      {
        "header": "歌词",
        "values": "${lyrics_lines}"
      },
      {
        "header": "字数",
        "formula": "=LEN(A{row})"
      }
    ],
    "column_widths": [80, 12],
    "overwrite": true
  },
  "postconditions": [
    {
      "type": "file_non_empty",
      "path": "D:\\Administrator\\Documents\\第一推动力歌词.xlsx"
    }
  ]
}
```

### 5.4 TransformEngine

首批转换：

- `split_lines`
- `trim_lines`
- `remove_blank_lines`
- `deduplicate`
- `filter_contains`
- `map_fields`
- `merge_rows`
- `count_chars`
- `aggregate_sum`
- `sort_by`

转换输入和输出都写入共享变量，不产生界面副作用。

### 5.5 MCPAdapter

继续使用现有应用 MCP 工具。MCP 工具结果可写入变量并参与后续
Word、Excel 或消息发送。

## 6. 应用配方

对 UIA 不充分的应用，保存可审计的配方，而不是每次重新猜坐标。

配方位置：

```text
desktop_agent/profiles/<app_id>.json
```

示例：

```json
{
  "schema_version": 1,
  "app_id": "kugou",
  "display_name": "酷狗音乐",
  "executable_path": "D:\\KGMusic\\KuGou.exe",
  "window": {
    "title": "酷狗音乐",
    "class_name": "kugou_ui"
  },
  "recipes": {
    "search_song": {
      "steps": [
        {"action": "click_relative", "x": 0.78, "y": 0.06},
        {"action": "type_text", "text": "${query}", "method": "clipboard"},
        {"action": "press_hotkey", "keys": "{ENTER}"}
      ],
      "verify": {"type": "window_title_changed"}
    },
    "open_lyrics": {
      "steps": [
        {"action": "click_relative", "x": 0.93, "y": 0.88}
      ],
      "verify": {"type": "window_visible", "title": "歌词"}
    }
  }
}
```

配方创建方式：

1. 优先自动生成。
2. 无 UIA 时允许用户执行一次录制或校准。
3. 每次成功执行后更新验证信息和相对坐标。
4. 配方不包含凭据和用户数据。

实现接口为 `load_app_profiles(root)`、`resolve_profile()`、
`validate_profile()` 与 `ActionValidator.validate_profile_recipe()`；
未标记 `verified=true` 的配方必须携带人工确认。

酷狗的桌面歌词为自绘窗口，不能依赖 UIA 或剪贴板可靠提取。
此场景使用只读 `fetch_kugou_lyrics`，参数为 `query`、
`min_chars` 和可选 `timeout_seconds`。该动作只访问固定域名
`mobilecdn.kugou.com`、`krcs.kugou.com`、`lyrics.kugou.com`，
限制响应大小并只读取公开歌词，不执行登录、风控或验证码绕过。

## 7. 网页抓取流水线

### 7.1 标准流水线

```text
导航
  -> 登录状态检查
  -> 搜索或筛选
  -> 收集列表项
  -> 分页循环
  -> 详情页循环
  -> 字段提取
  -> 清洗与去重
  -> 数量/字段校验
  -> 保存 Excel/CSV/JSON
```

### 7.2 安全与合规

1. 任务必须声明允许的域名。
2. 默认速率限制：同一域名不超过 1 请求/秒；可配置但不得超过
   站点要求。
3. 最大页数、最大条目数和最大运行时长必须配置。
4. 只采集当前账号有权访问的数据。
5. 遇到验证码或二次验证时暂停，人工完成后恢复。
6. 下载文件必须进入允许目录。
7. 不记录密码、Cookie、授权头和完整个人敏感字段。

实现中由 `crawl_policy` 统一约束 `allowed_domains`、
`requests_per_second`、`max_pages`、`max_items` 和可选的
`max_runtime_seconds`；使用新抓取动作时计划校验会强制要求该字段。

## 8. 执行与恢复

### 8.1 状态机

```text
pending
  -> running
  -> verifying
  -> success

running/verifying
  -> retrying
  -> repairing
  -> awaiting_user
  -> failed
  -> cancelled
```

### 8.2 恢复策略

- 定位失败：重新观察，换候选定位。
- 窗口状态失败：恢复、激活、调整尺寸。
- 剪贴板为空：重发复制快捷键并增加等待。
- 文件验证失败：检查目录、覆盖策略和 Office 进程。
- 登录失效：暂停并等待人工登录。
- 数据校验失败：重新抓取当前页，不重跑已完成页面。

### 8.3 检查点

检查点保存：

- 已完成步骤和幂等键。
- 当前变量和提取数据。
- 当前循环索引。
- 已生成文件。
- 最后观察快照摘要。

实现入口为 `RuntimeState.checkpoint()` 与
`RuntimeState.from_checkpoint(payload)`；运行时本身不决定落盘位置，
由调用方在允许目录内持久化。

## 9. 规划器约束

1. 不询问用户可执行文件路径、窗口类名或 UIA 定位信息。
2. 业务输入不足时才要求补充，例如目标网站、日期范围、账号。
3. 优先选择已有应用配方和通用动作。
4. 输出必须通过动作目录、参数、路径、域名和依赖校验。
5. 无法完成时返回缺失能力，不得编造动作。
6. 新动作必须同时加入目录、权限校验、计划摘要和测试。

编译器使用 `validate_task_graph_plan()` 校验变量生产者、前向引用、
循环上限、输出变量和文件白名单。`HybridExecutor` 对含 v2 字段的计划
内部委派 `TaskRuntime`，旧计划保持原 DAG 执行路径。

## 10. 前端执行监控

每个步骤显示：

- 当前动作和业务描述。
- 执行中、验证中、重试中、修复中、等待人工。
- 执行耗时和验证结果。
- 提取数据预览。
- 失败分类和恢复动作。
- 输出文件下载入口。

执行列表轮询和终态 `finished_at` 冻结继续使用当前修复。

## 11. 测试策略

### 11.1 单元测试

- 变量路径解析和未解析错误。
- 循环预算和中断恢复。
- 条件评估器。
- URL、域名和文件路径白名单。
- Office 写入和公式生成。
- 剪贴板文本提取。

### 11.2 契约测试

- 所有动作目录项都有处理函数和权限校验。
- 所有结果类型符合统一 StepResult。
- 所有新失败类型都有恢复策略。

### 11.3 集成场景

1. 酷狗歌词到 Word 和 Excel。
2. 网页分页抓取到 Excel。
3. 网页抓取结果到微信文件传输助手。
4. 登录失效后暂停并恢复。
5. 选择器失效后重新观察并继续。

### 11.4 验收标准

- 同一流程重复执行 3 次，结果文件均可打开。
- 每个关键步骤有前后置条件。
- 单步失败不会导致已完成数据丢失。
- 执行监控在任务完成后 3 秒内停止计时。
- 未授权路径、域名和危险动作被拒绝并记录审计。

## 12. 分阶段实施

### 阶段 1：任务运行时基础

- TaskGraph v2 字段。
- 变量、结果和模板插值。
- 条件验证器。
- 循环节点。
- 统一 StepResult。

### 阶段 2：桌面与文件能力

- `copy_selected_text`
- `click_relative`
- `create_spreadsheet`
- `create_document` 变量输入
- TransformEngine
- 基础应用配方

### 阶段 3：网页抓取

- `collect_links`
- `extract_fields`
- `paginate`
- 网络响应观察
- 域名、速率和预算限制

### 阶段 4：视觉与学习

- OCR/模板匹配提供者
- 无 UIA 应用的视觉锚点
- 成功配方自动保存
- 失败恢复策略扩展

### 阶段 5：可观测与加固

- 执行时间线升级
- 数据预览和验证结果
- 安全审计与资源配额
- 基准任务与回归测试

## 13. 风险

| 风险 | 处理 |
| --- | --- |
| 无 UIA 应用坐标变化 | 相对坐标、版本化配方、视觉验证 |
| 网站反爬或登录挑战 | 限速、暂停人工、不绕过安全机制 |
| 模型生成不可执行计划 | 严格动作目录和 Plan Validator |
| 循环失控 | 最大迭代、最大时长、检查点 |
| Office COM 残留进程 | 超时退出、进程清理、文件验证 |
| 数据敏感信息泄漏 | 脱敏、审计、变量不写入公开日志 |

