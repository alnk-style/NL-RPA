"""Backend protocol boundaries used by App MCP adapters."""

from app.engine.app_mcp.backends.api import ApiBackend
from app.engine.app_mcp.backends.browser import BrowserBackend
from app.engine.app_mcp.backends.cli import CliBackend
from app.engine.app_mcp.backends.desktop import DesktopBackend

__all__ = [
    "ApiBackend",
    "BrowserBackend",
    "CliBackend",
    "DesktopBackend",
]
