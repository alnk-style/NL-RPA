from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from app.agents.base_agent import LLMClient
from app.engine.code_validator import CodeValidator

if TYPE_CHECKING:
    from app.config import Settings


CODE_GENERATOR_SYSTEM_PROMPT = """你是一个资深 RPA 开发者。请根据用户提供的步骤规划，生成完整、可执行的 Python 异步 Playwright 代码。

严格遵循以下规范：
1. 必须使用 async/await 语法和 playwright.async_api。
2. 必须定义一个 async def main(page): 函数，所有代码写在里面；禁止生成 if __name__ == "__main__" 或任何其他程序入口块。
3. 严禁在代码中硬编码任何密码或敏感信息。所有凭据必须通过 context['credentials']['key_name'] 获取。
4. 每一步操作后调用截图辅助函数：await screenshot('step_{n}_description')。
5. 只允许使用 Python 标准库和 Playwright；禁止导入 pandas、numpy、requests 等未安装的第三方库。
6. 添加详细的 try-except 块捕获 playwright.Error 和 Exception。
7. 禁止 import 危险模块（os, subprocess, shutil, pathlib 等），也禁止调用 open、eval、exec、compile、getattr、setattr、delattr 等内置函数。
8. Playwright 只能操作网页，不能启动或控制 Windows 桌面应用。遇到桌面应用路径、窗口或进程操作时，不要伪造浏览器代码。
9. 需要保存结果文件时必须使用运行环境提供的 save_file 辅助函数：await save_file(目标路径, 内容)。绝对不要用 open() 直接写盘；用户指定了保存目录时传入完整文件路径（例如 r'D:\\Documents\\B站+2026-10-01.csv'），未指定目录时只传文件名。
10. 表格数据优先输出 UTF-8 BOM 编码的 CSV 文件（扩展名 .csv），保证 Excel 可以直接打开；不要假设 pandas、openpyxl 等第三方库可用。
11. 运行环境只提供 screenshot、log、extract_table、wait_for、save_file 这几个辅助函数，不要调用任何未定义的函数或属性。
12. 只输出 Python 代码，不要输出 Markdown 标记（如 ```python）。
13. 登录状态判断不能只看当前 URL。很多网站会在同一 URL 上弹出登录框；必须检查页面上可见的登录提示或登录表单（例如“请重新登录”“密码登录”“扫码登录”），检测到未登录时抛出 RuntimeError("AUTH_REQUIRED: ...")，提示用户先使用配置的浏览器用户目录完成登录。"""


RUNTIME_HELPER_GUIDE = """

Runtime helper signatures:
- await screenshot(name): capture a screenshot after a step.
- log(message): append a log message; do not await log.
- await extract_table(selector): extract rows from an HTML table.
- await wait_for(selector, timeout): wait for a selector to appear.
- await page.wait_for_timeout(milliseconds): wait for a fixed delay.
- await save_file(path, content): save a result file.
- If a workflow requires an authenticated session, verify a
  logged-in marker before extracting data. If the page is still a
  login page, raise RuntimeError("AUTH_REQUIRED: ..."); never save
  empty results as a successful extraction.
- Do not infer login state from page.url alone. Many sites render a
  login modal or form on the same URL. Check visible login markers
  such as "请重新登录", "密码登录", "扫码登录", or login form
  elements. Only proceed when those markers are absent.
"""

CODE_GENERATOR_SYSTEM_PROMPT += RUNTIME_HELPER_GUIDE


class CodeGeneratorAgent:
    def __init__(self, config: Settings):
        self.llm = LLMClient(config)

    async def generate(
        self,
        step_details: dict,
        context: dict,
        validation_feedback: list[str] | None = None,
    ) -> str:
        feedback = list(validation_feedback or [])
        code = ""

        for _attempt in range(2):
            code = await self._generate_once(
                step_details,
                context,
                feedback or None,
            )
            validation = CodeValidator.validate(
                code,
                strict_runtime_helpers=True,
            )
            problems = [
                *validation.violations,
                *validation.warnings,
            ]
            problems.extend(
                await CodeValidator.check_credential_leakage(
                    code,
                    context.get("credentials", {}),
                )
            )
            if not problems:
                return code
            feedback = problems

        return code

    async def _generate_once(
        self,
        step_details: dict,
        context: dict,
        validation_feedback: list[str] | None = None,
    ) -> str:
        messages = self._build_messages(
            step_details,
            context,
            validation_feedback,
        )
        response = await self.llm.chat(messages, temperature=0.2)
        return self._clean_code(response)

    @staticmethod
    def _build_messages(
        step_details: dict,
        context: dict,
        validation_feedback: list[str] | None = None,
    ) -> list[dict]:
        variables = context.get("variables", {})
        user_prompt = (
            "步骤规划：\n"
            + json.dumps(step_details, ensure_ascii=False, indent=2)
            + "\n\n变量信息：\n"
            + json.dumps(variables, ensure_ascii=False, indent=2)
        )

        if validation_feedback:
            user_prompt += (
                "\n\n上一次生成的代码未通过安全校验，"
                "请修复以下问题并重新生成：\n"
                + "\n".join(
                    f"- {violation}"
                    for violation in validation_feedback
                )
            )

        return [
            {"role": "system", "content": CODE_GENERATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

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
