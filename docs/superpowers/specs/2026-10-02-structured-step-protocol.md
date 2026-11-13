# 结构化步骤协议设计

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md`

代码位置：`app/protocols/structured.py`

## 1. 作用

本协议是规划器、观察层、执行器和修复器之间的共同语言，对应方案
阶段 0 的“定义 Step、LocatorSpec、ObservationSnapshot 和
FailureCategory”。协议本身不执行任何动作，也不改变现有生成代码
路径；阶段 1 起由执行器按需消费。

## 2. LocatorSpec

元素定位统一使用主定位加候选回退的结构：

```json
{
  "strategy": "role",
  "value": "button",
  "name": "登录",
  "nth": 0,
  "exact": true,
  "frame": null,
  "fallbacks": [
    {"strategy": "testid", "value": "login-button"},
    {"strategy": "text", "value": "登录"}
  ]
}
```

支持的策略与优先级：

| 优先级 | strategy | 说明 |
| --- | --- | --- |
| 0 | `role` | ARIA role，配合 `name` 作为可访问名称 |
| 1 | `label` | 表单标签文本 |
| 1 | `placeholder` | 输入框占位文本 |
| 2 | `testid` | `data-testid` 等测试标识 |
| 3 | `text` | 可见文本 |
| 4 | `css` | CSS 选择器 |
| 5 | `xpath` | XPath |

规则：

1. `value` 去除首尾空白后不能为空。
2. `nth` 从 0 开始，`exact` 表示文本或名称匹配是否区分完整匹配。
3. `fallbacks` 中的每一项不再嵌套回退项，避免无限展开。
4. `candidates()` 返回主定位加按优先级稳定排序的回退定位；
   同一优先级的回退项保持声明顺序。

## 3. FailureCategory

失败分类是确定性的，用于决定重试、修复还是人工接管：

| 分类 | 触发示例 | 默认处理 |
| --- | --- | --- |
| `locator_not_found` | `waiting for locator`、找不到控件 | 重新观察并修复定位 |
| `locator_not_unique` | `strict mode violation`、匹配到多个元素 | 收窄定位并修复 |
| `element_not_visible` | `element is not visible` | 等待或滚动后重试 |
| `element_disabled` | `element is not enabled` | 等待可用状态 |
| `timeout` | 页面加载或等待超时 | 重试或提高超时 |
| `auth_required` | `AUTH_REQUIRED`、请重新登录 | 暂停，人工登录后恢复 |
| `permission_denied` | `access denied`、白名单外 | 终止并提示用户 |
| `app_not_found` | 程序不存在、未安装 | 检查白名单与安装状态 |
| `window_not_found` | 窗口不存在 | 重新观察窗口 |
| `business_error` | 业务校验失败 | 终止并展示业务原因 |
| `unknown` | 未识别错误 | 允许一次定位修复 |

匹配规则按上表顺序执行，先命中先返回。定位器等待超时会在
`timeout` 之前命中 `locator_not_found`，从而进入定位修复而不是
无意义地整段重跑。`REPAIRABLE_CATEGORIES` 定义了允许自动修复的
范围，凭据与权限类问题不在其中。

## 4. StructuredStep

```json
{
  "step_id": 3,
  "executor": "web",
  "action": "click",
  "params": {},
  "locator": {},
  "preconditions": [],
  "postconditions": [],
  "retry_policy": {"max_attempts": 2, "backoff_seconds": 1},
  "timeout_seconds": 20,
  "on_failure": "repair",
  "idempotency_key": null,
  "depends_on": [2]
}
```

字段约束：

| 字段 | 约束 |
| --- | --- |
| `step_id` | 正整数，同一计划内唯一 |
| `executor` | `web`、`windows`、`mcp`、`code` |
| `action` | 非空，阶段 1 起用动作目录校验 |
| `retry_policy.max_attempts` | 1 到 10，默认 2 |
| `timeout_seconds` | 大于 0 且不超过 3600 |
| `on_failure` | `repair`、`abort`、`skip`，默认 `repair` |
| `depends_on` | 正整数列表，去重，禁止自依赖 |

`parse_structured_steps()` 接受完整计划字典或步骤列表，校验
step_id 唯一、依赖存在且依赖无环，并返回 `StructuredStep` 列表。

## 5. ObservationSnapshot

观察快照记录一次真实环境观察的结果：

| 字段 | 说明 |
| --- | --- |
| `kind` | `browser`、`desktop` 或 `mcp` |
| `captured_at` | UTC 采集时间 |
| `source` | 采集来源，例如 `playwright`、`desktop-agent` |
| `url` / `title` | 网页地址与标题 |
| `window` | 桌面窗口信息 |
| `summary` | 精简描述 |
| `elements` | 可交互元素或控件的结构化事实 |
| `accessibility` | 可访问性树摘要（节点、角色计数） |
| `payload` | 完整快照数据，仅作为执行产物保存 |
| `truncated` | 是否因上限被截断 |

`compact(max_elements)` 只返回规划所需的字段并限制元素数量，
不包含 `payload`，用于控制模型上下文。完整快照使用
`to_payload()` 输出。

## 6. 兼容性

1. 所有模型只新增能力，不修改现有 `structured_plan` 字段语义。
2. 现有 `generated_code` 路径不读取本协议，行为不变。
3. 功能开关 `OBSERVE_BEFORE_PLAN`、`WEB_STRUCTURED_ACTIONS`、
   `AUTH_REQUIRED_PAUSE`、`STEP_LEVEL_REPAIR` 默认关闭。
