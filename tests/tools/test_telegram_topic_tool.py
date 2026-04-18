"""Tests for tools/telegram_topic_tool.py."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.telegram_topic_tool import (
    _classify_error,
    _parse_telegram_target,
    telegram_topic_tool,
)


def _install_telegram_bot_mock(monkeypatch, bot):
    telegram_mod = SimpleNamespace(Bot=lambda token: bot)
    monkeypatch.setitem(sys.modules, "telegram", telegram_mod)


def _install_gateway_config(monkeypatch, enabled=True, token="TELEGRAM_TOKEN"):
    from gateway.config import Platform
    pconfig = SimpleNamespace(enabled=enabled, token=token, extra={})
    config = SimpleNamespace(platforms={Platform.TELEGRAM: pconfig})
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)


def _run_sync(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _patch_run_async(monkeypatch):
    # _handle_write_op calls model_tools._run_async; use a simple sync bridge in tests.
    import model_tools
    monkeypatch.setattr(model_tools, "_run_async", _run_sync, raising=False)


class TestParseTarget:
    def test_create_target_without_thread(self):
        chat_id, thread_id, err = _parse_telegram_target("telegram:-1001234567890")
        assert err is None
        assert chat_id == "-1001234567890"
        assert thread_id is None

    def test_target_with_thread(self):
        chat_id, thread_id, err = _parse_telegram_target("telegram:-1001234567890:17585")
        assert err is None
        assert chat_id == "-1001234567890"
        assert thread_id == "17585"

    def test_wrong_platform(self):
        _, _, err = _parse_telegram_target("discord:999")
        assert err and "only supports telegram" in err

    def test_missing_chat_id(self):
        _, _, err = _parse_telegram_target("telegram:")
        assert err and "telegram:<chat_id>" in err

    def test_empty_target(self):
        _, _, err = _parse_telegram_target("")
        assert err and "required" in err.lower()


class TestErrorClassification:
    @pytest.mark.parametrize("msg,code", [
        ("Bad Request: not enough rights to manage topics", "no_rights"),
        ("Bad Request: CHAT_ADMIN_REQUIRED", "no_rights"),
        ("Forbidden: bot needs administrator rights", "no_rights"),
        ("Bad Request: message thread not found", "topic_not_found"),
        ("Bad Request: TOPIC_ID_INVALID", "topic_not_found"),
        ("Bad Request: TOPIC_CLOSED", "topic_closed"),
        ("Bad Request: chat not found", "chat_not_found"),
        ("mystery failure", "unknown"),
    ])
    def test_classification(self, msg, code):
        assert _classify_error(Exception(msg)) == code


class TestValidation:
    def test_unknown_action(self):
        result = json.loads(telegram_topic_tool({"action": "nuke", "target": "telegram:-100"}))
        assert "error" in result and "Unknown action" in result["error"]

    def test_create_requires_name(self):
        result = json.loads(telegram_topic_tool({"action": "create", "target": "telegram:-100"}))
        assert "error" in result and "name" in result["error"].lower()

    def test_create_rejects_thread_in_target(self):
        result = json.loads(telegram_topic_tool({"action": "create", "target": "telegram:-100:5", "name": "x"}))
        assert "error" in result and "without thread_id" in result["error"]

    def test_close_requires_thread(self):
        result = json.loads(telegram_topic_tool({"action": "close", "target": "telegram:-100"}))
        assert "error" in result and "thread_id" in result["error"]

    def test_rename_requires_name(self):
        result = json.loads(telegram_topic_tool({"action": "rename", "target": "telegram:-100:5"}))
        assert "error" in result and "name" in result["error"].lower()

    def test_delete_requires_confirm(self):
        result = json.loads(telegram_topic_tool({"action": "delete", "target": "telegram:-100:5"}))
        assert result.get("code") == "confirm_required"

    def test_delete_confirm_must_be_true_not_truthy(self):
        result = json.loads(telegram_topic_tool({"action": "delete", "target": "telegram:-100:5", "confirm": "yes"}))
        assert result.get("code") == "confirm_required"


class TestWriteOps:
    def test_create_calls_bot_and_returns_thread_id(self, monkeypatch):
        bot = MagicMock()
        bot.create_forum_topic = AsyncMock(
            return_value=SimpleNamespace(message_thread_id=17585, name="discuss-q3")
        )
        _install_telegram_bot_mock(monkeypatch, bot)
        _install_gateway_config(monkeypatch)

        result = json.loads(telegram_topic_tool({
            "action": "create",
            "target": "telegram:-1001234567890",
            "name": "discuss-q3",
        }))

        assert result["success"] is True
        assert result["action"] == "create"
        assert result["thread_id"] == "17585"
        assert result["name"] == "discuss-q3"
        bot.create_forum_topic.assert_awaited_once_with(chat_id=-1001234567890, name="discuss-q3")

    def test_close_calls_bot(self, monkeypatch):
        bot = MagicMock()
        bot.close_forum_topic = AsyncMock(return_value=True)
        _install_telegram_bot_mock(monkeypatch, bot)
        _install_gateway_config(monkeypatch)

        result = json.loads(telegram_topic_tool({
            "action": "close",
            "target": "telegram:-1001234567890:17585",
        }))

        assert result["success"] is True
        assert result["thread_id"] == "17585"
        bot.close_forum_topic.assert_awaited_once_with(chat_id=-1001234567890, message_thread_id=17585)

    def test_reopen_calls_bot(self, monkeypatch):
        bot = MagicMock()
        bot.reopen_forum_topic = AsyncMock(return_value=True)
        _install_telegram_bot_mock(monkeypatch, bot)
        _install_gateway_config(monkeypatch)

        result = json.loads(telegram_topic_tool({
            "action": "reopen",
            "target": "telegram:-1001234567890:17585",
        }))
        assert result["success"] is True
        bot.reopen_forum_topic.assert_awaited_once()

    def test_delete_with_confirm(self, monkeypatch):
        bot = MagicMock()
        bot.delete_forum_topic = AsyncMock(return_value=True)
        _install_telegram_bot_mock(monkeypatch, bot)
        _install_gateway_config(monkeypatch)

        result = json.loads(telegram_topic_tool({
            "action": "delete",
            "target": "telegram:-1001234567890:17585",
            "confirm": True,
        }))
        assert result["success"] is True
        bot.delete_forum_topic.assert_awaited_once()

    def test_rename_calls_edit_forum_topic_with_name(self, monkeypatch):
        bot = MagicMock()
        bot.edit_forum_topic = AsyncMock(return_value=True)
        _install_telegram_bot_mock(monkeypatch, bot)
        _install_gateway_config(monkeypatch)

        result = json.loads(telegram_topic_tool({
            "action": "rename",
            "target": "telegram:-1001234567890:17585",
            "name": "new-name",
        }))
        assert result["success"] is True
        bot.edit_forum_topic.assert_awaited_once_with(
            chat_id=-1001234567890, message_thread_id=17585, name="new-name"
        )

    def test_bot_error_maps_to_structured_code(self, monkeypatch):
        bot = MagicMock()
        bot.close_forum_topic = AsyncMock(side_effect=Exception("Bad Request: not enough rights"))
        _install_telegram_bot_mock(monkeypatch, bot)
        _install_gateway_config(monkeypatch)

        result = json.loads(telegram_topic_tool({
            "action": "close",
            "target": "telegram:-100:5",
        }))
        assert "error" in result
        assert result["code"] == "no_rights"

    def test_telegram_not_configured(self, monkeypatch):
        _install_gateway_config(monkeypatch, enabled=False, token="")
        result = json.loads(telegram_topic_tool({
            "action": "close",
            "target": "telegram:-100:5",
        }))
        assert "error" in result and "not configured" in result["error"]


class TestList:
    def test_list_reads_sessions_json(self, monkeypatch, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        sessions_path = sessions_dir / "sessions.json"
        sessions_path.write_text(json.dumps({
            "s1": {
                "origin": {
                    "platform": "telegram",
                    "chat_id": "-1001",
                    "thread_id": "42",
                    "chat_topic": "release-plan",
                    "chat_name": "Hermes Dev",
                }
            },
            "s2": {
                "origin": {
                    "platform": "telegram",
                    "chat_id": "-1001",
                    "thread_id": "42",
                    "chat_name": "Hermes Dev",
                }
            },  # duplicate thread_id should dedupe
            "s3": {
                "origin": {
                    "platform": "telegram",
                    "chat_id": "-1001",
                    "thread_id": "99",
                    "chat_name": "Hermes Dev",
                }
            },
            "s4": {
                "origin": {
                    "platform": "telegram",
                    "chat_id": "-9999",  # different chat
                    "thread_id": "1",
                }
            },
            "s5": {
                "origin": {
                    "platform": "discord",
                    "chat_id": "-1001",
                    "thread_id": "55",
                }
            },
            "s6": {
                "origin": {
                    "platform": "telegram",
                    "chat_id": "-1001",
                    # no thread_id -- not a topic, skip
                }
            },
        }))
        monkeypatch.setattr("hermes_cli.config.get_hermes_home", lambda: tmp_path)

        result = json.loads(telegram_topic_tool({
            "action": "list",
            "target": "telegram:-1001",
        }))
        assert result["success"] is True
        assert result["source"] == "observed_sessions"
        topics = {t["thread_id"]: t for t in result["topics"]}
        assert set(topics.keys()) == {"42", "99"}
        assert topics["42"]["name"] == "release-plan"
        assert topics["99"]["name"] == "topic 99"  # fallback when chat_topic missing

    def test_list_handles_missing_sessions_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("hermes_cli.config.get_hermes_home", lambda: tmp_path)
        result = json.loads(telegram_topic_tool({
            "action": "list",
            "target": "telegram:-1001",
        }))
        assert result["success"] is True
        assert result["topics"] == []


class TestSchemaRegistration:
    def test_tool_is_registered(self):
        from tools.registry import registry
        entry = registry.get_entry("telegram_topic")
        assert entry is not None
        assert entry.toolset == "messaging"
        assert entry.schema["name"] == "telegram_topic"
        assert set(entry.schema["parameters"]["properties"]["action"]["enum"]) == {
            "create", "close", "reopen", "delete", "rename", "list",
        }
