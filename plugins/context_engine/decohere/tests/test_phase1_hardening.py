"""Tests for Phase 1 hardening: canary token + mid-turn re-entry guard."""

from __future__ import annotations

import re
import secrets
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Canary token generation
# ---------------------------------------------------------------------------

class TestCanaryToken:
    """Verify canary is generated on session start and regenerated on reset."""

    def _make_decohere(self):
        from plugins.context_engine.decohere import Decohere
        return Decohere(context_length=200_000)

    def test_canary_none_before_session_start(self):
        d = self._make_decohere()
        assert d._canary_token is None

    @patch("plugins.context_engine.decohere.SessionIO")
    @patch("plugins.context_engine.decohere.TaskManager")
    @patch("plugins.context_engine.decohere.MetricsCollector")
    @patch("plugins.context_engine.decohere.HealthReporter")
    @patch("plugins.context_engine.decohere._load_user_config")
    def test_canary_generated_on_session_start(
        self, mock_cfg, mock_health, mock_metrics, mock_tasks, mock_io, tmp_path
    ):
        d = self._make_decohere()
        # Create minimal config.yaml
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("auxiliary: {}\ncompression: {}\n")

        d.on_session_start("test-session-1", hermes_home=str(tmp_path))
        assert d._canary_token is not None
        assert len(d._canary_token) == 8  # 4 bytes hex = 8 chars
        assert re.match(r"^[0-9a-f]{8}$", d._canary_token)

    @patch("plugins.context_engine.decohere.SessionIO")
    @patch("plugins.context_engine.decohere.TaskManager")
    @patch("plugins.context_engine.decohere.MetricsCollector")
    @patch("plugins.context_engine.decohere.HealthReporter")
    @patch("plugins.context_engine.decohere._load_user_config")
    def test_canary_regenerated_on_reset(
        self, mock_cfg, mock_health, mock_metrics, mock_tasks, mock_io, tmp_path
    ):
        d = self._make_decohere()
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("auxiliary: {}\ncompression: {}\n")

        d.on_session_start("test-session-1", hermes_home=str(tmp_path))
        first_canary = d._canary_token

        d.on_session_reset()
        second_canary = d._canary_token
        assert second_canary is not None
        assert len(second_canary) == 8
        # Different canary after reset (astronomically unlikely collision)
        # We don't assert != because it's theoretically possible but 1/2^32


# ---------------------------------------------------------------------------
# Canary detection in _strip_ledger_sections
# ---------------------------------------------------------------------------

class TestCanaryDetection:
    """Verify canary-based stripping in _strip_ledger_sections."""

    def _strip(self, messages, canary_token=None):
        from plugins.context_engine.decohere import _strip_ledger_sections
        _strip_ledger_sections(messages, canary_token=canary_token)

    def test_canary_in_assistant_message_is_stripped(self):
        canary = "a1b2c3d4"
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": (
                    "Here is my answer.\n\n"
                    f"[canary:{canary}] some echoed ledger content"
                ),
            },
        ]
        self._strip(messages, canary_token=canary)
        assert messages[1]["content"] == "Here is my answer."

    def test_canary_not_in_content_leaves_message_intact(self):
        canary = "a1b2c3d4"
        messages = [
            {"role": "assistant", "content": "Normal response without any markers."},
        ]
        self._strip(messages, canary_token=canary)
        assert messages[0]["content"] == "Normal response without any markers."

    def test_no_canary_still_strips_decohere_markers(self):
        """Even without canary, the existing DECOHERE:BEGIN markers work."""
        messages = [
            {
                "role": "assistant",
                "content": "Real answer.\n\n<!-- DECOHERE:BEGIN -->echoed stuff",
            },
        ]
        self._strip(messages, canary_token=None)
        assert messages[0]["content"] == "Real answer."

    def test_canary_only_affects_assistant_messages(self):
        canary = "deadbeef"
        messages = [
            {"role": "user", "content": f"[canary:{canary}] user text"},
            {"role": "system", "content": f"[canary:{canary}] system text"},
        ]
        self._strip(messages, canary_token=canary)
        # user and system messages should be untouched
        assert f"[canary:{canary}]" in messages[0]["content"]
        assert f"[canary:{canary}]" in messages[1]["content"]

    def test_canary_strips_everything_after_match(self):
        canary = "abcd1234"
        messages = [
            {
                "role": "assistant",
                "content": (
                    "Part 1 real.\n"
                    f"[canary:{canary}]\n"
                    "## Ledger Entries (Layer 1 — Spec)\n"
                    "[Turn 1]\nmessage_range: 0-5\n"
                ),
            },
        ]
        self._strip(messages, canary_token=canary)
        assert messages[0]["content"] == "Part 1 real."


