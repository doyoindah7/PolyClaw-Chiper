"""In-process async event bus — pub/sub for decoupled components."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None]]


class EventBus:
    """Async pub/sub event bus.

    Bounded queue per subscriber — if queue full, drop oldest + log warning.
    This prevents slow consumers from blocking the whole system.
    """

    def __init__(self, queue_size: int = 1000):
        self._subscribers: dict[str, list[tuple[Handler, asyncio.Queue]]] = defaultdict(list)
        self._queue_size = queue_size
        self._tasks: list[asyncio.Task] = []
        self._dropped_count: dict[str, int] = defaultdict(int)

    def subscribe(self, topic: str, handler: Handler) -> None:
        """Subscribe handler to topic. Each handler gets its own queue."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[topic].append((handler, queue))

        async def _runner():
            while True:
                try:
                    payload = await queue.get()
                    await handler(payload)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("EventBus handler error on topic '%s': %s", topic, e, exc_info=True)

        task = asyncio.create_task(_runner(), name=f"bus_{topic}_{id(handler)}")
        self._tasks.append(task)
        logger.debug("EventBus: subscribed to '%s'", topic)

    async def publish(self, topic: str, payload: Any) -> None:
        """Publish payload to all subscribers of topic."""
        subs = self._subscribers.get(topic, [])
        for handler, queue in subs:
            try:
                if queue.full():
                    # Drop oldest to make room
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    self._dropped_count[topic] += 1
                    if self._dropped_count[topic] % 100 == 1:
                        logger.warning(
                            "EventBus: queue full for topic '%s', dropped %d events",
                            topic, self._dropped_count[topic],
                        )
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                self._dropped_count[topic] += 1

    async def close(self) -> None:
        """Cancel all subscriber tasks."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._subscribers.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "topics": list(self._subscribers.keys()),
            "subscribers_per_topic": {
                t: len(subs) for t, subs in self._subscribers.items()
            },
            "dropped_events": dict(self._dropped_count),
        }
