"""将已持久化 WorkflowEvent 转换为可续读的 SSE 数据流。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from app.persistence.repositories import ResearchRepository
from app.schemas.common import TaskStatus
from app.schemas.event import WorkflowEvent, WorkflowEventType


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.COMPLETED_WITH_WARNINGS,
    TaskStatus.FAILED,
    TaskStatus.BUDGET_EXCEEDED,
}
TERMINAL_EVENT_TYPES = {
    WorkflowEventType.WORKFLOW_COMPLETED,
    WorkflowEventType.WORKFLOW_FAILED,
    WorkflowEventType.WORKFLOW_BUDGET_EXCEEDED,
}


class EventStreamService:
    """从数据库分批读取事件，并按 SSE 协议输出游标、类型和 JSON。"""

    def __init__(
        self,
        repository: ResearchRepository,
        *,
        poll_interval_seconds: float = 0.25,
        heartbeat_interval_seconds: float = 10.0,
        batch_size: int = 100,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds 不能为负数")
        if heartbeat_interval_seconds < 0:
            raise ValueError("heartbeat_interval_seconds 不能为负数")
        if batch_size < 1:
            raise ValueError("batch_size 必须至少为 1")
        self._repository = repository
        self._poll_interval = poll_interval_seconds
        self._heartbeat_interval = heartbeat_interval_seconds
        self._batch_size = batch_size
        self._sleeper = sleeper

    async def stream(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[str]:
        """回放游标后的事件，然后轮询新事件直到终态或客户端断开。"""

        cursor = after_sequence
        last_heartbeat = time.monotonic()
        while True:
            if is_disconnected is not None and await is_disconnected():
                return

            events = self._repository.get_events(
                task_id,
                after_sequence=cursor,
                limit=self._batch_size,
            )
            if events:
                for event in events:
                    cursor = event.sequence
                    yield format_sse_event(event)
                if len(events) >= self._batch_size:
                    continue
                continue

            task = self._repository.get_task(task_id)
            if task is None:
                return
            if task.status in TERMINAL_STATUSES and not task.is_running:
                latest = self._repository.get_latest_event(task_id)
                if (
                    latest is not None
                    and latest.event_type in TERMINAL_EVENT_TYPES
                    and latest.sequence <= cursor
                ):
                    return

            now = time.monotonic()
            if now - last_heartbeat >= self._heartbeat_interval:
                yield ": heartbeat\n\n"
                last_heartbeat = now
            await self._sleeper(self._poll_interval)


def format_sse_event(event: WorkflowEvent) -> str:
    """生成标准 SSE 帧；sequence 同时承担断线续读游标。"""

    return (
        f"id: {event.sequence}\n"
        f"event: {event.event_type.value}\n"
        f"data: {event.model_dump_json()}\n\n"
    )
