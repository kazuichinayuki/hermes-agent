# Decohere Audit: Codebase vs May 5th Specification

Full audit of `plugins/context_engine/decohere/` against [2026-05-05_020000-structured-turn-summary.md](file:///Users/shurigenha/.hermes/hermes-agent/.hermes/plans/2026-05-05_020000-structured-turn-summary.md).

---

## Summary

The implementation is **architecturally faithful** to the specification. The 6-layer structure, dependency direction, immutable types, pure functions, and SQLite storage all match the design. The codebase has also evolved _beyond_ the spec with useful additions (knowledge injection, CLI/TUI, shared state, session lifecycle guards) that are well-integrated.

I found **12 findings** across 3 severity levels:

| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 Deviation | 4 | Behaviour differs from spec in a way that could cause bugs or spec confusion |
| 🟡 Drift | 5 | Intentional evolution from spec — document or reconcile |
| 🟢 Enhancement | 3 | Beyond-spec additions that are sound — just need spec acknowledgment |

---

## 🔴 Deviations (fix recommended)

### D1. Context injection uses `system` role, not `tool`/`user` as specified

**Spec (E01):** _"Two-layer injection: exactly 2 messages — tool role (all L1, reference-grade) + user role name=\"turn_context\" (all L2, engage-grade)"_

**Code ([builder.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/context/builder.py#L41-L42)):**
```python
{"role": "system", "name": "ledger_l1", ...}   # spec says "tool"
{"role": "system", "name": "turn_context", ...} # spec says "user"
```

**Impact:** Using `system` role instead of `tool`/`user` changes how the LLM's attention weights the two layers. The spec's design rationale is that different roles create an attention breakpoint (E02) — the model treats `tool` content as reference-grade and `user` content as engage-grade. With both as `system`, the attention separation is lost.

**Decision needed:** Was this changed intentionally because some providers don't support `tool` role without a preceding tool_call? If so, the spec should be updated. If not, it should be reverted.

> [!IMPORTANT]
> This is the most architecturally significant deviation. The two-layer attention design (A03) relies on role differentiation.

---

### D2. `task_manager.py` references `threading.Thread` at class-level before import

**Code ([task_manager.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/scheduling/task_manager.py#L30)):**
```python
self._thread: threading.Thread | None = None  # line 30 — threading not imported yet
```

`threading` is only imported lazily inside `_ensure_loop()` (line 40). The type annotation at line 30 references `threading.Thread` before the module is imported. This works at runtime because `from __future__ import annotations` defers annotation evaluation, but it's fragile — any runtime introspection of the annotation (e.g., `get_type_hints()`) would fail.

**Fix:** Add `import threading` to the top-level imports, or change the annotation to `"threading.Thread"` string form.

---

### D3. `LedgerStore.save_turn()` FTS5 rowid collision on multi-concept turns

**Code ([store.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/store.py#L93-L99)):**
```python
self._conn.execute("DELETE FROM concepts_fts WHERE rowid = ?", (turn_n,))
for c in turn.get("concepts_and_definitions", []) or []:
    if isinstance(c, dict):
        self._conn.execute(
            "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
            (turn_n, c.get("term", ""), c.get("definition", "")),
        )
```

When a turn has multiple concepts, all are inserted with the same `rowid = turn_n`. After the first insert, subsequent inserts for the same rowid will fail or overwrite. The migration code in [db.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/db.py#L117) correctly uses `base = turn_n * 1000 + idx`, but the live save_turn path doesn't.

**Fix:** Use `turn_n * 1000 + idx` for FTS5 rowid in `save_turn()`, consistent with the migration code. Also delete by range `WHERE rowid >= turn_n*1000 AND rowid < (turn_n+1)*1000`.

---

### D4. `poster.py` imports from `agent.auxiliary_client` — violates core purity (AL05)

**Spec (AL05):** _"core/ 零项目内依赖：不 import 本项目其他模块"_

**Code ([poster.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/core/poster.py#L5)):**
```python
from agent.auxiliary_client import async_call_llm, extract_content_or_reasoning
```

`poster.py` lives in `core/` but imports from Hermes's `agent.auxiliary_client`. This is the only file in `core/` that reaches outside the plugin. The spec says core should have zero project-internal dependencies.

**Decision needed:** Move `poster.py` to `scheduling/` (since it's inherently async + side-effectful via the LLM call), or accept this as a pragmatic exception and document it. The LLM call _is_ I/O, so it semantically belongs outside `core/`.

> [!WARNING]
> This also means `core/poster.py` is not "pure async computation" as its docstring claims — it makes a real network call.

---

## 🟡 Drift (intentional evolution — reconcile with spec)

### F1. `MetricsCollector` is no longer per-session keyed

**Spec:** `record_attempt(session_id)`, `record_success(session_id, elapsed_ms)`, etc. — all methods take `session_id`.

**Code ([metrics.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/scheduling/metrics.py)):** Methods take no `session_id` — the collector is instantiated once per `Decohere` instance (which maps to one session). This is simpler and correct for the current 1:1 mapping.

**Action:** Update spec to reflect the simplified signature, or note the assumption that `Decohere` instances are per-session.

---

### F2. `HealthReporter` methods lost `session_id` parameter

**Spec:** `verify_range(session_id, ...)`, `verify_persisted(session_id, ...)`, etc.

**Code ([reporter.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/monitoring/reporter.py#L28-L44)):** `verify_range(actual_range, msg_count)`, `verify_persisted(expected_n)` — no session_id. Same reason as F1.

**Action:** Same as F1 — update spec.

---

### F3. `threshold_percent` default changed from `0.0` to `1.0`

**Spec:** `threshold_percent: float = 0.0` — "always returns True for v2"

**Code ([__init__.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/__init__.py#L55)):** `threshold_percent: float = 1.0`

This was likely changed to integrate with `run_agent.py`'s threshold logic. The `1.0` default means "whole context window" which works correctly with the compression config's `threshold` override.

**Action:** Update spec.

---

### F4. `config.timeout` default changed from `5.0` to `30.0`

**Spec (I01):** _"Entry posting timeout: 5s (configurable)"_

**Code ([config.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/config.py#L25)):** `timeout: float = 30.0`

This was likely increased because real-world entry posting through OpenRouter takes longer than 5s.

**Action:** Update spec to reflect 30s default.

---

### F5. `compress()` has an early-return guard via `_last_compressed_turns`

**Spec:** `compress()` always builds context from ledger.

**Code ([__init__.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/__init__.py#L190-L194)):**
```python
if current_turns <= self._last_compressed_turns:
    return messages  # Return original messages, not ledger context
```

This optimization skips context rebuilding if no new turns have been posted since the last compression. It returns `messages` (raw) instead of ledger context — which means on consecutive calls within the same turn, the LLM gets raw messages rather than the ledger view.

**Action:** Verify this is intentional. It means the first `compress()` call for a turn writes the placeholder and builds ledger context, but any subsequent call within the same turn returns raw messages.

---

## 🟢 Enhancements beyond spec (sound — acknowledge in spec)

### E1. Knowledge injection system (`knowledge/` module)

The spec has no mention of cross-session knowledge injection. The codebase adds:
- [SharedStore](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/knowledge/shared_store.py) — cross-session concept database
- [injector.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/knowledge/injector.py) — builds injection messages from shared store
- Auto-import of concepts from posted turns ([task_manager.py:196-228](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/scheduling/task_manager.py#L196-L228))
- User config for knowledge sources, exclusions, retrieval mode ([config.py:82-143](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/config.py#L82-L143))

**Action:** Add a new section to the spec documenting the knowledge subsystem.

---

### E2. Session state store (`io/state_store.py`)

**Code:** `StateStore` provides per-session key-value state with scoping, injected as `shared_state` in context.

**Action:** Document in spec.

---

### E3. Full CLI/TUI tooling (`cli/` with 19 commands + TUI)

A comprehensive management interface with list, show, search, edit, delete, export, vacuum, repost, stats, knowledge management, and a full terminal UI.

**Action:** Document in spec as a new section.

---

## Constraint Checklist

### Architecture Layers (AL01-AL12)

| ID | Status | Notes |
|----|--------|-------|
| AL01 | ✅ | 6 layers present + 2 new layers (knowledge, cli) |
| AL02 | ✅ | No upward imports found (verified via grep) |
| AL03 | ✅ | No cross-layer violations in middle layers |
| AL04 | ✅ | No circular imports detected |
| AL05 | 🔴 D4 | `core/poster.py` imports `agent.auxiliary_client` |
| AL06 | ✅ | `io/` only imports from `core/` (via `..db`, `..store`) |
| AL07 | ✅ | `context/` only imports from `core/` |
| AL08 | ✅ | Layer separation maintained for iterative changes |
| AL09 | ✅ | No module-level mutable globals found |
| AL10 | ⚠️ | `_strip_ledger_sections` mutates messages in-place (justified in docstring) |
| AL11 | ⚠️ | `poster.py` makes network calls (LLM API) despite being in `core/` |
| AL12 | ✅ | All config defaults in `LedgerConfig` |

### Session Format (B01-B08)

| ID | Status | Notes |
|----|--------|-------|
| B01-B02 | ✅ | `is_v2()` always returns True for decohere sessions |
| B03 | ✅ | Session metadata stored in SQLite, not JSON |
| B04 | ✅ | Memory not stored in session |
| B05 | ✅ | USER PROFILE not in session storage |
| B06 | ✅ | Tool definitions excluded |
| B07 | ✅ | base_url excluded |
| B08 | ✅ | Raw messages in `RawMessageStore` table, separate from ledger |

### Turn Fields (C01-C14)

| ID | Status | Notes |
|----|--------|-------|
| C01 | ✅ | 13 fields in placeholder (9 semantic + 4 mechanical) |
| C02-C03 | ✅ | Tools and files extracted correctly with sanitization |
| C04-C12 | ✅ | All L2 fields present in validator defaults |
| C13 | ✅ | `message_range` set synchronously in `compute_range()` |
| C14 | ✅ | Empty content → `()` or `""` via validator |

### Context Injection (E01-E06)

| ID | Status | Notes |
|----|--------|-------|
| E01 | 🔴 D1 | Uses `system` role, not `tool`/`user` |
| E02 | 🟡 | Attention breakpoint reduced (both `system`) |
| E03 | ✅ | `max_turns = 20` in `LedgerConfig` |
| E04 | ✅ | All turns injected up to max_turns |
| E05 | ✅ | Cross-turn association preserved |
| E06 | ✅ | Token overflow handled by threshold_percent integration |

### Fallback (G01-G05)

| ID | Status | Notes |
|----|--------|-------|
| G01-G02 | ✅ | `build_fallback_context()` correctly uses raw for latest turn |
| G03 | ✅ | `format_raw_compressed()` caps to 3 sentences + tool names |
| G04 | ✅ | Legacy detection via `check_readiness()` |
| G05 | ✅ | F5 implemented — `asyncio.shield` + `_try_persist_late` callback |

### Concurrency (H01-H04)

| ID | Status | Notes |
|----|--------|-------|
| H01-H02 | ✅ | Per-session `asyncio.Lock` in TaskManager |
| H03 | ✅ | Lock only gates posting, not message handling |
| H04 | ✅ | `message_range` frozen at `compute_range()` (synchronous write) |

### Security (J01-J04)

| ID | Status | Notes |
|----|--------|-------|
| J01-J02 | ✅ | Platform filtering is gateway-side (out of plugin scope) |
| J03 | ✅ | `sanitize_path()` and `sanitize_text()` replace home dir |
| J04 | ✅ | `strip_credentials()` handles sk-*, Bearer, API_KEY patterns |
| J05 | ✅ | Injection guard wraps user messages in `wrap_user_message()` |

---

## Proposed Changes

### 1. Fix FTS5 rowid collision (D3)

#### [MODIFY] [store.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/store.py)

Change `save_turn()` to use `turn_n * 1000 + idx` for FTS5 rowid, matching the migration code:

```diff
-self._conn.execute("DELETE FROM concepts_fts WHERE rowid = ?", (turn_n,))
-for c in turn.get("concepts_and_definitions", []) or []:
-    if isinstance(c, dict):
-        self._conn.execute(
-            "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
-            (turn_n, c.get("term", ""), c.get("definition", "")),
-        )
+base = turn_n * 1000
+self._conn.execute(
+    "DELETE FROM concepts_fts WHERE rowid >= ? AND rowid < ?",
+    (base, base + 1000),
+)
+for idx, c in enumerate(turn.get("concepts_and_definitions", []) or []):
+    if isinstance(c, dict):
+        self._conn.execute(
+            "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
+            (base + idx, c.get("term", ""), c.get("definition", "")),
+        )
```

---

### 2. Fix threading annotation (D2)

#### [MODIFY] [task_manager.py](file:///Users/shurigenha/.hermes/hermes-agent/plugins/context_engine/decohere/scheduling/task_manager.py)

Add `import threading` to top-level imports (line 8 area).

---

## Open Questions

> [!IMPORTANT]
> **Q1: Context injection role (D1)** — Was the change from `tool`/`user` → `system` intentional? Some API providers reject `tool` messages without a preceding `tool_call`. If intentional, should we update the spec? If not, should we revert?

> [!IMPORTANT]
> **Q2: `poster.py` location (D4)** — Should `poster.py` move from `core/` to `scheduling/` to respect the "core has zero side-effects" principle? Or is the current placement acceptable as a pragmatic exception?

> [!NOTE]
> **Q3: Spec update scope** — The codebase has evolved significantly beyond the May 5th spec (knowledge injection, CLI/TUI, state store, revised defaults). Should we update the spec document itself, or create a new versioned spec?

---

## Verification Plan

### Automated Tests
- Run existing test suite: `python -m pytest tests/plugins/context_engine/decohere/ -v`
- Add a test for FTS5 multi-concept rowid correctness after fix D3

### Manual Verification
- Verify FTS5 concept search returns all concepts from a multi-concept turn
- Verify context injection messages reach the LLM correctly with current role settings
