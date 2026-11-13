from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import runtime_flags
from app.agents.debug_agent import DebugAgent
from app.agents.intent_parser import IntentParserAgent
from app.agents.step_planner import StepPlannerAgent
from app.agents.web_planner import WebPlannerAgent
from app.agents.desktop_catalog import ACTION_TYPES as DESKTOP_ACTION_TYPES
from app.agents.mcp_plan import MCPPlanError, validate_mcp_plan
from app.agents.web_catalog import (
    WEB_ACTIONS,
    WebPlanError,
    validate_web_plan,
    web_action_catalog_text,
)
from app.api.dependencies import get_current_user
from app.api.observations import get_browser_observer
from app.api.workspace_deps import get_workspace_context
from app.config import get_settings
from app.database import AsyncSessionLocal, get_db
from app.engine.browser_observer import (
    BrowserObserver,
    ObservationError,
)
from app.engine.dag import (
    ExecutionPlanError,
    normalize_execution_plan,
    normalize_executor,
)
from app.engine.mcp_tool_catalog import MCPToolCatalog
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.protocols import (
    StructuredStep,
    classify_failure,
    parse_structured_steps,
)
from app.scheduler import SchedulerManager
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowResponse,
    WorkflowUpdate,
)
from app.schemas.workflow_version import (
    WorkflowVersionDiff,
    WorkflowPublishResponse,
    WorkflowRollbackRequest,
    WorkflowVersionResponse,
)
from app.schemas.workflow_authoring import (
    DraftFromGoalRequest,
    WorkflowDraftExecutionResponse,
    WorkflowDraftRunRequest,
    WorkflowDraftResult,
    WorkflowObserveRequest,
    WorkflowTestStepRequest,
    WorkflowValidateRequest,
)
from app.services.workflow_authoring import (
    WorkflowAuthoringError,
    WorkflowAuthoringService,
    WorkflowDraftRunner,
)
from app.services.workflow_versions import (
    WorkflowVersionError,
    WorkflowVersionNotFound,
    WorkflowVersionService,
)
from app.security.workspaces import (
    WorkspaceContext,
    apply_workspace_scope,
)

router = APIRouter(prefix="/api/workflows", tags=["流程管理"])


class WorkflowDuplicateRequest(BaseModel):
    new_name: str


class WorkflowStepRepairRequest(BaseModel):
    url: str | None = None
    error: str | None = None
    failure_category: str | None = None
    observation: dict[str, Any] | None = None


def require_step_repair_enabled() -> None:
    if not get_settings().STEP_LEVEL_REPAIR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "步骤级修复未启用，请先在设置中开启 "
                "STEP_LEVEL_REPAIR。"
            ),
        )


def get_debug_agent() -> DebugAgent:
    return DebugAgent(get_settings())


def get_workflow_authoring_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WorkflowAuthoringService:
    del db
    config = get_settings()
    return WorkflowAuthoringService(
        intent_parser=IntentParserAgent(config),
        step_planner=StepPlannerAgent(config),
        web_planner=WebPlannerAgent(config),
        observer=get_browser_observer(),
    )


def get_workflow_draft_runner(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WorkflowDraftRunner:
    session_factory = db.info.get("session_factory") or AsyncSessionLocal
    return WorkflowDraftRunner(
        engine=getattr(request.app.state, "engine", None),
        desktop_executor=getattr(
            request.app.state,
            "desktop_dispatcher",
            None,
        ),
        session_factory=session_factory,
        config=get_settings(),
        mcp_registry=getattr(request.app.state, "mcp_registry", None),
    )


def _validated_workflow_steps(workflow: Workflow) -> list[StructuredStep]:
    try:
        validated = validate_web_plan(workflow.structured_plan)
    except WebPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"流程不是有效的网页结构化计划：{exc}",
        ) from exc
    return parse_structured_steps(validated)


def _structured_web_publishable(workflow: Workflow) -> bool:
    """结构化网页流程在开关开启且计划合法时可以免代码发布。"""

    if not runtime_flags.structured_execution_enabled(get_settings()):
        return False
    try:
        validate_web_plan(workflow.structured_plan)
    except WebPlanError:
        return False
    return True


