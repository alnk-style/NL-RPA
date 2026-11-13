"""凭据密钥轮换：用旧 Fernet 密钥解密、新密钥重新加密。

用法（推荐用环境变量，避免密钥进入命令历史）：
    $env:CREDENTIAL_OLD_KEY = "..."
    $env:CREDENTIAL_NEW_KEY = "..."
    .\\.venv\\Scripts\\python.exe scripts/rotate_credentials.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models.credential import Credential
from app.models.mcp import MCPServer
from app.security.credentials import build_fernet


async def rotate(database_url: str, old_key: str, new_key: str) -> dict:
    old_config = SimpleNamespace(CREDENTIAL_ENCRYPTION_KEY=old_key)
    new_config = SimpleNamespace(CREDENTIAL_ENCRYPTION_KEY=new_key)
    old_fernet = build_fernet(old_config)
    new_fernet = build_fernet(new_config)

    engine = create_async_engine(database_url)
    counts = {"credentials": 0, "mcp_secrets": 0}
    try:
        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with session_factory() as session:
            credentials = (
                await session.execute(select(Credential))
            ).scalars().all()
            for credential in credentials:
                plaintext = old_fernet.decrypt(
                    bytes(credential.encrypted_value)
                )
                credential.encrypted_value = new_fernet.encrypt(plaintext)
                counts["credentials"] += 1

            servers = (
                await session.execute(
                    select(MCPServer).where(
                        MCPServer.secret_encrypted.is_not(None)
                    )
                )
            ).scalars().all()
            for server in servers:
                plaintext = old_fernet.decrypt(
                    bytes(server.secret_encrypted)
                )
                server.secret_encrypted = new_fernet.encrypt(plaintext)
                counts["mcp_secrets"] += 1
            await session.commit()
    finally:
        await engine.dispose()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="凭据密钥轮换")
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument(
        "--old-key-env",
        default="CREDENTIAL_OLD_KEY",
    )
    parser.add_argument(
        "--new-key-env",
        default="CREDENTIAL_NEW_KEY",
    )
    args = parser.parse_args(argv)

    old_key = os.getenv(args.old_key_env)
    new_key = os.getenv(args.new_key_env)
    if not old_key or not new_key:
        raise SystemExit(
            f"请设置环境变量 {args.old_key_env} 与 {args.new_key_env}"
        )
    counts = asyncio.run(rotate(args.database_url, old_key, new_key))
    print(json.dumps({"ok": True, **counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
