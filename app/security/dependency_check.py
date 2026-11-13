"""依赖锁定文件与启动自检。"""

from __future__ import annotations

import importlib.metadata
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

LOCK_FILENAME = "requirements.lock"
logger = logging.getLogger(__name__)
_PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9_.\-]+)\s*==\s*(?P<version>[^\s;]+)"
)


@dataclass
class DependencyReport:
    ok: bool
    checked: int
    mismatched: list[dict[str, str]] = field(default_factory=list)
    missing: list[dict[str, str]] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "mismatched": list(self.mismatched),
            "missing": list(self.missing),
            "parse_errors": list(self.parse_errors),
        }


def parse_lock(text: str) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}
    errors: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_PATTERN.match(line)
        if match is None:
            errors.append(line)
            continue
        pins[match.group("name").lower()] = match.group("version")
    return pins, errors


def load_lock(path: Path | str | None = None) -> tuple[dict[str, str], list[str]]:
    lock_path = Path(path) if path else project_lock_path()
    if not lock_path.is_file():
        return {}, [f"缺少锁定文件：{lock_path}"]
    return parse_lock(lock_path.read_text(encoding="utf-8"))


def project_lock_path() -> Path:
    return Path(__file__).resolve().parents[2] / LOCK_FILENAME


def check_dependencies(
    path: Path | str | None = None,
    *,
    version_provider: Callable[[str], str | None] | None = None,
) -> DependencyReport:
    pins, errors = load_lock(path)
    provider = version_provider or _installed_version
    mismatched: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for name, expected in pins.items():
        actual = provider(name)
        if actual is None:
            missing.append({"name": name, "expected": expected})
        elif str(actual) != expected:
            mismatched.append(
                {
                    "name": name,
                    "expected": expected,
                    "actual": str(actual),
                }
            )
    return DependencyReport(
        ok=not mismatched and not missing and not errors,
        checked=len(pins),
        mismatched=mismatched,
        missing=missing,
        parse_errors=errors,
    )


def _installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_startup_check(config: object | None = None) -> DependencyReport:
    """启动阶段执行依赖校验；严格模式下不一致直接拒绝启动。"""

    if getattr(sys, "frozen", False):
        # PyInstaller onefile 不含 dist-info，打包产物的版本在构建时
        # 已由 requirements.lock 固定；这里跳过，避免误报缺依赖。
        logger.info("frozen bundle detected; skip requirements.lock check")
        return DependencyReport(ok=True, checked=0)

    enabled = True
    strict = False
    if config is not None:
        enabled = bool(getattr(config, "STARTUP_SELF_CHECK", True))
        strict = bool(
            getattr(config, "STARTUP_SELF_CHECK_STRICT", False)
        )
    if not enabled:
        return DependencyReport(ok=True, checked=0)
    report = check_dependencies()
    if not report.ok:
        logger.warning(
            "依赖自检未通过：mismatched=%s missing=%s errors=%s",
            report.mismatched,
            report.missing,
            report.parse_errors,
        )
        if strict:
            raise RuntimeError(
                "依赖自检失败："
                f"mismatched={report.mismatched} "
                f"missing={report.missing}"
            )
    return report


__all__ = [
    "DependencyReport",
    "LOCK_FILENAME",
    "check_dependencies",
    "load_lock",
    "parse_lock",
    "project_lock_path",
    "run_startup_check",
]
