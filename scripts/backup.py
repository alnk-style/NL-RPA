"""一致性备份 CLI。

用法：
    .\\.venv\\Scripts\\python.exe scripts/backup.py --target logs/backups/manual-01
    .\\.venv\\Scripts\\python.exe scripts/backup.py --include logs --include artifacts

密钥无效（例如 .env 占位值）时拒绝备份并输出轮换说明。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.ops.backup import create_backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NL-RPA 一致性备份")
    parser.add_argument(
        "--database-url",
        default=settings.DATABASE_URL,
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=PROJECT_ROOT
        / "logs"
        / "backups"
        / f"backup-{time.strftime('%Y%m%d-%H%M%S')}",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=None,
        help="要一并备份的目录（默认 logs）；可重复",
    )
    parser.add_argument(
        "--credential-key",
        default=settings.CREDENTIAL_ENCRYPTION_KEY,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    includes = args.include or [str(PROJECT_ROOT / "logs")]
    manifest = asyncio.run(
        create_backup(
            database_url=args.database_url,
            target_dir=args.target,
            source_dirs=[Path(item) for item in includes],
            credential_key=args.credential_key,
        )
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
