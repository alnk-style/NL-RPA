# Desktop Executor and Desktop Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Windows desktop execution path and desktop client without breaking the existing browser automation flow.

**Architecture:** Keep the existing FastAPI, React, SQLite, APScheduler, and Playwright web executor. Add an execution router, a paired Windows Agent connected by WebSocket, a capability-based desktop action protocol, a WPS COM adapter, and a Tauri desktop shell that can run locally or connect to a remote backend.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, SQLite, APScheduler, Pydantic, pytest, React 18, TypeScript, Vite, Tauri 2, pywin32, pywinauto, Pillow, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-09-16-desktop-executor-design.md`

## Global Constraints

- Existing workflows must continue to use `executor_type="web"` by default.
- Existing browser tests must remain green after every task.
- The desktop Agent must never execute arbitrary Python, PowerShell, CMD, or shell text.
- Every desktop action must be represented by a typed action model and a runtime allowlist check.
- The desktop Agent must run in the interactive Windows user session.
- WPS automation must prefer COM over UI clicks.
- Local mode may use `ws://127.0.0.1`; remote mode must use TLS.
- The current workspace is not a Git repository. Run `git init` before Task 1 if commit history is required.
- Backend verification command: `C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q`.
- Frontend verification commands: `cmd /c "npm test"` and `cmd /c "npm run build"` from `frontend/`.

---

## Task 0: Initialize Git and Baseline

**Files:**
- Create: `.gitignore`

**Interfaces:**
- Consumes: existing project tree.
- Produces: a Git repository so later tasks can commit independently.

- [ ] **Step 1: Add runtime artifacts to `.gitignore`**

```gitignore
.env
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
rpa.db
logs/*.log
logs/*.err.log
frontend/node_modules/
frontend/dist/
desktop/src-tauri/target/
desktop/dist/
```

- [ ] **Step 2: Run repository initialization**

Run:

```bash
git init
git add .
git commit -m "chore: baseline NL-RPA platform"
```

Expected: one initial commit containing the current source and documentation.

---

## Task 1: Add Workflow Executor Metadata

**Files:**
- Modify: `app/models/workflow.py`
- Modify: `app/schemas/workflow.py`
- Modify: `frontend/src/types/workflow.ts`
- Test: `tests/test_api_4b.py`

**Interfaces:**
- Consumes: existing `Workflow` model and workflow API.
- Produces:
  - `Workflow.executor_type: str`
  - `Workflow.required_capabilities: list[str]`
  - `Workflow.preferred_agent_id: int | None`
  - `WorkflowCreate.executor_type: str`
  - `WorkflowResponse.executor_type: str`

- [ ] **Step 1: Write the failing API test**

Add to `tests/test_api_4b.py`:

```python
def test_workflow_defaults_to_web_executor() -> None:
    async def scenario() -> dict:
        session, engine = await _database()
        try:
            user = await _seed_user(session)
            response = await _request(
                _workflow_app(session, user),
                "POST",
                "/api/workflows",
                json={
                    "name": "Browser flow",
                    "description": "Runs in Playwright",
                    "natural_language_input": "Open example.com",
                },
            )
            assert response.status_code == 200
            return response.json()
        finally:
            await session.close()
            await engine.dispose()

    payload = asyncio.run(scenario())

    assert payload["executor_type"] == "web"
    assert payload["required_capabilities"] == []
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_api_4b.py::test_workflow_defaults_to_web_executor -q
```

Expected: FAIL because `executor_type` does not exist.

- [ ] **Step 3: Add model fields**

In `app/models/workflow.py`, add:

```python
executor_type: Mapped[str] = mapped_column(
    String(20),
    default="web",
    nullable=False,
    index=True,
)
required_capabilities: Mapped[list[str] | None] = mapped_column(
    JSON,
    default=list,
    nullable=True,
)
preferred_agent_id: Mapped[int | None] = mapped_column(
    Integer,
    nullable=True,
)
```

- [ ] **Step 4: Add schema fields**

In `app/schemas/workflow.py`:

```python
class WorkflowCreate(BaseModel):
    name: str
    description: str | None = None
    natural_language_input: str
    executor_type: str = "web"
    required_capabilities: list[str] = Field(default_factory=list)
    preferred_agent_id: int | None = None


class WorkflowResponse(BaseModel):
    # Keep all existing fields.
    executor_type: str
    required_capabilities: list[str] | None
    preferred_agent_id: int | None
```

- [ ] **Step 5: Copy fields during create and duplicate**

In `app/api/workflows.py`:

```python
workflow = Workflow(
    user_id=current_user.id,
    name=payload.name,
    description=payload.description,
    natural_language_input=payload.natural_language_input,
    executor_type=payload.executor_type,
    required_capabilities=payload.required_capabilities,
    preferred_agent_id=payload.preferred_agent_id,
)
```

For duplicate:

```python
duplicate = Workflow(
    # Keep existing copied fields.
    executor_type=original.executor_type,
    required_capabilities=original.required_capabilities,
    preferred_agent_id=original.preferred_agent_id,
)
```

- [ ] **Step 6: Add frontend type fields**

In `frontend/src/types/workflow.ts`:

```ts
export type ExecutorType = 'web' | 'windows';

export interface WorkflowRecord {
  // Keep all existing fields.
  executor_type: ExecutorType;
  required_capabilities: string[] | null;
  preferred_agent_id: number | null;
}

export interface CreateWorkflowPayload {
  // Keep all existing fields.
  executor_type?: ExecutorType;
  required_capabilities?: string[];
  preferred_agent_id?: number | null;
}
```

- [ ] **Step 7: Run tests**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q
cd frontend && cmd /c "npm run build"
```

Expected: all tests and the web build pass.

- [ ] **Step 8: Commit**

```bash
git add app/models/workflow.py app/schemas/workflow.py app/api/workflows.py frontend/src/types/workflow.ts tests/test_api_4b.py
git commit -m "feat: add workflow executor metadata"
```

---

## Task 2: Define Desktop Actions and Compiler

**Files:**
- Create: `app/desktop/__init__.py`
- Create: `app/desktop/actions.py`
- Create: `app/desktop/compiler.py`
- Test: `tests/test_desktop_actions.py`

**Interfaces:**
- Consumes: `Workflow.structured_plan`.
- Produces:
  - `DesktopAction`
  - `DesktopActionPlan`
  - `compile_desktop_plan(structured_plan: dict) -> DesktopActionPlan`
  - `validate_desktop_actions(actions: list[DesktopAction]) -> None`

- [ ] **Step 1: Write action validation tests**

Create `tests/test_desktop_actions.py`:

```python
from app.desktop.actions import DesktopAction, DesktopActionPlan
from app.desktop.compiler import compile_desktop_plan


