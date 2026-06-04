# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## graphify

This project has a nodesify-graphify knowledge graph at .graphify/.

Rules:
- MUST read .graphify/graph_report.md before searching files for architecture or codebase questions
- MUST use `nodesify-graphify query "<question>"`, `nodesify-graphify path "<A>" "<B>"`, or `nodesify-graphify explain "<concept>"` for cross-module questions — do NOT grep/read files directly for these
- After modifying code files in this session, run `nodesify-graphify update .` to keep the graph current

## Development Commands

```bash
# Activate venv (probes .venv, then venv, then ~/.hermes/hermes-agent/venv)
source .venv/bin/activate

# Install with all extras
uv pip install -e ".[all,dev]"

# Run tests (ALWAYS use this wrapper — never call pytest directly)
scripts/run_tests.sh                                    # full suite
scripts/run_tests.sh tests/gateway/                     # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x    # single test
scripts/run_tests.sh -v --tb=long                       # pass-through pytest args
scripts/run_tests.sh --no-isolate tests/foo/            # faster, for debugging (disables per-test subprocess isolation)

# Lint
ruff check .

# Type check
ty .

# Run agent from repo
./hermes              # auto-detects venv
./hermes doctor       # diagnose issues
./hermes -q "Hello"   # non-interactive test
```

The test wrapper (`scripts/run_tests.sh`) enforces hermetic CI parity: TZ=UTC, LANG=C.UTF-8, clean env (no API keys leak), per-file subprocess isolation via `scripts/run_tests_parallel.py`. Direct `pytest` calls have caused multiple "works locally, fails in CI" incidents.

## Architecture Overview

**Entry points:** `cli.py` (interactive CLI), `hermes --tui` (Ink/React TUI via `ui-tui/` + `tui_gateway/`), `hermes gateway` (messaging via `gateway/run.py`), `hermes-acp` (VS Code/Zed integration via `acp_adapter/`). All converge on `AIAgent` in `run_agent.py`.

**Core loop** (`run_agent.py::AIAgent.run_conversation`): synchronous while-loop calling LLM → executing tool calls → appending results → repeat. Message format is OpenAI-compatible with reasoning stored in `assistant_msg["reasoning"]`.

**Tool system:** Tools self-register at import time via `tools/registry.py::registry.register()`. `model_tools.py::discover_builtin_tools()` auto-imports all `tools/*.py` files. Tools must also be wired into a toolset in `toolsets.py` (usually `_HERMES_CORE_TOOLS`) — a registered but unwired tool is invisible to agents.

**Key files:**
| File | Role |
|------|------|
| `run_agent.py` | AIAgent class — core conversation loop (~4250 lines) |
| `cli.py` | HermesCLI class — interactive CLI with prompt_toolkit |
| `model_tools.py` | Tool orchestration, schema collection, dispatch |
| `toolsets.py` | Toolset definitions, `_HERMES_CORE_TOOLS` list |
| `hermes_state.py` | SQLite session DB with FTS5 full-text search |
| `hermes_constants.py` | `get_hermes_home()`, `display_hermes_home()` — profile-aware paths |
| `gateway/run.py` | GatewayRunner — messaging platform lifecycle, message routing, cron |
| `tools/registry.py` | Central tool registry (schema, handler, availability) |

**Subsystems:** `agent/` (prompt building, context compression, memory management, provider adapters, display), `hermes_cli/` (CLI subcommands, config, setup wizard, skin engine, slash command registry), `gateway/` (messaging platforms), `cron/` (scheduler), `plugins/` (memory providers, model providers, general plugins).

User config at `~/.hermes/config.yaml` + `~/.hermes/.env` (secrets only). Profile-aware via `get_hermes_home()`.

Full development guide: `AGENTS.md` (51.8K — architecture details, adding tools/skills/commands, skin system, plugins, delegation, curator, cron, kanban).

## Critical Rules

**NEVER hardcode `~/.hermes` paths.** Use `get_hermes_home()` (code) or `display_hermes_home()` (user-facing messages) from `hermes_constants`. Hardcoding breaks multi-profile support.

**Use `scripts/run_tests.sh`** for all test runs. Direct `pytest` passes real API keys and locale settings that CI doesn't have.

**Don't write change-detector tests.** Tests that assert specific model names, config version literals, or enumeration counts break on routine updates. Test behavioral invariants instead.

**Prompt caching is load-bearing.** Don't alter past context, change toolsets, or reload memories mid-conversation — cache-breaking forces dramatically higher costs. Slash commands that mutate system-prompt state must default to deferred invalidation (takes effect next session).

**Non-secret settings go in `config.yaml`, not `.env`.** `.env` is for API keys/tokens/passwords only.

**New core tools** require changes in exactly 2 files: `tools/your_tool.py` (register) + `toolsets.py` (wire into a toolset). For custom/local tools, use the plugin system (`~/.hermes/plugins/<name>/`) instead.

**Skills vs Tools:** Make it a skill when the capability can be expressed as instructions + existing tools. Make it a tool only when it needs custom Python integration, auth flows, or binary/streaming data handling.

**Dependencies:** All must have upper bounds. Use `==X.Y.Z` for exact pins (core deps), `>=floor,<next_major` for extras. Run `uv lock` after changes. This policy was tightened after the Mini Shai-Hulud worm campaign (May 2026).

**Cross-platform:** Never assume Unix. Use `psutil.pid_exists` not `os.kill(pid, 0)`, `tempfile.gettempdir()` not `/tmp`, `pathlib.Path` not `/proc`. `os.kill(pid, 0)` is a silent killer on Windows.
