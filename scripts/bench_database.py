"""SQLite vs PostgreSQL 并发写入 / 执行吞吐实测。

用法：

    .\\.venv\\Scripts\\python.exe scripts/bench_database.py
    .\\.venv\\Scripts\\python.exe scripts/bench_database.py --rows 200
    .\\.venv\\Scripts\\python.exe scripts/bench_database.py \
        --postgres-url postgresql+asyncpg://user:pass@host/db

输出 JSON 报告（默认 logs/bench_database.json）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base, run_schema_migrations
from app.engine.execution_manager import ExecutionManager
from app.engine.runner import ExecutionResult
from app.models.execution import ExecutionLog
from app.models.user import User
from app.models.workflow import Workflow
from app.testing.postgres import postgres_url


async def _prepare(url: str):
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
        await run_schema_migrations(connection)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return engine, session_factory


async def bench_concurrent_inserts(
    url: str,
    *,
    rows: int,
    concurrency: int,
) -> dict:
    engine, session_factory = await _prepare(url)
    try:
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="bench",
                    email="bench@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="bench",
                natural_language_input="x",
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)

        async def writer(worker_id: int, count: int) -> None:
            async with session_factory() as session:
                for index in range(count):
                    session.add(
                        ExecutionLog(
                            workflow_id=workflow.id,
                            status="success",
                        )
                    )
                    if index % 10 == 9:
                        await session.commit()
                await session.commit()

        per_worker = max(1, rows // concurrency)
        started = time.perf_counter()
        await asyncio.gather(
            *(
                writer(worker_id, per_worker)
                for worker_id in range(concurrency)
            )
        )
        elapsed = time.perf_counter() - started
        total = per_worker * concurrency
        return {
            "rows": total,
            "concurrency": concurrency,
            "elapsed_seconds": round(elapsed, 4),
            "rows_per_second": round(total / elapsed, 2)
            if elapsed > 0
            else None,
        }
    finally:
        await engine.dispose()


async def bench_execution_chain(url: str, *, runs: int) -> dict:
    engine, session_factory = await _prepare(url)
    try:
        async with session_factory() as session:
            session.add(
                User(
                    id=1,
                    username="bench",
                    email="bench@example.com",
                    hashed_password="x",
                )
            )
            workflow = Workflow(
                user_id=1,
                name="bench-chain",
                natural_language_input="x",
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)

        fake_engine = SimpleNamespace(
            execute_with_debug=AsyncMock(
                return_value=ExecutionResult(
                    success=True,
                    output="ok",
                    error=None,
                    screenshots=[],
                    step_logs=[{"step_index": 0, "status": "success"}],
                    duration_seconds=0.001,
                )
            )
        )
        manager = ExecutionManager(
            fake_engine,
            session_factory=session_factory,
            config=SimpleNamespace(
                AUTH_REQUIRED_PAUSE=False,
                WEB_EXPORT_DIRS="",
                WORKSPACE_DEFAULT_MAX_CONCURRENT_EXECUTIONS=9999,
                WORKSPACE_DEFAULT_MAX_CONCURRENT_AGENT_TASKS=9999,
            ),
        )
        started = time.perf_counter()
        for _ in range(runs):
            execution = await manager.start_workflow(workflow.id)
            await manager.execute_existing(execution.id)
        elapsed = time.perf_counter() - started
        return {
            "runs": runs,
            "elapsed_seconds": round(elapsed, 4),
            "runs_per_second": round(runs / elapsed, 2)
            if elapsed > 0
            else None,
        }
    finally:
        await engine.dispose()


async def run_benchmarks(
    backend: str,
    url: str,
    *,
    rows: int,
    concurrency: int,
    runs: int,
) -> dict:
    return {
        "backend": backend,
        "database_url": _safe(url),
        "concurrent_inserts": await bench_concurrent_inserts(
            url,
            rows=rows,
            concurrency=concurrency,
        ),
        "execution_chain": await bench_execution_chain(url, runs=runs),
    }


def _safe(url: str) -> str:
    if "@" not in url:
        return url
    scheme, _, remainder = url.partition("://")
    _, _, host = remainder.rpartition("@")
    return f"{scheme}://***@{host}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SQLite/PG 基准测试")
    parser.add_argument("--rows", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument(
        "--postgres-url",
        default=None,
        help="显式 PostgreSQL+asyncpg URL；缺省尝试内嵌 PG",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "logs" / "bench_database.json",
    )
    args = parser.parse_args(argv)

    async def scenario() -> dict:
        sqlite_path = PROJECT_ROOT / ".tmp" / "bench_sqlite.db"
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite_url = f"sqlite+aiosqlite:///{sqlite_path.as_posix()}"
        report: dict = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": [],
        }
        report["results"].append(
            await run_benchmarks(
                "sqlite",
                sqlite_url,
                rows=args.rows,
                concurrency=args.concurrency,
                runs=args.runs,
            )
        )
        if args.postgres_url:
            report["results"].append(
                await run_benchmarks(
                    "postgresql",
                    args.postgres_url,
                    rows=args.rows,
                    concurrency=args.concurrency,
                    runs=args.runs,
                )
            )
        else:
            with postgres_url() as pg_url:
                if pg_url is None:
                    report["postgresql_skipped"] = (
                        "PostgreSQL 不可用：设置 --postgres-url 或安装 "
                        "embedded_postgres"
                    )
                else:
                    report["results"].append(
                        await run_benchmarks(
                            "postgresql",
                            pg_url,
                            rows=args.rows,
                            concurrency=args.concurrency,
                            runs=args.runs,
                        )
                    )
        return report

    report = asyncio.run(scenario())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
