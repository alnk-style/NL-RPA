import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from runtime_paths import stable_database_url


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite+aiosqlite:///./rpa.db"
    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    CREDENTIAL_ENCRYPTION_KEY: str
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    DEV_AUTH_BYPASS: bool = False
    CORS_ALLOW_ORIGINS: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173,"
        "http://tauri.localhost,"
        "https://tauri.localhost,"
        "tauri://localhost"
    )
    DOCKER_SANDBOX_IMAGE: str = "rpa-sandbox:latest"
    EXECUTION_TIMEOUT: int = 300
    MAX_RETRY: int = 3
    SCREENSHOT_ENABLED: bool = True
    SCREENSHOT_TIMEOUT_MS: int = 15000
    SCREENSHOT_FULL_PAGE: bool = True
    DESKTOP_REPAIR_ATTEMPTS: int = 2
    # auto: use Microsoft Edge profile in desktop mode, isolated otherwise.
    # Set to "edge" to require Edge or "isolated" to force a clean browser.
    BROWSER_MODE: str = "auto"
    EDGE_EXECUTABLE_PATH: str = ""
    EDGE_USER_DATA_DIR: str = ""
    EDGE_PROFILE_DIRECTORY: str = "auto"
    BROWSER_HEADLESS: bool = True
    WEB_EXPORT_DIRS: str = ""
    # 结构化执行是新工作流默认路径；开关仅作为紧急阻断。
    OBSERVE_BEFORE_PLAN: bool = False
    WEB_STRUCTURED_ACTIONS: bool = False
    STRUCTURED_EXECUTION_ENABLED: bool = True
    LEGACY_CODE_EXECUTION_ENABLED: bool = True
    AUTH_REQUIRED_PAUSE: bool = True
    STEP_LEVEL_REPAIR: bool = False
    WEB_REPAIR_ATTEMPTS: int = 2
    RECORDER_MODE: bool = False
    MCP_CLIENT: bool = False
    # MCP 客户端：单次调用超时、全局并发上限、未知工具的默认风险级别。
    MCP_CALL_TIMEOUT: float = 30.0
    MCP_MAX_CONCURRENCY: int = 2
    MCP_DEFAULT_RISK: str = "mutating"
    MCP_STDIO_ENABLED: bool = False
    MCP_CATALOG_MAX_TOOLS: int = 50
    # Local-only App MCP processes are never auto-started unless enabled.
    LOCAL_APP_AUTO_START_ENABLED: bool = True
    # MCP Server 导出：把平台有限工具暴露给外部客户端。
    MCP_SERVER_EXPORT: bool = False
    MCP_SERVER_TOKEN: str = ""
    MCP_SERVER_ALLOW_MUTATING: bool = False
    # 阶段 5：插件供应链安全（默认关闭）。
    PLUGIN_INTEGRITY: bool = False
    PLUGIN_REQUIRE_SIGNATURE: bool = False
    PLUGIN_SIGNING_SECRET: str = ""
    # 阶段 5：指标 API、失败分类聚合与阈值告警（默认关闭）。
    METRICS_API: bool = False
    METRICS_ALERTS_ENABLED: bool = False
    METRICS_ALERT_EMAIL: str = ""
    METRICS_ALERT_WEBHOOK: str = ""
    # JSON 覆盖默认阈值，例如 {"locator_failure_rate": {"threshold": 0.1}}
    METRICS_ALERT_THRESHOLDS: str = ""
    METRICS_ALERT_WINDOW_SECONDS: int = 3600
    METRICS_ALERT_COOLDOWN_SECONDS: int = 3600
    METRICS_ALERT_INTERVAL_SECONDS: int = 300
    # 阶段 5：多用户/多 Agent/工作区隔离（默认关闭，保持旧行为）。
    WORKSPACE_ISOLATION: bool = False
    DEFAULT_WORKSPACE_NAME: str = "默认工作区"
    WORKSPACE_DEFAULT_MAX_CONCURRENT_EXECUTIONS: int = 2
    WORKSPACE_DEFAULT_MAX_CONCURRENT_AGENT_TASKS: int = 2
    # 阶段 5：任务队列后端（inprocess 默认；redis 为最小外部队列实现）。
    EXTERNAL_QUEUE: bool = False
    QUEUE_BACKEND: str = "inprocess"
    REDIS_URL: str = "redis://localhost:6379/0"
    QUEUE_POLL_TIMEOUT: float = 5.0
    # 阶段 5：启动依赖自检（对 requirements.lock 做只读校验）。
    STARTUP_SELF_CHECK: bool = True
    STARTUP_SELF_CHECK_STRICT: bool = False
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    SMTP_DEFAULT_RECIPIENT: str | None = None
    # 本地假 SMTP / 明文 relay 调试时可关闭 STARTTLS。
    SMTP_START_TLS: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if os.getenv("NL_RPA_DESKTOP_MODE") == "1":
        settings.DATABASE_URL = stable_database_url()
    return settings


settings = get_settings()
