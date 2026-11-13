"""执行任务队列后端（阶段 5）。

默认 ``EXTERNAL_QUEUE=false``：ExecutionManager 继续用 asyncio task
在本进程执行，保持阶段 1–4 行为。打开后可以使用 Redis 最小实现：

- 入队：``LPUSH nl-rpa:executions <execution_id>``；
- 出队：``BRPOPLPUSH`` 到 processing 列表（至少一次投递）；
- ack：``LREM processing``；
- nack：重试次数未到上限时重新入队，超过后进入 failed 列表。

评估结论（详见阶段 5 报告）：最小部署用 in-process；多进程/多副本
或需要崩溃恢复时切 Redis；Celery/RQ 提供更多编排能力但引入更重的
依赖与 Windows 兼容负担，暂不采用。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_NAME = "nl-rpa:executions"
DEFAULT_PROCESSING_NAME = "nl-rpa:executions:processing"
DEFAULT_FAILED_NAME = "nl-rpa:executions:failed"


@dataclass(frozen=True)
class QueueMessage:
    execution_id: int
    raw: str | None = None


class ExecutionQueue(Protocol):
    async def enqueue(self, execution_id: int) -> None: ...

    async def dequeue(
        self,
        timeout: float = 5.0,
    ) -> QueueMessage | None: ...

    async def ack(self, message: QueueMessage) -> None: ...

    async def nack(self, message: QueueMessage) -> None: ...

    async def close(self) -> None: ...


class InProcessExecutionQueue:
    """进程内队列：默认后端，语义与旧 asyncio task 调度一致。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueueMessage] = asyncio.Queue()

    async def enqueue(self, execution_id: int) -> None:
        await self._queue.put(QueueMessage(execution_id=int(execution_id)))

    async def dequeue(
        self,
        timeout: float = 5.0,
    ) -> QueueMessage | None:
        try:
            return await asyncio.wait_for(
                self._queue.get(),
                timeout=max(0.0, float(timeout)),
            )
        except asyncio.TimeoutError:
            return None

    async def ack(self, message: QueueMessage) -> None:
        return None

    async def nack(self, message: QueueMessage) -> None:
        # 进程内队列没有持久化语义，失败任务由 ExecutionManager 落库。
        return None

    async def close(self) -> None:
        return None

    def qsize(self) -> int:
        return self._queue.qsize()


class RedisExecutionQueue:
    """Redis 最小实现（LPUSH/BRPOPLPUSH/LREM）。"""

    def __init__(
        self,
        url: str,
        *,
        queue_name: str = DEFAULT_QUEUE_NAME,
        processing_name: str = DEFAULT_PROCESSING_NAME,
        failed_name: str = DEFAULT_FAILED_NAME,
        max_attempts: int = 3,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.url = url
        self.queue_name = queue_name
        self.processing_name = processing_name
        self.failed_name = failed_name
        self.max_attempts = max(1, int(max_attempts))
        self._client_factory = client_factory
        self._client: Any | None = None

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory(self.url)
            return self._client
        try:
            from redis.asyncio import from_url
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "redis 依赖不可用，请先安装 redis 包"
            ) from exc
        self._client = from_url(self.url, decode_responses=True)
        return self._client

    async def enqueue(self, execution_id: int) -> None:
        client = await self._get_client()
        await client.lpush(self.queue_name, str(int(execution_id)))

    async def dequeue(
        self,
        timeout: float = 5.0,
    ) -> QueueMessage | None:
        client = await self._get_client()
        raw = await client.brpoplpush(
            self.queue_name,
            self.processing_name,
            timeout=max(1, int(timeout)),
        )
        if raw is None:
            return None
        try:
            execution_id = int(raw)
        except (TypeError, ValueError):
            await client.lrem(self.processing_name, 1, raw)
            return None
        return QueueMessage(execution_id=execution_id, raw=str(raw))

    async def ack(self, message: QueueMessage) -> None:
        client = await self._get_client()
        await client.lrem(
            self.processing_name,
            1,
            message.raw or str(message.execution_id),
        )

    async def nack(self, message: QueueMessage) -> None:
        client = await self._get_client()
        raw = message.raw or str(message.execution_id)
        await client.lrem(self.processing_name, 1, raw)
        attempts_key = f"nl-rpa:executions:attempts:{message.execution_id}"
        attempts = int(await client.incr(attempts_key) or 1)
        await client.expire(attempts_key, 86400)
        if attempts < self.max_attempts:
            await client.lpush(self.queue_name, raw)
            return
        await client.lpush(self.failed_name, raw)
        logger.warning(
            "execution %s 超过队列重试上限 %s，移入失败列表",
            message.execution_id,
            self.max_attempts,
        )

    async def close(self) -> None:
        if self._client is None:
            return
        close = getattr(self._client, "aclose", None) or getattr(
            self._client,
            "close",
            None,
        )
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result
        self._client = None


class QueueWorker:
    """从队列取任务并串行调用 handler（每个 worker 一次一条）。"""

    def __init__(
        self,
        queue: ExecutionQueue,
        handler: Callable[[int], Awaitable[None]],
        *,
        poll_timeout: float = 5.0,
    ) -> None:
        self.queue = queue
        self.handler = handler
        self.poll_timeout = max(0.1, float(poll_timeout))
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self.run(),
            name="nl-rpa-queue-worker",
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._task = None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                message = await self.queue.dequeue(self.poll_timeout)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("队列出队失败")
                await asyncio.sleep(1.0)
                continue
            if message is None:
                continue
            try:
                await self.handler(message.execution_id)
            except asyncio.CancelledError:
                await self.queue.nack(message)
                raise
            except Exception:
                logger.exception(
                    "队列任务执行失败 execution_id=%s",
                    message.execution_id,
                )
                await self.queue.nack(message)
            else:
                await self.queue.ack(message)

    async def drain_once(self) -> QueueMessage | None:
        """测试辅助：处理一条消息并返回。"""

        message = await self.queue.dequeue(timeout=0.1)
        if message is None:
            return None
        try:
            await self.handler(message.execution_id)
        except Exception:
            await self.queue.nack(message)
        else:
            await self.queue.ack(message)
        return message


def build_execution_queue(config: Any) -> ExecutionQueue | None:
    """按配置构建队列后端；默认返回 None（in-process 调度）。"""

    if not bool(getattr(config, "EXTERNAL_QUEUE", False)):
        return None
    backend = str(getattr(config, "QUEUE_BACKEND", "inprocess") or "").lower()
    if backend == "redis":
        return RedisExecutionQueue(
            str(getattr(config, "REDIS_URL", "redis://localhost:6379/0")),
        )
    if backend == "inprocess":
        return InProcessExecutionQueue()
    logger.warning("未知队列后端 %s，回退 in-process", backend)
    return None


__all__ = [
    "DEFAULT_FAILED_NAME",
    "DEFAULT_PROCESSING_NAME",
    "DEFAULT_QUEUE_NAME",
    "ExecutionQueue",
    "InProcessExecutionQueue",
    "QueueMessage",
    "QueueWorker",
    "RedisExecutionQueue",
    "build_execution_queue",
]