def _structured_plan_steps(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, dict):
        return []
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return []
    return [dict(step) for step in steps if isinstance(step, dict)]


def _plan_executors(workflow: Workflow) -> set[str]:
    default = workflow.executor_type or "web"
    return {
        normalize_executor(step.get("executor"), default=default)
        for step in _structured_plan_steps(workflow.structured_plan)
    }


def _mixed_plan_publishable(
    workflow: Workflow,
    mcp_tools: list[dict[str, Any]] | None = None,
) -> bool:
    """Validate web / desktop / MCP steps separately for hybrid plans."""

    plan = workflow.structured_plan
    try:
        nodes = normalize_execution_plan(
            plan,
            default_executor=workflow.executor_type or "web",
        )
    except ExecutionPlanError:
        return False

    web_steps: list[dict[str, Any]] = []
    saw_web = False
    saw_desktop = False
    saw_mcp = False
    for node in nodes:
        if node.executor == "windows":
            saw_desktop = True
            if node.action not in DESKTOP_ACTION_TYPES:
                return False
            continue
        if node.executor == "mcp":
            if not runtime_flags.resolve("mcp_client", get_settings()):
                return False
            saw_mcp = True
            continue
        if node.executor != "web":
            return False

        saw_web = True
        action = str(node.raw.get("action") or "").strip()
        action = "goto" if action == "navigate" else action
        if action not in WEB_ACTIONS:
            return False
        payload = {
            key: value
            for key, value in node.raw.items()
            if key != "depends_on"
        }
        payload.update(
            {
                "step_id": node.step_id,
                "executor": "web",
                "action": action,
                "depends_on": [],
            }
        )
        web_steps.append(payload)

    if not (saw_web or saw_desktop or saw_mcp):
        return False
    if saw_mcp:
        try:
            validate_mcp_plan(workflow.structured_plan, mcp_tools or [])
        except MCPPlanError:
            return False
    if saw_web:
        if not runtime_flags.structured_execution_enabled(get_settings()):
            return False
        try:
            validate_web_plan(
                {
                    "executor_type": "web",
                    "execution_mode": "structured",
                    "steps": web_steps,
                }
            )
        except WebPlanError:
            return False
    return True


def _workflow_publishable(
    workflow: Workflow,
    mcp_tools: list[dict[str, Any]] | None = None,
) -> bool:
    """Accept generated code, pure structured web, or hybrid plans."""

    has_code = bool((workflow.generated_code or "").strip())
    steps = _structured_plan_steps(workflow.structured_plan)
    if not steps:
        return has_code

    executors = _plan_executors(workflow)
    if not executors <= {"web", "windows", "mcp"}:
        return False
    if "mcp" in executors:
        # MCP 步骤只有在客户端开关打开时才允许发布。
        return _mixed_plan_publishable(workflow, mcp_tools)
    if executors == {"windows"}:
        return True
    if executors == {"web"}:
        return has_code or _structured_web_publishable(workflow)
    return _mixed_plan_publishable(workflow, mcp_tools)


def _step_index(steps: list[StructuredStep], step_id: int) -> int:
    for index, step in enumerate(steps):
        if step.step_id == step_id:
            return index
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"步骤不存在：{step_id}",
    )


def _infer_step_url(
    steps: list[StructuredStep],
    index: int,
) -> str | None:
    for step in reversed(steps[: index + 1]):
        if step.action != "goto":
            continue
        url = step.params.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


async def _get_owned_workflow(
    workflow_id: int,
    user_id: int,
    db: AsyncSession,
    workspace_id: int | None = None,
) -> Workflow:
    statement = select(Workflow).where(
        Workflow.id == workflow_id,
        Workflow.user_id == user_id,
    )
    if workspace_id is not None:
        statement = statement.where(
            Workflow.workspace_id == workspace_id
        )
    result = await db.execute(statement)
    workflow = result.scalar_one_or_none()
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    return workflow


def _ensure_draft_revision(
    workflow: Workflow,
    revision: int | None,
) -> None:
    if revision is None or revision == workflow.draft_revision:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "draft_revision_conflict",
            "expected": workflow.draft_revision,
            "received": revision,
        },
    )


