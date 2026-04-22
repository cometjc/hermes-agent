# Telegram Native Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in Telegram native streaming support so Hermes can stream assistant responses via Telegram's draft-based API path, with edit-based fallback preserved for other platforms and unsupported cases.

**Architecture:** Add a platform-agnostic `send_stream()` adapter hook with a safe default that behaves like the existing send/edit streaming loop. Telegram overrides that hook to call the native Bot API draft endpoint when streaming is configured as `native`, and falls back to the default edit-based path if native calls fail. The gateway stream consumer selects the transport via the existing streaming config, so Telegram can use native streaming without changing the rest of the message pipeline.

**Tech Stack:** Python, pytest, python-telegram-bot, Hermes gateway adapters, Telegram Bot API.

---

### Task 1: Write failing tests for native streaming

**Files:**
- Modify: `tests/gateway/test_telegram_thread_fallback.py`
- Create: `tests/gateway/test_telegram_native_streaming.py`

- [ ] **Step 1: Write the failing test**

Add tests that assert:
1. the generic stream consumer calls a platform `send_stream()` hook when streaming transport is `native`
2. the Telegram adapter native path invokes Telegram's `sendMessageDraft` endpoint through the bot request layer
3. the native path falls back to the normal send/edit flow if the native call fails

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/gateway/test_telegram_native_streaming.py -q`
Expected: fail because `send_stream()` / native draft behavior is not implemented yet.

- [ ] **Step 3: Commit the red test state only if needed**

No commit required yet; keep the new tests local until implementation passes.

### Task 2: Implement native streaming support

**Files:**
- Modify: `gateway/platforms/base.py`
- Modify: `gateway/platforms/telegram.py`
- Modify: `gateway/stream_consumer.py`
- Modify: `gateway/run.py`
- Modify: `cli-config.yaml.example`
- Modify: `website/docs/user-guide/configuration.md`

- [ ] **Step 1: Add a default `send_stream()` hook**

Add a platform-agnostic method to `BasePlatformAdapter` that preserves the current send/edit behavior by default.

- [ ] **Step 2: Add Telegram native draft streaming**

Implement a Telegram adapter helper that calls `sendMessageDraft` via the bot API request layer and returns a usable stream token / draft identifier. If the native call fails or is unavailable, fall back to the default send/edit behavior.

- [ ] **Step 3: Route the stream consumer through native transport**

Teach `GatewayStreamConsumer` to use `send_stream()` when `streaming.transport == "native"`, while keeping the existing edit loop for the default transport.

- [ ] **Step 4: Wire config + docs**

Update example config and docs so users know that `streaming.transport: native` enables Telegram's draft-based path.

### Task 3: Verify and clean up

**Files:**
- Possibly modify: `tests/gateway/test_display_config.py`

- [ ] **Step 1: Run the focused test suite**

Run: `python -m pytest tests/gateway/test_telegram_native_streaming.py tests/gateway/test_display_config.py -q`

- [ ] **Step 2: Run any affected Telegram gateway tests**

Run the smallest relevant gateway subset that exercises Telegram send/edit and stream consumer behavior.

- [ ] **Step 3: Commit once green**

Use a conventional commit describing native Telegram streaming support.
