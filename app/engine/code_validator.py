from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    is_safe: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CodeValidator:
    FORBIDDEN_IMPORTS = {
        "os",
        "subprocess",
        "shutil",
        "sys",
        "importlib",
        "ctypes",
        "socket",
        "http",
        "urllib",
        "requests",
        "pathlib",
        "glob",
        "tempfile",
    }
    FORBIDDEN_BUILTINS = {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
    }
    FORBIDDEN_CALLS = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "getattr",
        "setattr",
        "delattr",
    }
    FORBIDDEN_PATTERNS = [
        r"os\.\w+",
        r"subprocess\.\w+",
        r"__\w+__",
    ]

    @classmethod
    def validate(
        cls,
        code: str,
        *,
        strict_runtime_helpers: bool = False,
    ) -> ValidationResult:
        violations: list[str] = []
        warnings: list[str] = []

        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            message = (
                f"Syntax error: {exc.msg} "
                f"(line {exc.lineno}, column {exc.offset})"
            )
            return ValidationResult(
                is_safe=False,
                violations=[message],
                warnings=[],
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    cls._check_imported_module(
                        alias.name,
                        violations,
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    cls._check_imported_module(
                        node.module,
                        violations,
                    )
                elif node.level:
                    warnings.append(
                        "Relative imports may not be portable inside "
                        "the sandbox."
                    )
            elif isinstance(node, ast.Call):
                call_name = cls._get_call_name(node)
                if (
                    call_name in cls.FORBIDDEN_BUILTINS
                    or call_name in cls.FORBIDDEN_CALLS
                ):
                    message = f"Forbidden call: {call_name}"
                    if call_name == "open":
                        message += (
                            " (请使用 save_file(path, content) "
                            "保存结果文件)"
                        )
                    cls._append_unique(
                        violations,
                        message,
                    )
                if call_name == "wait_for" and cls._is_delay_wait(node):
                    target = (
                        violations
                        if strict_runtime_helpers
                        else warnings
                    )
                    cls._append_unique(
                        target,
                        (
                            "wait_for(selector, timeout) expects a "
                            "selector; use await "
                            "page.wait_for_timeout(milliseconds) for a "
                            "fixed delay."
                        ),
                    )
            elif isinstance(node, ast.Await):
                awaited = node.value
                if (
                    isinstance(awaited, ast.Call)
                    and cls._get_call_name(awaited) == "log"
                ):
                    target = (
                        violations
                        if strict_runtime_helpers
                        else warnings
                    )
                    cls._append_unique(
                        target,
                        (
                            "log(message) is synchronous; call it "
                            "without await."
                        ),
                    )
            elif (
                isinstance(node, ast.Name)
                and node.id in cls.FORBIDDEN_BUILTINS
            ):
                cls._append_unique(
                    violations,
                    f"Forbidden call: {node.id}",
                )

        for pattern in cls.FORBIDDEN_PATTERNS:
            for match in re.finditer(pattern, code):
                cls._append_unique(
                    violations,
                    f"Forbidden pattern: {match.group(0)}",
                )

        return ValidationResult(
            is_safe=not violations,
            violations=violations,
            warnings=warnings,
        )

    @classmethod
    def strip_main_guard(cls, code: str) -> str:
        """Remove a top-level __main__ guard before sandbox execution."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code

        lines = code.splitlines()
        removed_lines: set[int] = set()
        for node in tree.body:
            if not isinstance(node, ast.If):
                continue
            if not cls._is_main_guard(node.test):
                continue
            if node.end_lineno is None:
                continue
            removed_lines.update(
                range(node.lineno - 1, node.end_lineno)
            )

        if not removed_lines:
            return code

        remaining = [
            line
            for index, line in enumerate(lines)
            if index not in removed_lines
        ]
        return "\n".join(remaining).strip()

    @classmethod
    async def check_credential_leakage(
        cls,
        code: str,
        credentials: dict,
    ) -> list[str]:
        warnings: list[str] = []
        if not code or not isinstance(credentials, dict):
            return warnings

        for key, value in credentials.items():
            secret = cls._credential_text(value)
            if not secret:
                continue

            if secret in code:
                warnings.append(
                    f"Credential '{key}' is hardcoded; use "
                    f'context["credentials"]["{key}"] instead.'
                )

        return warnings

    @classmethod
    def sanitize(cls, code: str) -> str:
        sanitized_lines: list[str] = []
        previous_blank = False

        for line in code.splitlines():
            stripped = line.rstrip()
            if stripped:
                sanitized_lines.append(stripped)
                previous_blank = False
            elif sanitized_lines and not previous_blank:
                sanitized_lines.append("")
                previous_blank = True

        return "\n".join(sanitized_lines).strip()

    @classmethod
    def _check_imported_module(
        cls,
        module_name: str,
        violations: list[str],
    ) -> None:
        root_module = module_name.split(".", 1)[0]
        if root_module in cls.FORBIDDEN_IMPORTS:
            cls._append_unique(
                violations,
                f"Forbidden import: {root_module}",
            )

    @staticmethod
    def _get_call_name(node: ast.Call) -> str:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    @staticmethod
    def _is_delay_wait(node: ast.Call) -> bool:
        if len(node.args) != 1 or node.keywords:
            return False
        value = node.args[0]
        return (
            isinstance(value, ast.Constant)
            and isinstance(value.value, (int, float))
            and not isinstance(value.value, bool)
        )

    @staticmethod
    def _is_main_guard(node: ast.expr) -> bool:
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            return False
        if not isinstance(node.ops[0], ast.Eq):
            return False
        if len(node.comparators) != 1:
            return False

        left = node.left
        right = node.comparators[0]
        return (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        ) or (
            isinstance(right, ast.Name)
            and right.id == "__name__"
            and isinstance(left, ast.Constant)
            and left.value == "__main__"
        )

    @staticmethod
    def _credential_text(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)
