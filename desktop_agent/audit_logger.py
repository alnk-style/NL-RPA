from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, log_dir: str | Path = "logs") -> None:
        self.log_dir = Path(log_dir).expanduser()
        self._lock = threading.Lock()

    def log(
        self,
        task_id: int | str | None,
        step_index: int,
        action_type: str,
        params: dict[str, Any] | None,
        status: str,
        error: str | None = None,
    ) -> Path | None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            now = datetime.now().astimezone()
            log_path = self.log_dir / f"audit_{now.date().isoformat()}.jsonl"
            record = {
                "timestamp": now.isoformat(),
                "task_id": task_id,
                "step_index": step_index,
                "action_type": action_type,
                "params": params or {},
                "status": status,
                "error": error,
            }
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._lock:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            return log_path
        except OSError:
            logger.warning(
                "Could not write Desktop Agent audit log in %s; "
                "continuing without audit persistence",
                self.log_dir,
                exc_info=True,
            )
            return None

    def log_start(
        self,
        task_id: int | str | None,
        step_index: int,
        action_type: str,
        params: dict[str, Any] | None,
    ) -> Path | None:
        return self.log(
            task_id=task_id,
            step_index=step_index,
            action_type=action_type,
            params=params,
            status="started",
        )

    def log_end(
        self,
        task_id: int | str | None,
        step_index: int,
        action_type: str,
        params: dict[str, Any] | None,
        success: bool,
        error: str | None = None,
    ) -> Path | None:
        return self.log(
            task_id=task_id,
            step_index=step_index,
            action_type=action_type,
            params=params,
            status="success" if success else "failed",
            error=error,
        )


__all__ = ["AuditLogger"]
