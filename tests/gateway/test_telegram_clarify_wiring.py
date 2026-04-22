"""Tests for GatewayRunner Telegram clarify wiring."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource


class _StubTelegramAdapter:
    def build_clarify_callback(self, *, chat_id, thread_id, user_id):
        def _cb(question, choices):
            return f"{chat_id}|{thread_id}|{user_id}|{question}|{choices}"
        return _cb


@pytest.mark.parametrize("platform, expected", [(Platform.TELEGRAM, True), (Platform.DISCORD, False)])
def test_build_telegram_clarify_callback_only_for_telegram(platform, expected):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.adapters = {Platform.TELEGRAM: _StubTelegramAdapter()}

    source = SessionSource(
        platform=platform,
        chat_id="12345",
        user_id="777",
        thread_id="99",
    )

    callback = runner._build_telegram_clarify_callback(source)
    assert callable(callback) is expected
    if expected:
        assert callback("Q?", ["A", "B"]) == "12345|99|777|Q?|['A', 'B']"
