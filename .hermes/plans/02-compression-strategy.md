# Compression Strategy — Cache-Optimized Context Management

**Updated:** 2026-05-23
**Status:** Implemented and verified (54/54 tests pass)
**References:**
- [OpenClacky: Every AI Agent Feature Is a Cache Invalidation Surface](https://www.openclacky.com/engineering/cache-invalidation-surface)
- [Factory.ai: Evaluating Context Compression for AI Agents](https://factory.ai/news/evaluating-compression)

---

## Problem Statement

The original Decohere injected full L1+L2 structured blocks into the system
prompt. This caused:

1. **LLM regurgitation** — the model echoed injected ledger entries as its
   response, creating a feedback loop of nested/duplicated entries
2. **Cache invalidation** — large injected blocks mutated the prefix every
   turn, destroying KV cache reuse
3. **Compacting loop** — regurgitated content triggered re-compression,
   which triggered re-injection, which triggered more regurgitation

---

## Solution: Information-Dense Structured Summary

### Design Principles

| Source | Principle | Implementation |
|--------|-----------|---------------|
| OpenClacky Decision 5 | Insert-then-Compress: one small, stable message | Single `turn_context` system message |
| OpenClacky Decision 2 | Frozen system prompt — dynamic state stays out | Summary is `system_injected`, not in system prompt |
| OpenClacky Decision 1 | Double cache markers, skip injected messages | `_decohere_injected` flag, marker exclusion |
| Factory.ai Eval | Structured > prose for compression quality | Explicit sections: Files, Decisions, Procedures, Arc |
| Factory.ai Eval | Low-entropy details must survive compression | File paths with turn numbers, decision + rationale pairs |
| User directive | "Preserve everything that is still relevant" | Aggregate + deduplicate across entire window |
| User directive | "Maximize KV cache reusability rate" | One message, stable structure, no raw tail |
| User directive | "Keep the summary concise but information-dense" | Capped sections, no prose filler |

### Why NOT a Verbatim Tail

The "structured prefix + verbatim tail" pattern (keep last N raw messages)
was evaluated and **rejected** because:

- 20 raw messages bloat the cache prefix with volatile content
- Tool outputs change every turn → guaranteed cache misses
- OpenClacky targets **under 10K tokens** post-compression, not "10K + raw tail"
- The structured summary already preserves all decision-relevant artifacts
- `recall_context` provides on-demand drill-down into any specific turn

### Summary Structure

`build_hint_context()` in `builder.py` produces:

```
<!-- DECOHERE:BEGIN -->
[INTERNAL CONTEXT — DO NOT ECHO]
Session: 20 turns (18 extracted).
Current focus: Fix cache invalidation in compression pipeline
Files: ~/project/builder.py(T18), ~/project/config.py(T15)
Decisions:
• Use structured summary over verbatim tail → Cache stability
• Cap files at 15, decisions at 8 → Token budget
Active procedures:
• Run full test suite after each builder change
Recent arc:
  T16: Implemented basic hint builder with turn summaries
  T17: Added test coverage for hint context
  T18: Rewrote to information-dense structured format
Use recall_context for full turn details.
<!-- DECOHERE:END -->
```

### Section Aggregation Logic

| Section | Source fields | Deduplication | Cap |
|---------|-------------|---------------|-----|
| **Current focus** | Most recent `user_intent` | Last one wins | 200 chars |
| **Files** | `files_touched[]` across all recent turns | By path, keep latest turn number | 15 files |
| **Decisions** | `decisions_and_rationale[]` | By decision text (case-insensitive) | 8 decisions |
| **Procedures** | `procedures[]` | By procedure text (case-insensitive) | 5 procedures |
| **Recent arc** | `narrative.summary` from last 3 turns | None (always show last 3) | 3 entries, 150 chars each |

### Size Characteristics

| Metric | Full L1+L2 | Old hint (turn list) | New structured summary |
|--------|-----------|---------------------|----------------------|
| Messages | 2 | 1 | 1 |
| Typical size (20 turns) | 8,000-15,000 chars | 800-1,500 chars | 500-2,000 chars |
| Information preserved | Everything (too much) | Summaries only | Files + decisions + procedures + arc |
| Cache stability | Poor (changes every turn) | Good | Good |
| Regurgitation risk | High | Low | Low |

---

## Anti-Regurgitation Pipeline

### Root Cause Elimination

The primary fix: **don't inject large structured blocks**. The structured
summary is ~500-2000 chars with no echoing-friendly format (no headers like
`## Ledger Entries`, no `[Turn N]` blocks with field patterns). The LLM has
nothing substantial to regurgitate.

### Defense in Depth (6 layers)

For remaining edge cases, `_strip_ledger_sections()` runs on every assistant
message before extraction:

```
Layer 0: Canary token [canary:XXXX] — per-session, zero false positives
Layer 1: <!-- DECOHERE:BEGIN --> markers
Layer 2: ## Ledger Entries header patterns
Layer 3: [INTERNAL CONTEXT — DO NOT ECHO] guard text
Layer 4: [Turn N] + field pattern (message_range, tools, files, etc.)
Layer 5: Gutted-turn detection (post-strip empty assistant → skip extraction)
```

### Mid-Turn Re-Entry Guard

`compress()` is called before EACH LLM invocation, including mid-turn
iterations after tool calls. The `user_hash` guard prevents duplicate
placeholder creation and extraction scheduling during tool-call loops.

---

## Cache Marker Integration

### OpenClacky Double-Buffer Pattern

Decohere messages are tagged `_decohere_injected = True`. The cache marker
selector in `prompt_caching.py` **skips** these messages, placing markers only
on real conversation messages. This prevents:

1. Writing a cache breakpoint on an ephemeral message that won't exist next turn
2. Invalidating the cache prefix when the summary content changes

### Message Flow

```
System prompt        ← stable, cached (breakpoint 1)
[... conversation history ...]
Message N-1          ← cached (breakpoint 2 — rolling)
Message N            ← new breakpoint written here
[turn_context]       ← SKIPPED by marker selector
[shared_knowledge]   ← SKIPPED by marker selector
[shared_state]       ← SKIPPED by marker selector
```

---

## Compression Gating (should_compress)

Returns `True` only when there is work for `compress()` to do:

1. First call of the session (`_last_compressed_turns == 0`)
2. New posted turns available (`turn_count > _last_compressed_turns`)
3. Default: `True` (safety net — `compress()` Phase 1 guard handles re-entry)

This prevents redundant context rebuilds during tool-call loops while ensuring
new ledger entries are always injected.

---

## recall_context: On-Demand Retrieval

The structured summary intentionally omits per-turn details. When the LLM
needs specific historical context, it calls `recall_context`:

```json
{
  "name": "recall_context",
  "parameters": {
    "query": "What was the compression threshold decision?",
    "max_turns": 5
  }
}
```

Returns full L1+L2 formatted output for matching turns. This separation
(summary for orientation, tool for detail) keeps the injected context small
while preserving full recall capability.

---

## Test Coverage

54 tests across 4 test files:

| File | Tests | Coverage |
|------|-------|---------|
| `test_hint_context.py` | 19 | Structured summary: sections, dedup, capping, size |
| `test_phase1_hardening.py` | 10 | Canary tokens, re-entry guard |
| `test_phase2_cache.py` | 9 | Annotation, marker exclusion |
| `test_phase3_4.py` | 16 | recall_context, compression gating |