def test_compile_launch_and_screenshot_steps() -> None:
    plan = compile_desktop_plan(
        {
            "steps": [
                {
                    "step_id": 1,
                    "action": "launch_app",
                    "params": {
                        "path": r"C:\Windows\System32\notepad.exe",
                        "args": [],
                    },
                },
                {
                    "step_id": 2,
                    "action": "screenshot",
                    "params": {"name": "notepad-open"},
                },
            ]
        }
    )

    assert isinstance(plan, DesktopActionPlan)
    assert [action.type for action in plan.actions] == [
        "launch_app",
        "screenshot",
    ]
    assert isinstance(plan.actions[0], DesktopAction)


def test_compile_rejects_unknown_action() -> None:
    try:
        compile_desktop_plan(
            {
                "steps": [
                    {
                        "step_id": 1,
                        "action": "run_powershell",
                        "params": {},
                    }
                ]
            }
        )
    except ValueError as exc:
        assert "Unsupported desktop action" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
```

- [ ] **Step 2: Run the tests and confirm failure**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_desktop_actions.py -q
```

Expected: FAIL because `app.desktop` does not exist.

- [ ] **Step 3: Implement action models**

Create `app/desktop/actions.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DesktopActionType = Literal[
    "launch_app",
    "activate_window",
    "open_document",
    "click_element",
    "type_text",
    "press_hotkey",
    "wait",
    "screenshot",
    "read_text",
    "save_document",
    "export_pdf",
    "close_app",
]


class DesktopAction(BaseModel):
    type: DesktopActionType
    params: dict[str, Any] = Field(default_factory=dict)


class DesktopActionPlan(BaseModel):
    workflow_id: int | None = None
    execution_log_id: int | None = None
    timeout_seconds: int = 300
    actions: list[DesktopAction]
```

- [ ] **Step 4: Implement the compiler**

Create `app/desktop/compiler.py`:

```python
from __future__ import annotations

from typing import Any

from app.desktop.actions import DesktopAction, DesktopActionPlan


def compile_desktop_plan(structured_plan: dict[str, Any]) -> DesktopActionPlan:
    actions: list[DesktopAction] = []
    allowed = set(DesktopAction.model_fields["type"].annotation.__args__)

    for step in structured_plan.get("steps", []):
        action_type = str(step.get("action", "")).strip()
        if action_type not in allowed:
            raise ValueError(
                f"Unsupported desktop action: {action_type}"
            )
        params = step.get("params")
        actions.append(
            DesktopAction(
                type=action_type,
                params=params if isinstance(params, dict) else {},
            )
        )

    if not actions:
        raise ValueError("Desktop workflow contains no actions")

    return DesktopActionPlan(actions=actions)
```

- [ ] **Step 5: Add package export**

Create `app/desktop/__init__.py`:

```python
from app.desktop.actions import DesktopAction, DesktopActionPlan
from app.desktop.compiler import compile_desktop_plan

__all__ = [
    "DesktopAction",
    "DesktopActionPlan",
    "compile_desktop_plan",
]
```

- [ ] **Step 6: Run tests**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_desktop_actions.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/desktop tests/test_desktop_actions.py
git commit -m "feat: add desktop action schema and compiler"
```

---

## Task 3: Add Agent Registry and Pairing API

**Files:**
- Create: `app/models/desktop_agent.py`
- Modify: `app/models/__init__.py`
- Create: `app/desktop/registry.py`
- Create: `app/api/agents.py`
- Modify: `app/api/__init__.py`
- Test: `tests/test_desktop_agents.py`

**Interfaces:**
- Consumes: authenticated `User`, database session.
- Produces:
  - `DesktopAgent`
  - `hash_agent_token(token: str) -> str`
  - `validate_pairing_code(value: str) -> None`
  - `POST /api/agents/pair`
  - `GET /api/agents`

- [ ] **Step 1: Write pairing and listing tests**

Create `tests/test_desktop_agents.py`:

```python
import asyncio
from types import SimpleNamespace

from fastapi import FastAPI

from app.api.agents import router as agents_router
from app.database import Base, get_db
from app.models.user import User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory(), engine


def test_pair_and_list_agent() -> None:
    async def scenario() -> tuple[int, dict, int]:
        session, engine = await _database()
        try:
            user = User(
                id=1,
                username="dev",
                email="dev@example.com",
                hashed_password="hash",
            )
            session.add(user)
            await session.commit()
            app = FastAPI()
            app.include_router(agents_router)

            async def override_db():
                yield session

            async def override_user():
                return user

            from app.api.dependencies import get_current_user

            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[get_current_user] = override_user

            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                pair = await client.post(
                    "/api/agents/pair",
                    json={
                        "pairing_code": "123456",
                        "name": "DESKTOP-01",
                        "platform": "windows",
                        "version": "0.1.0",
                        "capabilities": ["window", "wps"],
                    },
                )
                listing = await client.get("/api/agents")
            return pair.status_code, pair.json(), listing.status_code
        finally:
            await session.close()
            await engine.dispose()

    pair_status, payload, list_status = asyncio.run(scenario())

    assert pair_status == 200
    assert payload["agent_id"] == 1
    assert len(payload["token"]) >= 32
    assert list_status == 200
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_desktop_agents.py -q
```

Expected: FAIL because the model and route do not exist.

- [ ] **Step 3: Implement the model**

Create `app/models/desktop_agent.py`:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DesktopAgent(Base):
    __tablename__ = "desktop_agent"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), default="windows")
    version: Mapped[str] = mapped_column(String(30), default="0.1.0")
    capabilities: Mapped[list[str] | None] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="offline")
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
```

- [ ] **Step 4: Export the model**

Modify `app/models/__init__.py`:

```python
from app.models.desktop_agent import DesktopAgent

__all__ = [
    # Keep existing exports.
    "DesktopAgent",
]
```

- [ ] **Step 5: Implement registry helpers**

Create `app/desktop/registry.py`:

```python
from __future__ import annotations

import hashlib

PAIRING_CODE = "123456"


def hash_agent_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_pairing_code(value: str) -> None:
    if value != PAIRING_CODE:
        raise PermissionError("Invalid pairing code")
```

- [ ] **Step 6: Implement pairing and listing routes**

Create `app/api/agents.py`:

