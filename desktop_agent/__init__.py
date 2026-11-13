"""Windows desktop Agent sidecar."""

from desktop_agent.action_validator import ActionValidator, ValidationResult
from desktop_agent.actions import ACTION_REGISTRY, ActionResult
from desktop_agent.audit_logger import AuditLogger
from desktop_agent.client import AgentWebSocketClient
from desktop_agent.config import AgentConfig
from desktop_agent.executor import TaskExecutor

__all__ = [
    "ActionValidator",
    "ACTION_REGISTRY",
    "AgentConfig",
    "AgentWebSocketClient",
    "AuditLogger",
    "ActionResult",
    "TaskExecutor",
    "ValidationResult",
]
