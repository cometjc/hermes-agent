"""Regression tests for gateway /model support of config.yaml custom_providers."""

import yaml
import pytest

from agent.models_dev import ModelInfo
from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli.model_switch import ModelSwitchResult


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    return runner


def _make_event(text="/model"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"),
    )


@pytest.mark.asyncio
async def test_handle_model_command_lists_saved_custom_provider(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "default": "gpt-5.4",
                    "provider": "openai-codex",
                    "base_url": "https://chatgpt.com/backend-api/codex",
                },
                "providers": {},
                "custom_providers": [
                    {
                        "name": "Local (127.0.0.1:4141)",
                        "base_url": "http://127.0.0.1:4141/v1",
                        "model": "rotator-openrouter-coding",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})

    result = await _make_runner()._handle_model_command(_make_event())

    assert result is not None
    assert "Local (127.0.0.1:4141)" in result
    assert "custom:local-(127.0.0.1:4141)" in result
    assert "rotator-openrouter-coding" in result


@pytest.mark.asyncio
async def test_handle_model_command_switch_formats_capabilities_from_model_info(tmp_path, monkeypatch):
    """Regression: /model switch response should not crash on ModelInfo capabilities formatting."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {
                    "default": "gpt-5.3-codex",
                    "provider": "openai-codex",
                    "base_url": "https://chatgpt.com/backend-api/codex",
                },
            }
        ),
        encoding="utf-8",
    )

    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)

    model_info = ModelInfo(
        id="gpt-5.4",
        name="gpt-5.4",
        family="gpt-5",
        provider_id="openai",
        reasoning=True,
        tool_call=True,
        context_window=272000,
        max_output=32000,
    )

    def _fake_switch_model(**_kwargs):
        return ModelSwitchResult(
            success=True,
            new_model="gpt-5.4",
            target_provider="openai-codex",
            provider_changed=False,
            api_key="token",
            base_url="https://chatgpt.com/backend-api/codex",
            api_mode="codex_responses",
            provider_label="OpenAI Codex",
            model_info=model_info,
        )

    monkeypatch.setattr("hermes_cli.model_switch.switch_model", _fake_switch_model)

    result = await _make_runner()._handle_model_command(_make_event("/model gpt-5.4"))

    assert result is not None
    assert "Model switched to `gpt-5.4`" in result
    assert "Capabilities: reasoning, tools" in result