```python
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.database import get_db
from app.desktop.registry import hash_agent_token, validate_pairing_code
from app.models.desktop_agent import DesktopAgent
from app.models.user import User

router = APIRouter(prefix="/api/agents", tags=["agents"])


class PairAgentRequest(BaseModel):
    pairing_code: str
    name: str
    platform: str = "windows"
    version: str = "0.1.0"
    capabilities: list[str] = Field(default_factory=list)


@router.post("/pair")
async def pair_agent(
    payload: PairAgentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int | str]:
    try:
        validate_pairing_code(payload.pairing_code)
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail="Invalid pairing code",
        ) from exc

    token = secrets.token_urlsafe(32)
    agent = DesktopAgent(
        user_id=current_user.id,
        name=payload.name,
        platform=payload.platform,
        version=payload.version,
        capabilities=payload.capabilities,
        token_hash=hash_agent_token(token),
        status="offline",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return {"agent_id": agent.id, "token": token}


@router.get("")
async def list_agents(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    result = await db.execute(
        select(DesktopAgent)
        .where(DesktopAgent.user_id == current_user.id)
        .order_by(DesktopAgent.id.desc())
    )
    return [
        {
            "id": agent.id,
            "name": agent.name,
            "platform": agent.platform,
            "version": agent.version,
            "capabilities": agent.capabilities or [],
            "status": agent.status,
            "last_seen_at": agent.last_seen_at,
        }
        for agent in result.scalars().all()
    ]
```

- [ ] **Step 7: Register the router**

Modify `app/api/__init__.py`:

```python
from app.api.agents import router as agents_router

__all__ = [
    "agents_router",
    # Keep existing exports.
]
```

Modify `app/main.py`:

```python
from app.api import agents_router

app.include_router(agents_router)
```

- [ ] **Step 8: Run tests**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_desktop_agents.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/models/desktop_agent.py app/models/__init__.py app/desktop/registry.py app/api/agents.py app/api/__init__.py app/main.py tests/test_desktop_agents.py
git commit -m "feat: add desktop agent pairing registry"
```

---

## Task 4: Add Desktop Tasks, WebSocket Hub, and Dispatcher

**Files:**
- Create: `app/models/desktop_task.py`
- Modify: `app/models/__init__.py`
- Create: `app/desktop/hub.py`
- Create: `app/desktop/dispatcher.py`
- Create: `app/api/agent_ws.py`
- Modify: `app/main.py`
- Test: `tests/test_desktop_dispatcher.py`

**Interfaces:**
- Consumes: `DesktopAgent`, `DesktopActionPlan`, authenticated WebSocket.
- Produces:
  - `DesktopTask`
  - `AgentHub.connect(agent_id, websocket)`
  - `AgentHub.send(agent_id, message)`
  - `DesktopDispatcher.dispatch(agent_id, plan) -> dict`
  - WebSocket `/api/agents/ws`

- [ ] **Step 1: Write dispatcher tests with a fake connection**

Create `tests/test_desktop_dispatcher.py`:

```python
import asyncio

from app.desktop.actions import DesktopAction, DesktopActionPlan
from app.desktop.dispatcher import DesktopDispatcher


class FakeHub:
    def __init__(self) -> None:
        self.sent = []
        self.complete = asyncio.Event()

    async def send(self, agent_id: int, message: dict) -> None:
        self.sent.append((agent_id, message))
        await self.complete.wait()

    def complete_task(self, result: dict) -> None:
        self.result = result
        self.complete.set()

    def get_result(self, task_id: int) -> dict:
        return self.result


