"""生成 requirements.lock（记录当前环境的精确版本）。

用法：.\\\\.venv\\\\Scripts\\\\python.exe scripts/lock_dependencies.py
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    requirements = PROJECT_ROOT / "requirements.txt"
    lock_path = PROJECT_ROOT / "requirements.lock"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()

    distributions = sorted(
        {
            dist.metadata["Name"]: dist.version
            for dist in importlib.metadata.distributions()
            if dist.metadata["Name"]
        }.items(),
        key=lambda item: item[0].lower(),
    )
    lines = [
        "# 由 scripts/lock_dependencies.py 生成，请勿手工编辑。",
        f"# requirements.txt sha256: {digest}",
        "# 复现：pip install -r requirements.lock",
    ]
    for name, version in distributions:
        lines.append(f"{name}=={version}")
    lock_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {lock_path} ({len(distributions)} packages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
