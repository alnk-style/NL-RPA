from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine import desktop_targets  # noqa: E402
from app.models.agent import DesktopAgent  # noqa: E402
from app.models.user import User  # noqa: E402


def _request(*agent_ids: int) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                connection_manager=SimpleNamespace(
                    get_online_agents=lambda: list(agent_ids),
                )
            )
        )
    )


async def _seed_agent(
    db_session: AsyncSession,
    **overrides,
) -> DesktopAgent:
    if await db_session.get(User, 1) is None:
        db_session.add(
            User(
                id=1,
                username="tester",
                email="tester@example.com",
                hashed_password="hash",
            )
        )
    values = {
        "id": 1,
        "user_id": 1,
        "name": "local-pc",
        "platform": "windows",
        "version": "0.1.0",
        "capabilities": ["window", "uia"],
        "allowed_apps": ["notepad.exe"],
        "allowed_doc_dirs": [],
        "status": "online",
        "token_hash": "hash",
    }
    values.update(overrides)
    agent = DesktopAgent(**values)
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)
    return agent


@pytest.mark.asyncio
async def test_context_reads_allowlist_of_online_agent(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_agent(db_session)
    monkeypatch.setattr(
        desktop_targets,
        "AsyncSessionLocal",
        db_session.info["session_factory"],
    )

    context = await desktop_targets.load_desktop_execution_context(
        _request(1)
    )

    assert context.allowed_apps == ("notepad.exe",)
    assert context.capabilities == frozenset({"window", "uia"})
    assert context.online_agent_count == 1
    assert context.paired_agent_count == 1
    assert context.has_agents is True
    assert context.has_allowed_apps is True


@pytest.mark.asyncio
async def test_context_falls_back_to_paired_agents_when_offline(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_agent(
        db_session,
        allowed_apps=[r"C:\Tools\wechat.exe"],
        capabilities=["window"],
        status="offline",
    )
    monkeypatch.setattr(
        desktop_targets,
        "AsyncSessionLocal",
        db_session.info["session_factory"],
    )

    context = await desktop_targets.load_desktop_execution_context(_request())

    assert context.allowed_apps == (r"C:\Tools\wechat.exe",)
    assert context.capabilities == frozenset({"window"})
    assert context.online_agent_count == 0
    assert context.paired_agent_count == 1
    assert context.has_agents is True


@pytest.mark.asyncio
async def test_context_merges_multiple_agents(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_agent(db_session)
    await _seed_agent(
        db_session,
        id=2,
        name="second-pc",
        allowed_apps=["notepad.exe", r"C:\Tools\wechat.exe"],
        capabilities=["window", "screenshot"],
    )
    monkeypatch.setattr(
        desktop_targets,
        "AsyncSessionLocal",
        db_session.info["session_factory"],
    )

    context = await desktop_targets.load_desktop_execution_context(
        _request(1, 2)
    )

    assert context.allowed_apps == (
        "notepad.exe",
        r"C:\Tools\wechat.exe",
    )
    assert context.capabilities == frozenset(
        {"window", "uia", "screenshot"}
    )
    assert context.paired_agent_count == 2
