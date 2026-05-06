# Turn Spec Deriver — Git Contribution Strategy

## Goal

Submit the turn-spec-deriver context engine plugin as a core contribution to `NousResearch/hermes-agent`. Determine branch strategy, directory mapping, and upstream update conflict resolution.

## Current Context

**Existing plan:** `2026-05-05_020000-structured-turn-summary.md` (2,371 lines) — full architectural spec for a context engine plugin that replaces raw message history injection with structured 13-field turn specifications. 17 modules across 6 layers.

**Current path:** Designed for `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/` — a profile-local path.

**Target repo:** `NousResearch/hermes-agent` — 135K stars, MIT license, 8.5K open issues. Extremely active. Branch naming convention: `type/descriptor` (e.g., `feat/secrets-phase1`, `fix/compaction-secrets-preservation`).

**Local repo:** `~/.hermes/hermes-agent/` — git clone of upstream, on `main`, tracking `origin/main`. 600+ remote branches visible.

## How the Plugin System Works (Relevant to Contribution Model)

### No Core File Changes Needed

Context engine plugins are **auto-discovered**. `plugins/context_engine/__init__.py` → `discover_context_engines()` scans subdirectories under `plugins/context_engine/` for `__init__.py` files. A plugin just needs:
1. A directory `plugins/context_engine/turn-spec-deriver/`
2. An `__init__.py` with either a `register(ctx)` function or a `ContextEngine` subclass

**Zero modifications to core files.** This is enforced by the CONTRIBUTING.md rule: "plugins MUST NOT modify core files (`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`, etc.)."

### Discovery Chain

```
config.yaml: context.engine = "turn-spec-deriver"
    → agent/context_engine.py loads engine by name
    → plugins/context_engine/__init__.py → load_context_engine("turn-spec-deriver")
    → plugins/context_engine/turn-spec-deriver/__init__.py
    → ContextEngine subclass instantiated
    → Agent loop calls should_compress() / compress() / update_from_response()
```

The `register(ctx)` pattern (used by general plugins) is also supported — `_EngineCollector` captures `register_context_engine()` calls.

### Optional Plugin Metadata

A `plugin.yaml` at the plugin root provides description for `discover_context_engines()`:
```yaml
name: turn-spec-deriver
description: "Turn specification derivation — structured semantic extraction replacing raw message injection"
version: 1.0.0
```

## Decision 1: Contribution Model — Core Plugin, Not Profile-Local

### Why core plugin

| Factor | Core plugin (`plugins/context_engine/turn-spec-deriver/`) | Profile-local (`~/.hermes/profiles/speak-off-the-cuff/plugins/...`) |
|--------|----------------------------------------------------------|---------------------------------------------------------------------|
| Discovery | Auto-discovered by `discover_context_engines()`, no config hack | Would need custom plugin path or symlink |
| PR merge | ✅ Directly into Hermes repo | ❌ Not in repo; user must manually install |
| Upstream compatibility | `hermes update` includes it | Must re-copy after updates |
| Config activation | `context.engine: "turn-spec-deriver"` in any config | Same, but plugin must be discoverable |
| Other profiles | Any profile can use it | Only speak-off-the-cuff profile |
| Maintenance burden | Reviewed as part of Hermes | Siloed, diverges |

**Decision:** Move from `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/` to `plugins/context_engine/turn-spec-deriver/` in the repo. The profile still uses it — just set `context.engine: "turn-spec-deriver"` in the profile's `config.yaml`.

### Directory Mapping

