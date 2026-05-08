"""Tests for context/placeholder.py — placeholder assembly."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.context.placeholder import build_placeholder
from plugins.context_engine.decohere.types import MechanicalFields


def test_build_placeholder_skipped():
    mechanical = MechanicalFields(tools=(), files_touched=())
    result = build_placeholder(1, (0, 2), mechanical, skipped=True)
    assert result["n"] == 1
    assert result["entry_skipped"] is True
    assert result["message_range"] == [0, 2]
    assert result["tools"] == []
    # Skipped: no semantic fields
    assert "critical_reflection" not in result


def test_build_placeholder_not_skipped():
    mechanical = MechanicalFields(
        tools=({"name": "read_file", "args_summary": "path=x"},),
        files_touched=("~/x",),
    )
    result = build_placeholder(5, (10, 15), mechanical, skipped=False)
    assert result["n"] == 5
    assert result["entry_skipped"] is False
    assert len(result["tools"]) == 1
    assert "critical_reflection" in result
    assert result["critical_reflection"] is None
    assert result["user_intent"] is None
    assert result["insights_and_learnings"] is None


def test_build_placeholder_returns_new_dict():
    mechanical = MechanicalFields(tools=(), files_touched=())
    r1 = build_placeholder(1, (0, 1), mechanical, False)
    r2 = build_placeholder(2, (1, 2), mechanical, False)
    assert r1 is not r2
    assert r1["n"] != r2["n"]


def test_build_placeholder_tools_conversion():
    """Tuple of {name, args_summary} dicts should be converted to list."""
    mechanical = MechanicalFields(
        tools=({"name": "web_search", "args_summary": "query=test"},),
        files_touched=(),
    )
    result = build_placeholder(1, (0, 1), mechanical, False)
    assert isinstance(result["tools"], list)
    assert result["tools"][0]["name"] == "web_search"
    assert result["tools"][0]["args_summary"] == "query=test"
