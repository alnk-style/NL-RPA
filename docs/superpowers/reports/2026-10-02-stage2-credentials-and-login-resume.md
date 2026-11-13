# 阶段 2 第一部分：凭据注入、登录等待与检查点

日期：2026-10-02

关联方案：`2026-10-02-reliability-and-mcp-plan.md` 阶段 2
任务 1、2、5

## 1. 交付内容

1. 凭据管理接口：按流程保存、列出、删除凭据，接口只返回键名，
   不回显明文。
2. 运行时凭据注入：执行开始时按流程解密凭据，注入到执行上下文；
   结构化步骤用 `{{credentials.KEY}}` 占位符引用。
3. 日志脱敏：执行结果、错误信息、步骤日志、产物记录和沙箱输出
   在写库前统一把明文替换为 `***`。
4. 登录等待：`AUTH_REQUIRED_PAUSE` 开启时，登录失效的执行进入
   `awaiting_user`，并写入检查点。
5. 登录后恢复：`POST /api/executions/{id}/resume-after-login`
   从检查点继续，已成功的步骤不重跑；幂等标记已完成的步骤跳过。
6. 前端：执行列表和执行详情展示“等待登录”，提供“登录后继续”
   按钮。

## 2. 凭据生命周期

```text
PUT /api/workflows/{id}/credentials/{key}
  -> Fernet 加密 -> credential 表（只存密文）
执行开始
  -> 按 workflow_id 解密到运行时上下文
  -> 结构化步骤把 {{credentials.KEY}} 替换为明文副本后执行
  -> 结果写库前用真实值做全量脱敏
  -> 沙箱路径改用环境变量传参，context.json 不再包含凭据
```

安全约束：

1. 明文不进入代码生成、修复规划等模型 Prompt；模型只能看到键名。
2. 步骤日志保留 `{{credentials.KEY}}` 占位符，不保留明文。
3. `DockerSandbox` 把凭据从 `context.json` 中剥离，改用容器的
   `NL_RPA_CREDENTIALS` 环境变量注入，入口脚本读取后立即从
   `os.environ` 删除。
4. 执行结果返回前，错误、输出、步骤日志、产物与明细统一按
   `secret_values()` 脱敏；未知占位符按业务错误处理。

## 3. 登录等待与恢复

### 3.1 检查点

检查点复用 `ExecutionLog.step_logs` JSON 字段，不新增数据库列：

```json
{
  "type": "checkpoint",
  "action": "checkpoint",
  "status": "awaiting_user",
  "next_step_index": 3,
  "step_id": 4,
  "failure_category": "auth_required",
  "completed_idempotency_keys": ["open-messages"]
}
```

流程：

1. 网页执行器检测到登录失效，`details.awaiting_user=true`，
   失败步骤序号与已完成幂等标记一并返回。
2. `ExecutionManager` 在 `AUTH_REQUIRED_PAUSE` 开启时把执行状态
   写为 `awaiting_user`，并把检查点追加到步骤日志。
3. 用户在本机浏览器或持久 Edge Profile 完成登录。
4. `resume-after-login` 读取检查点，恢复执行时：
   - 从 `next_step_index` 开始执行，成功前缀不重跑；
   - `completed_idempotency_keys` 中已完成的步骤直接跳过并记录
     `skipped`；
   - 原有步骤日志保留，检查点条目在恢复时移除。
5. 再次登录失效会更新检查点并继续保持 `awaiting_user`。

## 4. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/workflows/{id}/credentials` | 列出凭据键名 |
| PUT | `/api/workflows/{id}/credentials/{key}` | 保存或更新凭据 |
| DELETE | `/api/workflows/{id}/credentials/{key}` | 删除凭据 |
| POST | `/api/executions/{id}/resume-after-login` | 从登录检查点继续 |

`resume-after-login` 由 `AUTH_REQUIRED_PAUSE` 开关保护；执行不在
`awaiting_user` 状态或没有检查点时返回 409。

## 5. 验证

| 项目 | 结果 |
| --- | --- |
| 凭据接口 | `tests/test_credentials_api.py`（4 项） |
| 执行器凭据占位符、恢复、幂等 | `tests/test_web_executor.py` 新增 4 项 |
| 执行管理器注入、脱敏、等待与恢复 | `tests/test_execution_manager.py` 新增 4 项 |
| 沙箱凭据环境变量与脱敏 | `tests/test_sandbox_network.py` 新增 2 项 |
| 恢复接口 | `tests/test_execution_control_api.py` 新增 2 项 |
| 后端全量套件 | 350 通过、20 跳过、0 失败 |
| 前端 | `npm run build` 通过，`npm test` 28 项通过 |

覆盖的关键行为：凭据密文存储、接口不回显、按流程注入、占位符执行、
日志脱敏、`awaiting_user` 状态、检查点落库、登录后从失败步骤恢复、
幂等步骤跳过、本地与沙箱两条执行路径。

下一步：阶段 2 第二部分，扩展桌面巡检字段与定位评分，并扩大
桌面自动修复覆盖的动作范围。
