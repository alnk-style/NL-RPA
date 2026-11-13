import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.scheduler import router as scheduler_router


class FakeScheduler:
    def __init__(self) -> None:
        self.active = False

    async def start(self) -> None:
        self.active = True

    async def stop(self) -> None:
        self.active = False

    @property
    def is_running(self) -> bool:
        return self.active


async def _request(
    method: str,
    path: str,
) -> tuple[int, dict[str, object], FakeScheduler]:
    scheduler = FakeScheduler()
    app = FastAPI()
    app.state.scheduler = scheduler
    app.include_router(scheduler_router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.request(method, path)

    return response.status_code, response.json(), scheduler


def test_scheduler_start() -> None:
    status_code, payload, scheduler = asyncio.run(
        _request("POST", "/api/scheduler/start")
    )

    assert status_code == 200
    assert payload == {"status": "running"}
    assert scheduler.active is True


def test_scheduler_stop() -> None:
    async def scenario() -> tuple[int, dict[str, object], FakeScheduler]:
        scheduler = FakeScheduler()
        scheduler.active = True
        app = FastAPI()
        app.state.scheduler = scheduler
        app.include_router(scheduler_router)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/scheduler/stop")

        return response.status_code, response.json(), scheduler

    status_code, payload, scheduler = asyncio.run(scenario())

    assert status_code == 200
    assert payload == {"status": "stopped"}
    assert scheduler.active is False


def test_scheduler_status() -> None:
    async def scenario() -> tuple[int, dict[str, object], FakeScheduler]:
        scheduler = FakeScheduler()
        scheduler.active = True
        app = FastAPI()
        app.state.scheduler = scheduler
        app.include_router(scheduler_router)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/scheduler/status")

        return response.status_code, response.json(), scheduler

    status_code, payload, scheduler = asyncio.run(scenario())

    assert status_code == 200
    assert payload == {"running": True}
    assert scheduler.active is True