def test_dispatch_waits_for_agent_result() -> None:
    async def scenario() -> dict:
        hub = FakeHub()
        dispatcher = DesktopDispatcher(hub)
        plan = DesktopActionPlan(
            timeout_seconds=5,
            actions=[
                DesktopAction(
                    type="screenshot",
                    params={"name": "desktop"},
                )
            ],
        )

        task = asyncio.create_task(dispatcher.dispatch(1, plan))
        await asyncio.sleep(0)
        task_id = hub.sent[0][1]["task_id"]
        hub.complete_task(
            {
                "task_id": task_id,
                "success": True,
                "output": "ok",
                "error": None,
            }
        )
        return await task

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert result["output"] == "ok"
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_desktop_dispatcher.py -q
```

Expected: FAIL because the dispatcher does not exist.

- [ ] **Step 3: Implement the DesktopTask model**

Create `app/models/desktop_task.py`:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DesktopTask(Base):
    __tablename__ = "desktop_task"

    id: Mapped[int] = mapped_column(primary_key=True)
    execution_log_id: Mapped[int] = mapped_column(
        ForeignKey("execution_log.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflow.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("desktop_agent.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

- [ ] **Step 4: Implement the WebSocket hub**

Create `app/desktop/hub.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class AgentHub:
    def __init__(self) -> None:
        self._connections: dict[int, WebSocket] = {}
        self._results: dict[int, dict[str, Any]] = {}
        self._events: dict[int, asyncio.Event] = {}

    async def connect(self, agent_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[agent_id] = websocket

    def disconnect(self, agent_id: int) -> None:
        self._connections.pop(agent_id, None)

    async def send(self, agent_id: int, message: dict[str, Any]) -> None:
        websocket = self._connections.get(agent_id)
        if websocket is None:
            raise RuntimeError(f"Agent {agent_id} is offline")
        await websocket.send_json(message)

    def wait_event(self, task_id: int) -> asyncio.Event:
        event = asyncio.Event()
        self._events[task_id] = event
        return event

    def complete_task(self, task_id: int, result: dict[str, Any]) -> None:
        self._results[task_id] = result
        event = self._events.get(task_id)
        if event is not None:
            event.set()

    def get_result(self, task_id: int) -> dict[str, Any]:
        return self._results.pop(task_id)


agent_hub = AgentHub()
```

- [ ] **Step 5: Implement the dispatcher**

Create `app/desktop/dispatcher.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from app.desktop.actions import DesktopActionPlan


class HubProtocol(Protocol):
    def wait_event(self, task_id: int) -> asyncio.Event: ...

    async def send(self, agent_id: int, message: dict[str, Any]) -> None: ...

    def get_result(self, task_id: int) -> dict[str, Any]: ...

    def complete_task(self, task_id: int, result: dict[str, Any]) -> None: ...


class DesktopDispatcher:
    def __init__(self, hub: HubProtocol) -> None:
        self.hub = hub
        self._next_task_id = 1

    async def dispatch(
        self,
        agent_id: int,
        plan: DesktopActionPlan,
    ) -> dict[str, Any]:
        task_id = self._next_task_id
        self._next_task_id += 1
        event = self.hub.wait_event(task_id)
        await self.hub.send(
            agent_id,
            {
                "type": "desktop.task",
                "task_id": task_id,
                "timeout_seconds": plan.timeout_seconds,
                "actions": [
                    action.model_dump() for action in plan.actions
                ],
            },
        )
        await asyncio.wait_for(event.wait(), timeout=plan.timeout_seconds)
        return self.hub.get_result(task_id)
```

- [ ] **Step 6: Implement the WebSocket route**

Create `app/api/agent_ws.py`:

```python
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.agents import hash_agent_token
from app.database import get_db
from app.desktop.hub import agent_hub
from app.models.desktop_agent import DesktopAgent

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.websocket("/ws")
async def agent_websocket(
    websocket: WebSocket,
    agent_id: int,
    token: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(DesktopAgent).where(DesktopAgent.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None or agent.token_hash != hash_agent_token(token):
        await websocket.close(code=1008)
        return

    agent.status = "online"
    agent.last_seen_at = datetime.utcnow()
    await db.commit()
    await agent_hub.connect(agent_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "desktop.completed":
                agent_hub.complete_task(
                    int(message["task_id"]),
                    message,
                )
    except WebSocketDisconnect:
        pass
    finally:
        agent_hub.disconnect(agent_id)
        agent.status = "offline"
        agent.last_seen_at = datetime.utcnow()
        await db.commit()
```

- [ ] **Step 7: Register the router**

Modify `app/main.py`:

```python
from app.api.agent_ws import router as agent_ws_router

app.include_router(agent_ws_router)
```

- [ ] **Step 8: Run tests**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_desktop_dispatcher.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/models/desktop_task.py app/models/__init__.py app/desktop/hub.py app/desktop/dispatcher.py app/api/agent_ws.py app/main.py tests/test_desktop_dispatcher.py
git commit -m "feat: add desktop task dispatcher and agent websocket"
```

---

## Task 5: Route Workflows to Web or Desktop Execution

**Files:**
- Create: `app/engine/router.py`
- Modify: `app/engine/engine.py`
- Modify: `app/scheduler/jobs.py`
- Modify: `app/api/executions.py`
- Test: `tests/test_execution_router.py`

**Interfaces:**
- Consumes: `Workflow.executor_type`, `DesktopDispatcher`, `compile_desktop_plan`.
- Produces:
  - `ExecutionRouter.execute(workflow, context, execution_log_id) -> ExecutionResult`

- [ ] **Step 1: Write routing tests**

Create `tests/test_execution_router.py`:

```python
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.engine.router import ExecutionRouter
from app.engine.runner import ExecutionResult


def _result() -> ExecutionResult:
    return ExecutionResult(
        success=True,
        output="ok",
        error=None,
        screenshots=[],
        step_logs=[],
        duration_seconds=0.1,
    )


def test_router_uses_web_executor() -> None:
    web = SimpleNamespace(execute=AsyncMock(return_value=_result()))
    desktop = SimpleNamespace(execute=AsyncMock(return_value=_result()))
    router = ExecutionRouter(web, desktop)
    workflow = SimpleNamespace(executor_type="web")

    result = asyncio.run(router.execute(workflow, {}, 1))

    assert result.success is True
    web.execute.assert_awaited_once()
    desktop.execute.assert_not_awaited()


def test_router_uses_desktop_executor() -> None:
    web = SimpleNamespace(execute=AsyncMock(return_value=_result()))
    desktop = SimpleNamespace(execute=AsyncMock(return_value=_result()))
    router = ExecutionRouter(web, desktop)
    workflow = SimpleNamespace(executor_type="windows")

    result = asyncio.run(router.execute(workflow, {}, 1))

    assert result.success is True
    desktop.execute.assert_awaited_once()
    web.execute.assert_not_awaited()
```

- [ ] **Step 2: Run the tests and confirm failure**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/test_execution_router.py -q
```

Expected: FAIL because the router does not exist.

- [ ] **Step 3: Implement the router**

Create `app/engine/router.py`:

```python
from __future__ import annotations

from typing import Any

from app.engine.runner import ExecutionResult


class ExecutionRouter:
    def __init__(self, web_executor: Any, desktop_executor: Any) -> None:
        self.web_executor = web_executor
        self.desktop_executor = desktop_executor

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any],
        execution_log_id: int,
    ) -> ExecutionResult:
        if workflow.executor_type == "windows":
            return await self.desktop_executor.execute(
                workflow,
                context,
                execution_log_id,
            )
        return await self.web_executor.execute(workflow, context)
```

- [ ] **Step 4: Keep the existing engine as the web executor**

Do not change `ExecutionEngine.execute` behavior. Rename its role in documentation only. Existing tests continue to treat it as the web executor.

- [ ] **Step 5: Add a desktop executor skeleton**

Create `app/desktop/executor.py`:

```python
from __future__ import annotations

from typing import Any

from app.desktop.compiler import compile_desktop_plan
from app.desktop.dispatcher import DesktopDispatcher
from app.engine.runner import ExecutionResult


class DesktopExecutor:
    def __init__(
        self,
        dispatcher: DesktopDispatcher,
    ) -> None:
        self.dispatcher = dispatcher

    async def execute(
        self,
        workflow: Any,
        context: dict[str, Any],
        execution_log_id: int,
    ) -> ExecutionResult:
        plan = compile_desktop_plan(workflow.structured_plan or {})
        plan.workflow_id = workflow.id
        plan.execution_log_id = execution_log_id
        agent_id = context.get("agent_id") or workflow.preferred_agent_id
        if not isinstance(agent_id, int):
            raise ValueError(
                "Desktop workflow requires context['agent_id'] "
                "or Workflow.preferred_agent_id"
            )
        result = await self.dispatcher.dispatch(agent_id, plan)
        return ExecutionResult(
            success=bool(result.get("success")),
            output=str(result.get("output", "")),
            error=result.get("error"),
            screenshots=list(result.get("screenshots", [])),
            step_logs=list(result.get("step_logs", [])),
            duration_seconds=float(result.get("duration_seconds", 0)),
        )
```

- [ ] **Step 6: Wire router construction at application startup**

In `app/main.py` startup:

```python
from app.desktop.dispatcher import DesktopDispatcher
from app.desktop.executor import DesktopExecutor
from app.desktop.hub import agent_hub
from app.engine.router import ExecutionRouter

web_engine = ExecutionEngine(config)
desktop_executor = DesktopExecutor(
    DesktopDispatcher(agent_hub),
)
app.state.engine = ExecutionRouter(web_engine, desktop_executor)
```

- [ ] **Step 7: Run tests**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q
```

Expected: all existing tests plus router tests pass.

- [ ] **Step 8: Commit**

```bash
git add app/engine/router.py app/desktop/executor.py app/main.py tests/test_execution_router.py
git commit -m "feat: route workflows to web or desktop executors"
```

---

## Task 6: Build the Windows Agent MVP

**Files:**
- Create: `desktop_agent/__init__.py`
- Create: `desktop_agent/__main__.py`
- Create: `desktop_agent/client.py`
- Create: `desktop_agent/executor.py`
- Create: `desktop_agent/handlers/base.py`
- Create: `desktop_agent/handlers/apps.py`
- Create: `desktop_agent/handlers/windows.py`
- Create: `desktop_agent/handlers/screenshots.py`
- Create: `desktop_agent/policy.py`
- Test: `tests/desktop_agent/test_app_handlers.py`

**Interfaces:**
- Consumes: backend WebSocket protocol, `DesktopAction`.
- Produces:
  - `DesktopAgentClient`
  - `DesktopActionExecutor`
  - `LaunchAppHandler`
  - `WaitHandler`
  - `ScreenshotHandler`
  - `PolicyEngine.assert_allowed(...)`

- [ ] **Step 1: Write safe launch-handler tests**

Create `tests/desktop_agent/test_app_handlers.py`:

```python
from pathlib import Path

import pytest

from desktop_agent.policy import PolicyEngine


def test_policy_allows_configured_application(tmp_path: Path) -> None:
    app = tmp_path / "notepad.exe"
    app.write_bytes(b"fake")
    policy = PolicyEngine(
        allowed_apps=[str(app)],
        allowed_document_roots=[str(tmp_path)],
    )

    policy.assert_allowed_app(str(app))


def test_policy_rejects_unlisted_application(tmp_path: Path) -> None:
    app = tmp_path / "powershell.exe"
    app.write_bytes(b"fake")
    policy = PolicyEngine(
        allowed_apps=[],
        allowed_document_roots=[str(tmp_path)],
    )

    with pytest.raises(PermissionError):
        policy.assert_allowed_app(str(app))
```

- [ ] **Step 2: Run the tests and confirm failure**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/desktop_agent/test_app_handlers.py -q
```

Expected: FAIL because `desktop_agent` does not exist.

- [ ] **Step 3: Implement the policy engine**

Create `desktop_agent/policy.py`:

```python
from __future__ import annotations

from pathlib import Path


class PolicyEngine:
    def __init__(
        self,
        allowed_apps: list[str],
        allowed_document_roots: list[str],
    ) -> None:
        self.allowed_apps = {
            str(Path(path).resolve()).lower() for path in allowed_apps
        }
        self.allowed_document_roots = [
            Path(root).resolve() for root in allowed_document_roots
        ]

    def assert_allowed_app(self, app_path: str) -> None:
        resolved = str(Path(app_path).resolve()).lower()
        if resolved not in self.allowed_apps:
            raise PermissionError(f"Application is not allowlisted: {app_path}")
        if not Path(resolved).is_file():
            raise FileNotFoundError(app_path)

    def assert_allowed_document(self, document_path: str) -> None:
        resolved = Path(document_path).resolve()
        if not any(
            resolved == root or root in resolved.parents
            for root in self.allowed_document_roots
        ):
            raise PermissionError(
                f"Document path is outside allowlist: {document_path}"
            )
```

- [ ] **Step 4: Implement app and wait handlers**

Create `desktop_agent/handlers/apps.py`:

```python
from __future__ import annotations

import asyncio
import subprocess
from typing import Any

from desktop_agent.policy import PolicyEngine


class LaunchAppHandler:
    def __init__(self, policy: PolicyEngine) -> None:
        self.policy = policy

    def supports(self, action_type: str) -> bool:
        return action_type == "launch_app"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        app_path = str(params["path"])
        self.policy.assert_allowed_app(app_path)
        args = params.get("args") or []
        if not isinstance(args, list) or not all(
            isinstance(item, str) for item in args
        ):
            raise ValueError("launch_app args must be a list of strings")
        process = await asyncio.to_thread(
            subprocess.Popen,
            [app_path, *args],
            close_fds=True,
        )
        return {"pid": process.pid}


class WaitHandler:
    def supports(self, action_type: str) -> bool:
        return action_type == "wait"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        seconds = min(max(float(params.get("seconds", 1)), 0), 300)
        await asyncio.sleep(seconds)
        return {"seconds": seconds}
```

- [ ] **Step 5: Implement screenshot handler**

Create `desktop_agent/handlers/screenshots.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from PIL import ImageGrab


class ScreenshotHandler:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root

    def supports(self, action_type: str) -> bool:
        return action_type == "screenshot"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name", "screenshot"))
        safe_name = "".join(
            char for char in name if char.isalnum() or char in "-_"
        ) or "screenshot"
        self.output_root.mkdir(parents=True, exist_ok=True)
        path = self.output_root / f"{safe_name}.png"
        image = await asyncio.to_thread(ImageGrab.grab)
        await asyncio.to_thread(image.save, path)
        return {"path": str(path)}
```

- [ ] **Step 6: Implement the action executor**

Create `desktop_agent/executor.py`:

```python
from __future__ import annotations

import time
from typing import Any


class DesktopActionExecutor:
    def __init__(self, handlers: list[Any]) -> None:
        self.handlers = handlers

    async def execute(self, action: dict[str, Any]) -> dict[str, Any]:
        action_type = str(action["type"])
        params = action.get("params") or {}
        started_at = time.perf_counter()
        for handler in self.handlers:
            if handler.supports(action_type):
                result = await handler.execute(params)
                return {
                    "type": action_type,
                    "status": "success",
                    "result": result,
                    "duration_seconds": time.perf_counter() - started_at,
                }
        raise ValueError(f"Unsupported desktop action: {action_type}")
```

- [ ] **Step 7: Implement the WebSocket client**

Create `desktop_agent/client.py`:

```python
from __future__ import annotations

import asyncio
import json
from typing import Any

import websockets

from desktop_agent.executor import DesktopActionExecutor


class DesktopAgentClient:
    def __init__(
        self,
        server_url: str,
        agent_id: int,
        token: str,
        executor: DesktopActionExecutor,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.agent_id = agent_id
        self.token = token
        self.executor = executor

    async def run_forever(self) -> None:
        uri = (
            f"{self.server_url}/api/agents/ws"
            f"?agent_id={self.agent_id}&token={self.token}"
        )
        async for websocket in websockets.connect(uri):
            try:
                await self._consume(websocket)
            except Exception:
                await asyncio.sleep(2)

    async def _consume(self, websocket: Any) -> None:
        async for raw_message in websocket:
            message = json.loads(raw_message)
            if message.get("type") != "desktop.task":
                continue
            await self._run_task(websocket, message)

    async def _run_task(self, websocket: Any, message: dict[str, Any]) -> None:
        task_id = int(message["task_id"])
        completed_steps: list[dict[str, Any]] = []
        try:
            for action in message.get("actions", []):
                step = await self.executor.execute(action)
                completed_steps.append(step)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "desktop.progress",
                            "task_id": task_id,
                            "step": step,
                        }
                    )
                )
            await websocket.send(
                json.dumps(
                    {
                        "type": "desktop.completed",
                        "task_id": task_id,
                        "success": True,
                        "output": "desktop task completed",
                        "error": None,
                        "step_logs": completed_steps,
                    }
                )
            )
        except Exception as exc:
            await websocket.send(
                json.dumps(
                    {
                        "type": "desktop.completed",
                        "task_id": task_id,
                        "success": False,
                        "output": "",
                        "error": str(exc),
                        "step_logs": completed_steps,
                    }
                )
            )