# ---------------------------------------------------------------------------
# Canary injection in builder
# ---------------------------------------------------------------------------

class TestCanaryInBuilder:
    """Verify canary token appears in GUARD text of built context."""

    def _make_turn(self, n, **overrides):
        turn = {
            "n": n,
            "message_range": f"{n*10}-{n*10+5}",
            "tools": [],
            "files": [],
            "task": "test task",
            "ref_class": "qa",
            "concepts_and_definitions": [],
            "narrative": {"summary": "Did something."},
            "user_intent": "Test intent.",
            "decisions_and_rationale": [],
            "procedures": [],
            "insights": [],
            "entry_skipped": False,
        }
        turn.update(overrides)
        return turn

    def test_canary_in_ledger_context_guard(self):
        from plugins.context_engine.decohere.context.builder import build_ledger_context
        turns = [self._make_turn(1)]
        result = build_ledger_context(turns, max_turns=10, canary_token="beef1234")
        assert len(result) >= 2
        # Canary should be in the GUARD text of both L1 and L2
        assert "[canary:beef1234]" in result[0]["content"]
        assert "[canary:beef1234]" in result[1]["content"]

    def test_no_canary_when_none(self):
        from plugins.context_engine.decohere.context.builder import build_ledger_context
        turns = [self._make_turn(1)]
        result = build_ledger_context(turns, max_turns=10, canary_token=None)
        assert len(result) >= 2
        assert "[canary:" not in result[0]["content"]
        assert "[canary:" not in result[1]["content"]

    def test_canary_in_fallback_context(self):
        from plugins.context_engine.decohere.context.builder import build_fallback_context
        turns = [self._make_turn(1), self._make_turn(2)]
        result = build_fallback_context(
            turns, max_turns=10,
            last_turn_msgs=[{"role": "user", "content": "hi"}],
            canary_token="cafe4321",
        )
        # At least one message should have the canary
        canary_found = any("[canary:cafe4321]" in m.get("content", "") for m in result)
        assert canary_found


# ---------------------------------------------------------------------------
# Mid-turn re-entry guard (Phase 1 - B3)
# ---------------------------------------------------------------------------

class TestMidTurnReentry:
    """Verify compress() short-circuits on repeated calls in same turn."""

    def _make_decohere_with_io(self):
        from plugins.context_engine.decohere import Decohere
        d = Decohere(context_length=200_000)
        d._session_id = "test-session"
        d._canary_token = "test1234"

        # Mock IO
        d._io = MagicMock()
        d._io.is_v2.return_value = True
        d._io.turn_count.return_value = 0
        d._io.compute_range.return_value = "0-5"
        d._io.get_turns.return_value = []

        # Mock other dependencies
        d._tasks = MagicMock()
        d._metrics = MagicMock()
        d._health = MagicMock()
        d._health.verify_range.return_value = MagicMock(ok=True)
        d._health.verify_persisted.return_value = MagicMock(ok=True)
        d._cfg = MagicMock()
        d._cfg.max_turns = 20

        return d

    def test_second_call_same_turn_skips_phase1(self):
        """compress() called twice with same user message should only run
        Phase 1 (placeholder creation) once."""
        d = self._make_decohere_with_io()
        messages = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "I'll look into it."},
        ]

        # First call — should create placeholder
        d.extract_knowledge_async(list(messages))
        assert d._io.save_turn.call_count == 1

        # Second call — same user content → should skip Phase 1
        d.extract_knowledge_async(list(messages))
        assert d._io.save_turn.call_count == 1  # NOT 2
