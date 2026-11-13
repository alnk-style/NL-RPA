"""结构化网页规划器。

把自然语言意图和真实页面观察转换成 ``WEB_ACTIONS`` 步骤，并在
返回前用 :func:`validate_web_plan` 做确定性校验。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.base_agent import LLMClient, extract_json_object
from app.agents.web_catalog import (
    WebPlanError,
    validate_web_plan,
    web_action_catalog_text,
)

if TYPE_CHECKING:
    from app.config import Settings


WEB_PLANNER_SYSTEM_PROMPT = """你是网页 RPA 规划器。把用户的网页自动化需求转换成受控的结构化动作序列，只输出 JSON，不要输出 Markdown 标记。

规则：
1. 只能使用“网页动作目录”中的动作与参数，禁止发明新动作或新参数。
2. 如果提供“页面观察”，元素定位必须来自观察结果里 elements[].locators 或 best_locator 中真实存在的候选；主定位优先 role、label、placeholder、testid、text，必要时在 fallbacks 中补 1-2 个观察到的候选。
3. 没有页面观察时，只根据用户明确描述的元素文字生成 role、label、placeholder 或 text 定位，不要编造 CSS id 或 XPath。
4. 打开目标页面后用 assert_logged_in 检查登录态。
5. 抓取数据时优先用 collect_links、extract_fields、paginate 和
   wait_for_response；简单表格可用 extract_table，单段文本可用
   extract_text。结果写入变量后再交给 files 动作输出。
6. 用户没有提供目标网址时返回 {"error": "缺少目标网址"}。
7. 使用 collect_links、extract_fields 或 paginate 时，顶层必须同时输出
   crawl_policy，包含 allowed_domains、requests_per_second（默认 1）、
   max_pages 和 max_items。域名必须是主机名，不得包含协议或路径。
8. 列表页用 paginate 或 foreach 遍历详情链接，提取字段后再校验数量和
   去重结果；不要为每个页面硬编码选择器或坐标。
9. 输出结构：
{
  "executor_type": "web",
  "execution_mode": "structured",
  "trigger": {"type": "manual", "cron": null},
  "steps": [
    {
      "step_id": 1,
      "executor": "web",
      "action": "goto",
      "target": "打开消息页",
      "params": {"url": "https://example.com"},
      "depends_on": []
    }
  ],
  "variables": [],
  "crawl_policy": {
    "allowed_domains": ["example.com"],
    "requests_per_second": 1,
    "max_pages": 20,
    "max_items": 500
  }
}
每个步骤都要带简短的 target 描述，方便用户确认。"""


class WebPlannerAgent:
    def __init__(self, config: Settings):
        self.llm = LLMClient(config)

    async def plan(
        self,
        parsed_plan: dict[str, Any],
        *,
        observation: dict[str, Any] | None = None,
        start_url: str | None = None,
        detailed_plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成并校验结构化网页计划。"""

        messages = [
            {
                "role": "system",
                "content": WEB_PLANNER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": self._build_user_prompt(
                    parsed_plan,
                    observation=observation,
                    start_url=start_url,
                    detailed_plan=detailed_plan,
                ),
            },
        ]
        response = await self.llm.chat(messages)

        try:
            plan = extract_json_object(response)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise WebPlanError(
                "网页规划器没有返回有效的 JSON"
            ) from exc

        error = plan.get("error")
        if isinstance(error, str) and error.strip():
            raise WebPlanError(error.strip())

        merged = dict(plan)
        if not isinstance(merged.get("trigger"), dict):
            trigger = _plan_field(parsed_plan, detailed_plan, "trigger")
            if isinstance(trigger, dict):
                merged["trigger"] = trigger
        if not isinstance(merged.get("variables"), list):
            variables = _plan_field(
                parsed_plan,
                detailed_plan,
                "variables",
            )
            merged["variables"] = (
                variables if isinstance(variables, list) else []
            )
        return validate_web_plan(merged)

    @staticmethod
    def _build_user_prompt(
        parsed_plan: dict[str, Any],
        *,
        observation: dict[str, Any] | None,
        start_url: str | None,
        detailed_plan: dict[str, Any] | None,
    ) -> str:
        parts = [
            "用户需求的结构化意图：",
            json.dumps(parsed_plan, ensure_ascii=False, indent=2),
        ]
        if isinstance(detailed_plan, dict) and detailed_plan:
            parts.extend(
                [
                    "",
                    "细化步骤（供参考，动作名以目录为准）：",
                    json.dumps(
                        detailed_plan,
                        ensure_ascii=False,
                        indent=2,
                    ),
                ]
            )
        if start_url:
            parts.extend(["", f"目标网址：{start_url}"])
        if isinstance(observation, dict) and observation:
            parts.extend(
                [
                    "",
                    "页面观察（真实事实，定位必须来自这里）：",
                    json.dumps(
                        observation,
                        ensure_ascii=False,
                        indent=2,
                    ),
                ]
            )
        parts.extend(
            [
                "",
                "网页动作目录：",
                web_action_catalog_text(),
            ]
        )
        return "\n".join(parts)


def _plan_field(
    parsed_plan: dict[str, Any],
    detailed_plan: dict[str, Any] | None,
    key: str,
) -> Any:
    for plan in (parsed_plan, detailed_plan):
        if isinstance(plan, dict) and key in plan:
            return plan[key]
    return None


__all__ = [
    "WEB_PLANNER_SYSTEM_PROMPT",
    "WebPlannerAgent",
]
