"""Tests for Phase 3 (recall_context tool) and Phase 4 (compression gating)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Phase 3: recall_context tool
# ---------------------------------------------------------------------------

class TestRecallContextSchema:
    """Verify get_tool_schemas() and handle_tool_call() for recall_context."""

    def _make_decohere(self, with_io=False):
        from plugins.context_engine.decohere import Decohere
        d = Decohere(context_length=200_000)
        if with_io:
            d._io = MagicMock()
            d._io.is_v2.return_value = True
        return d

    def test_no_schemas_without_io(self):
        d = self._make_decohere(with_io=False)
        assert d.get_tool_schemas() == []

    def test_schema_with_io(self):
        d = self._make_decohere(with_io=True)
        schemas = d.get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "recall_context"
        assert "parameters" in schemas[0]

    def test_schema_has_optional_params(self):
        d = self._make_decohere(with_io=True)
        schema = d.get_tool_schemas()[0]
        props = schema["parameters"]["properties"]
        assert "query" in props
        assert "max_turns" in props
        assert schema["parameters"]["required"] == []


class TestRecallContextHandler:
    """Verify handle_tool_call dispatches recall_context correctly."""

    def _make_decohere_with_turns(self, turns):
        from plugins.context_engine.decohere import Decohere
        d = Decohere(context_length=200_000)
        d._io = MagicMock()
        d._io.is_v2.return_value = True
        d._io.get_turns.return_value = turns
        return d

    def _make_turn(self, n, **overrides):
        turn = {
            "n": n,
            "message_range": f"{n*10}-{n*10+5}",
            "tools": [],
            "files": [],
            "task": "test task",
            "ref_class": "qa",
            "concepts_and_definitions": [],
            "narrative": {"summary": f"Turn {n} did something."},
            "user_intent": "Test intent.",
            "decisions_and_rationale": [],
            "procedures": [],
            "insights": [],
            "entry_skipped": False,
        }
        turn.update(overrides)
        return turn

    def test_unknown_tool_returns_error(self):
        d = self._make_decohere_with_turns([])
        result = json.loads(d.handle_tool_call("unknown_tool", {}))
        assert "error" in result

    def test_no_io_returns_error(self):
        from plugins.context_engine.decohere import Decohere
        d = Decohere()
        result = json.loads(d.handle_tool_call("recall_context", {}))
        assert "error" in result

    def test_empty_turns(self):
        d = self._make_decohere_with_turns([])
        result = json.loads(d.handle_tool_call("recall_context", {}))
        assert result["turns"] == []
        assert "message" in result

    def test_returns_recent_turns(self):
        turns = [self._make_turn(i) for i in range(1, 6)]
        d = self._make_decohere_with_turns(turns)
        result = json.loads(d.handle_tool_call("recall_context", {"max_turns": 3}))
        assert result["turns"] == 3
        assert result["total_available"] == 5
        assert "Turn 3" in result["context"] or "Turn 5" in result["context"]

    def test_returns_all_turns_if_fewer_than_max(self):
        turns = [self._make_turn(1), self._make_turn(2)]
        d = self._make_decohere_with_turns(turns)
        result = json.loads(d.handle_tool_call("recall_context", {"max_turns": 10}))
        assert result["turns"] == 2
        assert result["total_available"] == 2

    def test_skipped_turns_excluded_from_context(self):
        turns = [
            self._make_turn(1),
            self._make_turn(2, entry_skipped=True),
            self._make_turn(3),
        ]
        d = self._make_decohere_with_turns(turns)
        result = json.loads(d.handle_tool_call("recall_context", {}))
        assert result["turns"] == 3  # All 3 selected
        # But context should only have Turn 1 and Turn 3
        assert "Turn 1" in result["context"]
        assert "Turn 2" not in result["context"]
        assert "Turn 3" in result["context"]

    def test_default_max_turns_is_10(self):
        turns = [self._make_turn(i) for i in range(1, 16)]
        d = self._make_decohere_with_turns(turns)
        result = json.loads(d.handle_tool_call("recall_context", {}))
        assert result["turns"] == 10


# ---------------------------------------------------------------------------
# Phase 4: Smart should_compress()
# ---------------------------------------------------------------------------

class TestSmartShouldCompress:
    """Verify should_compress() gates on actual need."""

    def _make_decohere(self, io_ready=True, turn_count=0, last_compressed=0):
        from plugins.context_engine.decohere import Decohere
        d = Decohere(context_length=200_000)
        if io_ready:
            d._io = MagicMock()
            d._io.is_v2.return_value = True
            d._io.turn_count.return_value = turn_count
        d._last_compressed_turns = last_compressed
        return d

    def test_false_without_io(self):
        d = self._make_decohere(io_ready=False)
        assert d.should_compress() is False

    def test_false_on_first_call_fresh_session(self):
        """Below threshold returns False."""
        d = self._make_decohere(io_ready=True, turn_count=0, last_compressed=0)
        assert d.should_compress(50_000) is False

    def test_true_on_first_call_resumed_session(self):
        """Above threshold returns True."""
        d = self._make_decohere(io_ready=True, turn_count=3, last_compressed=0)
        assert d.should_compress(250_000) is True

    def test_true_when_new_turns_available(self):
        """Above threshold returns True."""
        d = self._make_decohere(io_ready=True, turn_count=5, last_compressed=3)
        assert d.should_compress(250_000) is True

    def test_true_when_caught_up(self):
        """Above threshold returns True."""
        d = self._make_decohere(io_ready=True, turn_count=5, last_compressed=3)
        d._initial_compress_done = True
        assert d.should_compress(250_000) is True

    def test_false_after_session_end(self):
        """After on_session_end, _io is None."""
        from plugins.context_engine.decohere import Decohere
        d = Decohere(context_length=200_000)
        d._io = MagicMock()
        d._io.is_v2.return_value = True
        d._io.turn_count.return_value = 3
        d._session_id = "test-session"
        d._last_compressed_turns = 3

        # Simulate: on_session_end closes the DB
        d.on_session_end("test-session", [])
        assert d._io is None
        assert d.should_compress(50_000) is False

    def test_compress_noop_after_session_end(self):
        """compress() returns messages unchanged when _io is None."""
        from plugins.context_engine.decohere import Decohere
        d = Decohere(context_length=200_000)
        d._session_id = "test-session"
        d._io = MagicMock()
        d.on_session_end("test-session", [])

        msgs = [{"role": "user", "content": "hello"}]
        result = d.compress(msgs)
        assert result == msgs

    def test_should_compress_handles_closed_connection(self):
        """Below threshold or missing tokens returns False."""
        from plugins.context_engine.decohere import Decohere
        d = Decohere(context_length=200_000)
        d._io = MagicMock()
        d._io.is_v2.return_value = True
        assert d.should_compress(50_000) is False