def _draft_execution_response(
    workflow: Workflow,
    result: Any,
    *,
    mode: str,
) -> WorkflowDraftExecutionResponse:
    details = getattr(result, "details", None)
    if not isinstance(details, dict):
        details = {}
    variables = details.get("extracted")
    if not isinstance(variables, dict):
        variables = {}
    return WorkflowDraftExecutionResponse(
        workflow_id=workflow.id,
        draft_revision=workflow.draft_revision,
        mode=mode,
        success=bool(getattr(result, "success", False)),
        status="success" if getattr(result, "success", False) else "failed",
        message=str(getattr(result, "output", "") or ""),
        error=getattr(result, "error", None),
        duration_seconds=float(
            getattr(result, "duration_seconds", 0.0) or 0.0
        ),
        variables=variables,
        artifacts=[
            dict(item)
            for item in (getattr(result, "artifacts", None) or [])
            if isinstance(item, dict)
        ],
        step_logs=[
            dict(item)
            for item in (getattr(result, "step_logs", None) or [])
            if isinstance(item, dict)
        ],
        requirements={
            "mode": mode,
            "creates_version": False,
        },
    )


def _get_scheduler(request: Request) -> SchedulerManager:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler is not ready",
        )
    return scheduler


async def _invoke_scheduler(
    method_name: str,
    scheduler: SchedulerManager,
    *args: Any,
) -> None:
    method = getattr(scheduler, method_name, None)
    if not callable(method):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Scheduler does not support {method_name}",
        )

    result = method(*args)
    if inspect.isawaitable(result):
        await result


@router.post("", response_model=WorkflowResponse)
@router.post("/", response_model=WorkflowResponse, include_in_schema=False)
async def create_workflow(
    payload: WorkflowCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> Workflow:
    workflow = Workflow(
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id if context.isolation_enabled else None
        ),
        name=payload.name,
        description=payload.description,
        natural_language_input=payload.natural_language_input,
        executor_type=payload.executor_type,
        required_capabilities=payload.required_capabilities,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


@router.get("", response_model=list[WorkflowResponse])
@router.get(
    "/",
    response_model=list[WorkflowResponse],
    include_in_schema=False,
)
async def list_workflows(
    workflow_status: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> list[Workflow]:
    statement = select(Workflow).where(
        Workflow.user_id == current_user.id
    )
    statement = apply_workspace_scope(statement, Workflow, context)
    if workflow_status is not None:
        statement = statement.where(Workflow.status == workflow_status)
    statement = statement.order_by(
        Workflow.created_at.desc(),
        Workflow.id.desc(),
    )

    result = await db.execute(statement)
    return list(result.scalars().all())


@router.post(
    "/draft-from-goal",
    response_model=WorkflowDraftResult,
)
async def draft_workflow_from_goal(
    payload: DraftFromGoalRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
    service: WorkflowAuthoringService = Depends(
        get_workflow_authoring_service
    ),
) -> WorkflowDraftResult:
    base_plan = payload.base_plan
    if payload.workflow_id is not None:
        workflow = await _get_owned_workflow(
            payload.workflow_id,
            current_user.id,
            db,
            context.workspace_id if context.isolation_enabled else None,
        )
        base_plan = workflow.structured_plan or base_plan
    return await service.draft_from_goal(
        payload.goal,
        start_url=payload.start_url,
        session_id=payload.session_id,
        base_plan=base_plan,
        selected_step_id=payload.selected_step_id,
    )


@router.post("/{id}/validate")
async def validate_workflow_plan(
    id: int,
    payload: WorkflowValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
    service: WorkflowAuthoringService = Depends(
        get_workflow_authoring_service
    ),
) -> dict[str, Any]:
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    if (
        payload.draft_revision is not None
        and payload.draft_revision != workflow.draft_revision
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "draft_revision_conflict",
                "expected": workflow.draft_revision,
                "received": payload.draft_revision,
            },
        )
    plan = payload.plan or workflow.structured_plan or {}
    result = service.validate(
        plan,
        executor_type=workflow.executor_type or "web",
    )
    return {
        **result.model_dump(mode="json"),
        "workflow_id": workflow.id,
        "draft_revision": workflow.draft_revision,
        "requirements": {
            "mode": "test",
            "creates_version": False,
        },
    }


@router.post("/{id}/observe")
async def observe_workflow_page(
    id: int,
    payload: WorkflowObserveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
    service: WorkflowAuthoringService = Depends(
        get_workflow_authoring_service
    ),
) -> dict[str, Any]:
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    try:
        observation = await service.observe(
            payload.url,
            max_elements=payload.max_elements,
            include_accessibility=payload.include_accessibility,
        )
    except WorkflowAuthoringError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {
        "workflow_id": workflow.id,
        "observation": observation,
        "requirements": {
            "mode": "test",
            "creates_version": False,
        },
    }


@router.post(
    "/{id}/dry-run",
    response_model=WorkflowDraftExecutionResponse,
)
async def dry_run_workflow_draft(
    id: int,
    payload: WorkflowDraftRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
    service: WorkflowAuthoringService = Depends(
        get_workflow_authoring_service
    ),
    runner: WorkflowDraftRunner = Depends(get_workflow_draft_runner),
) -> WorkflowDraftExecutionResponse:
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    _ensure_draft_revision(workflow, payload.draft_revision)
    plan = service.normalize_plan(
        payload.plan,
        executor_type=workflow.executor_type or "web",
    )
    validation = service.validate(plan)
    if not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_draft",
                "valid": False,
                "errors": validation.errors,
            },
        )
    result = await runner.run(
        plan,
        mode="dry_run",
        context={
            "variables": payload.variables,
            "user_id": current_user.id,
            "workspace_id": workflow.workspace_id,
        },
    )
    return _draft_execution_response(
        workflow,
        result,
        mode="dry_run",
    )


