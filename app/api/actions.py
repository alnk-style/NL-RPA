from __future__ import annotations

from fastapi import APIRouter, Query

from app.actions.registry import default_action_registry


router = APIRouter(prefix="/api/action-catalog", tags=["动作目录"])


@router.get("")
@router.get("/", include_in_schema=False)
def get_action_catalog(
    executor: str | None = Query(default=None),
    include_legacy: bool = Query(default=False),
) -> dict:
    catalog = default_action_registry().catalog(
        include_legacy=include_legacy,
        executor=executor,
    )
    return catalog.model_dump(mode="json")


__all__ = ["router"]