```

- [ ] **Step 8: Add agent entry point**

Create `desktop_agent/__main__.py`:

```python
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from desktop_agent.client import DesktopAgentClient
from desktop_agent.executor import DesktopActionExecutor
from desktop_agent.handlers.apps import LaunchAppHandler, WaitHandler
from desktop_agent.handlers.screenshots import ScreenshotHandler
from desktop_agent.policy import PolicyEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--agent-id", type=int, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--allowed-app", action="append", default=[])
    parser.add_argument("--document-root", action="append", default=[])
    parser.add_argument("--output-root", default="agent-output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    policy = PolicyEngine(
        allowed_apps=args.allowed_app,
        allowed_document_roots=args.document_root,
    )
    executor = DesktopActionExecutor(
        [
            LaunchAppHandler(policy),
            WaitHandler(),
            ScreenshotHandler(Path(args.output_root)),
        ]
    )
    client = DesktopAgentClient(
        server_url=args.server_url,
        agent_id=args.agent_id,
        token=args.token,
        executor=executor,
    )
    asyncio.run(client.run_forever())


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: Run agent tests**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/desktop_agent -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add desktop_agent tests/desktop_agent
git commit -m "feat: add Windows desktop agent MVP"
```

---

## Task 7: Add WPS COM Adapter

**Files:**
- Create: `desktop_agent/adapters/__init__.py`
- Create: `desktop_agent/adapters/wps.py`
- Test: `tests/desktop_agent/test_wps_adapter.py`

**Interfaces:**
- Consumes: `PolicyEngine`.
- Produces:
  - `WpsAdapter.open_document(document_path) -> dict`
  - `WpsAdapter.save_as(document_path, format_name) -> dict`
  - `OpenDocumentHandler`
  - `SaveDocumentHandler`
  - `ExportPdfHandler`

- [ ] **Step 1: Write WPS adapter tests with a fake COM dispatcher**

Create `tests/desktop_agent/test_wps_adapter.py`:

```python
from pathlib import Path

