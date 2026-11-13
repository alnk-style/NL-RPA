from __future__ import annotations

from app.agents.code_generator import CodeGeneratorAgent
from app.engine.code_validator import CodeValidator


async def generate_and_validate(
    agent: CodeGeneratorAgent,
    step_details: dict,
    context: dict,
) -> tuple[str, list[str]]:
    """Generate code and retry twice when validation finds violations."""

    validation = None
    code = ""

    for _attempt in range(3):
        validation_feedback = (
            validation.violations if validation is not None else None
        )
        code = await agent.generate(
            step_details,
            context,
            validation_feedback=validation_feedback,
        )
        validation = CodeValidator.validate(
            code,
            strict_runtime_helpers=True,
        )

        if validation.is_safe:
            break

    return code, validation.warnings
