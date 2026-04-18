# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Primary reference

`AGENTS.md` at the repo root is the canonical development guide — project layout, agent loop,
CLI/TUI/gateway architecture, tool authoring, skin engine, profile rules, known pitfalls. Read it
before non-trivial work. This file only captures what you need up front.

## What this repo is

Hermes Agent is a self-improving AI agent with two entry points:

- **CLI** (`hermes`) — interactive TUI-or-classic terminal frontend driven by `cli.py` (classic)
  or the Ink/React frontend in `ui-tui/` talking JSON-RPC to `tui_gateway/` (modern).
- **Gateway** (`hermes gateway`) — long-running process bridging messaging platforms (Telegram,
  Discord, Slack, WhatsApp, Signal, Matrix, Feishu, Home Assistant, …) to the same agent core.

Both frontends share `AIAgent` in `run_agent.py` (synchronous tool-calling loop) and the tool
system in `tools/`.

## Essential commands

```bash
# Activate the venv FIRST for anything Python-side
source venv/bin/activate    # or source .venv/bin/activate

# Tests — ALWAYS via the wrapper; never call pytest directly
scripts/run_tests.sh                                   # full suite, CI-parity
scripts/run_tests.sh tests/tools/                      # one directory
scripts/run_tests.sh tests/tools/test_foo.py::test_x   # one test
scripts/run_tests.sh -v --tb=long                      # pass-through flags

# TUI (Ink + TypeScript) dev loop
cd ui-tui && npm run dev            # rebuild + tsx watch
cd ui-tui && npm run type-check     # tsc --noEmit
cd ui-tui && npm run lint           # eslint
cd ui-tui && npm test               # vitest
```

`scripts/run_tests.sh` enforces `-n 4` xdist workers (matches CI), blanks credential env vars,
pins `TZ=UTC` / `LANG=C.UTF-8`. Skipping it has historically caused "works locally, fails in CI"
incidents — don't.

## Architecture you must hold in your head

### Tool system (`tools/`)

- `tools/registry.py` is the central registry. Every file under `tools/*.py` that calls
  `registry.register(...)` at module top level is imported automatically by
  `discover_builtin_tools()`. **No import list to update** when adding a tool.
- Adding a tool = create `tools/your_tool.py` + list the name in `toolsets.py`
  (`_HERMES_CORE_TOOLS` makes it available on every platform; or put it in a named toolset).
- All handlers MUST return a JSON string.
- Schema descriptions must not name tools from other toolsets — availability is platform-dependent,
  cross-references hallucinate non-existent tools. Wire cross-refs dynamically in
  `model_tools.get_tool_definitions()` instead.

### Agent loop (`run_agent.py`)

`AIAgent.run_conversation()` is a synchronous while-loop that calls the model, dispatches each
tool call through `handle_function_call()`, appends results to `messages`, and repeats until the
model returns no tool calls. Messages follow OpenAI format (`{"role": ..., ...}`).

### Slash commands (`hermes_cli/commands.py`)

Every slash command is a single `CommandDef` entry in `COMMAND_REGISTRY`. CLI dispatch, gateway
dispatch, Telegram bot menu, Slack subcommand routing, autocomplete, and `/help` all derive from
that one list. Adding an alias = append to the tuple; nothing else to touch.

### Profiles (multi-instance)

`_apply_profile_override()` in `hermes_cli/main.py` sets `HERMES_HOME` before any imports. Every
stateful path goes through `get_hermes_home()` (from `hermes_constants`). **Never hardcode
`~/.hermes` or `Path.home() / ".hermes"`** — that breaks profiles, and this has caused real bugs
(PR #3575). Use `display_hermes_home()` for user-facing strings (prints the right path per profile).

Tests must not write to `~/.hermes/` — `tests/conftest.py`'s `_isolate_hermes_home` autouse fixture
redirects `HERMES_HOME` to a temp dir; profile-touching tests also need to monkey-patch `Path.home`.

### TUI split (`ui-tui/` + `tui_gateway/`)

Two-process model: Node/Ink renders, Python runs the agent. Newline-delimited JSON-RPC over stdio.
The method/event catalog lives in `tui_gateway/server.py`. Built-in client commands (`/help`,
`/quit`, `/clear`, `/copy`, `/paste`, …) handle in `app.tsx` locally; everything else goes to
`slash.exec` → persistent `_SlashWorker` subprocess.

### Gateway messaging (`gateway/`)

One long-lived process, one adapter per platform in `gateway/platforms/`. The cross-platform
`send_message` tool (`tools/send_message_tool.py`) routes to adapters by `Platform` enum. Adapters
that own a unique credential must acquire a token lock via `gateway.status.acquire_scoped_lock()`
so two profiles can't clobber each other on the same bot.

## Invariants — do NOT break these

- **Prompt caching.** Don't alter past context mid-conversation, don't change the toolset
  mid-conversation, don't reload memory or rebuild the system prompt mid-turn. The only
  legitimate mid-conversation mutation is the compression pass.
- **Test wrapper.** `scripts/run_tests.sh`, not `pytest`. See above.
- **Profile-safe paths.** `get_hermes_home()` / `display_hermes_home()` everywhere.
- **`simple_term_menu` is banned** for interactive menus (ghosts in tmux/iTerm2). Use `curses`
  — see `hermes_cli/tools_config.py`.
- **No `\033[K` in spinner/display code** — leaks as literal `?[K` under `prompt_toolkit`'s
  `patch_stdout`. Pad with spaces instead.

## When in doubt

Open `AGENTS.md` — the sections on "Adding New Tools", "Adding a Slash Command",
"Profiles: Multi-Instance Support", and "Known Pitfalls" are the ones you'll reach for most.