from desktop_agent.adapters.wps import WpsAdapter
from desktop_agent.policy import PolicyEngine


class FakeDocument:
    def __init__(self) -> None:
        self.saved_as = None
        self.exported_as = None

    def SaveAs(self, path: str) -> None:
        self.saved_as = path

    def ExportAsFixedFormat(self, path: str, export_format: int) -> None:
        self.exported_as = (path, export_format)


class FakeApplication:
    def __init__(self) -> None:
        self.visible = False
        self.document = FakeDocument()
        self.opened = None

    def Documents(self):
        return self

    def Open(self, path: str):
        self.opened = path
        return self.document


def test_wps_open_and_export_pdf(tmp_path: Path) -> None:
    source = tmp_path / "input.docx"
    source.write_text("test", encoding="utf-8")
    pdf = tmp_path / "output.pdf"
    app = FakeApplication()
    policy = PolicyEngine(
        allowed_apps=[],
        allowed_document_roots=[str(tmp_path)],
    )
    adapter = WpsAdapter(
        policy=policy,
        dispatch=lambda _progid: app,
    )

    adapter.open_document(str(source))
    adapter.export_pdf(str(pdf))

    assert app.opened == str(source)
    assert app.document.exported_as == (str(pdf), 17)
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/desktop_agent/test_wps_adapter.py -q
```

Expected: FAIL because the WPS adapter does not exist.

- [ ] **Step 3: Implement the WPS adapter**

Create `desktop_agent/adapters/wps.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from desktop_agent.policy import PolicyEngine


class WpsAdapter:
    WRITER_PROGID = "kwps.Application"
    SPREADSHEET_PROGID = "ket.Application"
    PRESENTATION_PROGID = "kwpp.Application"

    def __init__(
        self,
        policy: PolicyEngine,
        dispatch: Callable[[str], Any],
    ) -> None:
        self.policy = policy
        self.dispatch = dispatch
        self.application = None
        self.document = None

    def _get_application(self, progid: str) -> Any:
        if self.application is None:
            self.application = self.dispatch(progid)
            self.application.Visible = True
        return self.application

    def open_document(
        self,
        document_path: str,
        progid: str = WRITER_PROGID,
    ) -> dict[str, str]:
        self.policy.assert_allowed_document(document_path)
        application = self._get_application(progid)
        self.document = application.Documents().Open(document_path)
        return {"document_path": document_path, "progid": progid}

    def save_as(self, document_path: str) -> dict[str, str]:
        if self.document is None:
            raise RuntimeError("No WPS document is open")
        self.policy.assert_allowed_document(document_path)
        self.document.SaveAs(document_path)
        return {"document_path": document_path}

    def export_pdf(self, output_path: str) -> dict[str, str]:
        if self.document is None:
            raise RuntimeError("No WPS document is open")
        self.policy.assert_allowed_document(output_path)
        self.document.ExportAsFixedFormat(output_path, 17)
        return {"output_path": output_path}

    def close(self) -> None:
        if self.document is not None:
            self.document.Close()
            self.document = None
        if self.application is not None:
            self.application.Quit()
            self.application = None


class OpenDocumentHandler:
    def __init__(self, adapter: WpsAdapter) -> None:
        self.adapter = adapter

    def supports(self, action_type: str) -> bool:
        return action_type == "open_document"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.adapter.open_document(
            str(params["path"]),
            str(params.get("progid", WpsAdapter.WRITER_PROGID)),
        )


class SaveDocumentHandler:
    def __init__(self, adapter: WpsAdapter) -> None:
        self.adapter = adapter

    def supports(self, action_type: str) -> bool:
        return action_type == "save_document"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.adapter.save_as(str(params["path"]))


class ExportPdfHandler:
    def __init__(self, adapter: WpsAdapter) -> None:
        self.adapter = adapter

    def supports(self, action_type: str) -> bool:
        return action_type == "export_pdf"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.adapter.export_pdf(str(params["path"]))
```

- [ ] **Step 4: Add WPS handlers to the agent entry point**

In `desktop_agent/__main__.py`:

```python
from win32com.client import Dispatch

from desktop_agent.adapters.wps import (
    ExportPdfHandler,
    OpenDocumentHandler,
    SaveDocumentHandler,
    WpsAdapter,
)

