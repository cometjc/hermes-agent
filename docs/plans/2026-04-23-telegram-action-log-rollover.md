# Telegram Action Log Rollover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Telegram tool-progress/action-log grouping from growing past the platform message limit by rolling over into a fresh message before the edit/send payload becomes too large.

**Architecture:** Keep the existing gateway progress pipeline and make the rollover decision inside the Telegram-facing progress batching path in `gateway/run.py`. Before appending a new progress line, compute the projected rendered length of the current batch plus the new line; if it would exceed a conservative Telegram-safe budget, flush the current batch and start a fresh message. The budget must be based on the rendered payload length after formatting/escaping, not raw line text. Preserve existing deduplication, flood-control handling, and non-Telegram behavior. Use the Telegram adapter's existing message-length limit and truncation helpers for fallback handling when a single line is already too long for a normal edit/send flow.

**Tech Stack:** Python, pytest, Hermes gateway, Telegram adapter, existing message-length helpers.

---

### Task 1: Add regression tests for rollover behavior

**Files:**
- Modify: `tests/gateway/test_run_progress_topics.py`
- Create: `tests/gateway/test_progress_batch_budget.py`
- Create: `tests/gateway/test_telegram_edit_overflow.py`

- [ ] **Step 1: Write the failing test for rendered-length budgeting**

Add a pure unit test for the new progress-batch budget helper. Cover one case where the rendered payload is exactly at the safe limit and one case where a dedup suffix like `(×N)` pushes it over. The test must exercise Telegram's canonical rendering path — including MarkdownV2 escaping and UTF-16 length accounting — rather than raw Python string length.

- [ ] **Step 2: Write the failing test for batch rollover**

Add a focused gateway test that drives `_run_agent()` through the Telegram `tool.started` progress loop with concrete fixture sizes: one line that fits under the budget, then a second line that makes the rendered batch exceed the limit by a small margin. Assert the adapter sees a flush of the first batch and then a new message for the overflow batch, and that `progress_msg_id` advances to the latest sent message.

- [ ] **Step 3: Write the failing test for a single oversized line**

Add a Telegram-adapter-level test that exercises `edit_message()` overflow handling directly. Assert that a line too large for a normal edit path cannot silently lose content and that the fallback leaves the system in a state where future progress updates target the correct latest message.

- [ ] **Step 4: Add a cancellation / drain regression test**

Add a case that cancels the progress loop after several lines have been buffered. Assert the final partial batch is flushed using the same budget logic and does not exceed the Telegram limit on shutdown.

- [ ] **Step 5: Run the focused test file(s) to confirm they fail**

Run: `python -m pytest tests/gateway/test_progress_batch_budget.py tests/gateway/test_run_progress_topics.py tests/gateway/test_telegram_edit_overflow.py -q`

Expected: fail because the gateway currently keeps accumulating progress lines into the same edit/send path and does not proactively roll over before the limit.

---

### Task 2: Implement Telegram-safe progress batching in `gateway/run.py`

**Files:**
- Create: `gateway/progress_batch.py`
- Modify: `gateway/run.py`
- Possibly modify: `gateway/platforms/telegram.py` only if a small helper is needed for length accounting; avoid unrelated adapter changes if the gateway can use the existing adapter API.

- [ ] **Step 1: Introduce an explicit safe-limit calculation for progress batching**

Add a small helper module that computes the rendered Telegram payload length for a batch, using the same formatting/escaping rules that the adapter applies. Implement it as a thin wrapper around the canonical Telegram adapter helpers (`format_message`, `truncate_message`, `utf16_len`) so the budget matches the actual payload. The helper must account for newline separators, dedup suffixes like `(×N)`, and any prefix text added by the progress renderer.

- [ ] **Step 2: Check the projected batch size before appending each new progress line**

When draining `progress_queue`, compute the rendered text for the current batch plus the candidate line before mutating `progress_lines`. If the projected content would exceed the safe budget, flush the current batch first and then begin a new message with the incoming line.

- [ ] **Step 3: Preserve existing edit/send semantics for short batches**

Keep the current behavior for normal batches: edit the in-flight progress message when possible, fall back to send when editing is unsupported or flood-control disables edits, and keep dedup/throttle behavior intact.

- [ ] **Step 4: Handle a single oversized line without breaking the loop**

If one progress line by itself exceeds the safe budget, pre-split it locally into Telegram-safe chunks before sending, so the gateway always knows the message id of the latest emitted chunk. Send each chunk independently, update `progress_msg_id` to the last emitted chunk, and reset dedup/repeat tracking across chunk boundaries. Do not rely on `TelegramAdapter.send()` to split the content for you, because the gateway still needs the final chunk id for future edits.

- [ ] **Step 5: Keep Telegram-only behavior isolated and preserve rollover state**

Gate the rollover logic to Telegram or another explicitly chosen edit-capable path, and make sure the code updates `progress_msg_id`/current-batch state correctly after every rollover flush so follow-up edits target the latest message. Also ensure the `CancelledError` drain path reuses the same chunking helper for every buffered line and flushes the final partial batch with the same size checks.

---

### Task 3: Verify the rollover path and clean up

**Files:**
- Possibly modify: `tests/gateway/test_run_progress_topics.py`
- Possibly modify: `tests/gateway/test_telegram_text_batching.py` only if a shared helper needs coverage

- [ ] **Step 1: Run the focused rollover tests**

Run: `python -m pytest tests/gateway/test_run_progress_topics.py tests/gateway/test_run_progress_message_rollover.py -q`

- [ ] **Step 2: Run the smallest relevant Telegram gateway subset**

Run the Telegram gateway tests that exercise progress edits, text batching, and long-message handling, for example:

`python -m pytest tests/gateway/test_telegram_text_batching.py tests/gateway/test_telegram_thread_fallback.py tests/gateway/test_telegram_format.py tests/gateway/test_run_progress_topics.py -q`

- [ ] **Step 3: Confirm no regressions in progress delivery semantics**

Manually inspect the adapter call log in the tests to confirm:
- short batches still aggregate into a single message when appropriate
- rollover happens before the safe limit is crossed
- existing flood-control fallback still works
- no other platform-specific message routing changed

- [ ] **Step 4: Commit the plan implementation once green**

Use a conventional commit message that clearly names the Telegram rollover fix, then prepare for the branch-finishing workflow.
