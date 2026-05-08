"""Tests for context/classifier.py — skip/readiness classification."""

import pytest
import sys
import os

sys.path.insert(0, '/Users/shurigenha/.hermes/hermes-agent')

from plugins.context_engine.decohere.context.classifier import (
    should_skip_entry,
    check_readiness,
)
from plugins.context_engine.decohere.types import Readiness


# ── should_skip_entry ──

def test_skip_short_conversation():
    """≤3 messages with no tool_calls → True."""
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert should_skip_entry(msgs) is True


def test_skip_short_with_tool():
    """≤3 messages but contains tool_calls → False."""
    msgs = [
        {"role": "user", "content": "read file"},
        {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "read_file"}}]},
        {"role": "tool", "content": "data"},
    ]
    assert should_skip_entry(msgs) is False


def test_skip_long_conversation():
    """>3 messages → False even without tools."""
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ]
    assert should_skip_entry(msgs) is False


def test_skip_empty():
    assert should_skip_entry([]) is True


# ── check_readiness ──

def test_readiness_legacy():
    """None turns → legacy."""
    result = check_readiness(None, 0)
    assert result.state == "legacy"
    assert result.turns == ()


def test_readiness_empty():
    """turn_count == 0 → empty."""
    result = check_readiness([], 0)
    assert result.state == "empty"


def test_readiness_pending():
    """Latest turn has critical_reflection=None → pending."""
    turns = [
        {"n": 1, "critical_reflection": {"ignored_perspectives": ()}},
        {"n": 2, "critical_reflection": None},
    ]
    result = check_readiness(turns, 2)
    assert result.state == "pending"
    assert result.pending_turn_n == 2


def test_readiness_ready():
    """All turns have complete critical_reflection → ready."""
    turns = [
        {"n": 1, "critical_reflection": {"ignored_perspectives": ()}},
        {"n": 2, "critical_reflection": {"ignored_perspectives": ()}},
    ]
    result = check_readiness(turns, 2)
    assert result.state == "ready"
    assert result.pending_turn_n is None


def test_readiness_returns_frozen():
    result = check_readiness(None, 0)
    assert isinstance(result, Readiness)
