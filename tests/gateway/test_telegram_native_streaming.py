"""Tests for Telegram native streaming support.

These tests focus on the draft-based native stream path used by Telegram.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult


class FakeNetworkError(Exception):
    pass


class FakeBadRequest(FakeNetworkError):
    pass


class FakeTimedOut(FakeNetworkError):
    pass


_fake_telegram = types.ModuleType("telegram")
_fake_telegram.Update = object
_fake_telegram.Bot = object
_fake_telegram.Message = object
_fake_telegram.InlineKeyboardButton = object
_fake_telegram.InlineKeyboardMarkup = object
_fake_telegram_error = types.ModuleType("telegram.error")
_fake_telegram_error.NetworkError = FakeNetworkError
_fake_telegram_error.BadRequest = FakeBadRequest
_fake_telegram_error.TimedOut = FakeTimedOut
_fake_telegram.error = _fake_telegram_error
_fake_telegram_constants = types.ModuleType("telegram.constants")
_fake_telegram_constants.ParseMode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
_fake_telegram_constants.ChatType = SimpleNamespace(
    GROUP="group",
    SUPERGROUP="supergroup",
    CHANNEL="channel",
)
_fake_telegram.constants = _fake_telegram_constants
_fake_telegram_ext = types.ModuleType("telegram.ext")
_fake_telegram_ext.Application = object
_fake_telegram_ext.CommandHandler = object
_fake_telegram_ext.CallbackQueryHandler = object
_fake_telegram_ext.MessageHandler = object
_fake_telegram_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
_fake_telegram_ext.filters = object
_fake_telegram_request = types.ModuleType("telegram.request")
_fake_telegram_request.HTTPXRequest = object


@pytest.fixture(autouse=True)
def _inject_fake_telegram(monkeypatch):
    monkeypatch.setitem(sys.modules, "telegram", _fake_telegram)
    monkeypatch.setitem(sys.modules, "telegram.error", _fake_telegram_error)
    monkeypatch.setitem(sys.modules, "telegram.constants", _fake_telegram_constants)
    monkeypatch.setitem(sys.modules, "telegram.ext", _fake_telegram_ext)
    monkeypatch.setitem(sys.modules, "telegram.request", _fake_telegram_request)


class _FakeNativeBot:
    def __init__(self, *, fail_post: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail_post = fail_post

    async def _post(self, endpoint: str, data: dict | None = None, **kwargs):
        self.calls.append((endpoint, data or {}))
        if self.fail_post:
            raise FakeNetworkError("native endpoint unavailable")
        return {"draft_id": (data or {}).get("draft_id", 1), "message_id": 987}


@pytest.mark.asyncio
async def test_telegram_native_stream_uses_send_message_draft():
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.config = PlatformConfig(enabled=True, token="fake-token")
    adapter._config = adapter.config
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter._connected = True
    adapter._bot = _FakeNativeBot()
    adapter._reply_to_mode = "first"
    adapter._disable_link_previews = False
    adapter.format_message = lambda text: text
    adapter._link_preview_kwargs = lambda: {}

    result = await adapter.send_stream(
        chat_id="-1001234567890",
        content="Hello native stream",
        message_id=None,
        metadata={"thread_id": "7"},
    )

    assert result.success is True
    assert result.message_id == "1"
    assert adapter._bot.calls[0][0] == "sendMessageDraft"
    assert adapter._bot.calls[0][1]["chat_id"] == -1001234567890
    assert adapter._bot.calls[0][1]["draft_id"] == 1
    assert adapter._bot.calls[0][1]["text"] == "Hello native stream"
    assert adapter._bot.calls[0][1]["message_thread_id"] == 7


@pytest.mark.asyncio
async def test_telegram_native_stream_finalize_keeps_using_draft_endpoint():
    """Final native streaming updates should not fall back to a brand-new send.

    This regresses the duplicate-message bug where the draft stayed visible
    with a cursor, then Telegram received a second full message at finalize.
    """
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.config = PlatformConfig(enabled=True, token="fake-token")
    adapter._config = adapter.config
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter._connected = True
    adapter._bot = _FakeNativeBot()
    adapter._reply_to_mode = "first"
    adapter._disable_link_previews = False
    adapter.format_message = lambda text: text
    adapter._link_preview_kwargs = lambda: {}
    adapter.send = AsyncMock(side_effect=AssertionError("final native streaming must not call send()"))

    result = await adapter.send_stream(
        chat_id="-1001234567890",
        content="Hello native stream final",
        message_id="42",
        finalize=True,
        metadata={"thread_id": "7"},
    )

    assert result.success is True
    assert adapter.send.await_count == 0
    assert adapter._bot.calls[0][0] == "sendMessageDraft"
    assert adapter._bot.calls[0][1]["draft_id"] == 42
    assert adapter._bot.calls[0][1]["text"] == "Hello native stream final"


@pytest.mark.asyncio
async def test_telegram_native_stream_falls_back_when_native_call_fails():
    from gateway.platforms.telegram import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    adapter.config = PlatformConfig(enabled=True, token="fake-token")
    adapter._config = adapter.config
    adapter._platform = Platform.TELEGRAM
    adapter.platform = Platform.TELEGRAM
    adapter._connected = True
    adapter._bot = _FakeNativeBot(fail_post=True)
    adapter._reply_to_mode = "first"
    adapter._disable_link_previews = False
    adapter.format_message = lambda text: text
    adapter._link_preview_kwargs = lambda: {}
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="fallback-send"))
    adapter.edit_message = AsyncMock(return_value=SendResult(success=True, message_id="fallback-edit"))

    result = await adapter.send_stream(
        chat_id="-1001234567890",
        content="Hello fallback",
        message_id=None,
        metadata={"thread_id": "7"},
    )

    assert result.success is True
    adapter.send.assert_awaited()
    adapter.edit_message.assert_not_awaited()