wps_adapter = WpsAdapter(policy=policy, dispatch=Dispatch)

executor = DesktopActionExecutor(
    [
        LaunchAppHandler(policy),
        WaitHandler(),
        ScreenshotHandler(Path(args.output_root)),
        OpenDocumentHandler(wps_adapter),
        SaveDocumentHandler(wps_adapter),
        ExportPdfHandler(wps_adapter),
    ]
)
```

- [ ] **Step 5: Run tests**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest tests/desktop_agent/test_wps_adapter.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add desktop_agent/adapters tests/desktop_agent/test_wps_adapter.py desktop_agent/__main__.py
git commit -m "feat: add WPS COM automation adapter"
```

---

## Task 8: Add the Tauri Desktop Shell

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/src-tauri/Cargo.toml`
- Create: `desktop/src-tauri/build.rs`
- Create: `desktop/src-tauri/tauri.conf.json`
- Create: `desktop/src-tauri/src/main.rs`
- Create: `scripts/build-agent.ps1`
- Modify: `README.md`

**Interfaces:**
- Consumes: existing frontend build output and `desktop_agent` sidecar.
- Produces: a Windows desktop executable that starts the Agent sidecar and opens the existing UI.

- [ ] **Step 1: Create the desktop package**

Create `desktop/package.json`:

```json
{
  "name": "nl-rpa-desktop",
  "private": true,
  "version": "0.1.0",
  "scripts": {
    "tauri": "tauri"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2.0.0"
  }
}
```

- [ ] **Step 2: Configure Tauri**

Create `desktop/src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "NL-RPA",
  "version": "0.1.0",
  "identifier": "com.example.nlrpa",
  "build": {
    "beforeDevCommand": "npm --prefix ../../frontend run dev",
    "beforeBuildCommand": "npm --prefix ../../frontend run build",
    "devUrl": "http://localhost:5173",
    "frontendDist": "../../frontend/dist"
  },
  "app": {
    "windows": [
      {
        "title": "NL-RPA",
        "width": 1440,
        "height": 900,
        "resizable": true
      }
    ],
    "security": {
      "csp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "externalBin": ["bin/nl-rpa-agent"]
  }
}
```

- [ ] **Step 3: Add Rust dependencies**

Create `desktop/src-tauri/Cargo.toml`:

```toml
[package]
name = "nl-rpa-desktop"
version = "0.1.0"
edition = "2021"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = [] }
tauri-plugin-shell = "2"
```

- [ ] **Step 4: Add Tauri build script**

Create `desktop/src-tauri/build.rs`:

```rust
fn main() {
    tauri_build::build()
}
```

- [ ] **Step 5: Start the Agent sidecar from Rust**

Create `desktop/src-tauri/src/main.rs`:

```rust
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri_plugin_shell::ShellExt;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            app.shell()
                .sidecar("nl-rpa-agent")?
                .args(["--server-url", "http://127.0.0.1:8000"])
                .spawn()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to run NL-RPA desktop app");
}
```

- [ ] **Step 6: Add the Agent build script**

Create `scripts/build-agent.ps1`:

```powershell
$ErrorActionPreference = "Stop"

python -m pip install pyinstaller pywin32 pywinauto Pillow websockets

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name nl-rpa-agent-x86_64-pc-windows-msvc `
  --distpath desktop/src-tauri/bin `
  desktop_agent/__main__.py
```

- [ ] **Step 7: Document desktop development**

Add to `README.md`:

```markdown
## 桌面客户端

开发模式：

```powershell
cd desktop
npm install
npm run tauri dev
```

构建 Windows 安装包：

```powershell
.\scripts\build-agent.ps1
cd desktop
npm install
npm run tauri build
```
```

- [ ] **Step 8: Verify desktop shell**

Run:

```powershell
.\scripts\build-agent.ps1
cd desktop
npm install
npm run tauri dev
```

Expected: the desktop window opens, the tray-style close behavior hides the window, and the Agent sidecar starts.

- [ ] **Step 9: Commit**

```bash
git add desktop scripts/build-agent.ps1 README.md
git commit -m "feat: add Tauri desktop shell and agent sidecar"
```

---

## Task 9: Add Frontend Executor Selection and Agent Status

**Files:**
- Create: `frontend/src/api/agents.ts`
- Create: `frontend/src/components/agents/AgentStatusCard.tsx`
- Modify: `frontend/src/pages/WorkflowsPage.tsx`
- Modify: `frontend/src/components/workflows/WorkflowCard.tsx`
- Modify: `frontend/src/pages/WorkflowEditor.tsx`
- Test: `frontend/tests/executor-display.test.mjs`

**Interfaces:**
- Consumes: `GET /api/agents`, `Workflow.executor_type`.
- Produces:
  - `listAgents(): Promise<AgentRecord[]>`
  - workflow cards show `网页` or `桌面`.
  - editor prevents saving `windows` workflows without an online Agent.

- [ ] **Step 1: Add a pure display helper and failing test**

Create `frontend/src/lib/executor-display.ts`:

```ts
import type { ExecutorType } from '../types/workflow';

export function executorLabel(executorType: ExecutorType): string {
  return executorType === 'windows' ? '桌面执行' : '网页执行';
}
```

Create `frontend/tests/executor-display.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';

import { executorLabel } from '../src/lib/executor-display.ts';

test('executorLabel distinguishes web and desktop workflows', () => {
  assert.equal(executorLabel('web'), '网页执行');
  assert.equal(executorLabel('windows'), '桌面执行');
});
```

- [ ] **Step 2: Run the test**

Run:

```bash
cd frontend
cmd /c "npm test"
```

Expected: PASS after the helper file is present.

- [ ] **Step 3: Implement the Agent API client**

Create `frontend/src/api/agents.ts`:

```ts
import api from './axios';

export interface AgentRecord {
  id: number;
  name: string;
  platform: string;
  version: string;
  capabilities: string[];
  status: 'online' | 'offline';
  last_seen_at: string | null;
}

export async function listAgents(): Promise<AgentRecord[]> {
  const response = await api.get<AgentRecord[]>('/api/agents');
  return response.data;
}
```

- [ ] **Step 4: Add Agent status UI**

Create `frontend/src/components/agents/AgentStatusCard.tsx`:

```tsx
import { Monitor } from 'lucide-react';

import type { AgentRecord } from '../../api/agents';
import { Badge } from '../ui/Badge';
import { Card, CardContent } from '../ui/Card';

