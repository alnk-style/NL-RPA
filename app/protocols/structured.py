"""结构化步骤、定位与观察结果的数据契约。

这些模型是规划器、执行器和修复器之间的共同语言：规划阶段产出
``StructuredStep``，执行阶段用 ``LocatorSpec`` 定位元素，失败时用
``FailureCategory`` 分类，观察结果用 ``ObservationSnapshot`` 传递。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class LocatorStrategy(str, Enum):
    """网页与桌面共享的定位策略。"""

    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TESTID = "testid"
    TEXT = "text"
    CSS = "css"
    XPATH = "xpath"


# 数值越小优先级越高；label 与 placeholder 处于同一档。
LOCATOR_PRIORITY: dict[LocatorStrategy, int] = {
    LocatorStrategy.ROLE: 0,
    LocatorStrategy.LABEL: 1,
    LocatorStrategy.PLACEHOLDER: 1,
    LocatorStrategy.TESTID: 2,
    LocatorStrategy.TEXT: 3,
    LocatorStrategy.CSS: 4,
    LocatorStrategy.XPATH: 5,
}


class LocatorFallback(BaseModel):
    """``LocatorSpec`` 的候选定位，不允许再嵌套回退项。"""

    strategy: LocatorStrategy
    value: str = Field(min_length=1)
    name: str | None = None
    nth: int | None = Field(default=None, ge=0)
    exact: bool = False
    frame: str | None = None

    @field_validator("value")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("定位值不能为空")
        return stripped


class LocatorSpec(BaseModel):
    """一个元素的主定位与候选回退定位。"""

    strategy: LocatorStrategy
    value: str = Field(min_length=1)
    name: str | None = None
    nth: int | None = Field(default=None, ge=0)
    exact: bool = False
    frame: str | None = None
    fallbacks: list[LocatorFallback] = Field(default_factory=list)

    @field_validator("value")
    @classmethod
    def _strip_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("定位值不能为空")
        return stripped

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def primary(self) -> LocatorFallback:
        return LocatorFallback(
            strategy=self.strategy,
            value=self.value,
            name=self.name,
            nth=self.nth,
            exact=self.exact,
            frame=self.frame,
        )

    def candidates(self) -> list[LocatorFallback]:
        """按定位优先级返回主定位与回退定位。

        同一优先级的回退项保持声明顺序，role 永远排在最前。
        """

        ordered = sorted(
            self.fallbacks,
            key=lambda item: LOCATOR_PRIORITY[item.strategy],
        )
        return [self.primary(), *ordered]

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class FailureCategory(str, Enum):
    """步骤失败的确定性分类，用于决定重试、修复或人工接管。"""

    LOCATOR_NOT_FOUND = "locator_not_found"
    LOCATOR_NOT_UNIQUE = "locator_not_unique"
    ELEMENT_NOT_VISIBLE = "element_not_visible"
    ELEMENT_DISABLED = "element_disabled"
    TIMEOUT = "timeout"
    AUTH_REQUIRED = "auth_required"
    PERMISSION_DENIED = "permission_denied"
    APP_NOT_FOUND = "app_not_found"
    WINDOW_NOT_FOUND = "window_not_found"
    BUSINESS_ERROR = "business_error"
    BUSINESS_VALIDATION_FAILED = "business_validation_failed"
    DATA_EMPTY = "data_empty"
    ARTIFACT_MISSING = "artifact_missing"
    DELIVERY_FAILED = "delivery_failed"
    ENVIRONMENT_UNAVAILABLE = "environment_unavailable"
    VISUAL_AMBIGUOUS = "visual_ambiguous"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# auth_required / permission_denied / business_error 需要人工或业务处理，
# 其余失败允许 DebugAgent 尝试定位与参数修复。
REPAIRABLE_CATEGORIES: frozenset[FailureCategory] = frozenset(
    {
        FailureCategory.LOCATOR_NOT_FOUND,
        FailureCategory.LOCATOR_NOT_UNIQUE,
        FailureCategory.ELEMENT_NOT_VISIBLE,
        FailureCategory.ELEMENT_DISABLED,
        FailureCategory.TIMEOUT,
        FailureCategory.APP_NOT_FOUND,
        FailureCategory.WINDOW_NOT_FOUND,
        FailureCategory.UNKNOWN,
    }
)


_FAILURE_RULES: tuple[
    tuple[FailureCategory, tuple[str, ...]],
    ...,
] = (
    (
        FailureCategory.AUTH_REQUIRED,
        (
            r"\bauth_required\b",
            r"请重新登录",
            r"登录已失效",
            r"需要登录",
            r"扫码登录",
            r"\blogin required\b",
            r"\bnot logged in\b",
            r"\bunauthorized\b",
            r"\b401\b",
        ),
    ),
    (
        FailureCategory.PERMISSION_DENIED,
        (
            r"permission denied",
            r"access denied",
            r"forbidden",
            r"拒绝访问",
            r"权限不足",
            r"无权限",
            r"白名单",
            r"\b403\b",
        ),
    ),
    (
        FailureCategory.LOCATOR_NOT_UNIQUE,
        (
            r"strict mode violation",
            r"resolved to \d+ elements",
            r"matched \d+ elements",
            r"not unique",
            r"多个元素",
            r"匹配到多个",
            r"不唯一",
        ),
    ),
    (
        FailureCategory.ELEMENT_NOT_VISIBLE,
        (
            r"element is not visible",
            r"not visible",
            r"元素不可见",
            r"不可见",
        ),
    ),
    (
        FailureCategory.ELEMENT_DISABLED,
        (
            r"element is not enabled",
            r"\bdisabled\b",
            r"元素被禁用",
            r"控件不可用",
        ),
    ),
    (
        FailureCategory.WINDOW_NOT_FOUND,
        (
            r"window not found",
            r"窗口不存在",
            r"找不到窗口",
            r"未找到窗口",
            r"没有找到窗口",
        ),
    ),
    (
        FailureCategory.APP_NOT_FOUND,
        (
            r"was not found on this computer",
            r"app not found",
            r"程序不存在",
            r"找不到程序",
            r"没有找到程序",
            r"未安装",
        ),
    ),
    (
        # 放在 timeout 之前：Playwright 等待定位器超时应归为定位失败，
        # 这样才能触发更换选择器的修复，而不是无意义地整段重跑。
        FailureCategory.LOCATOR_NOT_FOUND,
        (
            r"waiting for locator",
            r"waiting for selector",
            r"no element",
            r"element not found",
            r"locator.*not found",
            r"找不到元素",
            r"未找到元素",
            r"找不到控件",
            r"未找到控件",
        ),
    ),
    (
        FailureCategory.TIMEOUT,
        (
            r"timeout",
            r"timed out",
            r"超时",
        ),
    ),
    (
        FailureCategory.BUSINESS_ERROR,
        (
            r"business_error",
            r"业务错误",
        ),
    ),
)


def classify_failure(error: str | None) -> FailureCategory:
    """把执行错误文本归类为稳定的 ``FailureCategory``。

    规则按下单顺序匹配，第一条命中的规则生效；无法识别时返回
    ``FailureCategory.UNKNOWN``。
    """

    text = str(error or "").strip()
    if not text:
        return FailureCategory.UNKNOWN

    for category, patterns in _FAILURE_RULES:
        for pattern in patterns:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return category
    return FailureCategory.UNKNOWN


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=2, ge=1, le=10)
    backoff_seconds: float = Field(default=1.0, ge=0, le=60)


class StepExecutor(str, Enum):
    WEB = "web"
    WINDOWS = "windows"
    FILES = "files"
    TRANSFORM = "transform"
    EMAIL = "email"
    MCP = "mcp"
    CODE = "code"


class OnFailure(str, Enum):
    REPAIR = "repair"
    ABORT = "abort"
    SKIP = "skip"


class StructuredStep(BaseModel):
    """可记录、可验证、可重试、可单独修复的执行单元。"""

    step_id: int = Field(ge=1)
    executor: StepExecutor = StepExecutor.WEB
    action: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    visual_fallback: bool = False
    locator: LocatorSpec | None = None
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    postconditions: list[dict[str, Any]] = Field(default_factory=list)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_seconds: float = Field(default=20.0, gt=0, le=3600)
    on_failure: OnFailure = OnFailure.REPAIR
    idempotency_key: str | None = None
    depends_on: list[int] = Field(default_factory=list)
    result_var: str | None = None
    foreach: dict[str, Any] | None = None
    output_var: str | None = None
    condition: dict[str, Any] | None = None
    verify: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("action")
    @classmethod
    def _strip_action(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("动作名不能为空")
        return stripped

    @field_validator("idempotency_key")
    @classmethod
    def _strip_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("result_var", "output_var")
    @classmethod
    def _strip_variable_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("变量名不能为空")
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
            stripped,
        ):
            raise ValueError("变量名只能包含字母、数字、下划线和点")
        return stripped

    @field_validator("depends_on")
    @classmethod
    def _normalize_depends_on(cls, value: list[int]) -> list[int]:
        normalized: list[int] = []
        for item in value:
            dependency = int(item)
            if dependency < 1:
                raise ValueError("depends_on 只能引用正整数 step_id")
            if dependency not in normalized:
                normalized.append(dependency)
        return normalized

    @model_validator(mode="after")
    def _check_self_dependency(self) -> StructuredStep:
        if self.step_id in self.depends_on:
            raise ValueError(f"步骤 {self.step_id} 不能依赖自身")
        return self


def parse_structured_steps(value: object) -> list[StructuredStep]:
    """校验并把结构化计划解析为步骤列表。

    接受完整的计划字典（含 ``steps``）或步骤列表本身，并检查
    step_id 唯一、依赖存在且依赖无环。
    """

    if isinstance(value, dict):
        raw_steps = value.get("steps")
    else:
        raw_steps = value

    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("结构化计划缺少 steps")

    steps = [
        StructuredStep.model_validate(step)
        for step in raw_steps
    ]
    step_ids = [step.step_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("step_id 不能重复")

    known = set(step_ids)
    for step in steps:
        missing = [
            dependency
            for dependency in step.depends_on
            if dependency not in known
        ]
        if missing:
            raise ValueError(
                f"步骤 {step.step_id} 依赖不存在的步骤："
                + ", ".join(str(item) for item in missing)
            )

    _check_dependency_cycles(steps)
    return steps


def _check_dependency_cycles(steps: list[StructuredStep]) -> None:
    dependencies = {
        step.step_id: list(step.depends_on) for step in steps
    }
    visited: set[int] = set()
    visiting: set[int] = set()

    def visit(step_id: int) -> None:
        if step_id in visited:
            return
        if step_id in visiting:
            raise ValueError("步骤依赖存在环")
        visiting.add(step_id)
        for dependency in dependencies[step_id]:
            visit(dependency)
        visiting.discard(step_id)
        visited.add(step_id)

    for step_id in dependencies:
        visit(step_id)


class ObservationKind(str, Enum):
    BROWSER = "browser"
    DESKTOP = "desktop"
    MCP = "mcp"


class ObservationSnapshot(BaseModel):
    """一次真实环境观察的完整快照。

    完整快照作为执行产物保存；规划阶段调用 ``compact()`` 只向模型
    暴露精简摘要，避免编造未观察到的控件。
    """

    kind: ObservationKind
    captured_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source: str | None = None
    url: str | None = None
    title: str | None = None
    window: dict[str, Any] | None = None
    summary: str = ""
    elements: list[dict[str, Any]] = Field(default_factory=list)
    accessibility: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False

    def compact(self, max_elements: int = 40) -> dict[str, Any]:
        """生成适合放进模型上下文的最小摘要。"""

        limit = max(0, int(max_elements))
        elements = self.elements[:limit]
        return {
            "kind": self.kind.value,
            "captured_at": self.captured_at.isoformat(),
            "source": self.source,
            "url": self.url,
            "title": self.title,
            "window": self.window,
            "summary": self.summary,
            "elements": elements,
            "accessibility": self.accessibility,
            "truncated": self.truncated
            or len(elements) < len(self.elements),
        }

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "LOCATOR_PRIORITY",
    "REPAIRABLE_CATEGORIES",
    "FailureCategory",
    "LocatorFallback",
    "LocatorSpec",
    "LocatorStrategy",
    "ObservationKind",
    "ObservationSnapshot",
    "OnFailure",
    "RetryPolicy",
    "StepExecutor",
    "StructuredStep",
    "classify_failure",
    "parse_structured_steps",
]
