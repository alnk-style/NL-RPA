# 阶段 1 收尾：结构化规划器与试点验收

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 第 4.1、4.2 节
与阶段 1 验收标准

## 1. 交付内容

1. `app/agents/web_planner.py`：`WebPlannerAgent` 结构化网页规划器，
   把用户意图和真实页面观察转换为 `WEB_ACTIONS` 步骤，返回前用
   `validate_web_plan()` 校验动作、参数、定位与依赖。
2. `app/api/chat.py`：对话生成与修改接入结构化网页规划器。
   开启 `WEB_STRUCTURED_ACTIONS` 时优先生成结构化步骤；规划失败
   或观察失败自动回退原有代码生成路径。
3. `app/api/workflows.py`：结构化网页流程在开关开启且计划合法时
   可以免代码发布。
4. `WebExecutor.save_file`：列表、字典类型变量按 JSON 序列化，
   使 `extract_table` 的结果可以直接导出为执行产物。

## 2. 对话生成流程

```text
用户需求
  -> IntentParserAgent（结构化意图）
  -> StepPlannerAgent（细化步骤）
  -> 是否开启 WEB_STRUCTURED_ACTIONS？
     否 -> CodeGeneratorAgent（原有代码路径）
     是 -> 能否找到目标网址且开启 OBSERVE_BEFORE_PLAN？
         是 -> BrowserObserver 观察真实页面
              -> 观察失败 -> 回退代码路径
         否 -> 不观察，仅使用用户描述
     -> WebPlannerAgent 生成结构化步骤
     -> validate_web_plan 校验
        失败 -> 回退代码路径
        成功 -> 返回 execution_mode=structured 的计划
```

规划器提示词约束：

1. 只能使用网页动作目录内的动作与参数。
2. 有页面观察时，定位必须来自 `elements[].locators` 或
   `best_locator`；没有观察时只允许 role、label、placeholder、
   text 这类用户可描述的策略。
3. 打开页面后插入 `assert_logged_in` 检查登录态。
4. 抓取数据时先 `extract_table` / `extract_text` 写入变量，再
   `save_file` 输出结果文件。
5. 缺少目标网址时返回错误，由上层回退代码路径。

## 3. 试点验收

B 站消息抓取需要登录态和外网访问，按阶段 0 的默认决策改用
本地静态测试页验证同一套链路。真实无头 Chromium 下运行了 3 条
集成测试：

| 场景 | 覆盖点 | 结果 |
| --- | --- | --- |
| 结构化抓取流程 | goto、assert_logged_in、fill、click、extract_text、assert_text、extract_table、save_file | 通过 |
| 定位失败修复 | CSS 定位失败 → 现场观察 → 重写为 role 定位 → 从失败步骤续跑 | 通过 |
| 观察层采集 | DOM、可访问性、定位候选、iframe、Shadow DOM | 通过 |

试点页面上的结构化流程共 8 个步骤，全部以 `success` 写入步骤
日志；抓取结果写入 `messages.json` 执行产物，内容与页面消息
列表一致。修复场景中 `goto` 只执行一次，失败步骤日志状态为
`repaired`，修复后重新执行的步骤状态为 `success`，定位策略为
`role`。

## 4. 阶段 1 验收对照

| 验收项 | 结论 |
| --- | --- |
| 试点网页流程以结构化步骤执行 | 通过（本地试点页，真实 Chromium） |
| 步骤日志包含定位策略和执行结果 | 通过（`locator_strategy`、`attempts`、状态、截图） |
| 定位失败时可以自动修复并复跑当前步骤 | 通过（现场观察 + DebugAgent 修复 + 原地续跑） |
| 旧网页流程不回归 | 通过（开关关闭时仍走生成代码路径，全量测试通过） |

## 5. 验证

| 项目 | 结果 |
| --- | --- |
| 全量套件（沙箱内） | 334 通过、20 跳过、0 失败 |
| 真实浏览器集成（沙箱外） | 结构化流程、修复流程、观察层 3 项通过 |
| 新增测试 | 规划器 4 项、对话接入 5 项、发布 2 项、产物序列化与试点扩展 |

下一步进入阶段 2：凭据注入与持久会话、`awaiting_user` 暂停恢复、
桌面巡检字段扩展和步骤检查点。
