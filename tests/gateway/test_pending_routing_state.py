"""Tests for session-scoped pending steering/queue routing state."""

from datetime import datetime
from unittest.mock import patch

from gateway.config import GatewayConfig, Platform, SessionResetPolicy
from gateway.session import SessionEntry, SessionStore, SessionSource


def _make_store(tmp_path):
    config = GatewayConfig(default_reset_policy=SessionResetPolicy(mode="none"))
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(sessions_dir=tmp_path, config=config)
    store._loaded = True
    return store


def _make_entry() -> SessionEntry:
    now = datetime.now()
    return SessionEntry(
        session_key="agent:main:telegram:dm:123",
        session_id="sid-123",
        created_at=now,
        updated_at=now,
        platform=Platform.TELEGRAM,
        chat_type="dm",
        origin=SessionSource(platform=Platform.TELEGRAM, chat_id="123", user_id="42"),
        pending_steer=[{"id": "a", "text": "steer me"}],
        pending_queue=[{"id": "b", "text": "queue me"}],
        steering_failed=True,
    )


def test_session_entry_round_trips_pending_state():
    entry = _make_entry()

    data = entry.to_dict()
    restored = SessionEntry.from_dict(data)

    assert restored.pending_steer == [{"id": "a", "text": "steer me"}]
    assert restored.pending_queue == [{"id": "b", "text": "queue me"}]
    assert restored.steering_failed is True


def test_session_store_reclassify_and_clear_pending(tmp_path):
    store = _make_store(tmp_path)
    entry = _make_entry()
    store._entries[entry.session_key] = entry

    state = store.get_pending_state(entry.session_key)
    assert state == {
        "pending_steer": [{"id": "a", "text": "steer me"}],
        "pending_queue": [{"id": "b", "text": "queue me"}],
    }

    moved = store.reclassify_pending_queue(entry.session_key)
    assert moved == 1
    assert store._entries[entry.session_key].pending_queue == []
    assert store._entries[entry.session_key].pending_steer == [
        {"id": "a", "text": "steer me"},
        {"id": "b", "text": "queue me"},
    ]

    changed = store.clear_pending_routing(entry.session_key)
    assert changed is True
    assert store._entries[entry.session_key].pending_steer == []
    assert store._entries[entry.session_key].pending_queue == []
    assert store._entries[entry.session_key].steering_failed is False


def test_pending_item_expiry_is_pruned(tmp_path):
    store = _make_store(tmp_path)
    entry = _make_entry()
    entry.pending_queue = [{"id": "expired", "expires_at": "2000-01-01T00:00:00"}]
    store._entries[entry.session_key] = entry

    state = store.get_pending_state(entry.session_key)
    assert state == {"pending_steer": [{"id": "a", "text": "steer me"}], "pending_queue": []}
