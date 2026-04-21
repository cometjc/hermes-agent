"""Tests for AIAgent.steer() — mid-run same-turn user input injection.

/steer lets the user add a note to the agent's active turn without
interrupting the current tool call. The agent sees the note as pending
user input on the next model-request boundary, preserving message-role
alternation and turn-boundary semantics.
"""
from __future__ import annotations

import threading

import pytest

from run_agent import AIAgent


def _bare_agent() -> AIAgent:
    """Build an AIAgent without running __init__, then install the steer
    state manually — matches the existing object.__new__ stub pattern
    used elsewhere in the test suite.
    """
    agent = object.__new__(AIAgent)
    agent._active_turn_token = "test-turn"
    agent._pending_steer = None
    agent._pending_steer_turn_token = None
    agent._pending_steer_lock = threading.Lock()
    return agent


class TestSteerAcceptance:
    def test_accepts_non_empty_text(self):
        agent = _bare_agent()
        assert agent.steer("go ahead and check the logs") is True
        assert agent._pending_steer == "go ahead and check the logs"

    def test_rejects_empty_string(self):
        agent = _bare_agent()
        assert agent.steer("") is False
        assert agent._pending_steer is None

    def test_rejects_whitespace_only(self):
        agent = _bare_agent()
        assert agent.steer("   \n\t  ") is False
        assert agent._pending_steer is None

    def test_rejects_none(self):
        agent = _bare_agent()
        assert agent.steer(None) is False  # type: ignore[arg-type]
        assert agent._pending_steer is None

    def test_rejects_without_active_turn(self):
        agent = _bare_agent()
        agent._active_turn_token = None
        assert agent.steer("hello") is False
        assert agent._pending_steer is None

    def test_strips_surrounding_whitespace(self):
        agent = _bare_agent()
        assert agent.steer("  hello world  \n") is True
        assert agent._pending_steer == "hello world"

    def test_concatenates_multiple_steers_with_newlines(self):
        agent = _bare_agent()
        agent.steer("first note")
        agent.steer("second note")
        agent.steer("third note")
        assert agent._pending_steer == "first note\nsecond note\nthird note"


class TestSteerDrain:
    def test_drain_returns_and_clears(self):
        agent = _bare_agent()
        agent.steer("hello")
        assert agent._drain_pending_steer() == "hello"
        assert agent._pending_steer is None

    def test_drain_on_empty_returns_none(self):
        agent = _bare_agent()
        assert agent._drain_pending_steer() is None

    def test_turn_end_cleanup_clears_pending_steer_and_binding(self):
        agent = _bare_agent()
        agent.steer("hello")
        assert agent._pending_steer == "hello"
        assert agent._pending_steer_turn_token == "test-turn"
        agent._clear_pending_steer()
        agent._active_turn_token = None
        assert agent._pending_steer is None
        assert agent._pending_steer_turn_token is None


class TestSteerInjection:
    def test_appends_as_user_message(self):
        agent = _bare_agent()
        agent.steer("please also check auth.log")
        messages = [
            {"role": "user", "content": "what's in /var/log?"},
            {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
            {"role": "tool", "content": "ls output A", "tool_call_id": "a"},
            {"role": "tool", "content": "ls output B", "tool_call_id": "b"},
        ]
        appended = agent._append_pending_steer_as_user_message(messages)
        assert appended is True
        # Earlier messages are untouched; steer becomes a fresh user message.
        assert messages[2]["content"] == "ls output A"
        assert messages[3]["content"] == "ls output B"
        assert messages[-1] == {"role": "user", "content": "please also check auth.log"}
        assert agent._pending_steer is None

    def test_no_op_when_no_steer_pending(self):
        agent = _bare_agent()
        messages = [
            {"role": "assistant", "tool_calls": [{"id": "a"}]},
            {"role": "tool", "content": "output", "tool_call_id": "a"},
        ]
        appended = agent._append_pending_steer_as_user_message(messages)
        assert appended is False
        assert messages[-1]["content"] == "output"

    def test_appends_even_without_tool_messages(self):
        agent = _bare_agent()
        agent.steer("steer")
        messages = [{"role": "user", "content": "hi"}]
        appended = agent._append_pending_steer_as_user_message(messages)
        assert appended is True
        assert messages[-1] == {"role": "user", "content": "steer"}

    def test_turn_end_cleanup_persists_pending_steer(self):
        agent = _bare_agent()
        agent.steer("stop after next step")
        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "done"}]
        # Simulate the turn-end cleanup path.
        assert agent._append_pending_steer_as_user_message(messages) is True
        assert messages[-1] == {"role": "user", "content": "stop after next step"}
        assert agent._pending_steer is None

    def test_turn_end_cleanup_does_not_duplicate_if_already_drained(self):
        agent = _bare_agent()
        agent.steer("stop after next step")
        messages = [{"role": "assistant", "content": "done"}]
        assert agent._append_pending_steer_as_user_message(messages) is True
        assert agent._append_pending_steer_as_user_message(messages) is False
        assert messages[-1] == {"role": "user", "content": "stop after next step"}