@router.post(
    "/{id}/test-step",
    response_model=WorkflowDraftExecutionResponse,
)
async def test_workflow_draft_step(
    id: int,
    payload: WorkflowTestStepRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
    service: WorkflowAuthoringService = Depends(
        get_workflow_authoring_service
    ),
    runner: WorkflowDraftRunner = Depends(get_workflow_draft_runner),
) -> WorkflowDraftExecutionResponse:
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    _ensure_draft_revision(workflow, payload.draft_revision)
    plan = service.normalize_plan(
        payload.plan,
        executor_type=workflow.executor_type or "web",
    )
    validation = service.validate(plan)
    if not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_draft",
                "valid": False,
                "errors": validation.errors,
            },
        )
    selected: dict[str, Any] | None = None
    for raw in plan.get("steps") or []:
        if (
            isinstance(raw, dict)
            and int(raw.get("step_id") or 0) == payload.step_id
        ):
            selected = dict(raw)
            break
    if selected is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step not found: {payload.step_id}",
        )
    selected["depends_on"] = []
    single_plan = {
        **plan,
        "steps": [selected],
    }
    result = await runner.run(
        single_plan,
        mode="test",
        context={
            "variables": payload.variables,
            "user_id": current_user.id,
            "workspace_id": workflow.workspace_id,
        },
    )
    return _draft_execution_response(
        workflow,
        result,
        mode="test",
    )


@router.get("/{id}", response_model=WorkflowResponse)
async def get_workflow(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> Workflow:
    return await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )


