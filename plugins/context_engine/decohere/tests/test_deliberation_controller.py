"""Unit tests for Decohere Deliberation Controller components:
- NogoodStore & NogoodClause (CDCL 0ms/0-token preemption)
- Infotractor (Four-state distillation & Redulog evaporation)
- PredictiveController (Residual ϵ_t calculation & System 2 wake-up gate)
- Hermes Lifecycle Hooks & Cache-Safe Ledger Injection
- Database Schema v4 Migration & Persistence
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Setup mock for 'agent' module if running in standalone test environment
if "agent" not in sys.modules:
    mock_agent = types.ModuleType("agent")
    mock_agent.__path__ = []
    sys.modules["agent"] = mock_agent
    sys.modules["agent.context_engine"] = types.ModuleType("agent.context_engine")
    sys.modules["agent.context_engine"].ContextEngine = object
    sys.modules["agent.auxiliary_client"] = MagicMock()
    sys.modules["agent.redact"] = MagicMock()

from plugins.context_engine.decohere.core.infotractor import Infotractor, ObservationState
from plugins.context_engine.decohere.core.nogood_store import NogoodClause, NogoodStore
from plugins.context_engine.decohere.core.predictive_controller import PredictiveController
from plugins.context_engine.decohere.db import ensure_schema, get_schema_version, run_migrations
from plugins.context_engine.decohere.io.session_io import SessionIO
from plugins.context_engine.decohere import Decohere


class TestDeliberationController(unittest.TestCase):

    # ── 1. NogoodStore Tests ───────────────────────────────────────────────────

    def test_nogood_clause_creation_and_matching(self):
        clause = NogoodClause.create(
            predicate="symbol_absent",
            tool_name="grep_search",
            pattern={"Query": "target_func", "SearchPath": "src/utils.py"},
            reason="Symbol 'target_func' verified absent in 'src/utils.py'",
        )
        self.assertTrue(clause.clause_id.startswith("NG-"))

        # Match exact
        self.assertTrue(clause.matches("grep_search", {"Query": "target_func", "SearchPath": "src/utils.py"}))
        # Non-match tool
        self.assertFalse(clause.matches("run_command", {"Query": "target_func", "SearchPath": "src/utils.py"}))
        # Non-match query
        self.assertFalse(clause.matches("grep_search", {"Query": "other_func", "SearchPath": "src/utils.py"}))

    def test_nogood_store_preemption(self):
        store = NogoodStore(session_id="test_sess")
        clause = NogoodClause.create(
            predicate="file_not_found",
            tool_name="view_file",
            pattern={"AbsolutePath": "/nonexistent/path.py"},
            reason="File does not exist",
        )
        store.add_clause(clause)

        # Calling with matching args must return preemption directive
        decision = store.evaluate_preemption("view_file", {"AbsolutePath": "/nonexistent/path.py"})
        self.assertIsNotNone(decision)
        self.assertEqual(decision["action"], "block")
        self.assertIn("NOGOOD PREEMPTION", decision["message"])
        self.assertEqual(clause.hit_count, 1)

        # Calling with different args must pass (return None)
        allowed = store.evaluate_preemption("view_file", {"AbsolutePath": "/existing/path.py"})
        self.assertIsNone(allowed)

    # ── 2. Infotractor Tests ───────────────────────────────────────────────────

    def test_infotractor_commitment(self):
        tractor = Infotractor(session_id="test_sess")
        res, state, clause, raw_archive = tractor.compile(
            tool_name="run_command",
            args={"CommandLine": "pytest tests/"},
            raw_result="====== 15 passed in 0.42s ======",
        )
        self.assertEqual(state, ObservationState.COMMITMENT)
        self.assertIsNone(clause)
        self.assertIsNone(raw_archive)
        self.assertIn("15 passed", res)

    def test_infotractor_hypobranch(self):
        tractor = Infotractor(session_id="test_sess")
        res, state, clause, raw_archive = tractor.compile(
            tool_name="view_file",
            args={"AbsolutePath": "app.py"},
            raw_result="def main():\n    print('Hello')",
        )
        self.assertEqual(state, ObservationState.HYPOBRANCH)
        self.assertIsNone(clause)
        self.assertIsNone(raw_archive)

    def test_infotractor_conflict_extraction(self):
        tractor = Infotractor(session_id="test_sess")
        # Empty grep
        res, state, clause, raw_archive = tractor.compile(
            tool_name="grep_search",
            args={"Query": "missing_symbol", "SearchPath": "lib/"},
            raw_result="Total results: 0",
        )
        self.assertEqual(state, ObservationState.CONFLICT)
        self.assertIsNotNone(clause)
        self.assertEqual(clause.predicate, "symbol_absent")
        self.assertEqual(clause.pattern["Query"], "missing_symbol")
        self.assertIn("Conflict Identified", res)

    def test_infotractor_redulog_evaporation(self):
        tractor = Infotractor(session_id="test_sess")
        # Fabricate a 40-line Python traceback
        tb_lines = ["Traceback (most recent call last):"]
        for i in range(35):
            tb_lines.append(f'  File "/usr/lib/python3.10/module_{i}.py", line {i*2}, in run')
            tb_lines.append(f'    call_subroutine_{i}()')
        tb_lines.append('ImportError: cannot import name "NonExistentClass" from "my_package"')
        huge_traceback = "\n".join(tb_lines)

        evaporated, state, clause, raw_archive = tractor.compile(
            tool_name="run_command",
            args={"CommandLine": "python3 main.py"},
            raw_result=huge_traceback,
        )
        self.assertEqual(state, ObservationState.REDULOG)
        self.assertEqual(raw_archive, huge_traceback)
        # Evaporated must be compact (<= 5 lines)
        self.assertLessEqual(len(evaporated.splitlines()), 5)
        self.assertIn("REDULOG EVAPORATED", evaporated)
        self.assertIn('ImportError: cannot import name "NonExistentClass"', evaporated)

    # ── 3. PredictiveController Tests ──────────────────────────────────────────

    def test_predictive_controller_fast_path(self):
        pc = PredictiveController(threshold=0.35)
        # Success scenario: grep finds 3 matches
        residual = pc.evaluate(
            tool_name="grep_search",
            args={"Query": "found_symbol", "SearchPath": "src/"},
            actual_result='[{"LineNumber": 10, "LineContent": "def found_symbol():"}]',
            duration_ms=45,
        )
        self.assertLess(residual.surprise_score, 0.35)
        self.assertFalse(residual.should_wake_system2)

    def test_predictive_controller_surprise_wakeup(self):
        pc = PredictiveController(threshold=0.35)
        # Failure scenario: run_command exits with error and traceback
        residual = pc.evaluate(
            tool_name="run_command",
            args={"CommandLine": "python3 run.py"},
            actual_result="Traceback (most recent call last):\n  File 'run.py', line 1\nSyntaxError: invalid syntax\nexit code 1",
            duration_ms=200,
        )
        self.assertGreaterEqual(residual.surprise_score, 0.35)
        self.assertTrue(residual.should_wake_system2)
        self.assertEqual(residual.residual_vector["r_exit"], 1.0)
        self.assertEqual(residual.residual_vector["r_traceback"], 1.0)

    # ── 4. Lifecycle & Cache-Safe Dynamic Ledger Injection ─────────────────────

    def test_cache_safe_ledger_injection(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            decohere = Decohere(context_length=200_000)
            decohere.on_session_start("test_session_life", hermes_home=str(tmp_path))

            # Add learned nogood clause
            clause = NogoodClause.create(
                predicate="symbol_absent",
                tool_name="grep_search",
                pattern={"Query": "foo_var", "SearchPath": "core/"},
                reason="Symbol 'foo_var' absent in 'core/'",
            )
            decohere._nogood_store.add_clause(clause)

            # Verify pre_tool_call intercepts matching call
            block_resp = decohere.pre_tool_call("grep_search", {"Query": "foo_var", "SearchPath": "core/"})
            self.assertIsNotNone(block_resp)
            self.assertEqual(block_resp["action"], "block")

            # Verify build_context_payload() injects into dynamic ledger without modifying system prompt header
            messages = [{"role": "user", "content": "How do I fix this?"}]
            compressed = decohere.build_context_payload(messages)
            
            # Check that a nogood_constraints message exists in the returned dynamic messages
            ng_msgs = [m for m in compressed if m.get("name") == "nogood_constraints"]
            self.assertEqual(len(ng_msgs), 1)
            self.assertIn("Symbol 'foo_var' absent", ng_msgs[0]["content"])

    # ── 5. Database Schema v4 Migration & Persistence ──────────────────────────

    def test_schema_v4_migration_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "test_decohere.db"
            conn = sqlite3.connect(str(db_path))
            ensure_schema(conn)
            run_migrations(conn)

            self.assertEqual(get_schema_version(conn), 4)

            # Verify nogood_clauses table exists and works
            conn.execute(
                """INSERT INTO nogood_clauses (clause_id, session_id, predicate, tool_name, pattern_json, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("NG-TEST", "sess1", "test_pred", "grep_search", "{}", "test reason"),
            )
            row = conn.execute("SELECT clause_id, reason FROM nogood_clauses WHERE clause_id = 'NG-TEST'").fetchone()
            self.assertEqual(row, ("NG-TEST", "test reason"))

            # Verify tool_results_archive table exists and works
            conn.execute(
                """INSERT INTO tool_results_archive (session_id, tool_name, raw_result, state)
                   VALUES (?, ?, ?, ?)""",
                ("sess1", "run_command", "full raw log", "redulog"),
            )
            archive_row = conn.execute("SELECT raw_result, state FROM tool_results_archive WHERE session_id = 'sess1'").fetchone()
            self.assertEqual(archive_row, ("full raw log", "redulog"))

            conn.close()


if __name__ == "__main__":
    unittest.main()
