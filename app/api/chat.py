from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import runtime_flags
from app.agents import (
    CodeGeneratorAgent,
    DesktopExecutionContext,
    DesktopPlanError,
    DesktopPlannerAgent,
    IntentParserAgent,
    StepPlannerAgent,
    WebPlannerAgent,
)
from app.agents.desktop_plan import build_desktop_document_plan
from app.agents.desktop_planner import (
    is_desktop_intent,
    resolve_allowed_apps,
    select_target_window,
    validate_task_graph_plan,
    window_hints,
)
from app.agents.mcp_plan import (
    MCPPlanError,
    contains_mcp_steps,
    validate_mcp_plan,
)
from app.agents.plan_summary import confirmation_reply
from app.agents.web_catalog import WebPlanError
from app.api.dependencies import get_current_user
from app.api.observations import get_browser_observer
from app.api.workspace_deps import get_workspace_context
from app.config import get_settings
from app.database import get_db
from app.engine.browser_observer import (
    BrowserObserver,
    ObservationError,
)
from app.engine.desktop_dispatcher import DesktopDispatcher
from app.engine.desktop_targets import load_desktop_execution_context
from app.engine.installed_app_mcp import generated_integrations_by_key
from app.engine.installed_apps import cached_installed_apps
from app.engine.mcp_tool_catalog import MCPToolCatalog
from app.models.chat_session import ChatSession
from app.models.user import User
from app.models.workflow import Workflow
from app.schemas.workflow import (
    ChatMessage,
    ChatResponse,
    ChatSessionPayload,
    ChatSessionResponse,
)
from app.security.workspaces import WorkspaceContext

router = APIRouter(prefix="/api/chat", tags=["对话交互"])
logger = logging.getLogger(__name__)

session_contexts: dict[str, list[dict]] = {}


def require_llm_configuration() -> None:
    if not get_settings().DEEPSEEK_API_KEY.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "DeepSeek API Key 未配置，请在桌面客户端"
                "“设置”页面填写。"
            ),
        )


class ChatModifyRequest(BaseModel):
    session_id: str
    modification: str
    workflow_id: int


class ChatConfirmRequest(BaseModel):
    session_id: str
    workflow_id: int


def _utc_naive(value: datetime | None) -> datetime:
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def get_intent_parser_agent() -> IntentParserAgent:
    return IntentParserAgent(get_settings())


def get_step_planner_agent() -> StepPlannerAgent:
    return StepPlannerAgent(get_settings())


def get_code_generator_agent() -> CodeGeneratorAgent:
    return CodeGeneratorAgent(get_settings())


def get_web_planner_agent() -> WebPlannerAgent:
    return WebPlannerAgent(get_settings())


def get_desktop_planner_agent() -> DesktopPlannerAgent:
    return DesktopPlannerAgent(get_settings())


def get_mcp_tool_catalog(request: Request) -> MCPToolCatalog:
    registry = getattr(request.app.state, "mcp_registry", None)
    settings = get_settings()
    return MCPToolCatalog(
        registry,
        max_tools=int(
            getattr(settings, "MCP_CATALOG_MAX_TOOLS", 50) or 50
        ),
    )


async def get_desktop_context(
    request: Request,
) -> DesktopExecutionContext:
    return await load_desktop_execution_context(request)


def get_desktop_dispatcher(
    request: Request,
) -> DesktopDispatcher | None:
    return getattr(request.app.state, "desktop_dispatcher", None)


def _format_context_entry(entry: dict[str, Any]) -> str:
    parts: list[str] = []
    role = str(entry.get("role", "context"))
    content = entry.get("content")
    if content:
        parts.append(f"{role}: {content}")

    structured_plan = entry.get("structured_plan")
    if structured_plan is not None:
        parts.append(
            "structured_plan: "
            + json.dumps(structured_plan, ensure_ascii=False, default=str)
        )

    generated_code = entry.get("generated_code")
    if generated_code:
        parts.append(f"generated_code: {generated_code}")

    return "\n".join(parts)


def _history_text(history: list[dict]) -> str:
    return "\n".join(
        text
        for text in (_format_context_entry(entry) for entry in history)
        if text
    )


def _clarification_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _has_structured_plan(history: list[dict]) -> bool:
    return any(entry.get("structured_plan") is not None for entry in history)


