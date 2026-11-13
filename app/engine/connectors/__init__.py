"""连接器层：统一接口、权限门、审计与不可信结果处理。"""

from app.engine.connectors.audit import ToolCallAuditWriter, audit_secrets
from app.engine.connectors.base import (
    Connector,
    ConnectorRegistry,
    connector_result,
)
from app.engine.connectors.desktop_bridge import DesktopBridgeConnector
from app.engine.connectors.mcp_connector import MCPConnector
from app.engine.connectors.permissions import (
    DEFAULT_RISK_LEVEL,
    RISK_DESTRUCTIVE,
    RISK_LEVELS,
    RISK_MUTATING,
    RISK_READ_ONLY,
    PermissionDecision,
    evaluate_permission,
    infer_risk_level,
    normalize_risk_level,
)
from app.engine.connectors.untrusted import (
    format_untrusted_for_log,
    wrap_untrusted_result,
)

__all__ = [
    "Connector",
    "ConnectorRegistry",
    "DEFAULT_RISK_LEVEL",
    "DesktopBridgeConnector",
    "MCPConnector",
    "PermissionDecision",
    "RISK_DESTRUCTIVE",
    "RISK_LEVELS",
    "RISK_MUTATING",
    "RISK_READ_ONLY",
    "ToolCallAuditWriter",
    "audit_secrets",
    "connector_result",
    "evaluate_permission",
    "format_untrusted_for_log",
    "infer_risk_level",
    "normalize_risk_level",
    "wrap_untrusted_result",
]
