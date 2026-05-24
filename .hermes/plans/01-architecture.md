# Decohere Architecture — Canonical Reference

**Updated:** 2026-05-23
**Status:** Implemented and verified (54/54 tests pass)

---

## What Decohere Is

A context engine plugin for Hermes that replaces raw message history injection
with structured, information-dense context. It fulfills the `ContextEngine` ABC
and is auto-discovered by the plugin loader when `context.engine: "decohere"`
is set in `config.yaml`.

## Core Principle

> Spend your engineering budget on the harness, save your intelligence budget
> for the model. — OpenClacky

Every design decision optimizes for **KV cache reusability**. One small, stable
context message. No system prompt mutation. No verbatim tail. No prose blobs.

---

## Two-Layer Entry Model

Each conversation turn is extracted by an auxiliary LLM into a 13-field
structured entry with two epistemic layers:

### Layer 1 — Spec (structurally verifiable)

| Field | Source | Mutability |
|-------|--------|-----------|
| `n` | Mechanical (counter) | Immutable |
| `message_range` | Mechanical (indices) | Immutable |
| `tools[]` | Mechanical (from tool_calls) | Immutable |
| `files_touched[]` | Mechanical (from tool args) | Immutable |
| `reference_documentation` | LLM-derived, formal | Settled once recorded |
| `relevant_metadata` | LLM-derived, formal | Settled once recorded |

### Layer 2 — Proc (all provisional)

| Field | Nature |
|-------|--------|
| `concepts_and_definitions` | May be refined/contradicted by later turns |
| `narrative` | Turn's story (`.summary`, `.cross_references`) |
| `user_intent` | Always a hypothesis |
| `decisions_and_rationale` | Examined for bias |
| `procedures` | Steps taken/planned |
| `insights_and_learnings` | Observations |
| `critical_reflection` | Self-doubt mechanism |

---

## Plugin Directory Structure

```
plugins/context_engine/decohere/
├── __init__.py           # Decohere class — thin coordinator, ZERO business logic
├── config.py             # LedgerConfig (frozen), DecohereUserConfig (mutable)
├── context/
│   ├── builder.py        # build_hint_context(), build_ledger_context(), etc.
│   ├── classifier.py     # check_readiness(), should_skip_entry()
│   ├── formatter.py      # format_entry_layer(), format_proc_layer()
│   └── placeholder.py    # build_placeholder()
├── core/
│   ├── extractor.py      # last_turn_messages(), mechanical_fields()
│   └── indexer.py        # build_turn_index(), pick_turns_from_index()
├── io/
│   ├── session_io.py     # SessionIO — all DB reads/writes
│   ├── ledger_store.py   # LedgerStore (SQLite table)
│   └── raw_store.py      # RawMessageStore (SQLite table)
├── knowledge/
│   └── __init__.py       # SharedStore, build_injection_message()
├── monitoring/
│   └── reporter.py       # HealthReporter
├── scheduling/
│   ├── task_manager.py   # TaskManager (async posting)
│   └── metrics.py        # MetricsCollector
└── tests/
    ├── test_hint_context.py   # 19 tests — structured summary
    ├── test_phase1_hardening.py # 10 tests — canary, re-entry
    ├── test_phase2_cache.py     # 9 tests — annotation, markers
    └── test_phase3_4.py         # 16 tests — recall_context, gating
```

---

## compress() Flow

Called BEFORE each LLM turn. Single entry point:

```
compress(messages, current_tokens, focus_topic) → messages
```

### Phase 1: Post-Turn Processing
1. Strip ledger-named messages (`ledger_l1`, `turn_context`, etc.)
2. Strip regurgitated content from assistant messages (canary + pattern detection)
3. Guard against mid-turn re-entry (user_hash dedup)
4. Extract `last_turn_messages` → build placeholder → async posting

### Phase 2: Context Building
1. Check readiness (legacy / empty / partial / ready)
2. Build **information-dense structured summary** via `build_hint_context()`
3. Annotate all injected messages with `_decohere_injected = True`
4. Optionally append shared knowledge + session state

### Output Structure
```
[structured_summary]       ← single system message, ~500-2000 chars
[shared_knowledge]         ← optional cross-session concepts
[shared_state]             ← optional L2 working memory
```

---

## Anti-Regurgitation Stack

| Layer | Mechanism | Location |
|-------|-----------|----------|
| 0 | Per-session canary token `[canary:XXXX]` | `__init__.py` |
| 1 | `<!-- DECOHERE:BEGIN -->` machine markers | `builder.py` |
| 2 | `## Ledger Entries` header detection | `__init__.py` |
| 3 | `[INTERNAL CONTEXT — DO NOT ECHO]` guard | `__init__.py` |
| 4 | `[Turn N]` + field pattern detection | `__init__.py` |
| 5 | Gutted-turn detection (empty assistant after strip) | `__init__.py` |
| 6 | `_decohere_injected` flag for cache marker exclusion | `__init__.py` |

---

## recall_context Tool

Exposed to the LLM for on-demand history retrieval. Returns formatted
turn-by-turn summaries from the ledger. Parameters:

- `query` (optional) — focus query for relevance filtering (future)
- `max_turns` (optional, default 10) — cap on returned turns

---

## Configuration

### LedgerConfig (frozen at init)

```yaml
auxiliary:
  compression:
    model: openai/gpt-5.4-mini
    provider: openrouter
    decohere:
      temperature: 0.1
      timeout: 30.0
      max_turns: 20       # recent turns window for hint
      max_tokens: null     # no cap on extraction output
```

### DecohereUserConfig (mutable)

```yaml
decohere:
  knowledge_injection: false
  retrieval:
    mode: text            # "text" | "semantic"
    semantic_model: null
    semantic_threshold: 0.75
    top_k: 10
  knowledge_sources: []
  knowledge_exclude: []
  injection:
    max_tokens_pct: 0.10
    max_concepts: 20
```

---

## Storage

Per-session SQLite DB at `<hermes_home>/sessions/<session_id>/decohere.db`:

| Table | Purpose |
|-------|---------|
| `ledger_entries` | `(turn_n, entry_json, posted_at, validated)` |
| `concepts_fts` | FTS5 full-text index over concepts |
| `raw_messages` | `(store_id, role, content, tool_name, tool_call_id, timestamp)` |
| `metadata` | `(key, value)` key-value store |

Cross-session: `<hermes_home>/decohere_shared.db` (SharedStore for knowledge injection).