def _is_direct_desktop_plan(plan: dict[str, Any]) -> bool:
    executor_type = str(plan.get("executor_type") or "").strip().lower()
    if executor_type in {"windows", "desktop"}:
        return True
    app_hint = plan.get("app_hint")
    if (
        executor_type != "web"
        and isinstance(app_hint, str)
        and app_hint.strip()
    ):
        return True
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return False
    executors = {
        str(step.get("executor") or "").strip().lower()
        for step in steps
        if isinstance(step, dict)
    }
    return bool(executors) and executors <= {"windows", "desktop"}


def _uses_task_graph_v2(plan: dict[str, Any]) -> bool:
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return False
    v2_fields = {
        "result_var",
        "output_var",
        "foreach",
        "condition",
        "preconditions",
        "postconditions",
        "verify",
        "on_failure",
        "retry_policy",
    }
    for step in steps:
        if not isinstance(step, dict):
            continue
        executor = str(step.get("executor") or "").strip().lower()
        if executor in {"files", "transform"}:
            return True
        if any(field in step for field in v2_fields):
            return True
    return False


def _strip_desktop_runtime_clarifications(plan: dict[str, Any]) -> None:
    if not is_desktop_intent(plan):
        return
    plan.pop("needs_clarification", None)
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict):
            continue
        step.pop("selector_reason", None)
        if str(step.get("selector") or "") == "needs_user_input":
            step.pop("selector", None)


def _resolved_desktop_apps(
    raw_message: str,
    parsed_plan: dict[str, Any],
    desktop_context: DesktopExecutionContext,
) -> list[dict[str, str]]:
    text = raw_message + "\n" + json.dumps(
        parsed_plan,
        ensure_ascii=False,
        default=str,
    )
    direct = resolve_allowed_apps(
        text,
        desktop_context.allowed_apps,
    )
    if len(direct) >= 2 or not desktop_context.allowed_apps:
        return direct
    candidates: list[Any] = []
    try:
        candidates.extend(generated_integrations_by_key().values())
    except Exception:
        logger.debug(
            "Generated app integration discovery failed",
            exc_info=True,
        )
    generated = resolve_allowed_apps(
        text,
        desktop_context.allowed_apps,
        candidates,
    )
    if len(generated) >= 2:
        return generated
    try:
        candidates.extend(cached_installed_apps())
    except Exception:
        logger.debug("Installed app discovery failed", exc_info=True)
    return resolve_allowed_apps(
        text,
        desktop_context.allowed_apps,
        candidates,
    )


