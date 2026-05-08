"""Tests for context/builder.py — context message assembly."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.context.builder import (
    build_ledger_context,
    build_fallback_context,
    build_raw_context,
    build_indexed_context,
)


def _make_turn(n, critical_reflection=None, entry_skipped=False, user_intent="test"):
    """Helper for turn dicts."""
    turn = {
        "n": n,
        "message_range": [n, n + 1],
        "tools": [],
        "files_touched": [],
        "reference_documentation": [],
        "relevant_metadata": {"task": "test", "reference_class": "test"},
        "concepts_and_definitions": [],
        "narrative": {"summary": f"Turn {n} summary", "cross_references": []},
        "user_intent": user_intent,
        "decisions_and_rationale": [],
        "procedures": [],
        "insights_and_learnings": [],
        "critical_reflection": critical_reflection if critical_reflection is not None else {
            "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
        },
    }
    if entry_skipped:
        turn["entry_skipped"] = True
    return turn


# ── build_ledger_context ──

def test_build_ledger_context_two_messages():
    turns = [_make_turn(1), _make_turn(2)]
    result = build_ledger_context(turns, 20)
    assert len(result) == 2
    assert result[0]["role"] == "tool"
    assert result[1]["role"] == "user"
    assert result[1]["name"] == "turn_context"


def test_build_ledger_context_skips_entry_skipped():
    turns = [
        _make_turn(1),
        _make_turn(2, entry_skipped=True),
        _make_turn(3),
    ]
    result = build_ledger_context(turns, 20)
    content = result[0]["content"] + result[1]["content"]
    assert "Turn 1" in content
    assert "Turn 3" in content
    assert "Turn 2" not in content  # skipped


def test_build_ledger_context_max_turns():
    turns = [_make_turn(i) for i in range(1, 11)]
    result = build_ledger_context(turns, max_turns=3)
    content = result[0]["content"] + result[1]["content"]
    assert "[Turn 8]" in content
    assert "[Turn 1]" not in content  # truncated out


def test_build_ledger_context_empty():
    result = build_ledger_context([], 20)
    assert result == []


def test_build_ledger_context_all_skipped():
    turns = [_make_turn(1, entry_skipped=True), _make_turn(2, entry_skipped=True)]
    result = build_ledger_context(turns, 20)
    assert result == []


# ── build_fallback_context ──

def test_build_fallback_context():
    turns = [_make_turn(1), _make_turn(2, critical_reflection=None)]
    last_msgs = [{"role": "user", "content": "new question"}]
    result = build_fallback_context(turns, 20, last_msgs)
    assert len(result) >= 2
    assert "raw fallback" in result[-2]["content"] or "raw compressed" in result[-2]["content"]


def test_build_fallback_context_single_turn():
    """Only one turn (the pending one): use raw compressed for everything."""
    turns = [_make_turn(1, critical_reflection=None)]
    last_msgs = [{"role": "user", "content": "hello"}]
    result = build_fallback_context(turns, 20, last_msgs)
    assert len(result) == 2


# ── build_raw_context ──

def test_build_raw_context_passthrough():
    msgs = [{"role": "user", "content": "test"}]
    result = build_raw_context(msgs)
    assert result == msgs


# ── build_indexed_context ──

def test_build_indexed_context_few_turns():
    """≤20 turns delegates to build_ledger_context (2 messages)."""
    turns = [_make_turn(i) for i in range(1, 6)]
    result = build_indexed_context(turns, 20)
    assert len(result) == 2


def test_build_indexed_context_many_turns():
    """>20 turns returns 3 messages: index + L1 + L2."""
    turns = [_make_turn(i) for i in range(1, 25)]
    result = build_indexed_context(turns, 20)
    assert len(result) == 3
    assert result[0]["role"] == "tool"
    assert "Turn Index" in result[0]["content"]
