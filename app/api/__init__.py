"""HTTP API route package."""

from app.api.agent_config import router as agent_config_router
from app.api.actions import router as action_catalog_router
from app.api.agent_ws import router as agent_ws_router
from app.api.agents import router as agents_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.connections import router as connections_router
from app.api.credentials import router as credentials_router
from app.api.executions import router as executions_router
from app.api.human_tasks import router as human_tasks_router
from app.api.hosts import router as hosts_router
from app.api.local_apps import router as local_apps_router
from app.api.mcp import router as mcp_router
from app.api.mcp_export import router as mcp_export_router
from app.api.metrics import router as metrics_router
from app.api.observations import router as observations_router
from app.api.recordings import router as recordings_router
from app.api.scheduler import router as scheduler_router
from app.api.settings import router as settings_router
from app.api.workflows import router as workflows_router
from app.api.visual import router as visual_router

__all__ = [
    "agent_config_router",
    "action_catalog_router",
    "agent_ws_router",
    "agents_router",
    "auth_router",
    "chat_router",
    "connections_router",
    "credentials_router",
    "executions_router",
    "human_tasks_router",
    "hosts_router",
    "local_apps_router",
    "observations_router",
    "recordings_router",
    "mcp_export_router",
    "mcp_router",
    "metrics_router",
    "scheduler_router",
    "settings_router",
    "workflows_router",
    "visual_router",
]