def _plan_context(
    structured_plan: dict[str, Any],
    fallback_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    variables = structured_plan.get("variables")
    if variables is None and fallback_plan is not None:
        variables = fallback_plan.get("variables")
    if variables is None:
        variables = []
    return {"variables": variables}


def _mcp_executor_type(plan: dict[str, Any]) -> str:
    executors = {
        str(step.get("executor") or plan.get("executor_type") or "web")
        for step in plan.get("steps") or []
        if isinstance(step, dict)
    }
    return "mcp" if executors <= {"mcp"} else "hybrid"


def _mcp_unavailable_response(
    session_id: str,
    structured_plan: dict[str, Any] | None,
) -> ChatResponse:
    reply = (
        "当前没有已授权且可用的 MCP 工具。请先安装或配置应用，"
        "测试连接并完成工具授权后再试。"
    )
    _remember_reply(session_id, reply, structured_plan)
    return ChatResponse(
        reply=reply,
        structured_plan=structured_plan,
        generated_code=None,
        needs_confirmation=False,
        suggestions=["打开应用设置", "修改描述"],
        executor_type="web",
    )


async def _mcp_tools_for_request(
    *,
    catalog: MCPToolCatalog,
    current_user: User,
    context: WorkspaceContext,
    limit: int = 30,
) -> list[dict[str, Any]]:
    return await catalog.list_tools(
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        limit=limit,
    )


def _extract_web_url(*plans: Any) -> str | None:
    for plan in plans:
        url = _find_http_url(plan)
        if url:
            return url
    return None


def _find_http_url(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("http://", "https://")):
            return text
        return None
    if isinstance(value, dict):
        for item in value.values():
            url = _find_http_url(item)
            if url:
                return url
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            url = _find_http_url(item)
            if url:
                return url
    return None


async def _plan_structured_web(
    *,
    parsed_plan: dict[str, Any],
    detailed_plan: dict[str, Any] | None,
    web_planner: WebPlannerAgent,
    observer: BrowserObserver | None,
) -> dict[str, Any]:
    """生成结构化网页计划；失败时由调用方 fail closed。"""

    settings = get_settings()
    url = _extract_web_url(parsed_plan, detailed_plan)
    observation: dict[str, Any] | None = None

    if url and settings.OBSERVE_BEFORE_PLAN:
        if observer is None:
            raise WebPlanError(
                "Page observation is required but unavailable"
            )
        try:
            snapshot = await observer.observe(
                url,
                wait_until="domcontentloaded",
                timeout_ms=30000,
                max_elements=60,
                include_accessibility=True,
            )
        except ObservationError as exc:
            raise WebPlanError(
                f"Page observation failed: {exc}"
            ) from exc
        observation = snapshot.compact(max_elements=60)

    try:
        return await web_planner.plan(
            parsed_plan,
            observation=observation,
            start_url=url,
            detailed_plan=detailed_plan,
        )
    except (WebPlanError, ValueError) as exc:
        raise WebPlanError(str(exc)) from exc


def _append_generation_turn(
    session_id: str,
    structured_plan: dict[str, Any],
    generated_code: str | None,
    reply: str = "已为您生成自动化流程，请确认是否需要修改？",
) -> None:
    session_contexts.setdefault(session_id, []).append(
        {
            "role": "assistant",
            "content": reply,
            "structured_plan": structured_plan,
            "generated_code": generated_code,
        }
    )


def _success_response(
    structured_plan: dict[str, Any],
    generated_code: str | None,
    executor_type: str = "web",
    reply: str | None = None,
) -> ChatResponse:
    return ChatResponse(
        reply=reply or confirmation_reply(structured_plan, executor_type),
        structured_plan=structured_plan,
        generated_code=generated_code,
        needs_confirmation=True,
        suggestions=["修改参数", "直接保存", "试运行"],
        executor_type=executor_type,
    )


def _remember_reply(
    session_id: str,
    reply: str,
    structured_plan: dict[str, Any] | None,
) -> None:
    session_contexts.setdefault(session_id, []).append(
        {
            "role": "assistant",
            "content": reply,
            "structured_plan": structured_plan,
        }
    )


async def _plan_desktop_workflow(
    *,
    session_id: str,
    parsed_plan: dict[str, Any],
    raw_message: str,
    desktop_planner: DesktopPlannerAgent,
    desktop_context: DesktopExecutionContext,
    dispatcher: DesktopDispatcher | None = None,
) -> ChatResponse:
    if not desktop_context.has_agents:
        reply = (
            "这个需求需要操作 Windows 桌面应用，但当前还没有已配对的"
            "桌面 Agent。请先打开桌面客户端完成配对，再发送这条需求。"
        )
        _remember_reply(session_id, reply, parsed_plan)
        return ChatResponse(
            reply=reply,
            structured_plan=parsed_plan,
            needs_confirmation=False,
            suggestions=["打开设置", "取消"],
        )

    if not desktop_context.has_allowed_apps:
        reply = (
            "桌面 Agent 已连接，但“允许的程序”白名单还是空的。请先在"
            "桌面客户端的设置里添加要操作的程序（程序名或 .exe 路径），"
            "再发送这条需求。"
        )
        _remember_reply(session_id, reply, parsed_plan)
        return ChatResponse(
            reply=reply,
            structured_plan=parsed_plan,
            needs_confirmation=False,
            suggestions=["打开设置", "取消"],
        )

    snapshot, inspected_window = await _load_desktop_snapshot(
        dispatcher,
        parsed_plan,
        desktop_context,
    )

    planner_input = dict(parsed_plan)
    planner_input["raw_request"] = raw_message
    resolved_apps = _resolved_desktop_apps(
        raw_message,
        parsed_plan,
        desktop_context,
    )
    if resolved_apps:
        planner_input["resolved_apps"] = resolved_apps
    try:
        desktop_plan = await desktop_planner.plan(
            planner_input,
            desktop_context,
            snapshot=snapshot,
        )
    except DesktopPlanError as exc:
        message = str(exc)
        if message.startswith("该需求暂不支持"):
            reply = (
                f"{message}。你可以换一种做法，"
                "或者告诉我需要补上这个能力。"
            )
        else:
            reply = (
                f"桌面流程没有通过校验：{message}。"
                "请补充要操作的程序或界面控件名称后重试。"
            )
        _remember_reply(session_id, reply, parsed_plan)
        return ChatResponse(
            reply=reply,
            structured_plan=parsed_plan,
            needs_confirmation=False,
            suggestions=["修改描述", "取消"],
        )

    intro = "已生成 Windows 桌面流程，请确认要操作的程序和步骤后执行。"
    if isinstance(inspected_window, dict) and inspected_window.get("title"):
        intro = (
            f"已按窗口“{inspected_window['title']}”的控件信息生成 Windows "
            "桌面流程，请确认步骤后执行。"
        )
    reply = confirmation_reply(desktop_plan, "windows", intro=intro)
    _remember_reply(session_id, reply, desktop_plan)
    return ChatResponse(
        reply=reply,
        structured_plan=desktop_plan,
        generated_code=None,
        needs_confirmation=True,
        suggestions=["修改步骤", "确认保存"],
        executor_type="windows",
    )


async def _load_desktop_snapshot(
    dispatcher: DesktopDispatcher | None,
    parsed_plan: dict[str, Any],
    desktop_context: DesktopExecutionContext,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """读取可见窗口与目标窗口的控件摘要，供规划器使用。"""

    if dispatcher is None or not desktop_context.agent_ids:
        return None, None
    inspect = getattr(dispatcher, "inspect", None)
    if not callable(inspect):
        return None, None

    agent_id = desktop_context.agent_ids[0]
    try:
        inventory = await inspect(
            {"max_windows": 40, "max_elements": 0},
            agent_id=agent_id,
            timeout=8.0,
        )
    except Exception:
        logger.debug("Desktop inventory failed", exc_info=True)
        return None, None

    if not isinstance(inventory, dict) or not inventory.get("success"):
        return None, None

    windows = [
        window
        for window in (inventory.get("windows") or [])
        if isinstance(window, dict)
    ]
    snapshot: dict[str, Any] = {
        "windows": windows,
        "target": None,
        "elements": [],
    }
    target = select_target_window(windows, window_hints(parsed_plan))
    if target is None:
        return snapshot, None

    try:
        detail = await inspect(
            {
                "window": {"handle": target.get("handle")},
                "max_elements": 60,
                "max_depth": 4,
            },
            agent_id=agent_id,
            timeout=8.0,
        )
    except Exception:
        logger.debug("Desktop inspect failed", exc_info=True)
        return snapshot, None

    if not isinstance(detail, dict) or not detail.get("success"):
        return snapshot, None

    target_info = detail.get("target")
    snapshot["target"] = (
        target_info if isinstance(target_info, dict) else target
    )
    snapshot["elements"] = [
        element
        for element in (detail.get("elements") or [])
        if isinstance(element, dict)
    ]
    return snapshot, snapshot["target"]


@router.post(
    "/message",
    response_model=ChatResponse,
    dependencies=[Depends(require_llm_configuration)],
)
async def send_message(
    payload: ChatMessage,
    intent_parser: IntentParserAgent = Depends(get_intent_parser_agent),
    step_planner: StepPlannerAgent = Depends(get_step_planner_agent),
    code_generator: CodeGeneratorAgent = Depends(get_code_generator_agent),
    web_planner: WebPlannerAgent = Depends(get_web_planner_agent),
    web_observer: BrowserObserver = Depends(get_browser_observer),
    desktop_planner: DesktopPlannerAgent = Depends(
        get_desktop_planner_agent
    ),
    desktop_context: DesktopExecutionContext = Depends(
        get_desktop_context
    ),
    desktop_dispatcher: DesktopDispatcher | None = Depends(
        get_desktop_dispatcher
    ),
    current_user: User = Depends(get_current_user),
    workspace_context: WorkspaceContext = Depends(get_workspace_context),
    mcp_catalog: MCPToolCatalog = Depends(get_mcp_tool_catalog),
) -> ChatResponse:
    history = session_contexts.setdefault(payload.session_id, [])
    history.append({"role": "user", "content": payload.message})

    first_conversation = (
        payload.workflow_id is None and not _has_structured_plan(history)
    )
    parse_input = (
        payload.message
        if first_conversation
        else _history_text(history)
    )
    mcp_tools = await _mcp_tools_for_request(
        catalog=mcp_catalog,
        current_user=current_user,
        context=workspace_context,
    )
    try:
        parsed_plan = (
            await intent_parser.parse(
                parse_input,
                mcp_tools=mcp_tools,
            )
            if mcp_tools
            else await intent_parser.parse(parse_input)
        )
    except ValueError as exc:
        if not mcp_tools and "mcp" in str(exc).casefold():
            return _mcp_unavailable_response(
                payload.session_id,
                None,
            )
        raise

    _strip_desktop_runtime_clarifications(parsed_plan)

    clarification = parsed_plan.get("needs_clarification")
    if clarification:
        reply = _clarification_text(clarification)
        session_contexts[payload.session_id].append(
            {
                "role": "assistant",
                "content": reply,
                "structured_plan": parsed_plan,
            }
        )
        return ChatResponse(
            reply=reply,
            structured_plan=parsed_plan,
            needs_confirmation=False,
        )

    if _is_direct_desktop_plan(parsed_plan):
        return await _plan_desktop_workflow(
            session_id=payload.session_id,
            parsed_plan=parsed_plan,
            raw_message=payload.message,
            desktop_planner=desktop_planner,
            desktop_context=desktop_context,
            dispatcher=desktop_dispatcher,
        )

    try:
        structured_plan = (
            await step_planner.plan(
                parsed_plan,
                mcp_tools=mcp_tools,
            )
            if mcp_tools
            else await step_planner.plan(parsed_plan)
        )
        structured_plan = validate_mcp_plan(
            structured_plan,
            mcp_tools,
        )
    except MCPPlanError as exc:
        if not mcp_tools:
            return _mcp_unavailable_response(
                payload.session_id,
                parsed_plan,
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"MCP 计划校验失败：{exc}",
        ) from exc
    if contains_mcp_steps(structured_plan):
        executor_type = _mcp_executor_type(structured_plan)
        structured_plan["executor_type"] = executor_type
        structured_plan["needs_confirmation"] = True
        reply = confirmation_reply(
            structured_plan,
            executor_type,
            intro=(
                "已生成应用 MCP 流程，请确认工具权限、"
                "参数和风险提示后执行。"
            ),
        )
        _append_generation_turn(
            payload.session_id,
            structured_plan,
            None,
            reply=reply,
        )
        return ChatResponse(
            reply=reply,
            structured_plan=structured_plan,
            generated_code=None,
            needs_confirmation=True,
            suggestions=["检查工具参数", "确认保存"],
            executor_type=executor_type,
        )
    if _uses_task_graph_v2(structured_plan):
        executors = {
            str(step.get("executor") or "windows").strip().lower()
            for step in structured_plan.get("steps", [])
            if isinstance(step, dict)
        }
        if "windows" in executors and not desktop_context.has_agents:
            reply = (
                "这个混合任务包含 Windows 桌面步骤，但当前还没有已配对的"
                "桌面 Agent。请先打开桌面客户端完成配对后重试。"
            )
            _remember_reply(
                payload.session_id,
                reply,
                structured_plan,
            )
            return ChatResponse(
                reply=reply,
                structured_plan=structured_plan,
                generated_code=None,
                needs_confirmation=False,
                suggestions=["打开设置", "取消"],
                executor_type="hybrid",
            )
        try:
            structured_plan = validate_task_graph_plan(
                structured_plan,
                allowed_apps=desktop_context.allowed_apps,
                allowed_doc_dirs=desktop_context.allowed_doc_dirs,
            )
        except (DesktopPlanError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"复杂任务计划校验失败：{exc}",
            ) from exc
        executor_type = str(
            structured_plan.get("executor_type") or "hybrid"
        )
        structured_plan["executor_type"] = executor_type
        reply = confirmation_reply(
            structured_plan,
            executor_type,
            intro=(
                "已生成通用任务流程，请确认变量、循环、"
                "文件路径和网页抓取范围后执行。"
            ),
        )
        _append_generation_turn(
            payload.session_id,
            structured_plan,
            None,
            reply=reply,
        )
        return _success_response(
            structured_plan,
            None,
            executor_type=executor_type,
            reply=reply,
        )
    desktop_plan = build_desktop_document_plan(structured_plan)
    if desktop_plan is not None:
        reply = confirmation_reply(
            desktop_plan,
            "windows",
            intro=(
                "已生成 Windows/WPS 文档创建流程，"
                "请确认保存路径和内容后执行。"
            ),
        )
        session_contexts[payload.session_id].append(
            {
                "role": "assistant",
                "content": reply,
                "structured_plan": desktop_plan,
            }
        )
        return ChatResponse(
            reply=reply,
            structured_plan=desktop_plan,
            generated_code=None,
            needs_confirmation=True,
            suggestions=["修改路径", "修改内容", "确认保存"],
            executor_type="windows",
        )

    if is_desktop_intent(parsed_plan, structured_plan):
        return await _plan_desktop_workflow(
            session_id=payload.session_id,
            parsed_plan=parsed_plan,
            raw_message=payload.message,
            desktop_planner=desktop_planner,
            desktop_context=desktop_context,
            dispatcher=desktop_dispatcher,
        )

    if not runtime_flags.structured_execution_enabled(get_settings()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Structured execution is disabled",
        )
    try:
        structured_web_plan = await _plan_structured_web(
            parsed_plan=parsed_plan,
            detailed_plan=structured_plan,
            web_planner=web_planner,
            observer=web_observer,
        )
        if structured_web_plan is not None:
            reply = confirmation_reply(
                structured_web_plan,
                "web",
                intro="已生成结构化网页流程，请确认步骤后执行。",
            )
            _append_generation_turn(
                payload.session_id,
                structured_web_plan,
                None,
                reply=reply,
            )
            return _success_response(
                structured_web_plan,
                None,
                reply=reply,
            )

    except WebPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "structured_draft_failed",
                "message": str(exc),
            },
        ) from exc
    return _success_response(
        structured_web_plan,
        None,
    )


