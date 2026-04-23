"""Helpers for Telegram progress batching and rollover."""

from __future__ import annotations

import re
from typing import Any, Optional

from gateway.platforms.base import SendResult, utf16_len

_SAFE_HEADROOM = 100


def telegram_progress_safe_limit(adapter: Any, *, headroom: int = _SAFE_HEADROOM) -> int:
    """Return a conservative Telegram-safe payload budget for progress text."""

    raw_limit = getattr(adapter, "MAX_MESSAGE_LENGTH", 4096)
    try:
        raw_limit = int(raw_limit)
    except (TypeError, ValueError):
        raw_limit = 4096
    try:
        headroom = int(headroom)
    except (TypeError, ValueError):
        headroom = _SAFE_HEADROOM
    return max(500, raw_limit - headroom)


def telegram_progress_rendered_text(adapter: Any, lines: list[str]) -> str:
    """Render a progress batch using the Telegram adapter's canonical formatter."""

    if not lines:
        return ""
    return adapter.format_message("\n".join(lines))


def telegram_progress_rendered_length(adapter: Any, lines: list[str]) -> int:
    """Measure the UTF-16 length of the rendered Telegram progress payload."""

    return utf16_len(telegram_progress_rendered_text(adapter, lines))


def telegram_progress_fits(
    adapter: Any,
    lines: list[str],
    *,
    limit: Optional[int] = None,
    headroom: int = _SAFE_HEADROOM,
) -> bool:
    """Return ``True`` when the rendered batch fits within the chosen budget."""

    if limit is None:
        limit = telegram_progress_safe_limit(adapter, headroom=headroom)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = telegram_progress_safe_limit(adapter, headroom=headroom)
    return telegram_progress_rendered_length(adapter, lines) <= limit


def telegram_progress_chunks(adapter: Any, lines: list[str]) -> list[str]:
    """Split a rendered progress payload into Telegram-safe chunks."""

    rendered = telegram_progress_rendered_text(adapter, lines)
    if not rendered or not rendered.strip():
        return []

    raw_limit = getattr(adapter, "MAX_MESSAGE_LENGTH", 4096)
    try:
        raw_limit = int(raw_limit)
    except (TypeError, ValueError):
        raw_limit = 4096

    chunks = adapter.truncate_message(rendered, raw_limit, len_fn=utf16_len)
    if len(chunks) > 1:
        # BasePlatformAdapter.truncate_message appends a raw " (1/2)" suffix.
        # Escape the parentheses so Telegram MarkdownV2 accepts the chunk.
        chunks = [
            re.sub(
                r" \((\d+)/(\d+)\)$",
                lambda m: f" \\({m.group(1)}/{m.group(2)}\\)",
                chunk,
            )
            for chunk in chunks
        ]
    return chunks


async def send_telegram_progress_lines(
    adapter: Any,
    chat_id: str,
    lines: list[str],
    *,
    metadata: Optional[dict[str, Any]] = None,
    reply_to: Optional[str] = None,
) -> SendResult:
    """Send a rendered Telegram progress payload, returning the *last* chunk id."""

    bot = getattr(adapter, "_bot", None)
    if not bot:
        return SendResult(success=False, error="Not connected")

    chunks = telegram_progress_chunks(adapter, lines)
    if not chunks:
        return SendResult(success=True, message_id=None)

    thread_id = None
    if hasattr(adapter, "_metadata_thread_id"):
        thread_id = adapter._metadata_thread_id(metadata)
    message_thread_id = None
    if hasattr(adapter, "_message_thread_id_for_send"):
        message_thread_id = adapter._message_thread_id_for_send(thread_id)

    send_kwargs_base: dict[str, Any] = {
        "chat_id": int(chat_id),
        **getattr(adapter, "_link_preview_kwargs", lambda: {})(),
    }
    if message_thread_id is not None:
        send_kwargs_base["message_thread_id"] = message_thread_id
    if reply_to not in (None, ""):
        send_kwargs_base["reply_to_message_id"] = int(reply_to)

    try:  # pragma: no cover - depends on telegram package availability
        from telegram.constants import ParseMode  # type: ignore
    except Exception:  # pragma: no cover - tests may inject a mock telegram module
        ParseMode = None

    if ParseMode is not None:
        send_kwargs_base["parse_mode"] = ParseMode.MARKDOWN_V2

    last_msg = None
    message_ids: list[str] = []
    for chunk in chunks:
        msg = await bot.send_message(text=chunk, **send_kwargs_base)
        last_msg = msg
        message_ids.append(str(getattr(msg, "message_id", "")))

    return SendResult(
        success=True,
        message_id=str(getattr(last_msg, "message_id", "")) or None,
        raw_response={"message_ids": message_ids},
    )
