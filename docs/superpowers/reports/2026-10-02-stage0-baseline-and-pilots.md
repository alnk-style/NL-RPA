# 阶段 0 基线报告与试点清单

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md`

## 1. 交付物

1. 数据 Schema：`app/protocols/structured.py`（设计说明见
   `docs/superpowers/specs/2026-10-02-structured-step-protocol.md`）。
2. 基线采集脚本：`scripts/collect_baseline.py`。
3. 基线指标定义与选定的试点流程（本文档）。
4. 功能开关：`OBSERVE_BEFORE_PLAN`、`WEB_STRUCTURED_ACTIONS`、
   `AUTH_REQUIRED_PAUSE`、`STEP_LEVEL_REPAIR`、`RECORDER_MODE`、
   `MCP_CLIENT`，默认全部关闭。

## 2. 当前基线

### 2.1 回归测试

| 项目 | 结果 |
| --- | --- |
| 测试命令 | `python -m pytest -q --basetemp=.tmp/pytest` |
| 通过 | 253 |
| 跳过 | 17 |
| 失败 | 0 |
| 运行环境 | Windows，Python 3.14.6 |

### 2.2 生产执行数据

当前工作区没有 `rpa.db`，也没有可统计的生产执行记录，因此
成功率与定位失败率的初始样本为空。后续每次执行都会写入
`execution_log`，运行以下命令即可刷新基线：

```bash
python scripts/collect_baseline.py
python scripts/collect_baseline.py --format json --output logs/baseline.json
```

当前脚本输出：

```text
数据库文件不存在，当前没有可统计的生产执行记录。
后续每次执行都会写入 execution_log，重新运行本脚本即可更新基线。
```

### 2.3 指标定义

| 指标 | 定义 |
| --- | --- |
| 首次成功率 | 成功执行数 /（成功执行数 + 失败执行数） |
| 定位失败率 | 定位类失败数 / 失败执行数 |
| 人工介入次数 | `paused`、`awaiting_user`，以及失败分类为 `auth_required`、`permission_denied` 的执行数 |
| 耗时基线 | `finished_at - started_at` 的平均值与中位数 |
| 失败分类 | 按 `app.protocols.classify_failure` 对错误文本分类 |

方案第 8 节的提升目标（首次成功率提升 30%、定位失败率下降
50% 等）在积累第一批试点执行记录后，以本报告为对比基准。

## 3. 试点流程

| 类型 | 试点 | 选择理由 | 主要验证点 |
| --- | --- | --- | --- |
| 网页 | B 站消息抓取 | 需要登录态、动态列表与分页，能覆盖定位、A11y、产物导出 | 结构化步骤执行、登录断言、定位修复、失败截图 |
| 桌面 | WPS 文档创建 | 已有 `create_document` 动作与 COM 通道，结果可确定验证 | 桌面巡检字段、定位评分、步骤检查点 |

备选网页试点：企业后台表单填写（若无法提供 B 站登录账号，
改用本地静态测试页验证选择器与修复链路）。

## 4. 实施前决策（阶段 0 采用的默认值）

| 决策项 | 默认取值 | 说明 |
| --- | --- | --- |
| 第一网页试点 | B 站消息抓取 | 登录失败时改用本地测试页 |
| 第一桌面试点 | WPS 文档创建 | 复用现有 COM 动作 |
| 代码生成兜底 | 保留，默认仍走代码路径 | 新路径由 `WEB_STRUCTURED_ACTIONS` 开关控制 |
| 多用户与远程 Agent | 第一阶段不支持 | 沿用现有单用户与本地 Agent 模型 |
| 登录失败恢复 | 人工登录后从检查点继续 | 不实现绕过扫码、验证码或 MFA |
| MCP | 先做客户端 | 服务端能力留到阶段 4 后半段 |
| 常驻浏览器与桌面 Agent | 允许 | 桌面模式已具备 |

如以上默认值与预期不符，可在进入阶段 1 前调整；调整只影响
试点选择与开关默认值，不影响已定义的数据协议。

## 5. 下一步

阶段 1 第一部分：实现网页观察层（阶段 1 任务 1），并接入
`POST /api/observations/browser`。观察层只在
`OBSERVE_BEFORE_PLAN` 开启时参与流程生成。
