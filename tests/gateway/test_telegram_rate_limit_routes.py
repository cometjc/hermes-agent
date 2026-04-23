from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.telegram import TelegramAdapter
from gateway.platforms.telegram_rate_limit import TelegramRateLimitedBotProxy


class SpyDispatcher:
    def __init__(self):
        self.jobs = []

    async def dispatch(self, job):
        self.jobs.append(job)
        return await job.runner(job)


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        return SimpleNamespace(message_id=101)

    async def edit_message_text(self, **kwargs):
        self.calls.append(("edit_message_text", kwargs))
        return SimpleNamespace(message_id=kwargs.get("message_id", 101))

    async def send_chat_action(self, **kwargs):
        self.calls.append(("send_chat_action", kwargs))
        return None

    async def send_voice(self, **kwargs):
        self.calls.append(("send_voice", kwargs))
        return SimpleNamespace(message_id=102)

    async def send_document(self, **kwargs):
        self.calls.append(("send_document", kwargs))
        return SimpleNamespace(message_id=103)

    async def send_video(self, **kwargs):
        self.calls.append(("send_video", kwargs))
        return SimpleNamespace(message_id=104)

    async def send_photo(self, **kwargs):
        self.calls.append(("send_photo", kwargs))
        return SimpleNamespace(message_id=105)

    async def send_animation(self, **kwargs):
        self.calls.append(("send_animation", kwargs))
        return SimpleNamespace(message_id=106)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name, kwargs, expected_kind",
    [
        (
            "send_update_prompt",
            {"chat_id": "123", "prompt": "Need input", "default": "yes", "session_key": "sess"},
            "send_message",
        ),
        (
            "send_exec_approval",
            {"chat_id": "123", "command": "rm -rf /tmp/nope", "session_key": "sess", "metadata": {"thread_id": "7"}},
            "send_message",
        ),
        (
            "send_typing",
            {"chat_id": "123", "metadata": {"thread_id": "7"}},
            "send_chat_action",
        ),
    ],
)
async def test_adapter_visible_routes_go_through_dispatcher(method_name, kwargs, expected_kind):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="bot-token"))
    spy = SpyDispatcher()
    bot = FakeBot()
    adapter._telegram_dispatcher = spy
    adapter._bot = TelegramRateLimitedBotProxy(bot, spy)

    result = await getattr(adapter, method_name)(**kwargs)

    assert spy.jobs, "expected dispatcher to receive a job"
    assert spy.jobs[0].kind == expected_kind
    assert bot.calls[0][0] == expected_kind
    if method_name == "send_typing":
        assert result is None
    else:
        assert result.success is True


@pytest.mark.asyncio
async def test_callback_query_edits_go_through_rate_limited_helper():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="bot-token"))
    spy = SpyDispatcher()
    bot = FakeBot()
    adapter._telegram_dispatcher = spy
    adapter._bot = TelegramRateLimitedBotProxy(bot, spy)

    query = SimpleNamespace(
        message=SimpleNamespace(chat=SimpleNamespace(id=123), message_id=456),
        edit_message_text=bot.edit_message_text,
    )

    await adapter._rate_limited_query_edit_message_text(query, text="updated")

    assert spy.jobs[0].kind == "query.edit_message_text"
    assert spy.jobs[0].coalesce_key == "123:456"
    assert bot.calls[0][0] == "edit_message_text"


class TestTelegramSendMessageToolSource:
    def test_telegram_tool_routes_through_shared_dispatcher(self):
        path = Path(__file__).resolve().parents[2] / "tools" / "send_message_tool.py"
        src = path.read_text(encoding="utf-8")
        assert "TelegramOutboundDispatcher" in src
        assert "TelegramRateLimitedBotProxy" in src
        assert "rate_limited_bot = TelegramRateLimitedBotProxy" in src
