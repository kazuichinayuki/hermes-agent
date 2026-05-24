# Decohere Roadmap — Future Work

**Updated:** 2026-05-23
**Status:** Planning (not yet executed)

---

## Implemented ✅

| Feature | Date | Status |
|---------|------|--------|
| Core plugin (ContextEngine ABC, L1/L2 extraction, SQLite storage) | 2026-05-05 | ✅ Stable |
| Structured summary (build_hint_context) | 2026-05-23 | ✅ Verified |
| Anti-regurgitation stack (6 layers + canary) | 2026-05-23 | ✅ Verified |
| Cache marker integration (_decohere_injected) | 2026-05-23 | ✅ Verified |
| recall_context tool | 2026-05-23 | ✅ Verified |
| Compression gating (should_compress) | 2026-05-23 | ✅ Verified |
| Mid-turn re-entry guard | 2026-05-23 | ✅ Verified |
| Shared knowledge injection (SharedStore) | 2026-05-11 | ✅ Basic |

---

## Phase 1: Cache & Compression Refinement

Priority: HIGH (directly impacts token cost and session stability)

### 1A. Proactive Compression Trigger ⬜

**Problem:** When the user is idle for >90 seconds, the KV cache expires. If
history is near threshold, the next request pays full cache-warming cost.

**Solution:** Implement an idle-timer (90s) trigger that proactively calls
`compress()` when:
- No user input for ≥90 seconds
- History message count > configurable threshold

**Files to modify:**
- `__init__.py` — add timer management in `compress()` or `on_idle()` hook
- Requires upstream support: Hermes needs to expose an idle callback or
  periodic tick to context engine plugins

**Complexity:** Medium (depends on Hermes upstream API)

### 1B. Tool Output Offloading ⬜

**Problem:** Large tool outputs (file reads, terminal dumps) bloat the
history. Even after compression, their token cost is paid on every cache warm.

**Solution:** Create `offload.py` utility:
1. During Phase 1, scan `role: tool` messages for content > 4KB
2. Write content to filesystem: `<session_dir>/tool_outputs/<hash>.txt`
3. Replace message content with: `[offloaded: tool_outputs/<hash>.txt, 12KB, first 200 chars...]`
4. `recall_context` can read offloaded files back when needed

**Files to create:**
- `decohere/context/offload.py` — `offload_large_tool_outputs(messages, session_dir, threshold=4096)`

**Files to modify:**
- `__init__.py` → call offload before Phase 1 extraction

**Complexity:** Low

### 1C. Summary Hash for Audit ⬜

**Problem:** When the structured summary changes, there's no record of what
was in the previous version.

**Solution:** Store a SHA-256 hash of the compressed content alongside each
compression event. Enables:
- Diff detection (did the summary actually change this turn?)
- Audit trail (what was compressed away?)
- Optional rollback (re-expand from stored entries)

**Complexity:** Low

---

## Phase 2: CLI Tools

Priority: MEDIUM (data visibility and debuggability)

### 2A. Core CLI Commands ⬜

8 subcommands under `hermes decohere`:

| Command | Purpose | Complexity |
|---------|---------|-----------|
| `sessions` | List all sessions with decohere.db | Low |
| `list` | List ledger entries (turn summaries) | Low |
| `show` | Show single entry in full detail | Low |
| `search` | FTS5 search across concepts/narrative | Medium |
| `stats` | Session statistics (turns, concepts, storage) | Low |
| `export` | Export to JSON/Markdown/YAML | Medium |
| `edit` | Modify entry fields (with audit log) | High |
| `delete` | Delete entries/sessions (with confirm) | Medium |

**Architecture:**
```
plugins/context_engine/decohere/cli/
├── __init__.py          # Click command group
├── _shared.py           # resolve_session, open_db, audit_log
├── sessions_cmd.py
├── list_cmd.py
├── show_cmd.py
├── search_cmd.py
├── stats_cmd.py
├── export_cmd.py
├── edit_cmd.py
└── delete_cmd.py
```

**Multi-profile support:**
- `--profile <name>` → resolve via `hermes profile show <name> --json`
- `--home <path>` → direct path override
- Default: current active profile

**Test requirement:** ≥35 tests across all commands.

**Full spec:** See archived plan `_archive/2026-05-09_codex-goal-decohere-management.md`

### 2B. Data Quality Health Check ⬜

Monitoring system for ledger integrity:
- Field completeness checker (9 required fields per entry)
- FTS5 index sync checker
- Orphan raw_messages detector
- Auto-repair engine (rebuild FTS5, fill defaults, mark corrupted)
- Health report (JSON + Markdown)

**Test requirement:** ≥12 tests.

---

## Phase 3: Cross-Session Knowledge

Priority: MEDIUM-LOW (value compounds over many sessions)

### 3A. Automatic Concept Migration ⬜

After each posting, automatically extract concepts and merge into
`decohere_shared.db`. Currently requires user opt-in via
`knowledge_injection: true` in config.

**Design decision (settled):** Embedding-agnostic architecture.
- `SharedStore` stores text only: `(term, definition, source_session, source_turn)`
- No vector columns in the DB
- `embed_fn: str → list[float]` is passed at query time
- Supports future multi-model pipeline (DeepSeek → GPT → Gemini)

### 3B. Relevance Matching Enhancement ⬜

Current: text-based FTS5 matching.
Future: semantic matching via configurable embedding model.

```yaml
decohere:
  retrieval:
    mode: semantic
    semantic_model: text-embedding-3-small
    semantic_threshold: 0.75
    top_k: 10
```

### 3C. Preference Extraction ⬜

Extract user preferences from patterns in `user_intent` and `decisions_and_rationale`:
- Tool preferences (which tools the user prefers for which tasks)
- Style preferences (verbosity, language, format)
- Domain knowledge (concepts the user has explained or corrected)

Time-decay scoring: recent preferences weighted higher.

---

## Phase 4: Interactive UI

Priority: LOW (nice-to-have, depends on Phase 2)

### Three-layer implementation:

| Layer | Mode | Framework |
|-------|------|-----------|
| 1 | CLI output beautification | Rich/Click formatting |
| 2 | `--interactive` mode | prompt_toolkit |
| 3 | TUI panel | Integration with hermes TUI |

### Three panels:
1. **Sessions** — browse/search/edit ledger entries
2. **Knowledge** — cross-session shared knowledge with checkbox selection
3. **Health** — data quality dashboard

**Full spec:** See archived plan `_archive/2026-05-11_decohere-ui.md`

---

## Phase 5: Multi-Agent & Advanced

Priority: FUTURE (aspirational)

| Feature | Description |
|---------|-------------|
| Multi-agent shared_store writes | Child agents contribute to parent's knowledge base |
| Cross-modal retrieval | Text/image/audio via same embedding interface |
| Upstream contribution | Submit as core plugin to NousResearch/hermes-agent |

---

## ADK Comparison (Reference)

| Layer | Google ADK | Decohere | Gap |
|-------|-----------|----------|-----|
| L1 Session | InMemorySessionService (volatile) | SessionIO + SQLite WAL | ✅ Decohere exceeds (persistent + LLM-refined) |
| L2 Working | `session.state` dict + `state_delta` | SharedStore + knowledge_injection | ⚠️ Passive import vs. active multi-agent state |
| L3 Persistent | DatabaseSessionService | decohere_shared.db + SharedStore | ⚠️ Manual selection vs. automatic extraction |

Phases 3A-3C close the L2/L3 gaps.
