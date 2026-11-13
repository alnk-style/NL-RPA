# ruff: noqa: E402

import asyncio
import inspect
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if (
    not getattr(sys, "frozen", False)
    and str(PROJECT_ROOT) not in sys.path
):
    sys.path.insert(0, str(PROJECT_ROOT))


def _configure_bundled_playwright() -> None:
    if not getattr(sys, "frozen", False):
        return
    bundle_root = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).parent)
    )
    browsers_dir = bundle_root / "ms-playwright"
    if browsers_dir.exists():
        os.environ.setdefault(
            "PLAYWRIGHT_BROWSERS_PATH",
            str(browsers_dir),
        )


_configure_bundled_playwright()

if __name__ == "__main__":
    from app.engine.app_mcp.process_main import (
        is_internal_worker_invocation,
        main as app_mcp_worker_main,
    )

    if is_internal_worker_invocation(sys.argv[1:]):
        raise SystemExit(app_mcp_worker_main(sys.argv[1:]))

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app import runtime_flags
from app.api import (
    action_catalog_router,
    agent_config_router,
    agent_ws_router,
    agents_router,
    auth_router,
    chat_router,
    connections_router,
    credentials_router,
    executions_router,
    human_tasks_router,
    hosts_router,
    local_apps_router,
    mcp_export_router,
    mcp_router,
    metrics_router,
    observations_router,
    recordings_router,
    scheduler_router,
    settings_router,
    visual_router,
    workflows_router,
)
from app.api.dependencies import ensure_dev_user, get_current_user
from app.config import get_settings
from app.database import AsyncSessionLocal, init_db
from app.engine import (
    AgentConnectionManager,
    DesktopDispatcher,
    ExecutionEngine,
    ExecutionManager,
    ExecutionRouter,
    MCPRegistry,
    recover_interrupted_executions,
)
from app.engine.browser_login import (
    BrowserLoginSessionManager,
    BrowserLoginVerifier,
)
from app.engine.human_verification import CompositeHumanVerifier
from app.engine.visual_runtime import default_visual_runtime
from app.engine.app_mcp.local_manager import LocalAppManager
from app.engine.connectors import (
    ConnectorRegistry,
    DesktopBridgeConnector,
    MCPConnector,
)
from app.engine.mcp_server import MCPPlatformServer
from app.engine.queue_backends import QueueWorker, build_execution_queue
from app.metrics.monitor import MetricsAlertMonitor
from app.notifications import NotificationService
from app.scheduler import SchedulerManager
from app.security.dependency_check import run_startup_check
from app.security.workspaces import backfill_workspace_ids
from app.services.human_tasks import HumanTaskManager
from app.services.hosts import session_fingerprint
from app.services.host_events import HostEventBus
from runtime_guard import watch_parent_process

app = FastAPI(title="NL-RPA Platform")
app.state.engine = None
app.state.execution_manager = None
app.state.scheduler = None
app.state.local_app_manager = None
app.state.browser_login_sessions = None
app.state.human_task_manager = None

logger = logging.getLogger(__name__)