@router.post(
    "/modify",
    response_model=ChatResponse,
    dependencies=[Depends(require_llm_configuration)],
)
async def modify_workflow(
    payload: ChatModifyRequest,
    db: AsyncSession = Depends(get_db),
    step_planner: StepPlannerAgent = Depends(get_step_planner_agent),
    code_generator: CodeGeneratorAgent = Depends(get_code_generator_agent),
    web_planner: WebPlannerAgent = Depends(get_web_planner_agent),
    web_observer: BrowserObserver = Depends(get_browser_observer),
    desktop_planner: DesktopPlannerAgent = Depends(
        get_desktop_planner_agent
    ),
    desktop_context: DesktopExecutionContext = Depends(
        get_desktop_context
    ),
    current_user: User = Depends(get_current_user),
    workspace_context: WorkspaceContext = Depends(get_workspace_context),
    mcp_catalog: MCPToolCatalog = Depends(get_mcp_tool_catalog),
) -> ChatResponse:
    workflow = await db.get(Workflow, payload.workflow_id)
    if (
        workflow is None
        or workflow.user_id != current_user.id
        or (
            workspace_context.isolation_enabled
            and workflow.workspace_id != workspace_context.workspace_id
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    if workflow.execution_mode == "legacy_code":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Legacy code workflows are read-only; migrate the draft "
                "to structured execution before editing"
            ),
        )

    original_plan = workflow.structured_plan
    if not isinstance(original_plan, dict):
        original_plan = {}

    executor_type = workflow.executor_type or "web"
    planning_input = dict(original_plan)
    planning_input["modification"] = payload.modification

    history = session_contexts.setdefault(payload.session_id, [])
    history.append({"role": "user", "content": payload.modification})
    mcp_tools = await mcp_catalog.list_tools(
        user_id=current_user.id,
        workspace_id=(
            workspace_context.workspace_id
            if workspace_context.isolation_enabled
            else None
        ),
        limit=30,
        session=db,
    )

    if executor_type == "windows":
        try:
            structured_plan = await desktop_planner.plan(
                planning_input,
                desktop_context,
            )
        except DesktopPlanError as exc:
            message = str(exc)
            if message.startswith("该需求暂不支持"):
                reply = (
                    f"{message}。你可以换一种做法，"
                    "或者告诉我需要补上这个能力。"
                )
            else:
                reply = (
                    f"修改后的桌面流程没有通过校验：{message}。"
                    "请补充要操作的程序或界面控件名称后重试。"
                )
            _remember_reply(payload.session_id, reply, original_plan)
            return ChatResponse(
                reply=reply,
                structured_plan=original_plan,
                needs_confirmation=False,
                suggestions=["修改描述", "取消"],
                executor_type="windows",
            )
        generated_code = None
    elif (
        executor_type in {"mcp", "hybrid", "files", "transform"}
        or contains_mcp_steps(original_plan)
        or _uses_task_graph_v2(original_plan)
    ):
        try:
            structured_plan = (
                await step_planner.plan(
                    planning_input,
                    mcp_tools=mcp_tools,
                )
                if mcp_tools
                else await step_planner.plan(planning_input)
            )
            structured_plan = validate_mcp_plan(
                structured_plan,
                mcp_tools,
            )
            if _uses_task_graph_v2(structured_plan):
                structured_plan = validate_task_graph_plan(
                    structured_plan,
                    allowed_apps=desktop_context.allowed_apps,
                    allowed_doc_dirs=desktop_context.allowed_doc_dirs,
                )
        except MCPPlanError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"MCP 修改计划校验失败：{exc}",
            ) from exc
        except (DesktopPlanError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"复杂任务修改计划校验失败：{exc}",
            ) from exc
        executor_type = _mcp_executor_type(structured_plan)
        structured_plan["executor_type"] = executor_type
        structured_plan["needs_confirmation"] = True
        generated_code = None
    else:
        generated_code = None
        if not runtime_flags.structured_execution_enabled(get_settings()):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Structured execution is disabled",
            )
        try:
            structured_plan = await _plan_structured_web(
                parsed_plan=planning_input,
                detailed_plan=original_plan,
                web_planner=web_planner,
                observer=web_observer,
            )
        except WebPlanError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "structured_draft_failed",
                    "message": str(exc),
                },
            ) from exc

    workflow.structured_plan = structured_plan
    workflow.generated_code = generated_code
    workflow.executor_type = executor_type
    await db.commit()
    await db.refresh(workflow)

    _append_generation_turn(
        payload.session_id,
        structured_plan,
        generated_code,
    )
    return _success_response(
        structured_plan,
        generated_code,
        executor_type=executor_type,
    )


