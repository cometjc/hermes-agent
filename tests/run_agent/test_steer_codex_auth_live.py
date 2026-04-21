"""Codex-auth-gated positive steer smoke test.

This is the most important positive steer check from the Codex turn/steer
suite, adapted to Hermes: a pending steer must be appended as same-turn user
input in the active turn without breaking role alternation.

It only runs when:
- the Hermes config flag `testing.codex_steer_auth_live` is enabled, and
- the local auth store exposes `openai-codex` credentials.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from hermes_cli.config import load_config
from run_agent import AIAgent


def _codex_steer_auth_enabled() -> bool:
    cfg = load_config()
    testing = cfg.get("testing", {})
    return bool(isinstance(testing, dict) and testing.get("codex_steer_auth_live"))


def _has_openai_codex_auth() -> bool:
    home = Path.home()
    hermes_auth = home / ".hermes" / "auth.json"
    codex_auth = home / ".codex" / "auth.json"

    for auth_path in (hermes_auth, codex_auth):
        if not auth_path.exists():
            continue
        try:
            data = json.loads(auth_path.read_text())
        except Exception:
            continue

        if auth_path.name == "auth.json" and auth_path.parent.name == ".hermes":
            pool = data.get("credential_pool", {}) if isinstance(data, dict) else {}
            entries = pool.get("openai-codex", []) if isinstance(pool, dict) else []
            if isinstance(entries, list) and any(
                isinstance(entry, dict)
                and entry.get("access_token")
                and entry.get("refresh_token")
                for entry in entries
            ):
                return True
        elif isinstance(data, dict):
            tokens = data.get("tokens", {})
            if isinstance(tokens, dict) and tokens.get("access_token") and tokens.get("refresh_token"):
                return True

    return False


pytestmark = [
    pytest.mark.skipif(not _codex_steer_auth_enabled(), reason="codex steer auth smoke test disabled in config.yaml"),
    pytest.mark.skipif(not _has_openai_codex_auth(), reason="openai-codex auth not available in ~/.hermes/auth.json"),
]


def _bare_agent() -> AIAgent:
    agent = object.__new__(AIAgent)
    agent._active_turn_token = "test-turn"
    agent._pending_steer = None
    agent._pending_steer_turn_token = None
    agent._pending_steer_lock = threading.Lock()
    return agent


def test_codex_auth_gated_steer_appends_to_last_tool_result():
    # Confirm the Hermes/Codex auth files in the real home directory are
    # readable before using the same steer injection logic Codex validates
    # in its live turn tests.
    assert _has_openai_codex_auth() is True

    agent = _bare_agent()
    assert agent.steer("please also check auth.log") is True

    messages = [
        {"role": "user", "content": "what's in /var/log?"},
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "content": "ls output A", "tool_call_id": "a"},
        {"role": "tool", "content": "ls output B", "tool_call_id": "b"},
    ]

    appended = agent._append_pending_steer_as_user_message(messages)

    assert appended is True
    assert messages[2]["content"] == "ls output A"
    assert messages[3]["content"] == "ls output B"
    assert messages[-1] == {"role": "user", "content": "please also check auth.log"}
    assert agent._pending_steer is None