cors_origins = [
    origin.strip()
    for origin in get_settings().CORS_ALLOW_ORIGINS.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(action_catalog_router)
app.include_router(chat_router)
app.include_router(connections_router)
app.include_router(agents_router)
app.include_router(agent_config_router)
app.include_router(agent_ws_router)
app.include_router(scheduler_router)
app.include_router(
    workflows_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    credentials_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    executions_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    human_tasks_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    hosts_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(local_apps_router)
app.include_router(
    observations_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    recordings_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    mcp_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    mcp_export_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    metrics_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    settings_router,
    dependencies=[Depends(get_current_user)],
)
app.include_router(
    visual_router,
    dependencies=[Depends(get_current_user)],
)


@app.middleware("http")
async def log_request_duration(
    request: Request,
    call_next,
):
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "%s %s failed after %.4fs",
            request.method,
            request.url.path,
            time.perf_counter() - started_at,
        )
        raise

    duration = time.perf_counter() - started_at
    logger.info(
        "%s %s completed in %.4fs",
        request.method,
        request.url.path,
        duration,
    )
    return response


async def _invoke_optional_method(
    target,
    method_names: tuple[str, ...],
) -> bool:
    if target is None:
        return False

    for method_name in method_names:
        method = getattr(target, method_name, None)
        if not callable(method):
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.debug(
                "Failed to run %s.%s(): %s",
                type(target).__name__,
                method_name,
                exc,
            )
        return True
    return False


async def _shutdown_engine(engine) -> None:
    if engine is None:
        return

    cleaned = await _invoke_optional_method(
        engine,
        ("cleanup", "close", "shutdown"),
    )
    if cleaned:
        return

    await _invoke_optional_method(
        getattr(getattr(engine, "web_engine", engine), "sandbox", None),
        ("cleanup", "close"),
    )
    await _invoke_optional_method(
        getattr(getattr(engine, "web_engine", engine), "runner", None),
        ("cleanup", "close"),
    )


async def _deferred_local_auto_start(manager) -> None:
    try:
        # Uvicorn begins accepting requests after startup handlers return.
        await asyncio.sleep(0.25)
        await manager.auto_start()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("deferred local App MCP auto-start failed")


@app.on_event("startup")
async def on_startup() -> None:
    watch_parent_process()
    await init_db()
    restored_flags = await runtime_flags.load_overrides(AsyncSessionLocal)
    if restored_flags:
        logger.info("runtime feature flags restored: %s", restored_flags)
    await recover_interrupted_executions()
    config = get_settings()
    run_startup_check(config)
    if config.WORKSPACE_ISOLATION:
        counts = await backfill_workspace_ids(
            AsyncSessionLocal,
            config=config,
        )
        logger.info("workspace backfill: %s", counts)
    if config.DEV_AUTH_BYPASS:
        async with AsyncSessionLocal() as session:
            await ensure_dev_user(session)
    connection_manager = AgentConnectionManager()
    app.state.connection_manager = connection_manager
    host_event_bus = HostEventBus()
    app.state.host_event_bus = host_event_bus
    desktop_dispatcher = DesktopDispatcher(
        connection_manager,
        host_events=host_event_bus,
    )
    web_engine = ExecutionEngine(config)
    app.state.desktop_dispatcher = desktop_dispatcher
    app.state.web_engine = web_engine
    app.state.visual_runtime = default_visual_runtime(
        session_factory=AsyncSessionLocal,
        config=config,
    )
    web_engine.web_executor.visual_runtime = app.state.visual_runtime
    desktop_dispatcher.visual_runtime = app.state.visual_runtime
    app.state.engine = ExecutionRouter(
        web_engine,
        desktop_dispatcher,
    )
    mcp_registry = MCPRegistry(config=config)
    app.state.mcp_registry = mcp_registry
    # 让 HybridExecutor 能从 ExecutionRouter 找到 MCP 注册表。
    app.state.engine.mcp_registry = mcp_registry
    connector_registry = ConnectorRegistry()
    connector_registry.register(MCPConnector(mcp_registry))
    desktop_connector = DesktopBridgeConnector(desktop_dispatcher)
    connector_registry.register(
        desktop_connector,
        aliases=["windows"],
    )
    app.state.connectors = connector_registry
    local_app_manager = LocalAppManager(
        session_factory=AsyncSessionLocal,
        registry=mcp_registry,
        connectors={
            "desktop": desktop_connector,
            "windows": desktop_connector,
        },
        settings=config,
        workspace_isolation=config.WORKSPACE_ISOLATION,
    )
    app.state.local_app_manager = local_app_manager
    await local_app_manager.scan()
    if config.LOCAL_APP_AUTO_START_ENABLED:
        app.state.local_auto_start_task = asyncio.create_task(
            _deferred_local_auto_start(local_app_manager),
            name="local-app-auto-start",
        )
    else:
        app.state.local_auto_start_task = None
    app.state.notifier = NotificationService(config)
    app.state.metrics_alert_monitor = MetricsAlertMonitor(
        config,
        app.state.notifier,
    )
    if config.METRICS_API and config.METRICS_ALERTS_ENABLED:
        await app.state.metrics_alert_monitor.start()
    execution_queue = build_execution_queue(config)
    app.state.execution_queue = execution_queue
    app.state.execution_manager = ExecutionManager(
        app.state.engine,
        app.state.notifier,
        config=config,
        queue=execution_queue,
    )
    browser_login_sessions = BrowserLoginSessionManager()
    human_verifier = CompositeHumanVerifier(
        browser_verifier=BrowserLoginVerifier(browser_login_sessions),
        desktop_dispatcher=desktop_dispatcher,
    )
    human_task_manager = HumanTaskManager(
        session_factory=AsyncSessionLocal,
        execution_manager=app.state.execution_manager,
        verifier=human_verifier,
        session_manager=browser_login_sessions,
        online_agent_ids=app.state.execution_manager.online_agent_ids,
        host_session_fingerprint=lambda agent_id: session_fingerprint(
            connection_manager.get_session_id(int(agent_id))
        ),
    )
    app.state.execution_manager.human_tasks = human_task_manager
    app.state.browser_login_sessions = browser_login_sessions
    app.state.human_task_manager = human_task_manager
    await human_task_manager.expire_stale()
    queue_worker = None
    if (
        execution_queue is not None
        and str(config.QUEUE_BACKEND).lower() == "redis"
    ):
        queue_worker = QueueWorker(
            execution_queue,
            app.state.execution_manager.run_local,
            poll_timeout=config.QUEUE_POLL_TIMEOUT,
        )
        await queue_worker.start()
    app.state.queue_worker = queue_worker
    app.state.mcp_platform_server = MCPPlatformServer(
        start_workflow=app.state.execution_manager.start_workflow,
        allow_mutating=config.MCP_SERVER_ALLOW_MUTATING,
    )
    scheduler = SchedulerManager(config)
    app.state.scheduler = scheduler
    await scheduler.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    auto_start_task = getattr(
        app.state,
        "local_auto_start_task",
        None,
    )
    if auto_start_task is not None:
        auto_start_task.cancel()
        try:
            await auto_start_task
        except asyncio.CancelledError:
            pass
    await _invoke_optional_method(
        getattr(app.state, "local_app_manager", None),
        ("close_all",),
    )
    queue_worker = getattr(app.state, "queue_worker", None)
    if queue_worker is not None:
        await queue_worker.stop()
    execution_queue = getattr(app.state, "execution_queue", None)
    if execution_queue is not None:
        await _invoke_optional_method(execution_queue, ("close", "aclose"))
    await _invoke_optional_method(
        getattr(app.state, "metrics_alert_monitor", None),
        ("stop", "shutdown", "close"),
    )
    connectors = getattr(app.state, "connectors", None)
    if connectors is not None:
        await connectors.aclose()
    else:
        await _invoke_optional_method(
            getattr(app.state, "mcp_registry", None),
            ("aclose", "close"),
        )
    await _invoke_optional_method(
        getattr(app.state, "scheduler", None),
        ("shutdown", "close"),
    )
    execution_manager = getattr(
        app.state,
        "execution_manager",
        None,
    )
    if execution_manager is not None:
        await execution_manager.shutdown()
    await _invoke_optional_method(
        getattr(app.state, "browser_login_sessions", None),
        ("shutdown", "close"),
    )
    await _shutdown_engine(getattr(app.state, "engine", None))


@app.get("/health")
async def health_check() -> dict[str, str]:
    config = get_settings()
    return {
        "status": "ok",
        "database": config.DATABASE_URL,
    }


@app.get("/api/engine/health")
async def engine_health() -> dict[str, str | bool]:
    engine = getattr(app.state, "engine", None)
    if engine is None:
        return {
            "status": "starting",
            "engine_ready": False,
            "runner": "",
            "sandbox": "",
        }

    web_engine = getattr(engine, "web_engine", engine)
    return {
        "status": "ok",
        "engine_ready": True,
        "runner": type(web_engine.runner).__name__,
        "sandbox": type(web_engine.sandbox).__name__,
    }


def run() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )


if __name__ == "__main__":
    run()