@router.post("/confirm")
async def confirm_workflow(
    payload: ChatConfirmRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, int | str]:
    workflow = await db.get(Workflow, payload.workflow_id)
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    workflow.status = "draft"
    await db.commit()
    session_contexts.pop(payload.session_id, None)
    return {"status": "confirmed", "workflow_id": payload.workflow_id}


@router.get(
    "/sessions",
    response_model=list[ChatSessionResponse],
)
async def list_chat_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ChatSessionResponse]:
    statement = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(
            ChatSession.updated_at.desc(),
            ChatSession.session_id,
        )
    )
    result = await db.execute(statement)
    return [
        ChatSessionResponse.model_validate(session)
        for session in result.scalars().all()
    ]


@router.put(
    "/sessions/{session_id}",
    response_model=ChatSessionResponse,
)
async def save_chat_session(
    session_id: str,
    payload: ChatSessionPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatSessionResponse:
    if not session_id.strip() or len(session_id) > 64:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="session_id must contain between 1 and 64 characters",
        )

    statement = select(ChatSession).where(
        ChatSession.session_id == session_id,
        ChatSession.user_id == current_user.id,
    )
    session = (await db.execute(statement)).scalar_one_or_none()
    updated_at = _utc_naive(payload.updated_at)
    if session is None:
        session = ChatSession(
            session_id=session_id,
            user_id=current_user.id,
        )
        db.add(session)
    elif updated_at <= session.updated_at:
        return ChatSessionResponse.model_validate(session)

    session.title = payload.title
    session.messages = list(payload.messages)
    session.structured_plan = payload.structured_plan
    session.generated_code = payload.generated_code
    session.needs_confirmation = payload.needs_confirmation
    session.active_workflow_id = payload.active_workflow_id
    session.initial_input = payload.initial_input
    session.updated_at = updated_at
    await db.commit()
    await db.refresh(session)
    return ChatSessionResponse.model_validate(session)


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    statement = select(ChatSession).where(
        ChatSession.session_id == session_id,
        ChatSession.user_id == current_user.id,
    )
    session = (await db.execute(statement)).scalar_one_or_none()
    if session is not None:
        await db.delete(session)
        await db.commit()
    return {"status": "deleted", "session_id": session_id}


__all__ = [
    "ChatConfirmRequest",
    "ChatModifyRequest",
    "get_code_generator_agent",
    "get_intent_parser_agent",
    "get_mcp_tool_catalog",
    "get_step_planner_agent",
    "router",
    "session_contexts",
]
