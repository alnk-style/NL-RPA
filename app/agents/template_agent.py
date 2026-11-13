from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.workflow import Workflow


class TemplateManagerAgent:
    def __init__(self):
        self.templates_dir = Path("templates")
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, workflow: Workflow) -> str:
        workflow_data = self._serialize_workflow(workflow)
        path = self._template_path(workflow.id)
        serialized = json.dumps(
            workflow_data,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

        await asyncio.to_thread(
            path.write_text,
            serialized,
            encoding="utf-8",
        )

        return str(path)

    async def load(self, workflow_id: int) -> dict:
        path = self._template_path(workflow_id)
        if not path.exists():
            raise FileNotFoundError(path)

        content = await asyncio.to_thread(path.read_text, encoding="utf-8")

        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError(
                f"Template workflow_{workflow_id}.json must contain an object"
            )
        return data

    async def list_templates(self) -> list[dict]:
        templates: list[dict] = []

        for path in sorted(self.templates_dir.glob("workflow_*.json")):
            try:
                content = await asyncio.to_thread(
                    path.read_text,
                    encoding="utf-8",
                )
                data = json.loads(content)
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(data, dict):
                continue

            workflow_id = data.get("id", self._workflow_id_from_path(path))
            templates.append(
                {
                    "id": workflow_id,
                    "workflow_id": workflow_id,
                    "name": data.get("name"),
                    "description": data.get("description"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "file_name": path.name,
                    "path": str(path),
                }
            )

        return templates

    async def delete(self, workflow_id: int) -> bool:
        path = self._template_path(workflow_id)
        if not path.exists():
            return False

        path.unlink()
        return True

    def _template_path(self, workflow_id: int) -> Path:
        return self.templates_dir / f"workflow_{workflow_id}.json"

    @staticmethod
    def _workflow_id_from_path(path: Path) -> int | None:
        match = re.fullmatch(r"workflow_(\d+)\.json", path.name)
        if not match:
            return None
        return int(match.group(1))

    @classmethod
    def _serialize_workflow(cls, workflow: Workflow) -> dict[str, Any]:
        if isinstance(workflow, dict):
            raw_data = dict(workflow)
        elif hasattr(workflow, "model_dump"):
            raw_data = workflow.model_dump()
        else:
            raw_data = cls._serialize_sqlalchemy_workflow(workflow)

        return cls._to_json_compatible(raw_data)

    @staticmethod
    def _serialize_sqlalchemy_workflow(
        workflow: Workflow,
    ) -> dict[str, Any]:
        table = getattr(workflow, "__table__", None)
        columns = getattr(table, "columns", None)
        column_names: list[str] = []

        if columns is not None:
            try:
                column_names = [column.name for column in columns]
            except TypeError:
                column_names = []

        if column_names:
            return {
                name: getattr(workflow, name, None)
                for name in column_names
            }

        if hasattr(workflow, "__dict__"):
            return {
                name: value
                for name, value in vars(workflow).items()
                if not name.startswith("_")
            }

        raise TypeError("workflow must be a Workflow object or mapping")

    @classmethod
    def _to_json_compatible(cls, value: Any) -> Any:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {
                str(key): cls._to_json_compatible(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [cls._to_json_compatible(item) for item in value]
        if hasattr(value, "model_dump"):
            return cls._to_json_compatible(value.model_dump())
        return value
