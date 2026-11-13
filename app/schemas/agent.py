from datetime import datetime
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.host_capabilities import (
    normalize_capabilities,
    normalize_environment,
)


class PairRequest(BaseModel):
    pairing_code: str
    name: str
    platform: str = "windows"
    version: str = "0.1.0"
    environment: str = "local"
    capabilities: list[str] = Field(default_factory=list)
    human_access_url: str | None = None
    human_access_instructions: str | None = None
    connection_security: str = "unknown"

    @field_validator("environment")
    @classmethod
    def _environment(cls, value: str) -> str:
        return normalize_environment(value)

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, value: list[str]) -> list[str]:
        return normalize_capabilities(value)

    @field_validator("connection_security")
    @classmethod
    def _connection_security(cls, value: str) -> str:
        text = str(value or "unknown").strip().lower()
        if text not in {"ws", "wss", "unknown"}:
            raise ValueError("connection_security must be ws, wss or unknown")
        return text

    @field_validator("human_access_url")
    @classmethod
    def _human_access_url(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("human_access_url must be an HTTP(S) URL")
        return text

    @model_validator(mode="after")
    def _secure_vm_transport(self) -> "PairRequest":
        if (
            self.environment == "vm"
            and self.connection_security != "wss"
        ):
            raise ValueError(
                "VM Agent registration requires WSS"
            )
        return self


class PairResponse(BaseModel):
    agent_id: int
    token: str


class AgentResponse(BaseModel):
    id: int
    name: str
    platform: str
    version: str
    environment: str = "local"
    capabilities: list[str]
    status: str
    last_seen_at: datetime | None = None
    human_access_url: str | None = None
    human_access_instructions: str | None = None
    connection_security: str = "unknown"

    model_config = ConfigDict(from_attributes=True)


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]


class AgentConfigResponse(BaseModel):
    allowed_apps: list[str] = Field(default_factory=list)
    allowed_doc_dirs: list[str] = Field(default_factory=list)


class AgentConfigUpdate(BaseModel):
    allowed_apps: list[str]
    allowed_doc_dirs: list[str]


class HumanAccessUpdate(BaseModel):
    human_access_url: str | None = None
    human_access_instructions: str | None = Field(
        default=None,
        max_length=4000,
    )

    @field_validator("human_access_url")
    @classmethod
    def _human_access_url(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        text = str(value).strip()
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("human_access_url must be an HTTP(S) URL")
        return text


class InstalledAppResponse(BaseModel):
    key: str
    name: str
    executable_path: str
    source: str
    icon_url: str
    generated_app_id: str | None = None


class InstalledAppsResponse(BaseModel):
    apps: list[InstalledAppResponse] = Field(default_factory=list)
    supported: bool = True