| Current path (plan) | New path (repo) | Notes |
|---------------------|-----------------|-------|
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/__init__.py` | `plugins/context_engine/turn-spec-deriver/__init__.py` | Entry layer — implements `register(ctx)` with `ctx.register_context_engine()` |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/config.py` | `plugins/context_engine/turn-spec-deriver/config.py` | `DeriverConfig` — reads from `auxiliary.compression` in config.yaml (same as built-in compressor) |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/types.py` | Same, under new root | |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/core/*.py` | Same, under new root | |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/context/*.py` | Same, under new root | |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/io/*.py` | Same, under new root | |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/scheduling/*.py` | Same, under new root | |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/monitoring/*.py` | Same, under new root | |
| `~/.hermes/profiles/speak-off-the-cuff/plugins/context_engine/turn-spec-deriver/plugin.yaml` | `plugins/context_engine/turn-spec-deriver/plugin.yaml` | New file — plugin metadata |

**What stays in the profile:** `SOUL.md`, `sessions/`, `config.yaml`. The profile is configuration + data, not code.

## Decision 2: Git Branch Strategy

### Branch naming

Following Hermes convention: `feat/turn-spec-deriver`

```
git checkout -b feat/turn-spec-deriver origin/main
```

### Commit Strategy

**Single cohesive PR** rather than incremental merges. Reason: the plugin is a self-contained unit with internal dependencies (6 layers, strict one-way dep graph). Submitting partial layers creates broken intermediate states.

Commit sequence (each one buildable):
1. `feat: add turn-spec-deriver plugin skeleton` — `__init__.py`, `plugin.yaml`, `config.py`, `types.py`
2. `feat: add turn-spec-deriver core layer` — `core/` (deriver, prompt, extractor, validator, utils, indexer)
3. `feat: add turn-spec-deriver io layer` — `io/session_io.py`
4. `feat: add turn-spec-deriver context layer` — `context/` (builder, formatter, placeholder, classifier)
5. `feat: add turn-spec-deriver scheduling layer` — `scheduling/` (task_manager, metrics)
6. `feat: add turn-spec-deriver monitoring layer` — `monitoring/` (checks, reporter, snapshots)
7. `test: add turn-spec-deriver test suite` — `tests/plugins/context_engine/test_turn_spec_deriver.py`

All commits are additive to a new directory. Zero merge conflicts with upstream.

### Why not fork?

The user's local repo already tracks `origin → https://github.com/NousResearch/hermes-agent.git`. For PR submission, they need push access. Two options:

**Option A — Fork (standard):**
```
# Create fork on GitHub via UI
git remote add myfork git@github.com:<username>/hermes-agent.git
git push myfork feat/turn-spec-deriver
# Open PR from myfork:feat/turn-spec-deriver → NousResearch:main
```

**Option B — Direct push (if collaborator):**
```
git push origin feat/turn-spec-deriver
# Open PR from origin:feat/turn-spec-deriver → NousResearch:main
```

Option A is the standard path unless the user has write access to NousResearch/hermes-agent.

## Decision 3: Upstream Update Conflict Resolution

### The architecture makes this trivial

Because the plugin lives entirely under `plugins/context_engine/turn-spec-deriver/` and touches zero core files, merges from upstream `main` are conflict-free UNLESS someone else modifies the same directory. The probability of this is near-zero for an entirely new plugin.

### Workflow During Development

```
# Start of development session: sync upstream
git fetch origin
git rebase origin/main
# Conflicts possible only if origin/main changed plugins/context_engine/turn-spec-deriver/
# (near-zero probability for a new plugin)

# ... develop, commit ...

# Before PR: final rebase
git fetch origin
git rebase origin/main
# Push updated branch
git push myfork feat/turn-spec-deriver --force-with-lease
```

### What could actually conflict?

| Scenario | Likelihood | Resolution |
|----------|-----------|------------|
| Someone else adds `plugins/context_engine/turn-spec-deriver/` | Near-zero | Coordinate — this is a unique namespace |
| `agent/context_engine.py` ContextEngine ABC changes | Possible | Plugin implements the ABC; if new abstract methods added, implement them |
| `plugins/context_engine/__init__.py` discovery logic changes | Possible | Backward-compatible — new discovery features don't break existing plugins |
| `config.yaml` schema changes | Possible | Plugin reads from `auxiliary.compression` — same path as built-in compressor |
| SessionDB / raw message store API changes | Moderate risk | `io/session_io.py` is the sole integration point. If SessionDB changes, only this one file needs updating |

### Mitigation: Integration Point Isolation

The plugin touches Hermes internals at exactly **two points**, both through the `ContextEngine` ABC:

1. **`compress()` called by agent loop** — receives `messages: List[Dict]`, returns `List[Dict]`. Standard interface, stable.
2. **SessionDB access** — `io/session_io.py` uses `hermes_state.SessionDB` to read/write turn specs. This is the brittle point. Mitigation: wrap all SessionDB access through a thin adapter in `io/session_io.py` so if the API changes, only one file needs updating.

### If upstream moves fast and the PR sits open

```
# Periodic rebase to keep current
git fetch origin
git rebase origin/main
# Resolve any conflicts (should be none, but if any: only in io/session_io.py)
git push myfork feat/turn-spec-deriver --force-with-lease
```

### Post-merge: profile migration

After the plugin is merged into Hermes and the user runs `hermes update`, two things need to happen:

1. **Remove the old profile-local copy** — delete `~/.hermes/profiles/speak-off-the-cuff/plugins/` entirely. The plugin now ships with Hermes.
2. **Config already works** — `config.yaml` in the profile already says `context.engine: "turn-spec-deriver"`. The auto-discovery finds it under the repo's `plugins/context_engine/` directory.

## Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| PR sits open for months, upstream diverges | Rebase burden builds | Push small, reviewable PR. The plugin is self-contained, so review surface is bounded. |
| ContextEngine ABC adds mandatory abstract methods | Plugin breaks on rebase | If new methods are added to ABC, implement them before re-pushing. |
| SessionDB API changes break `io/session_io.py` | Plugin can't read/write turn specs | `io/session_io.py` is thin — ~50 lines of SessionDB calls. Easy to update. |
| Plugin is too niche for core inclusion | PR rejected | Fallback: keep as profile-local plugin with symlink to `~/.hermes/plugins/`. Hermes discovers plugins from `~/.hermes/plugins/` via the general plugin system (not context engine autodiscovery) — but the context engine loader only scans `plugins/context_engine/` in the repo. Would need a tiny core change to also scan `~/.hermes/plugins/context_engine/`. This could be proposed separately. |
| Auxiliary model config (`auxiliary.compression`) not present in user's config | Plugin can't derive specs | Graceful degradation — fall back to built-in compressor behavior. `should_compress()` returns False, raw messages pass through. |

## What This Plan Does NOT Cover

- Actual implementation of the 17 modules (covered by the existing 2,371-line plan)
- Test strategy (separate concern)
- Documentation (separate concern)
- Performance benchmarking (separate concern)

## Next Steps

1. Create fork of `NousResearch/hermes-agent` on GitHub
2. Add fork as remote: `git remote add myfork git@github.com:<username>/hermes-agent.git`
3. Create branch: `git checkout -b feat/turn-spec-deriver origin/main`
4. Implement plugin following the existing architectural plan
5. Rebase on `origin/main` before PR
6. Open PR with description linking to the architectural plan
