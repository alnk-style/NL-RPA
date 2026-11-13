"""升级 CLI：兼容检查 + 自动备份 + 迁移。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.ops.upgrade import check_upgrade_compatibility, run_upgrade


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NL-RPA 升级")
    parser.add_argument("--database-url", default=settings.DATABASE_URL)
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--include", action="append", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.dry_run:
        report = asyncio.run(
            check_upgrade_compatibility(args.database_url)
        )
    else:
        includes = args.include or [str(PROJECT_ROOT / "logs")]
        report = asyncio.run(
            run_upgrade(
                args.database_url,
                backup_dir=args.backup_dir,
                source_dirs=[Path(item) for item in includes],
                credential_key=settings.CREDENTIAL_ENCRYPTION_KEY,
            )
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
