from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Optional

logger = logging.getLogger(__name__)


class TelegramRetryAfterOwnerError(RuntimeError):
    """Raised when a queue job needs to be rescheduled after flood control."""


@dataclass(slots=True)
class TelegramOutboundJob:
    chat_id: str
    kind: str
    runner: Callable[["TelegramOutboundJob"], Awaitable[Any]]
    thread_id: Optional[str] = None
    coalesce_key: Optional[str] = None
    payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    futures: list[asyncio.Future[Any]] = field(default_factory=list, repr=False)


@dataclass
class _LaneState:
    queue: Deque[TelegramOutboundJob] = field(default_factory=deque)
    event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task[Any]] = None


class TelegramOutboundDispatcher:
    """Queue Telegram outbound jobs per lane, with queue-time coalescing.

    The dispatcher preserves per-chat FIFO while allowing different chats to
    progress independently. It owns retry-after sleeps for the jobs it runs,
    but delegates transport rate limiting to the underlying bot layer.
    """

    def __init__(self) -> None:
        self._lanes: dict[str, _LaneState] = {}
        self._lock = asyncio.Lock()
        self._closing = False
        self._close_timeout = 5.0
        self._queued_jobs = 0
        self._coalesced_jobs = 0

    @staticmethod
    def _lane_key(job: TelegramOutboundJob) -> str:
        thread = job.thread_id or ""
        return f"{job.chat_id}:{thread}"

    @staticmethod
    def _can_coalesce(left: TelegramOutboundJob, right: TelegramOutboundJob) -> bool:
        return (
            left.kind == right.kind
            and left.coalesce_key is not None
            and left.coalesce_key == right.coalesce_key
        )

    @staticmethod
    def _merge_jobs(target: TelegramOutboundJob, source: TelegramOutboundJob) -> None:
        target.payload = source.payload
        target.metadata = source.metadata
        target.futures.extend(source.futures)

    def _ensure_lane(self, lane_key: str) -> _LaneState:
        lane = self._lanes.get(lane_key)
        if lane is None:
            lane = _LaneState()
            self._lanes[lane_key] = lane
        return lane

    async def dispatch(self, job: TelegramOutboundJob) -> Any:
        if self._closing:
            raise RuntimeError("Telegram outbound dispatcher is closing")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        job.futures.append(future)
        lane_key = self._lane_key(job)

        async with self._lock:
            lane = self._ensure_lane(lane_key)
            if lane.queue and self._can_coalesce(lane.queue[-1], job):
                self._merge_jobs(lane.queue[-1], job)
                self._coalesced_jobs += 1
            else:
                lane.queue.append(job)
                self._queued_jobs += 1
            if lane.task is None or lane.task.done():
                lane.task = asyncio.create_task(self._lane_worker(lane_key, lane))
            loop.call_soon(lane.event.set)

        return await future

    async def _lane_worker(self, lane_key: str, lane: _LaneState) -> None:
        while True:
            async with self._lock:
                if lane.queue:
                    job = lane.queue.popleft()
                    self._queued_jobs = max(0, self._queued_jobs - 1)
                    while lane.queue and self._can_coalesce(job, lane.queue[0]):
                        next_job = lane.queue.popleft()
                        self._queued_jobs = max(0, self._queued_jobs - 1)
                        self._merge_jobs(job, next_job)
                        self._coalesced_jobs += 1
                    wait_event = None
                else:
                    if self._closing:
                        lane.task = None
                        self._lanes.pop(lane_key, None)
                        return
                    wait_event = lane.event
                    job = None

            if wait_event is not None:
                await wait_event.wait()
                wait_event.clear()
                continue

            assert job is not None
            await self._run_job(job)

    async def _run_job(self, job: TelegramOutboundJob) -> None:
        while True:
            try:
                result = await job.runner(job)
            except Exception as exc:  # noqa: BLE001
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is not None:
                    await asyncio.sleep(float(retry_after))
                    continue
                for fut in job.futures:
                    if not fut.done():
                        fut.set_exception(exc)
                return

            for fut in job.futures:
                if not fut.done():
                    fut.set_result(result)
            return

    async def aclose(self, timeout: float | None = None) -> None:
        self._closing = True
        self._close_timeout = timeout if timeout is not None else self._close_timeout
        async with self._lock:
            for lane in self._lanes.values():
                lane.event.set()
        tasks = [lane.task for lane in self._lanes.values() if lane.task is not None]
        if not tasks:
            return
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=self._close_timeout)
        except asyncio.TimeoutError:
            for task in tasks:
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @property
    def queued_jobs(self) -> int:
        return self._queued_jobs

    @property
    def coalesced_jobs(self) -> int:
        return self._coalesced_jobs


class TelegramRateLimitedBotProxy:
    """Proxy PTB bot methods through the Telegram outbound dispatcher."""

    _RATE_LIMITED_METHODS = {
        "edit_message_text",
        "send_animation",
        "send_chat_action",
        "send_audio",
        "send_document",
        "send_message",
        "send_photo",
        "send_video",
        "send_voice",
    }

    def __init__(self, bot: Any, dispatcher: TelegramOutboundDispatcher) -> None:
        self._bot = bot
        self._dispatcher = dispatcher

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._bot, name)
        if name not in self._RATE_LIMITED_METHODS or not callable(attr):
            return attr

        async def _wrapped(*args: Any, **kwargs: Any) -> Any:
            chat_id = kwargs.get("chat_id")
            thread_id = kwargs.get("message_thread_id")
            coalesce_key = None
            if name == "edit_message_text":
                message_id = kwargs.get("message_id")
                if chat_id is not None and message_id is not None:
                    coalesce_key = f"{chat_id}:{message_id}"
            if chat_id is None:
                raise RuntimeError(f"{name} requires chat_id for rate limiting")

            async def _run(_: TelegramOutboundJob) -> Any:
                return await attr(*args, **kwargs)

            job = TelegramOutboundJob(
                chat_id=str(chat_id),
                thread_id=str(thread_id) if thread_id is not None else None,
                kind=name,
                coalesce_key=coalesce_key,
                payload={"args": args, "kwargs": kwargs},
                runner=_run,
            )
            return await self._dispatcher.dispatch(job)

        return _wrapped
