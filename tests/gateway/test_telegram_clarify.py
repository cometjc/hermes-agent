"""Tests for Telegram clarify callback bridging."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sys
from pathlib import Path
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
def _ensure_telegram_mock():
    """Wire up the minimal mocks required to import TelegramAdapter."""
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

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

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


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
async def test_build_clarify_callback_exists_and_returns_callable():
    """TelegramAdapter should expose a sync clarify callback factory.

    The gateway needs this to bridge the agent thread's blocking clarify()
    call into Telegram's async send/callback flow.
    """
    adapter = _make_adapter()
    adapter._loop = asyncio.get_running_loop()

    callback = adapter.build_clarify_callback(
        chat_id="12345",
        thread_id="99",
        user_id="777",
    )

    assert callable(callback)


@pytest.mark.asyncio
async def test_clarify_callback_query_rejects_other_user():
    """Only the asker should be able to answer the clarification."""
    adapter = _make_adapter()
    future: concurrent.futures.Future[str] = concurrent.futures.Future()
    adapter._clarify_pending = {
        "clarify-2": {
            "future": future,
            "user_id": "777",
            "chat_id": "12345",
            "thread_id": "99",
            "message_id": "42",
            "choices": ["A", "B"],
        }
    }

    query = AsyncMock()
    query.data = "clr:clarify-2:0"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.message_id = 42
    query.from_user = MagicMock()
    query.from_user.id = 888
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()

    await adapter._handle_callback_query(update, context)

    assert not future.done()
    assert "clarify-2" in adapter._clarify_pending
    query.answer.assert_called_once()
    assert "not authorized" in query.answer.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_clarify_callback_query_resolves_pending_request():
    """A clarify inline button should resolve the pending sync callback."""
    adapter = _make_adapter()
    future: concurrent.futures.Future[str] = concurrent.futures.Future()
    adapter._clarify_pending = {
        "cl-1": {
            "future": future,
            "user_id": "777",
            "chat_id": "12345",
            "thread_id": "99",
            "message_id": "42",
            "choices": ["A", "B"],
        }
    }

    query = AsyncMock()
    query.data = "clr:cl-1:1"
    query.message = MagicMock()
    query.message.chat_id = 12345
    query.message.message_id = 42
    query.from_user = MagicMock()
    query.from_user.id = 777
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    context = MagicMock()

    await adapter._handle_callback_query(update, context)

    assert future.done()
    assert future.result() == "B"
    assert "cl-1" not in adapter._clarify_pending
    query.answer.assert_called_once()
    query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)
