"""Tests for the information-dense structured summary (build_hint_context).

Validates that the summary preserves the exact artifacts that downstream
turns depend on: files, decisions, procedures, narrative arc.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


def _make_turn(n, summary=None, intent=None, skipped=False, **overrides):
    """Build a realistic turn dict for testing."""
    turn = {
        "n": n,
        "message_range": f"{n*10}-{n*10+5}",
        "tools": [],
        "files_touched": [],
        "relevant_metadata": {"task": "test task", "reference_class": "qa"},
        "concepts_and_definitions": [],
        "narrative": {"summary": summary or f"Turn {n} summary."},
        "user_intent": intent or f"Intent for turn {n}.",
        "decisions_and_rationale": [],
        "procedures": [],
        "insights_and_learnings": [],
        "entry_skipped": skipped,
    }
    turn.update(overrides)
    return turn


class TestStructuredSummaryBasics:
    """Core behavior: empty, skipped, single turn."""

    def test_empty_turns_returns_empty(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        result = build_hint_context([], max_turns=10)
        assert result == []

    def test_all_skipped_turns_returns_empty(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1, skipped=True), _make_turn(2, skipped=True)]
        result = build_hint_context(turns, max_turns=10)
        assert result == []

    def test_single_turn_produces_one_message(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1, summary="Fixed the login bug.")]
        result = build_hint_context(turns, max_turns=10)
        assert len(result) == 1
        assert result[0]["role"] == "system"
        assert result[0]["name"] == "turn_context"

    def test_has_decohere_markers(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1)]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "<!-- DECOHERE:BEGIN -->" in content
        assert "<!-- DECOHERE:END -->" in content


class TestStructuredSummaryContent:
    """Verify the summary contains information-dense sections."""

    def test_contains_turn_count(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(i) for i in range(1, 6)]
        result = build_hint_context(turns, max_turns=10)
        assert "5 turns" in result[0]["content"]

    def test_contains_files_section(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [
            _make_turn(1, files_touched=["/home/user/project/auth.py"]),
            _make_turn(2, files_touched=["/home/user/project/db.py"]),
        ]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "Files:" in content
        assert "auth.py" in content
        assert "db.py" in content

    def test_files_deduplicated_with_latest_turn(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [
            _make_turn(1, files_touched=["/tmp/a.py"]),
            _make_turn(5, files_touched=["/tmp/a.py"]),
        ]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        # Should show T5, not T1
        assert "(T5)" in content
        # Should appear only once
        assert content.count("a.py") == 1

    def test_contains_decisions(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1, decisions_and_rationale=[
            {"decision": "Use PostgreSQL", "rationale": "Better for JSONB queries"},
        ])]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "Decisions:" in content
        assert "Use PostgreSQL" in content
        assert "Better for JSONB queries" in content

    def test_decisions_deduplicated(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [
            _make_turn(1, decisions_and_rationale=[
                {"decision": "Use PostgreSQL", "rationale": "reason 1"},
            ]),
            _make_turn(2, decisions_and_rationale=[
                {"decision": "Use PostgreSQL", "rationale": "reason 2"},
            ]),
        ]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert content.count("Use PostgreSQL") == 1

    def test_contains_procedures(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1, procedures=[
            {"procedure": "Run migrations with --dry-run first"},
        ])]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "Active procedures:" in content
        assert "--dry-run" in content

    def test_contains_narrative_arc(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [
            _make_turn(1, summary="Set up the project."),
            _make_turn(2, summary="Implemented auth module."),
            _make_turn(3, summary="Fixed CORS headers."),
        ]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "Recent arc:" in content
        assert "Fixed CORS" in content

    def test_contains_current_focus(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1, intent="Fix the auth bug in login.py")]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "Current focus:" in content
        assert "Fix the auth bug" in content

    def test_mentions_recall_context(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1)]
        result = build_hint_context(turns, max_turns=10)
        assert "recall_context" in result[0]["content"]


class TestStructuredSummaryCanary:
    """Canary token integration."""

    def test_includes_canary(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1)]
        result = build_hint_context(turns, max_turns=10, canary_token="abcd1234")
        assert "[canary:abcd1234]" in result[0]["content"]

    def test_no_canary_when_none(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1)]
        result = build_hint_context(turns, max_turns=10, canary_token=None)
        assert "[canary:" not in result[0]["content"]


class TestStructuredSummaryBounds:
    """Capping and filtering behavior."""

    def test_skips_skipped_turns(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [
            _make_turn(1, summary="Good turn."),
            _make_turn(2, skipped=True),
            _make_turn(3, summary="Another good turn."),
        ]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "3 turns (2 extracted)" in content

    def test_respects_max_turns(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(i, files_touched=[f"/tmp/file_{i}.py"]) for i in range(1, 20)]
        result = build_hint_context(turns, max_turns=5)
        content = result[0]["content"]
        # Should not include file from turn 1 (outside max_turns window)
        assert "file_1.py" not in content
        # Should include file from turn 19 (inside window)
        assert "file_19.py" in content

    def test_uses_intent_when_no_summary(self):
        from plugins.context_engine.decohere.context.builder import build_hint_context
        turns = [_make_turn(1, intent="Fix the auth bug", narrative={"summary": ""})]
        result = build_hint_context(turns, max_turns=10)
        content = result[0]["content"]
        assert "Fix the auth bug" in content


class TestStructuredSummarySize:
    """Verify the summary stays compact."""

    def test_smaller_than_full_ledger(self):
        from plugins.context_engine.decohere.context.builder import (
            build_hint_context, build_ledger_context,
        )
        turns = [
            _make_turn(i,
                summary=f"Did thing {i} in session.",
                files_touched=[f"/tmp/file_{i}.py"],
                decisions_and_rationale=[{"decision": f"Choice {i}", "rationale": f"Because {i}"}],
                procedures=[{"procedure": f"Step {i} of plan"}],
            )
            for i in range(1, 11)
        ]

        full = build_ledger_context(turns, max_turns=10)
        hint = build_hint_context(turns, max_turns=10)

        full_size = sum(len(m.get("content", "")) for m in full)
        hint_size = sum(len(m.get("content", "")) for m in hint)

        # Hint should be much smaller (typically 5-20x)
        assert hint_size < full_size / 2, (
            f"Hint ({hint_size} chars) should be much smaller than full ({full_size} chars)"
        )
        assert len(hint) == 1
        assert len(full) == 2
