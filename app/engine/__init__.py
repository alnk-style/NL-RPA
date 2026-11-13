"""Playwright execution and sandbox engine."""

from app.engine.agent_connection_manager import AgentConnectionManager
from app.engine.code_validator import CodeValidator, ValidationResult
from app.engine.desktop_dispatcher import DesktopDispatcher
from app.engine.desktop_repair import DesktopRepairCoordinator
from app.engine.engine import ExecutionEngine
from app.engine.execution_manager import (
    ExecutionManager,
    ExecutionStateError,
    recover_interrupted_executions,
)
from app.engine.mcp_registry import MCPCallOutcome, MCPRegistry
from app.engine.mcp_tool_catalog import MCPToolCatalog
from app.engine.router import ExecutionRouter
from app.engine.runner import ExecutionResult, PlaywrightRunner, StepLog
from app.engine.sandbox import DockerSandbox
from app.engine.web_executor import WebExecutor

__all__ = [
    "AgentConnectionManager",
    "CodeValidator",
    "DesktopDispatcher",
    "DesktopRepairCoordinator",
    "DockerSandbox",
    "ExecutionEngine",
    "ExecutionManager",
    "ExecutionResult",
    "ExecutionRouter",
    "ExecutionStateError",
    "MCPCallOutcome",
    "MCPRegistry",
    "MCPToolCatalog",
    "PlaywrightRunner",
    "StepLog",
    "ValidationResult",
    "WebExecutor",
    "recover_interrupted_executions",
]
