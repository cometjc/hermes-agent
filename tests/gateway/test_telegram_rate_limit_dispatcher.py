from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from gateway.platforms.telegram_rate_limit import TelegramOutboundDispatcher, TelegramOutboundJob


class RetryAfterError(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"RetryAfter({retry_after})")
        self.retry_after = retry_after


@pytest.mark.asyncio
async def test_same_chat_fifo_and_lane_isolation():
    calls: list[tuple[str, Any]] = []
    chat_a_release = asyncio.Event()

    async def run(job: TelegramOutboundJob):
        calls.append((job.chat_id, job.payload))
        if job.chat_id == "chat-a" and job.payload == "a1":
            await chat_a_release.wait()
        return {"chat_id": job.chat_id, "payload": job.payload}

    dispatcher = TelegramOutboundDispatcher()
    try:
        a1 = asyncio.create_task(
            dispatcher.dispatch(
                TelegramOutboundJob(chat_id="chat-a", kind="stream_edit", payload="a1", runner=run)
            )
        )
        a2 = asyncio.create_task(
            dispatcher.dispatch(
                TelegramOutboundJob(chat_id="chat-a", kind="stream_edit", payload="a2", runner=run)
            )
        )
        b1 = asyncio.create_task(
            dispatcher.dispatch(
                TelegramOutboundJob(chat_id="chat-b", kind="stream_edit", payload="b1", runner=run)
            )
        )

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls[0] == ("chat-a", "a1")
        assert ("chat-b", "b1") in calls

        result_b = await asyncio.wait_for(b1, timeout=0.5)
        assert result_b == {"chat_id": "chat-b", "payload": "b1"}
        assert not a1.done(), "chat-a should still be blocked while chat-b completes"

        chat_a_release.set()
        assert await a1 == {"chat_id": "chat-a", "payload": "a1"}
        assert await a2 == {"chat_id": "chat-a", "payload": "a2"}
        assert calls.index(("chat-a", "a1")) < calls.index(("chat-a", "a2"))
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_retry_after_requeues_only_affected_lane():
    calls: list[tuple[str, Any]] = []
    first_attempt = True

    async def run(job: TelegramOutboundJob):
        nonlocal first_attempt
        calls.append((job.chat_id, job.payload))
        if job.chat_id == "chat-a" and first_attempt:
            first_attempt = False
            raise RetryAfterError(0.05)
        return {"chat_id": job.chat_id, "payload": job.payload}

    dispatcher = TelegramOutboundDispatcher()
    try:
        a = asyncio.create_task(
            dispatcher.dispatch(
                TelegramOutboundJob(chat_id="chat-a", kind="send", payload="a", runner=run)
            )
        )
        await asyncio.sleep(0)
        b = await dispatcher.dispatch(
            TelegramOutboundJob(chat_id="chat-b", kind="send", payload="b", runner=run)
        )
        assert b == {"chat_id": "chat-b", "payload": "b"}
        assert calls[0] == ("chat-a", "a")
        assert ("chat-b", "b") in calls
        assert await a == {"chat_id": "chat-a", "payload": "a"}
        assert calls.count(("chat-a", "a")) == 2
    finally:
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_same_message_streaming_updates_coalesce_before_send():
    calls: list[Any] = []

    async def run(job: TelegramOutboundJob):
        calls.append(job.payload)
        return job.payload

    dispatcher = TelegramOutboundDispatcher()
    try:
        tasks = [
            asyncio.create_task(
                dispatcher.dispatch(
                    TelegramOutboundJob(
                        chat_id="chat-a",
                        kind="stream_edit",
                        coalesce_key="msg-1",
                        payload=payload,
                        runner=run,
                    )
                )
            )
            for payload in ["old", "older", "latest"]
        ]
        results = await asyncio.gather(*tasks)
        assert results == ["latest", "latest", "latest"]
        assert calls == ["latest"]
    finally:
        await dispatcher.aclose()
