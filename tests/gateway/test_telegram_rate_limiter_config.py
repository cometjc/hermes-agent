from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import PlatformConfig
from gateway.platforms import telegram as tg
from gateway.platforms.telegram import TelegramAdapter


class FakeLimiter:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeBuilder:
    def __init__(self):
        self.calls = []
        self.limiter = None

    def token(self, value):
        self.calls.append(("token", value))
        return self

    def base_url(self, value):
        self.calls.append(("base_url", value))
        return self

    def base_file_url(self, value):
        self.calls.append(("base_file_url", value))
        return self

    def rate_limiter(self, value):
        self.calls.append(("rate_limiter", value))
        self.limiter = value
        return self

    def request(self, value):
        self.calls.append(("request", value))
        return self

    def get_updates_request(self, value):
        self.calls.append(("get_updates_request", value))
        return self

    def build(self):
        async def _noop(*args, **kwargs):
            return None

        bot = SimpleNamespace(delete_webhook=_noop)
        app = SimpleNamespace(
            bot=bot,
            add_handler=lambda *args, **kwargs: None,
            initialize=_noop,
            start=_noop,
            updater=SimpleNamespace(start_webhook=_noop, start_polling=_noop),
        )
        return app


@pytest.mark.asyncio
async def test_connect_wires_ptb_rate_limiter(monkeypatch):
    builder = FakeBuilder()

    class FakeApplication:
        @classmethod
        def builder(cls):
            return builder

    async def _discover_fallback_ips(*args, **kwargs):
        return []

    monkeypatch.setattr(tg, "TELEGRAM_AVAILABLE", True)
    monkeypatch.setattr(tg, "AIORateLimiter", FakeLimiter, raising=False)
    monkeypatch.setattr(tg, "Application", FakeApplication)
    monkeypatch.setattr(tg.TelegramAdapter, "_acquire_platform_lock", lambda *args, **kwargs: True)
    monkeypatch.setattr(tg, "discover_fallback_ips", _discover_fallback_ips)
    monkeypatch.setattr(tg, "resolve_proxy_url", lambda *args, **kwargs: None)

    monkeypatch.setenv("HERMES_TELEGRAM_DISABLE_FALLBACK_IPS", "1")
    monkeypatch.delenv("TELEGRAM_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)

    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="bot-token"))
    ok = await adapter.connect()

    assert ok is True
    assert builder.limiter is not None
    assert isinstance(builder.limiter, FakeLimiter)
    assert builder.limiter.kwargs == {
        "overall_max_rate": 30,
        "overall_time_period": 1,
        "group_max_rate": 20,
        "group_time_period": 60,
    }
    assert any(name == "rate_limiter" for name, _ in builder.calls)
