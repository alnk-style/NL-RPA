from __future__ import annotations

import inspect

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/api/scheduler", tags=["调度管理"])


def _is_loopback_request(request: Request) -> bool:
    client = request.client
    if client is None:
        return False

    host = (client.host or "").strip().lower()
    return host in {"localhost", "::1"} or host.startswith("127.")


def _get_scheduler(request: Request) -> object:
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler is not ready",
        )
    return scheduler


def _require_loopback(request: Request) -> None:
    if not _is_loopback_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Scheduler control is available from localhost only",
        )


async def _call_scheduler_method(
    scheduler: object,
    method_name: str,
) -> None:
    method = getattr(scheduler, method_name, None)
    if not callable(method):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Scheduler does not support {method_name}",
        )

    result = method()
    if inspect.isawaitable(result):
        await result


@router.post("/start")
async def start_scheduler(request: Request) -> dict[str, str]:
    _require_loopback(request)
    scheduler = _get_scheduler(request)
    await _call_scheduler_method(scheduler, "start")
    return {"status": "running"}


@router.post("/stop")
async def stop_scheduler(request: Request) -> dict[str, str]:
    _require_loopback(request)
    scheduler = _get_scheduler(request)
    await _call_scheduler_method(scheduler, "stop")
    return {"status": "stopped"}


@router.get("/status")
async def scheduler_status(request: Request) -> dict[str, bool]:
    _require_loopback(request)
    scheduler = _get_scheduler(request)
    return {"running": bool(getattr(scheduler, "is_running", False))}


@router.post("/toggle")
async def toggle_scheduler(request: Request) -> dict[str, bool]:
    _require_loopback(request)
    scheduler = _get_scheduler(request)
    toggle = getattr(scheduler, "toggle", None)
    if not callable(toggle):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler does not support toggle",
        )

    running = toggle()
    if inspect.isawaitable(running):
        running = await running

    return {"running": bool(running)}


__all__ = ["router"]
