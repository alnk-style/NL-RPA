"""本地 PostgreSQL 测试环境。

优先级：

1. ``TEST_POSTGRES_URL`` / ``NL_RPA_TEST_POSTGRES_URL`` 环境变量；
2. 可选依赖 ``embedded_postgres``（内置 PG 二进制，开发机可用）；
3. 都不可用时让调用方 skip，不影响普通用例。

注意：中文 Windows 上 initdb 的 post-bootstrap 会把本地化文本写入
初始化流，UTF8 编码会失败；这里用 EUC_CN（GB2312 兼容，可服务 UTF-8
客户端）作为本地测试集簇编码。生产环境应使用 Linux/UTF8 初始化。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Iterator


def _env_url() -> str | None:
    for name in ("TEST_POSTGRES_URL", "NL_RPA_TEST_POSTGRES_URL"):
        value = os.getenv(name)
        if value:
            return value
    return None


def embedded_available() -> bool:
    try:
        import embedded_postgres  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def cluster_dir() -> Path:
    override = os.getenv("NL_RPA_TEST_PG_DATA")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "nlrpa-pg-test"


def _init_cluster(pgdata: Path) -> None:
    from embedded_postgres import initdb

    initdb(
        [
            "--auth=trust",
            "--auth-local=trust",
            "--encoding=EUC_CN",
            "--locale=C",
            "-U",
            "postgres",
        ],
        pgdata=pgdata,
    )


@contextlib.contextmanager
def postgres_url() -> Iterator[str | None]:
    """Yield a ``postgresql+asyncpg://`` URL or None when unavailable."""

    env = _env_url()
    if env:
        yield (
            env.replace("postgresql://", "postgresql+asyncpg://", 1)
            if env.startswith("postgresql://")
            else env
        )
        return

    if not embedded_available():
        yield None
        return

    from embedded_postgres import PostgresServer

    pgdata = cluster_dir()
    server = None
    try:
        if not (pgdata / "PG_VERSION").is_file():
            pgdata.mkdir(parents=True, exist_ok=True)
            _init_cluster(pgdata)
        server = PostgresServer(pgdata)
        server.ensure_postgres_running()
        yield server.get_uri().replace(
            "postgresql://",
            "postgresql+asyncpg://",
            1,
        )
    except Exception:  # noqa: BLE001
        yield None
    finally:
        if server is not None:
            with contextlib.suppress(Exception):
                server.cleanup()


__all__ = ["cluster_dir", "embedded_available", "postgres_url"]