class TestSteerThreadSafety:
    def test_concurrent_steer_calls_preserve_all_text(self):
        agent = _bare_agent()
        N = 200

        def worker(idx: int) -> None:
            agent.steer(f"note-{idx}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        text = agent._drain_pending_steer()
        assert text is not None
        # Every single note must be preserved — none dropped by the lock.
        lines = text.split("\n")
        assert len(lines) == N
        assert set(lines) == {f"note-{i}" for i in range(N)}


class TestSteerClearedOnInterrupt:
    def test_clear_interrupt_drops_pending_steer(self):
        """A hard interrupt supersedes any pending steer — the agent's
        next tool iteration won't happen, so delivering the steer later
        would be surprising."""
        agent = _bare_agent()
        # Minimal surface needed by clear_interrupt()
        agent._interrupt_requested = True
        agent._interrupt_message = None
        agent._interrupt_thread_signal_pending = False
        agent._execution_thread_id = None
        agent._tool_worker_threads = None
        agent._tool_worker_threads_lock = None

        agent.steer("will be dropped")
        assert agent._pending_steer == "will be dropped"

        agent.clear_interrupt()
        assert agent._pending_steer is None


class TestPreApiCallSteerDrain:
    """Test that steers arriving during an API call are drained before the
    next API call — not deferred until the next model-request boundary.  This is the
    fix for the scenario where /steer sent during model thinking only lands
    after the agent is completely done."""

    def test_pre_api_drain_appends_user_message(self):
        """If a steer is pending when the main loop starts building
        api_messages, it should be appended as a fresh user message in the
        messages list."""
        agent = _bare_agent()
        # Simulate messages after a model-request boundary completed
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "ok", "tool_calls": [
                {"id": "tc1", "function": {"name": "terminal", "arguments": "{}"}}
            ]},
            {"role": "tool", "content": "output here", "tool_call_id": "tc1"},
        ]
        # Steer arrives during API call (set after tool execution)
        agent.steer("focus on error handling")
        appended = agent._append_pending_steer_as_user_message(messages)
        assert appended is True
        assert messages[-1] == {"role": "user", "content": "focus on error handling"}
        assert agent._pending_steer is None

    def test_pre_api_drain_appends_even_without_tool_message(self):
        """If there are no tool results yet (first iteration), the steer is
        still turned into a user message instead of being parked for a later
        model-request boundary."""
        agent = _bare_agent()
        messages = [
            {"role": "user", "content": "hello"},
        ]
        agent.steer("early steer")
        appended = agent._append_pending_steer_as_user_message(messages)
        assert appended is True
        assert messages[-1] == {"role": "user", "content": "early steer"}
        assert agent._pending_steer is None

    def test_turn_end_cleanup_persists_steer_as_user_message(self):
        """If the turn finishes before another model request happens, the
        pending steer should still be persisted into the finished turn."""
        agent = _bare_agent()
        messages = [
            {"role": "user", "content": "do something"},
            {"role": "assistant", "content": "done"},
        ]
        agent.steer("change approach")
        # Turn-end cleanup should make the steer visible in returned history.
        assert agent._append_pending_steer_as_user_message(messages) is True
        assert messages[-1] == {"role": "user", "content": "change approach"}
        assert agent._pending_steer is None


class TestSteerCommandRegistry:
    def test_steer_in_command_registry(self):
        """The /steer slash command must be registered so it reaches all
        platforms (CLI, gateway, TUI autocomplete, Telegram/Slack menus).
        """
        from hermes_cli.commands import resolve_command, ACTIVE_SESSION_BYPASS_COMMANDS

        cmd = resolve_command("steer")
        assert cmd is not None
        assert cmd.name == "steer"
        assert cmd.category == "Session"
        assert cmd.args_hint == "<prompt>"

    def test_steer_in_bypass_set(self):
        """When the agent is running, /steer MUST bypass the Level-1
        base-adapter queue so it reaches the gateway runner's /steer
        handler. Otherwise it would be queued as user text and only
        delivered at turn end — defeating the whole point.
        """
        from hermes_cli.commands import ACTIVE_SESSION_BYPASS_COMMANDS, should_bypass_active_session

        assert "steer" in ACTIVE_SESSION_BYPASS_COMMANDS
        assert should_bypass_active_session("steer") is True


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