@router.post("/{id}/steps/{step_id}/validate")
async def validate_workflow_step(
    id: int,
    step_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    """校验单个步骤是否在网页动作目录内。"""

    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    steps = _validated_workflow_steps(workflow)
    index = _step_index(steps, step_id)
    step = steps[index]
    return {
        "valid": True,
        "workflow_id": workflow.id,
        "step_id": step.step_id,
        "step_index": index,
        "step": step.model_dump(mode="json"),
        "executor": step.executor.value,
        "locator_candidates": (
            [
                candidate.model_dump(mode="json")
                for candidate in step.locator.candidates()
            ]
            if step.locator
            else []
        ),
    }


@router.post(
    "/{id}/steps/{step_id}/repair",
    dependencies=[Depends(require_step_repair_enabled)],
)
async def repair_workflow_step(
    id: int,
    step_id: int,
    payload: WorkflowStepRepairRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    debug_agent: DebugAgent = Depends(get_debug_agent),
    observer: BrowserObserver = Depends(get_browser_observer),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, Any]:
    """用真实页面观察重写失败步骤及其后续步骤，并写回流程。"""

    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    steps = _validated_workflow_steps(workflow)
    index = _step_index(steps, step_id)

    observation = payload.observation
    if observation is None:
        url = payload.url or _infer_step_url(steps, index)
        if not url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "需要提供 url 或 observation 才能重新观察页面"
                ),
            )
        if not get_settings().OBSERVE_BEFORE_PLAN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "重新观察页面需要开启 OBSERVE_BEFORE_PLAN，"
                    "或在请求中直接提供 observation。"
                ),
            )
        try:
            snapshot = await observer.observe(
                url,
                wait_until="domcontentloaded",
                timeout_ms=30000,
                max_elements=80,
                include_accessibility=True,
            )
        except ObservationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": str(exc),
                    "failure_category": classify_failure(
                        str(exc)
                    ).value,
                },
            ) from exc
        observation = snapshot.compact(max_elements=80)

    step = steps[index]
    error = payload.error or "未找到元素：步骤定位失败"
    failure = {
        "step_index": index,
        "step_id": step.step_id,
        "action": step.action,
        "error": error,
        "failure_category": (
            payload.failure_category
            or classify_failure(error).value
        ),
        "attempts": 1,
        "locator": (
            step.locator.to_payload() if step.locator else None
        ),
    }
    plan_payload = {
        "executor_type": "web",
        "execution_mode": "structured",
        "steps": [item.model_dump(mode="json") for item in steps],
    }

    try:
        repaired_steps = await debug_agent.repair_steps(
            plan=plan_payload,
            step_index=index,
            failure=failure,
            observation=observation,
            action_catalog=web_action_catalog_text(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"修复规划失败：{exc}",
        ) from exc

    merged_payload = [
        item.model_dump(mode="json") for item in steps[:index]
    ] + list(repaired_steps)
    try:
        merged = validate_web_plan(
            {
                "executor_type": "web",
                "execution_mode": "structured",
                "steps": merged_payload,
            }
        )
    except WebPlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"修复结果未通过校验：{exc}",
        ) from exc

    merged_steps = parse_structured_steps(merged)
    workflow.structured_plan = merged
    workflow.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(workflow)
    return {
        "status": "repaired",
        "workflow_id": workflow.id,
        "step_id": step_id,
        "repaired_from": index,
        "steps": [
            item.model_dump(mode="json") for item in merged_steps
        ],
    }


