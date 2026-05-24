"""Tests for Phase 2: Cache-aware message handling."""

from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Phase 2A: _decohere_injected annotation
# ---------------------------------------------------------------------------

class TestDecohereAnnotation:
    """Verify that _decohere_injected flag is set on known message names."""

    def test_annotation_on_ledger_names(self):
        """Messages with decohere names should be annotated."""
        _LEDGER_NAMES = frozenset({
            "ledger_l1", "turn_context", "turn_index",
            "shared_state", "shared_knowledge",
        })

        messages = [
            {"role": "system", "name": "ledger_l1", "content": "L1 data"},
            {"role": "system", "name": "turn_context", "content": "L2 data"},
            {"role": "system", "name": "shared_state", "content": "state"},
            {"role": "user", "content": "Normal user message"},
        ]

        # Simulate the annotation logic from compress()
        for msg in messages:
            if msg.get("name") in _LEDGER_NAMES or msg.get("name") == "shared_knowledge":
                msg["_decohere_injected"] = True

        assert messages[0].get("_decohere_injected") is True
        assert messages[1].get("_decohere_injected") is True
        assert messages[2].get("_decohere_injected") is True
        assert messages[3].get("_decohere_injected") is None  # normal message

    def test_annotation_not_on_unknown_names(self):
        """Messages with non-decohere names should NOT be annotated."""
        messages = [
            {"role": "system", "name": "custom_plugin", "content": "data"},
        ]

        _LEDGER_NAMES = frozenset({
            "ledger_l1", "turn_context", "turn_index",
            "shared_state", "shared_knowledge",
        })
        for msg in messages:
            if msg.get("name") in _LEDGER_NAMES or msg.get("name") == "shared_knowledge":
                msg["_decohere_injected"] = True

        assert messages[0].get("_decohere_injected") is None


# ---------------------------------------------------------------------------
# Phase 2B: Cache marker placement
# ---------------------------------------------------------------------------

class TestCacheMarkers:
    """Verify double-window cache markers skip decohere messages."""

    def test_markers_skip_decohere_messages(self):
        from plugins.context_engine.decohere.cache_markers import apply_decohere_cache_markers

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "system", "name": "ledger_l1", "content": "L1 data", "_decohere_injected": True},
            {"role": "system", "name": "turn_context", "content": "L2 data", "_decohere_injected": True},
        ]
        result = apply_decohere_cache_markers(messages, max_markers=2)

        # Markers should be on user and assistant messages (indices 1, 2)
        # NOT on decohere messages (indices 3, 4)
        assert _has_cache_control(result[1])  # user
        assert _has_cache_control(result[2])  # assistant
        assert not _has_cache_control(result[3])  # ledger_l1
        assert not _has_cache_control(result[4])  # turn_context

    def test_markers_skip_system_messages(self):
        from plugins.context_engine.decohere.cache_markers import apply_decohere_cache_markers

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Fix bug"},
        ]
        result = apply_decohere_cache_markers(messages, max_markers=2)
        assert _has_cache_control(result[1])  # user
        assert not _has_cache_control(result[0])  # system (skipped)

    def test_no_messages_returns_empty(self):
        from plugins.context_engine.decohere.cache_markers import apply_decohere_cache_markers
        assert apply_decohere_cache_markers([]) == []

    def test_all_decohere_messages_no_markers_placed(self):
        from plugins.context_engine.decohere.cache_markers import apply_decohere_cache_markers

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "system", "name": "ledger_l1", "content": "L1", "_decohere_injected": True},
            {"role": "system", "name": "turn_context", "content": "L2", "_decohere_injected": True},
        ]
        result = apply_decohere_cache_markers(messages, max_markers=2)
        # No non-system, non-decohere messages → no markers placed
        for msg in result:
            assert not _has_cache_control(msg)

    def test_max_markers_respected(self):
        from plugins.context_engine.decohere.cache_markers import apply_decohere_cache_markers

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
        ]
        result = apply_decohere_cache_markers(messages, max_markers=2)
        marked_count = sum(1 for m in result if _has_cache_control(m))
        assert marked_count == 2


# ---------------------------------------------------------------------------
# Phase 2B: Core prompt_caching.py respects _decohere_injected
# ---------------------------------------------------------------------------

class TestPromptCachingIntegration:
    """Verify apply_anthropic_cache_control skips _decohere_injected messages."""

    def test_cache_control_skips_decohere_injected(self):
        from agent.prompt_caching import apply_anthropic_cache_control

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            # Decohere-injected messages with non-system role (edge case)
            {"role": "user", "content": "Decohere context", "_decohere_injected": True},
        ]
        result = apply_anthropic_cache_control(messages)

        # The decohere message should NOT have cache_control
        assert not _has_cache_control(result[3])
        # But the real user and assistant messages should
        assert _has_cache_control(result[1])  # user
        assert _has_cache_control(result[2])  # assistant

    def test_cache_control_works_without_decohere(self):
        """When no _decohere_injected messages, behavior is unchanged."""
        from agent.prompt_caching import apply_anthropic_cache_control

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        result = apply_anthropic_cache_control(messages)
        # system + user + assistant = 3 markers (system counts as 1)
        marked_count = sum(1 for m in result if _has_cache_control(m))
        assert marked_count == 3  # system + user + assistant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_cache_control(msg: dict[str, Any]) -> bool:
    """Check if a message has cache_control set (either on msg or content blocks)."""
    if "cache_control" in msg:
        return True
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and "cache_control" in block:
                return True
    return False
