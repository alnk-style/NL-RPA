"""统一 Connector 接口。

MCP Server、Desktop Bridge 以及后续官方 API 都实现同一组方法：
``capabilities / observe / act / verify / auth_status``。执行链只依赖
这个接口，不关心底层是 HTTP、WebSocket 还是本地进程。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


def connector_result(
    success: bool,
    *,
    data: Any = None,
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """统一的 Connector 返回信封。"""

    payload: dict[str, Any] = {
        "success": bool(success),
        "error": error,
        "data": data,
    }
    payload.update(extra)
    return payload


class Connector(ABC):
    """一个可观察、可调用、可验证的外部能力。"""

    name: str
    kind: str

    @abstractmethod
    async def capabilities(self) -> dict[str, Any]:
        """返回连接器支持的能力、资源与限制。"""

    @abstractmethod
    async def observe(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """观察当前环境或远端状态。"""

    @abstractmethod
    async def act(
        self,
        action: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行一个动作，返回统一信封。"""

    @abstractmethod
    async def verify(
        self,
        assertion: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """验证断言是否成立。"""

    @abstractmethod
    async def auth_status(self) -> dict[str, Any]:
        """返回认证/授权状态摘要。"""

    async def aclose(self) -> None:
        """释放连接资源，默认无操作。"""

        return None


class ConnectorRegistry:
    """按名字注册连接器，供 API 与执行链查询。"""

    def __init__(self) -> None:
        self._connectors: dict[str, Connector] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self,
        connector: Connector,
        *,
        aliases: list[str] | None = None,
    ) -> None:
        name = str(getattr(connector, "name", "") or "").strip()
        if not name:
            raise ValueError("Connector 必须有名字")
        self._connectors[name] = connector
        for alias in aliases or []:
            text = str(alias or "").strip()
            if text:
                self._aliases[text] = name

    def get(self, name: str) -> Connector | None:
        text = str(name or "").strip()
        if not text:
            return None
        resolved = self._aliases.get(text, text)
        return self._connectors.get(resolved)

    def list(self) -> list[Connector]:
        return list(self._connectors.values())

    def names(self) -> list[str]:
        return list(self._connectors)

    async def capabilities(self) -> dict[str, Any]:
        capabilities: dict[str, Any] = {}
        for name, connector in self._connectors.items():
            try:
                capabilities[name] = await connector.capabilities()
            except Exception as exc:  # noqa: BLE001
                capabilities[name] = {
                    "success": False,
                    "error": str(exc) or exc.__class__.__name__,
                }
        return capabilities

    async def auth_status(self) -> dict[str, Any]:
        statuses: dict[str, Any] = {}
        for name, connector in self._connectors.items():
            try:
                statuses[name] = await connector.auth_status()
            except Exception as exc:  # noqa: BLE001
                statuses[name] = {
                    "success": False,
                    "error": str(exc) or exc.__class__.__name__,
                }
        return statuses

    async def aclose(self) -> None:
        for connector in self._connectors.values():
            try:
                await connector.aclose()
            except Exception:  # noqa: BLE001
                continue


__all__ = ["Connector", "ConnectorRegistry", "connector_result"]
