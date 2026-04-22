"""Tests for Telegram clarify callback bridging."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure the repo root is importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Minimal Telegram mock so TelegramAdapter can be imported
# ---------------------------------------------------------------------------
class _KeyboardButton:
    def __init__(self, text):
        self.text = text


class _ReplyKeyboardMarkup:
    def __init__(self, keyboard, **kwargs):
        self.keyboard = keyboard
        self.kwargs = kwargs


class _ReplyKeyboardRemove:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _ensure_telegram_mock():
    """Wire up the minimal mocks required to import TelegramAdapter."""
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    mod.ReplyKeyboardMarkup = _ReplyKeyboardMarkup
    mod.ReplyKeyboardRemove = _ReplyKeyboardRemove
    mod.KeyboardButton = _KeyboardButton

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules[name] = mod
    sys.modules["telegram.error"] = mod.error


_ensure_telegram_mock()

from gateway.config import Platform, PlatformConfig
from gateway.platforms.telegram import TelegramAdapter


def _make_adapter(extra=None):
    config = PlatformConfig(enabled=True, token="test-token", extra=extra or {})
    adapter = TelegramAdapter(config)
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


@pytest.mark.asyncio
async def test_build_clarify_callback_uses_reply_keyboard_and_resolves_text_reply():
    """Telegram clarify prompts should use reply keyboards and resolve from text replies."""
    adapter = _make_adapter()
    adapter._loop = asyncio.get_running_loop()
    sent_message = MagicMock()
    sent_message.message_id = 321
    adapter._bot.send_message = AsyncMock(return_value=sent_message)

    callback = adapter.build_clarify_callback(
        chat_id="12345",
        thread_id="99",
        user_id="777",
    )

    task = asyncio.create_task(asyncio.to_thread(callback, "Pick one?", ["Alpha", "Beta"]))
    await asyncio.sleep(0.05)

    adapter._bot.send_message.assert_awaited_once()
    prompt_kwargs = adapter._bot.send_message.await_args.kwargs
    assert prompt_kwargs["chat_id"] == 12345
    assert prompt_kwargs["message_thread_id"] == 99
    assert isinstance(prompt_kwargs["reply_markup"], _ReplyKeyboardMarkup)
    assert [row[0].text for row in prompt_kwargs["reply_markup"].keyboard] == ["Alpha", "Beta"]

    update = MagicMock()
    update.message = MagicMock()
    update.message.text = "Beta"
    update.message.chat = SimpleNamespace(id=12345)
    update.message.message_thread_id = 99
    update.message.from_user = SimpleNamespace(id=777)
    update.update_id = 1

    await adapter._handle_text_message(update, MagicMock())
    assert await asyncio.wait_for(task, timeout=1) == "Beta"

    assert adapter._bot.send_message.await_count == 2
    cleanup_kwargs = adapter._bot.send_message.await_args_list[1].kwargs
    assert cleanup_kwargs["chat_id"] == 12345
    assert cleanup_kwargs["message_thread_id"] == 99
    assert isinstance(cleanup_kwargs["reply_markup"], _ReplyKeyboardRemove)
    assert "收到" in cleanup_kwargs["text"]


@pytest.mark.asyncio
async def test_build_clarify_callback_times_out_and_clears_keyboard():
    """Telegram clarify prompts should time out cleanly when the user never responds."""
    adapter = _make_adapter(extra={"clarify_timeout": 0.01})
    adapter._loop = asyncio.get_running_loop()
    sent_message = MagicMock()
    sent_message.message_id = 321
    adapter._bot.send_message = AsyncMock(return_value=sent_message)

    callback = adapter.build_clarify_callback(
        chat_id="12345",
        thread_id="99",
        user_id="777",
    )

    result = await asyncio.to_thread(callback, "Pick one?", ["Alpha", "Beta"])
    assert result.startswith("The user did not provide a response")
    await asyncio.sleep(0.05)

    assert adapter._bot.send_message.await_count == 2
    cleanup_kwargs = adapter._bot.send_message.await_args_list[1].kwargs
    assert isinstance(cleanup_kwargs["reply_markup"], _ReplyKeyboardRemove)
    assert "timed out" in cleanup_kwargs["text"]
