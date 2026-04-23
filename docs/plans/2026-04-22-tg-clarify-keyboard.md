# Telegram Clarify Reply Keyboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Telegram clarify prompts use `ReplyKeyboardMarkup` for multiple-choice answers, then resolve the blocking clarify callback from the user's reply message.

**Architecture:** Extend the Telegram adapter with a pending-clarify state keyed by session/chat/user. When `clarify()` is invoked on Telegram, send a normal bot message with a reply keyboard containing the provided choices, wait on a thread-safe future, and consume the next matching user text message as the answer. Remove the reply keyboard after resolution and leave existing CLI / inline callback behavior untouched.

**Tech Stack:** Python, python-telegram-bot, pytest

---

### Task 1: Add Telegram clarify prompt plumbing

**Files:**
- Modify: `gateway/platforms/telegram.py`
- Modify: `gateway/platforms/telegram.py` message handling around `_handle_text_message`

- [ ] **Step 1: Write the failing test**

```python
async def test_build_clarify_callback_sends_reply_keyboard_and_resolves_answer():
    ...
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/gateway/test_telegram_clarify.py -q`
Expected: failure because `build_clarify_callback` is missing / reply keyboard behavior is absent.

- [ ] **Step 3: Implement the minimal Telegram clarify flow**
  - add a clarify counter/state map
  - send `ReplyKeyboardMarkup` with the provided choices
  - wait on a thread-safe future
  - resolve the future when the matching user sends a text reply
  - clear the reply keyboard after resolution

- [ ] **Step 4: Run the clarify tests**

Run: `pytest tests/gateway/test_telegram_clarify.py -q`
Expected: PASS

### Task 2: Keep gateway wiring intact

**Files:**
- Modify: `tests/gateway/test_telegram_clarify_wiring.py` if needed
- Verify: `gateway/run.py` stays compatible with the adapter factory contract

- [ ] **Step 1: Verify the gateway still builds Telegram clarify callbacks only for Telegram sessions**

Run: `pytest tests/gateway/test_telegram_clarify_wiring.py -q`
Expected: PASS

- [ ] **Step 2: Run a focused Telegram gateway test slice**

Run: `pytest tests/gateway/test_telegram_clarify.py tests/gateway/test_telegram_clarify_wiring.py -q`
Expected: PASS

### Task 3: Verify no regression in clarify tool behavior

**Files:**
- Verify: `tools/clarify_tool.py`
- Verify: `run_agent.py`

- [ ] **Step 1: Run clarify tool tests**

Run: `pytest tests/tools/test_clarify_tool.py -q`
Expected: PASS

- [ ] **Step 2: Run the broader gateway/clarify regression slice**

Run: `pytest tests/gateway/test_telegram_clarify.py tests/gateway/test_telegram_clarify_wiring.py tests/tools/test_clarify_tool.py -q`
Expected: PASS
