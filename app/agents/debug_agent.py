from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from app.agents.base_agent import LLMClient, extract_json_object

if TYPE_CHECKING:
    from app.config import Settings


DEBUG_AGENT_SYSTEM_PROMPT = """你是一个 RPA 调试专家。以下代码在执行时发生了错误。请分析错误日志，修复代码，并返回完整、可执行的新代码。
不要只返回修改的片段，必须返回完整的 'async def main(page):' 函数。
不要输出 Markdown 标记。"""

DEBUG_STEPS_SYSTEM_PROMPT = """你是 RPA 网页流程修复专家。某一个步骤在真实执行中失败了，请根据当前页面的真实观察结果，重写失败步骤及其后续步骤。只输出 JSON，不要输出 Markdown 标记。

规则：
1. 只输出 {"steps": [...]}。steps 从失败的那一步开始：第一项是修复后的失败步骤，后面保留原有后续步骤的意图，可以按需插入等待步骤。
2. 只能使用“网页动作目录”中列出的动作与参数，禁止发明新动作或新参数。
3. 元素定位必须来自“当前页面观察”里 elements[].locators 或 best_locator 中真实存在的候选，不要编造选择器或控件。
4. 被修复步骤的 step_id 必须与原失败步骤一致；后续步骤沿用原有 step_id。
5. 观察结果里确实没有可用定位时，返回 {"error": "具体原因"}。"""


class DebugAgent:
    def __init__(self, config: Settings):
        self.llm = LLMClient(config)

    async def fix(
        self,
        code: str,
        error_log: str,
        screenshot_path: str | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": DEBUG_AGENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": self._build_user_prompt(
                    code,
                    error_log,
                    screenshot_path,
                ),
            },
        ]
        response = await self.llm.chat(messages)
        return self._clean_code(response)

    async def repair_steps(
        self,
        *,
        plan: dict[str, Any],
        step_index: int,
        failure: dict[str, Any],
        observation: dict[str, Any],
        action_catalog: str = "",
    ) -> list[dict[str, Any]]:
        """用真实观察结果重写失败步骤及其后续步骤。

        返回从失败步骤开始的步骤字典列表，调用方负责与已成功的前缀
        合并，并重新校验动作目录与参数 Schema。
        """

        messages = [
            {
                "role": "system",
                "content": DEBUG_STEPS_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": self._build_repair_steps_prompt(
                    plan=plan,
                    step_index=step_index,
                    failure=failure,
                    observation=observation,
                    action_catalog=action_catalog,
                ),
            },
        ]
        response = await self.llm.chat(messages)

        try:
            payload = extract_json_object(response)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(
                "修复规划器没有返回有效的 JSON"
            ) from exc

        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            raise ValueError(error.strip())

        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("修复规划器没有返回步骤")
        if any(not isinstance(step, dict) for step in steps):
            raise ValueError("修复规划器返回的步骤格式无效")
        return [dict(step) for step in steps]

    @staticmethod
    def _build_repair_steps_prompt(
        *,
        plan: dict[str, Any],
        step_index: int,
        failure: dict[str, Any],
        observation: dict[str, Any],
        action_catalog: str,
    ) -> str:
        catalog = str(action_catalog or "").strip() or "（未提供）"
        return "\n".join(
            [
                f"失败步骤序号（从 0 开始）：{step_index}",
                "",
                "失败信息：",
                json.dumps(failure, ensure_ascii=False, indent=2),
                "",
                "当前页面观察：",
                json.dumps(observation, ensure_ascii=False, indent=2),
                "",
                "网页动作目录：",
                catalog,
                "",
                "原流程：",
                json.dumps(plan, ensure_ascii=False, indent=2),
            ]
        )

    @staticmethod
    def _build_user_prompt(
        code: str,
        error_log: str,
        screenshot_path: str | None,
    ) -> str:
        prompt = (
            "原代码：\n"
            f"{code}\n\n"
            "错误日志：\n"
            f"{error_log}"
        )
        if screenshot_path:
            prompt += (
                f"\n\n失败时的截图保存在 {screenshot_path}，"
                "请尝试更换更稳定的 CSS 选择器。"
            )
        return prompt

    @staticmethod
    def _clean_code(content: str) -> str:
        code = content.strip()
        fenced_match = re.search(
            r"```(?:python|py)?\s*(.*?)```",
            code,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if fenced_match:
            code = fenced_match.group(1)

        return code.strip()