export function AgentStatusCard({ agents }: { agents: AgentRecord[] }) {
  const online = agents.filter((agent) => agent.status === 'online');

  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 p-4">
        <div className="flex items-center gap-3">
          <Monitor className="h-5 w-5 text-slate-500" />
          <div>
            <p className="text-sm font-medium">Windows 执行器</p>
            <p className="text-xs text-slate-500">
              {online.length > 0
                ? `${online[0].name} 在线`
                : '没有在线 Agent'}
            </p>
          </div>
        </div>
        <Badge variant={online.length > 0 ? 'success' : 'secondary'}>
          {online.length > 0 ? '可用' : '离线'}
        </Badge>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 5: Show executor type on workflow cards**

In `frontend/src/components/workflows/WorkflowCard.tsx`:

```tsx
import { Monitor, Globe2 } from 'lucide-react';
import { executorLabel } from '../../lib/executor-display';

const ExecutorIcon =
  workflow.executor_type === 'windows' ? Monitor : Globe2;

<Badge variant="outline">
  <ExecutorIcon className="mr-1 h-3.5 w-3.5" />
  {executorLabel(workflow.executor_type)}
</Badge>
```

- [ ] **Step 6: Add Agent status to WorkflowsPage**

In `frontend/src/pages/WorkflowsPage.tsx`:

```tsx
import * as agentApi from '../api/agents';
import { AgentStatusCard } from '../components/agents/AgentStatusCard';

const [agents, setAgents] = useState<agentApi.AgentRecord[]>([]);

useEffect(() => {
  void agentApi.listAgents().then(setAgents).catch(() => setAgents([]));
}, []);

<AgentStatusCard agents={agents} />
```

- [ ] **Step 7: Run frontend verification**

Run:

```bash
cd frontend
cmd /c "npm test"
cmd /c "npm run build"
```

Expected: tests and build pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/agents.ts frontend/src/components/agents frontend/src/lib/executor-display.ts frontend/src/pages/WorkflowsPage.tsx frontend/src/components/workflows/WorkflowCard.tsx frontend/tests/executor-display.test.mjs
git commit -m "feat: expose executor type and agent status"
```

---

## Task 10: End-to-End Windows Acceptance

**Files:**
- Create: `docs/desktop-agent-verification.md`
- Test: `tests/integration/test_desktop_executor_e2e.py`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: repeatable evidence that web and desktop execution both work.

- [ ] **Step 1: Add an integration test with a fake Agent**

Create `tests/integration/test_desktop_executor_e2e.py`:

```python
import asyncio

from app.desktop.actions import DesktopAction, DesktopActionPlan
from app.desktop.dispatcher import DesktopDispatcher


class FakeDesktopHub:
    def __init__(self) -> None:
        self.event = None
        self.sent = None

    def wait_event(self, task_id: int):
        self.event = asyncio.Event()
        self.task_id = task_id
        return self.event

    async def send(self, agent_id: int, message: dict) -> None:
        self.sent = message
        self.complete_task(
            task_id=message["task_id"],
            result={
                "task_id": message["task_id"],
                "success": True,
                "output": "desktop completed",
                "error": None,
                "screenshots": ["logs/task/step-1.png"],
                "step_logs": [{"step_index": 0, "status": "success"}],
                "duration_seconds": 0.2,
            },
        )

    def complete_task(self, task_id: int, result: dict) -> None:
        self.result = result
        self.event.set()

    def get_result(self, task_id: int) -> dict:
        return self.result


def test_desktop_executor_end_to_end_with_fake_agent() -> None:
    async def scenario() -> dict:
        hub = FakeDesktopHub()
        dispatcher = DesktopDispatcher(hub)
        plan = DesktopActionPlan(
            timeout_seconds=5,
            actions=[
                DesktopAction(
                    type="screenshot",
                    params={"name": "final"},
                )
            ],
        )
        return await dispatcher.dispatch(1, plan)

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert result["output"] == "desktop completed"
    assert result["screenshots"] == ["logs/task/step-1.png"]
```

- [ ] **Step 2: Run backend and frontend suites**

Run:

```bash
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pytest -q
cd frontend
cmd /c "npm test"
cmd /c "npm run build"
```

Expected: all tests pass and the frontend builds.

- [ ] **Step 3: Run Windows smoke test with Notepad**

On a Windows desktop:

```powershell
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m desktop_agent `
  --server-url http://127.0.0.1:8000 `
  --agent-id 1 `
  --token <paired-token> `
  --allowed-app C:\Windows\System32\notepad.exe `
  --document-root C:\Users\Administrator\Documents `
  --output-root logs\desktop-agent
```

Then create a workflow with:

```json
{
  "executor_type": "windows",
  "steps": [
    {
      "action": "launch_app",
      "params": {
        "path": "C:\\Windows\\System32\\notepad.exe",
        "args": []
      }
    },
    {
      "action": "wait",
      "params": {
        "seconds": 2
      }
    },
    {
      "action": "screenshot",
      "params": {
        "name": "notepad-open"
      }
    }
  ]
}
```

Expected: Notepad opens and the execution record contains a screenshot.

- [ ] **Step 4: Run WPS smoke test**

Create a WPS workflow with:

```json
{
  "executor_type": "windows",
  "steps": [
    {
      "action": "launch_app",
      "params": {
        "path": "C:\\Users\\Administrator\\AppData\\Local\\Kingsoft\\WPS Office\\wps.exe",
        "args": []
      }
    },
    {
      "action": "wait",
      "params": {
        "seconds": 3
      }
    },
    {
      "action": "screenshot",
      "params": {
        "name": "wps-open"
      }
    }
  ]
}
```

Expected: WPS opens and the execution record contains a screenshot.

- [ ] **Step 5: Record evidence**

Create `docs/desktop-agent-verification.md` and write:

```markdown
# Desktop Agent Verification

- Backend tests: command, date, pass count.
- Frontend tests: command, date, pass count.
- Notepad workflow execution ID and screenshot path.
- WPS workflow execution ID and screenshot path.
- Agent name, version, capabilities, and connection status.
- Any UAC, locked-screen, or WPS version limitation observed.
```

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_desktop_executor_e2e.py docs/desktop-agent-verification.md
git commit -m "test: verify desktop executor end to end"
```

---

## Final Verification Checklist

- [ ] Existing web workflows still pass all tests.
- [ ] `executor_type` defaults to `web`.
- [ ] A Windows Agent can pair, connect, and appear online.
- [ ] The Agent can launch Notepad and capture a screenshot.
- [ ] The Agent can launch WPS and capture a screenshot.
- [ ] WPS COM can open and export a document.
- [ ] A published desktop workflow can be manually run from the editor.
- [ ] The execution monitor lists desktop execution records.
- [ ] The desktop client can connect in local mode.
- [ ] The same Agent can connect to a remote backend.
- [ ] No arbitrary shell or script execution path exists.
- [ ] Application and document path allowlists are enforced.
