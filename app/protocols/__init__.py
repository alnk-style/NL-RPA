"""规划器、执行器与修复器之间的共享数据契约。"""

from app.protocols.structured import (
    LOCATOR_PRIORITY,
    REPAIRABLE_CATEGORIES,
    FailureCategory,
    LocatorFallback,
    LocatorSpec,
    LocatorStrategy,
    ObservationKind,
    ObservationSnapshot,
    OnFailure,
    RetryPolicy,
    StepExecutor,
    StructuredStep,
    classify_failure,
    parse_structured_steps,
)

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
