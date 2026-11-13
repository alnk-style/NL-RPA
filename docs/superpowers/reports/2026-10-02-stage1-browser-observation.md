# 阶段 1 第一部分：网页观察层

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 第 4.2 节

## 1. 交付内容

1. `app/engine/browser_observer.py`：打开页面并采集一次真实观察。
2. `POST /api/observations/browser`：受 `OBSERVE_BEFORE_PLAN`
   开关保护的观察接口。
3. `app/protocols/structured.py`：`ObservationSnapshot` 新增
   `accessibility` 摘要字段。
4. `PlaywrightRunner.launch_browser()`：执行器与观察层共用的浏览器
   启动入口，复用 Edge 持久 Profile 与隔离 Chromium 逻辑。

## 2. 观察内容

网页观察返回以下事实：

| 类别 | 内容 |
| --- | --- |
| 页面状态 | URL、标题、`readyState`、视口尺寸与像素比 |
| 可访问性 | Playwright 可访问性树摘要，节点上限 200，附角色计数 |
| 可交互元素 | 按钮、链接、输入框、下拉框、文本域、自定义 role、可点击元素等 |
| 元素状态 | 可见性、可用性、是否被遮挡、勾选状态、边界框 |
| 定位候选 | role、label、placeholder、testid、text、css、xpath，含匹配数量与评分 |
| 结构信息 | 表单、表格、弹窗、Shadow DOM 宿主、iframe 列表 |
| 登录标识 | 密码框数量、登录相关文本标记、是否疑似登录页 |

元素数量由 `max_elements` 控制（1 到 300，默认 80），可见元素
优先保留；超出上限时 `truncated=true`。iframe 最多展开 5 个，
子框架中的元素带 `frame` 标识。

## 3. 候选定位与评分

每个元素返回按评分降序排列的 `locators` 与 `best_locator`：

| 策略 | 基础分 | 说明 |
| --- | --- | --- |
| role | 100 | ARIA role，配合可访问名称 |
| label | 92 | 表单标签 |
| placeholder | 90 | 占位文本 |
| testid | 86 | `data-testid` / `data-test` / `data-cy` |
| text | 74 | 可见文本，仅对文本型角色 |
| css | 62 | 稳定 CSS 路径，优先 id |
| xpath | 40 | 最后兜底 |

匹配数量为 1 时保留基础分；匹配不到或匹配多个时降分。评分
只用于候选排序，执行器仍会逐个验证候选并记录实际命中项。

## 4. 接口

请求：

```json
{
  "url": "https://example.com/login",
  "wait_until": "domcontentloaded",
  "timeout_ms": 30000,
  "max_elements": 80,
  "include_accessibility": true,
  "store_snapshot": true
}
```

响应返回精简摘要 `observation` 与完整快照路径 `snapshot_path`。
完整快照写入 `logs/observations/observation_<时间>_<随机>.json`；
桌面模式下写入稳定的用户数据目录。失败时返回 502 和
`failure_category`，分类规则与步骤失败一致。

启用方式：

```dotenv
OBSERVE_BEFORE_PLAN=true
```

开关关闭时接口返回 403，流程生成仍走原有代码路径。

## 5. 验证

| 项目 | 结果 |
| --- | --- |
| 单元与接口测试 | `tests/test_browser_observer.py`、`tests/test_observations_api.py` |
| 真实浏览器集成 | 本地测试页采集通过（含 iframe、Shadow DOM、登录表单、可访问性树） |
| 回归测试 | 全量套件通过，旧代码生成路径无变化 |

下一步：阶段 1 第二部分，实现 `WEB_ACTIONS`、结构化执行器和
单步重试，由 `WEB_STRUCTURED_ACTIONS` 开关控制。
