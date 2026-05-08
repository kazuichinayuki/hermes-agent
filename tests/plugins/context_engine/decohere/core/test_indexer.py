"""Tests for core/indexer.py — turn index building and selection."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.core.indexer import (
    build_turn_index,
    pick_turns_from_index,
)


def _make_turn(n, summary="", concepts=None, files=None, tools=None):
    """Helper to build turn dicts for index tests."""
    turn = {
        "n": n,
        "narrative": {"summary": summary, "cross_references": []},
        "concepts_and_definitions": [{"term": c, "definition": ""} for c in (concepts or [])],
        "files_touched": files or [],
        "tools": [{"name": t, "args_summary": ""} for t in (tools or [])],
    }
    return turn


# ── build_turn_index ──

def test_build_index_few_turns():
    """≤20 turns returns None (use full spec context)."""
    turns = [_make_turn(i, f"Turn {i} summary") for i in range(1, 6)]
    result = build_turn_index(turns)
    assert result is None


def test_build_index_many_turns():
    """>20 turns returns a full index dict."""
    turns = [_make_turn(i, f"Turn {i} did important work") for i in range(1, 26)]
    result = build_turn_index(turns)
    assert result is not None
    assert "entries" in result
    assert len(result["entries"]) == 25
    assert "concept_map" in result
    assert "file_map" in result


def test_build_index_entries_have_required_fields():
    turns = [_make_turn(1, "First turn", concepts=["architecture"]) for _ in range(21)]
    result = build_turn_index(turns)
    entry = result["entries"][0]
    assert "n" in entry
    assert "title" in entry
    assert "summary_1line" in entry
    assert "tools_used" in entry
    assert "key_concepts" in entry
    assert "files_touched" in entry


def test_build_index_concept_map():
    turns = [
        _make_turn(1, "T1", concepts=["architecture", "testing"]),
        _make_turn(2, "T2", concepts=["architecture"]),
        _make_turn(3, "T3", concepts=["testing"]),
    ] * 8  # 24 turns, over threshold
    result = build_turn_index(turns)
    assert "architecture" in result["concept_map"]
    assert "testing" in result["concept_map"]


def test_build_index_file_map():
    turns = [
        _make_turn(1, "T1", files=["~/config.yaml"]),
        _make_turn(2, "T2", files=["~/config.yaml", "~/main.py"]),
    ] * 11  # 22 turns
    result = build_turn_index(turns)
    assert "~/config.yaml" in result["file_map"]


def test_build_index_empty_turns():
    result = build_turn_index([])
    assert result is None


# ── pick_turns_from_index ──

def test_pick_turns_expands_by_concept():
    turns = [
        _make_turn(1, "T1", concepts=["architecture"]),
        _make_turn(2, "T2", concepts=["architecture"]),
        _make_turn(3, "T3", concepts=["testing"]),
    ] * 8  # 24 turns
    index = build_turn_index(turns)
    result = pick_turns_from_index(index, [1, 3])
    # Turn 1 has concept "architecture", so turn 2 should be included
    assert 2 in result


def test_pick_turns_expands_by_file():
    turns = [
        _make_turn(1, "T1", files=["~/config.yaml"]),
        _make_turn(2, "T2", files=["~/config.yaml"]),
        _make_turn(3, "T3", files=["~/main.py"]),
    ] * 8
    index = build_turn_index(turns)
    result = pick_turns_from_index(index, [1])
    assert 2 in result  # same file


def test_pick_turns_returns_sorted():
    turns = [_make_turn(i, f"T{i}") for i in range(1, 25)]
    index = build_turn_index(turns)
    result = pick_turns_from_index(index, [20, 5, 10])
    assert result == sorted(result)


def test_pick_turns_empty_index():
    result = pick_turns_from_index({}, [1, 2, 3])
    assert result == [1, 2, 3]


def test_pick_turns_none_index():
    result = pick_turns_from_index(None, [1, 2])
    assert result == [1, 2]
