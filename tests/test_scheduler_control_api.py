import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

from app.api.scheduler import router as scheduler_router
from app.scheduler.jobs import SchedulerManager


class FakeScheduler:
    def __init__(self) -> None:
        self.running = True

    async def toggle(self) -> bool:
        self.running = not self.running
        return self.running


class FakeApscheduler:
    def __init__(self) -> None:
        self.running = True
        self.state = STATE_RUNNING

    def pause(self) -> None:
        self.state = STATE_PAUSED

    def resume(self) -> None:
        self.state = STATE_RUNNING

    def start(self) -> None:
        self.running = True
        self.state = STATE_RUNNING


def test_scheduler_toggle_endpoint() -> None:
    async def scenario() -> tuple[int, dict[str, bool], bool]:
        scheduler = FakeScheduler()
        app = FastAPI()
        app.state.scheduler = scheduler
        app.include_router(scheduler_router)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/scheduler/toggle")

        return response.status_code, response.json(), scheduler.running

    status_code, payload, running = asyncio.run(scenario())

    assert status_code == 200
    assert payload == {"running": False}
    assert running is False


def test_scheduler_manager_toggle_pauses_and_resumes() -> None:
    manager = SchedulerManager.__new__(SchedulerManager)
    manager.scheduler = FakeApscheduler()

    assert asyncio.run(manager.toggle()) is False
    assert manager.scheduler.state == STATE_PAUSED

    assert asyncio.run(manager.toggle()) is True
    assert manager.scheduler.state == STATE_RUNNING
