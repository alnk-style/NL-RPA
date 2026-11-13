# 阶段 1 第二部分：网页结构化动作与执行器

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 第 4.1、4.3 节

## 1. 交付内容

1. `app/agents/web_catalog.py`：`WEB_ACTIONS` 动作目录、参数
   Schema、`validate_web_plan()` 校验器和规划器提示文本。
2. `app/engine/web_executor.py`：`WebExecutor` 结构化执行器，
   支持逐候选定位、按步骤重试、失败分类和失败截图。
3. `ExecutionEngine`：接入 `WEB_STRUCTURED_ACTIONS` 开关，有效
   结构化计划走新执行器，旧计划继续走生成代码路径。
4. `PlaywrightRunner.capture_screenshot()`：执行器与旧路径共用
   截图逻辑，支持按步骤覆盖整页截图设置。
5. 导出：`app.agents`、`app.engine` 统一暴露新入口。

## 2. 网页动作目录

第一版目录共 17 个动作，与执行器一一对应：

| 动作 | 用途 | 需要 locator |
| --- | --- | --- |
| `goto` | 打开 http/https 页面并等待加载 | 否 |
| `click` | 点击元素 | 是 |
| `fill` | 清空输入框并输入文本 | 是 |
| `press` | 在元素上按键 | 是 |
| `select_option` | 选择下拉框选项 | 是 |
| `check` / `uncheck` | 勾选或取消勾选 | 是 |
| `wait_for` | 等待毫秒数或元素状态 | 否 |
| `extract_text` | 读取元素文本并写入变量 | 是 |
| `extract_table` | 提取表格为字典数组 | 否 |
| `screenshot` | 保存页面截图 | 否 |
| `save_file` | 把文本或变量保存为执行产物 | 否 |
| `assert_text` | 文本断言 | 否 |
| `assert_logged_in` | 登录态断言 | 否 |
| `download` | 等待下载并保存产物 | 否 |
| `upload` | 设置上传文件 | 是 |
| `code_step` | 页面内 JavaScript 兜底 | 否 |

校验规则：

1. 动作必须属于目录，未知动作、未知参数直接拒绝。
2. 必填参数、参数组合（`any_of`）和 `requires_locator` 在生成
   阶段确定性地校验，避免“规划通过、执行报错”。
3. `goto` 只接受 http/https 地址；`upload.files` 必须是路径或
   路径数组。
4. 步骤结构复用 `StructuredStep`：`step_id`、`retry_policy`、
   `timeout_seconds`、`on_failure`、`depends_on` 一并校验；
   缺省时补齐 `step_id` 与默认重试策略。
5. 校验结果标记 `execution_mode: structured`，作为新执行路径的
   显式声明。

## 3. 结构化执行器

### 3.1 定位解析

每个步骤按 `LocatorSpec.candidates()` 的顺序尝试主定位与回退
定位，支持 role、label、placeholder、testid、text、css、xpath，
单个候选支持 `nth`、`exact` 和 `frame`。执行器先统计匹配数量：

1. 匹配 0 个：记录候选失败，继续尝试下一个候选。
2. 匹配多个且未指定 `nth`：记录“不唯一”，继续尝试下一个候选。
3. 全部候选失败：按是否出现过重复命中，分别给出
   `locator_not_found` 或 `locator_not_unique`。

命中的定位策略写入步骤日志的 `locator_strategy` 字段。

### 3.2 重试与失败分类

1. 每个步骤按 `retry_policy.max_attempts` 重试，间隔
   `backoff_seconds`；定位在每次尝试时重新解析，适配动态页面。
2. `auth_required`、`permission_denied`、`business_error` 等不在
   `REPAIRABLE_CATEGORIES` 内的失败不重试。
3. 失败时采集一张 `step_XX_<action>_failed.png` 截图，并写入
   `failure_category`、`attempts` 和 `locator_attempts`。
4. `on_failure=skip` 记录 `skipped` 并继续后续步骤；
   `abort` 与 `repair` 目前都停止在当前步骤，`repair` 留给
   下一步的 DebugAgent 单步修复消费。
5. 检测到登录失效时，结果附带 `awaiting_user=true`，为后续
   `AUTH_REQUIRED_PAUSE` 暂停恢复提供信号。

### 3.3 变量与产物

- `extract_text`、`extract_table`、`code_step` 可以把结果写入
  变量，供 `save_file` 等后续步骤引用。
- `save_file` 与 `download` 写入执行产物目录，沿用
  `deliver_execution_artifacts()` 的白名单投递逻辑。
- 每个步骤记录 `step_index`、`step_id`、`action`、`status`、
  `attempts`、`locator_strategy`、`error`、`failure_category`、
  `screenshot_path` 等字段，UI 与基线脚本可直接消费。

## 4. 开关与灰度

`WEB_STRUCTURED_ACTIONS` 默认关闭：

| 开关 | 计划形态 | 行为 |
| --- | --- | --- |
| 关 | 任意 | 完全走原有生成代码路径 |
| 开 | 动作目录内、结构合法的步骤 | 走结构化执行器 |
| 开 | 旧版步骤（如 `navigate`） | 回退生成代码路径 |
| 开 | 显式 `execution_mode: structured` 但校验失败 | 直接报错，不静默降级 |

结构化路径在步骤内已经重试，因此 `execute_with_debug()` 不再对
整条流程做整体重跑；整段重跑只保留给旧代码路径。

## 5. 验证

| 项目 | 结果 |
| --- | --- |
| 动作目录与计划校验 | `tests/test_web_catalog.py`（10 项） |
| 执行器行为 | `tests/test_web_executor.py`（12 项，1 项真实浏览器用例在无浏览器环境下跳过） |
| 引擎开关与回退 | `tests/test_engine.py` 新增 4 项 |
| 全量套件 | `python -m pytest -q --basetemp=.tmp/pytest`，309 通过、19 跳过、0 失败 |

覆盖的关键行为：动作顺序、定位回退、重试计数、不可重试失败、
失败截图与分类、`on_failure=skip`、变量提取、产物写入、
iframe 定位、真实页面条件性集成。

下一步：阶段 1 第三部分，接入 DebugAgent 做单步修复，提供
步骤级校验与修复接口，并让定位失败只复跑当前步骤及其后置条件。
