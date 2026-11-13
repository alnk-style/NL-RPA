"""Run one manifest-driven App MCP server over local Streamable HTTP."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.engine.app_mcp.process_main import (  # noqa: E402
    main,
    validate_bind_host,
)


if __name__ == "__main__":
    raise SystemExit(main())