@router.put("/{id}", response_model=WorkflowResponse)
async def update_workflow(
    id: int,
    payload: WorkflowUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> Workflow:
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    updates = payload.model_dump(exclude_unset=True)
    expected_revision = updates.pop("draft_revision", None)
    _ensure_draft_revision(workflow, expected_revision)
    draft_fields = {
        "structured_plan",
        "generated_code",
        "dag_definition",
        "execution_policy",
        "executor_type",
        "required_capabilities",
        "schedule_timezone",
    }
    changes_draft = bool(set(updates) & draft_fields)
    for field, value in updates.items():
        setattr(workflow, field, value)
    if changes_draft:
        workflow.draft_revision = int(workflow.draft_revision or 0) + 1
    workflow.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(workflow)
    return workflow


@router.delete("/{id}")
async def delete_workflow(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, int | str]:
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    await db.execute(
        delete(ExecutionLog).where(ExecutionLog.workflow_id == workflow.id)
    )
    await db.delete(workflow)
    await db.commit()
    return {"status": "deleted", "workflow_id": id}


@router.post(
    "/{id}/publish",
    response_model=WorkflowPublishResponse,
)
async def publish_workflow(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> WorkflowPublishResponse:
    mcp_tools: list[dict[str, Any]] = []
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    if "mcp" in _plan_executors(workflow):
        if not runtime_flags.resolve("mcp_client", get_settings()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MCP client is disabled",
            )
        registry = getattr(request.app.state, "mcp_registry", None)
        if registry is not None:
            catalog = MCPToolCatalog(
                registry,
                max_tools=int(
                    getattr(
                        get_settings(),
                        "MCP_CATALOG_MAX_TOOLS",
                        50,
                    )
                    or 50
                ),
            )
            mcp_tools = await catalog.list_tools(
                user_id=current_user.id,
                workspace_id=(
                    context.workspace_id
                    if context.isolation_enabled
                    else None
                ),
                limit=50,
                session=db,
            )
        try:
            validate_mcp_plan(
                workflow.structured_plan or {},
                mcp_tools,
            )
        except MCPPlanError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
    service = WorkflowVersionService(db)
    try:
        version = await service.publish(
            id,
            current_user.id,
            workspace_id=(
                context.workspace_id
                if context.isolation_enabled
                else None
            ),
        )
    except WorkflowVersionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except WorkflowVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if workflow.trigger_type == "cron":
        await _invoke_scheduler(
            "schedule_workflow",
            _get_scheduler(request),
            workflow,
        )
    await db.refresh(workflow)
    return WorkflowPublishResponse(
        status="published",
        workflow_id=id,
        active_version_id=int(version.id),
        version=version.version,
        action_catalog_version=version.action_catalog_version,
    )


@router.get("/{id}/versions")
async def list_workflow_versions(
    id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, list[WorkflowVersionResponse]]:
    service = WorkflowVersionService(db)
    try:
        versions = await service.list(
            id,
            current_user.id,
            workspace_id=(
                context.workspace_id
                if context.isolation_enabled
                else None
            ),
        )
    except WorkflowVersionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return {
        "items": [
            WorkflowVersionResponse.model_validate(version)
            for version in versions
        ]
    }


@router.get(
    "/{id}/versions/diff",
    response_model=WorkflowVersionDiff,
)
async def diff_workflow_versions(
    id: int,
    from_version_id: int = Query(gt=0),
    to_version_id: int = Query(gt=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> WorkflowVersionDiff:
    try:
        return await WorkflowVersionService(db).diff(
            id,
            current_user.id,
            from_version_id,
            to_version_id,
            workspace_id=(
                context.workspace_id
                if context.isolation_enabled
                else None
            ),
        )
    except WorkflowVersionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{id}/rollback",
    response_model=WorkflowPublishResponse,
)
async def rollback_workflow(
    id: int,
    payload: WorkflowRollbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> WorkflowPublishResponse:
    service = WorkflowVersionService(db)
    try:
        version = await service.rollback(
            id,
            current_user.id,
            payload.version_id,
            workspace_id=(
                context.workspace_id
                if context.isolation_enabled
                else None
            ),
        )
    except WorkflowVersionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except WorkflowVersionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return WorkflowPublishResponse(
        status="published",
        workflow_id=id,
        active_version_id=int(version.id),
        version=version.version,
        action_catalog_version=version.action_catalog_version,
    )


@router.post("/{id}/unpublish")
async def unpublish_workflow(
    id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> dict[str, int | str]:
    workflow = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    workflow.status = "draft"
    if workflow.trigger_type == "cron":
        await _invoke_scheduler(
            "unschedule_workflow",
            _get_scheduler(request),
            workflow.id,
        )
    await db.commit()
    await db.refresh(workflow)
    return {"status": "unpublished", "workflow_id": id}


@router.post("/{id}/duplicate", response_model=WorkflowResponse)
async def duplicate_workflow(
    id: int,
    payload: WorkflowDuplicateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(get_workspace_context),
) -> Workflow:
    original = await _get_owned_workflow(
        id,
        current_user.id,
        db,
        context.workspace_id if context.isolation_enabled else None,
    )
    duplicate = Workflow(
        user_id=current_user.id,
        workspace_id=(
            context.workspace_id
            if context.isolation_enabled
            else original.workspace_id
        ),
        name=payload.new_name,
        description=original.description,
        natural_language_input=original.natural_language_input,
        structured_plan=original.structured_plan,
        generated_code=original.generated_code,
        dag_definition=original.dag_definition,
        trigger_type=original.trigger_type,
        cron_expression=original.cron_expression,
        status="draft",
        notification_config=original.notification_config,
    )
    db.add(duplicate)
    await db.commit()
    await db.refresh(duplicate)
    return duplicate


__all__ = [
    "WorkflowDuplicateRequest",
    "get_workflow_authoring_service",
    "router",
]
