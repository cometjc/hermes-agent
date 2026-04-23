"""Tests for Telegram progress-batch budgeting helpers."""

from types import SimpleNamespace

import pytest

from gateway.platforms.base import BasePlatformAdapter, utf16_len
from gateway.platforms.telegram import TelegramAdapter
from gateway.progress_batch import (
    send_telegram_progress_lines,
    telegram_progress_chunks,
    telegram_progress_fits,
    telegram_progress_rendered_length,
    telegram_progress_rendered_text,
)


def _make_adapter():
    adapter = object.__new__(TelegramAdapter)
    adapter.MAX_MESSAGE_LENGTH = 4096
    return adapter


class _FakeBot:
    def __init__(self):
        self.calls = []
        self._next_id = 1

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        msg = SimpleNamespace(message_id=str(self._next_id))
        self._next_id += 1
        return msg


class _FakeTelegramSender:
    MAX_MESSAGE_LENGTH = 20

    def __init__(self):
        self._bot = _FakeBot()

    def format_message(self, content: str) -> str:
        return content

    @staticmethod
    def truncate_message(content, max_length, len_fn=None):
        return BasePlatformAdapter.truncate_message(content, max_length, len_fn=len_fn)

    def _metadata_thread_id(self, metadata):
        return metadata.get("thread_id") if metadata else None

    def _message_thread_id_for_send(self, thread_id):
        return None

    def _link_preview_kwargs(self):
        return {}


def test_rendered_length_uses_markdownv2_and_utf16():
    adapter = _make_adapter()
    line = "tool.started: foo_bar 😀"

    rendered = telegram_progress_rendered_text(adapter, [line])
    expected = adapter.format_message(line)

    assert rendered == expected
    assert telegram_progress_rendered_length(adapter, [line]) == utf16_len(expected)
    assert telegram_progress_rendered_length(adapter, [line]) > len(line)


def test_exact_safe_limit_and_dedup_suffix_overflow():
    adapter = _make_adapter()
    base = "tool.started: foo_bar 😀"
    limit = telegram_progress_rendered_length(adapter, [base])

    assert telegram_progress_fits(adapter, [base], limit=limit)
    assert not telegram_progress_fits(adapter, [f"{base} (×2)"], limit=limit)


def test_chunk_suffix_is_markdownv2_safe():
    adapter = _make_adapter()
    long_line = "tool.started: " + ("x" * 5000)

    chunks = telegram_progress_chunks(adapter, [long_line])

    assert len(chunks) > 1
    assert all(utf16_len(chunk) <= adapter.MAX_MESSAGE_LENGTH for chunk in chunks)
    assert all(chunk.endswith("\\)") for chunk in chunks)


@pytest.mark.asyncio
async def test_oversized_progress_line_returns_last_chunk_id():
    adapter = _FakeTelegramSender()
    long_line = "tool.started: " + ("x" * 200)

    result = await send_telegram_progress_lines(
        adapter,
        chat_id="12345",
        lines=[long_line],
        metadata={"thread_id": "17585"},
    )

    assert result.success is True
    assert result.message_id == str(len(adapter._bot.calls))
    assert len(adapter._bot.calls) > 1
