"""Unit tests for Decohere Trajectory Recording and Decision Dataset Compilation."""

import json
import sqlite3
import tempfile
from pathlib import Path
import unittest

# Ensure agent mocks are set up if running in an environment where agent.redact is incompatible
import sys
import types
from unittest.mock import MagicMock

if "agent" not in sys.modules:
    mock_agent = types.ModuleType("agent")
    mock_agent.__path__ = []
    sys.modules["agent"] = mock_agent
    sys.modules["agent.context_engine"] = types.ModuleType("agent.context_engine")
    sys.modules["agent.context_engine"].ContextEngine = object
    sys.modules["agent.auxiliary_client"] = MagicMock()
    sys.modules["agent.redact"] = MagicMock()

from plugins.context_engine.decohere.db import configure_connection, ensure_schema, run_migrations
from plugins.context_engine.decohere.store import TrajectoryStore
from plugins.context_engine.decohere.io.session_io import SessionIO
from plugins.context_engine.decohere.core.trajectory import (
    clean_messages_for_trajectory,
    build_trajectory_record,
    extract_typed_decisions,
)
from plugins.context_engine.decohere.cli.export_cmd import _format_trajectories, _format_dataset


class TestTrajectoryCore(unittest.TestCase):
    def test_clean_messages_for_trajectory(self):
        raw_msgs = [
            {"role": "system", "content": "You are Hermes."},
            {"role": "system", "name": "ledger_l1", "content": "Ledger state...", "_decohere_injected": True},
            {"role": "system", "name": "shared_state", "content": "Active goals...", "_decohere_injected": True},
            {"role": "user", "content": "Read the main config file."},
            {
                "role": "assistant",
                "content": "<think>I should read config.yaml</think>",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "config.yaml"}'}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "token_limit: 4096"},
            {"role": "assistant", "content": "The token limit is 4096."},
        ]

        cleaned = clean_messages_for_trajectory(raw_msgs)
        # Should filter out ledger_l1 and shared_state
        names = [m.get("name") for m in cleaned if "name" in m]
        self.assertNotIn("ledger_l1", names)
        self.assertNotIn("shared_state", names)
        self.assertEqual(len(cleaned), 5)
        self.assertEqual(cleaned[0]["role"], "system")
        self.assertEqual(cleaned[1]["role"], "user")
        self.assertEqual(cleaned[2]["role"], "assistant")
        self.assertEqual(cleaned[3]["role"], "tool")
        self.assertEqual(cleaned[4]["role"], "assistant")

    def test_build_trajectory_record(self):
        raw_msgs = [
            {"role": "system", "content": "System prompt."},
            {"role": "user", "content": "Check status."},
            {
                "role": "assistant",
                "reasoning_content": "Checking status of the engine.",
                "tool_calls": [{"id": "call_123", "function": {"name": "get_status", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_123", "content": '{"status": "ok"}'},
            {"role": "assistant", "content": "Status is operational."},
        ]

        record = build_trajectory_record(
            session_id="test-session-1",
            turn_n=1,
            messages=raw_msgs,
            model="claude-3-5-sonnet",
            completed=True,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            metadata={"source": "cli"},
        )

        self.assertEqual(record["id"], "traj_test-session-1_1")
        self.assertEqual(record["turn_n"], 1)
        self.assertEqual(record["model"], "claude-3-5-sonnet")
        self.assertTrue(record["completed"])
        convs = record["conversations"]
        self.assertEqual(convs[0]["from"], "system")
        self.assertEqual(convs[1]["from"], "human")
        self.assertEqual(convs[2]["from"], "gpt")
        self.assertIn("<think>\nChecking status of the engine.\n</think>", convs[2]["value"])
        self.assertIn("<tool_call>", convs[2]["value"])
        self.assertEqual(convs[3]["from"], "tool")
        self.assertEqual(convs[4]["from"], "gpt")
        self.assertEqual(convs[4]["value"], "Status is operational.")

    def test_extract_typed_decisions(self):
        msgs = [
            {"role": "user", "content": "Find all errors in the log."},
            {
                "role": "assistant",
                "content": "<think>I need to run grep to search for errors.</think>",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "grep_search", "arguments": '{"query": "error"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "error: connection reset"},
            {"role": "assistant", "content": "Found 1 error: connection reset."},
        ]

        decisions = extract_typed_decisions(msgs, session_id="test-sess", turn_n=2)
        # Should produce:
        # 1. score (complexity assessment at user prompt)
        # 2. choice (tool selection for grep_search)
        # 3. noul (final response indicating completion)
        types = [d["decision_type"] for d in decisions]
        self.assertIn("score", types)
        self.assertIn("choice", types)
        self.assertIn("noul", types)

        choice_dec = next(d for d in decisions if d["decision_type"] == "choice")
        self.assertEqual(choice_dec["target_label"], "grep_search")
        self.assertEqual(choice_dec["decision"]["tool"], "grep_search")

        noul_decs = [d for d in decisions if d["decision_type"] == "noul"]
        self.assertEqual(len(noul_decs), 2)
        # Step 0: took a tool action, so more work needed
        self.assertEqual(noul_decs[0]["target_label"], "needs_action")
        self.assertFalse(noul_decs[0]["decision"])
        # Step 1: gave final response, so goal complete
        self.assertEqual(noul_decs[1]["target_label"], "complete")
        self.assertTrue(noul_decs[1]["decision"])


class TestTrajectoryStoreAndIO(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_db_migration_and_store(self):
        db_path = self.home / "test.db"
        conn = sqlite3.connect(str(db_path))
        configure_connection(conn)
        ensure_schema(conn)
        run_migrations(conn)

        store = TrajectoryStore(conn)
        self.assertEqual(store.trajectory_count(), 0)
        self.assertEqual(store.decision_point_count(), 0)

        # Save trajectory
        tid = store.save_trajectory(
            session_id="s1",
            turn_n=1,
            model="gpt-4o",
            completed=True,
            trajectory={"conversations": [{"from": "human", "value": "hi"}]},
            metadata={"test": True},
        )
        self.assertGreater(tid, 0)
        self.assertEqual(store.trajectory_count(), 1)

        # Save decision point
        d_count = store.save_decision_points(
            session_id="s1",
            turn_n=1,
            decision_points=[
                {
                    "decision_type": "choice",
                    "state": {"intent": "test"},
                    "decision": {"selected_tool": "view_file"},
                    "target_label": "view_file",
                }
            ],
        )
        self.assertEqual(d_count, 1)
        self.assertEqual(store.decision_point_count(), 1)

        # Retrieve
        trajs = store.get_trajectories("s1")
        self.assertEqual(len(trajs), 1)
        self.assertEqual(trajs[0]["model"], "gpt-4o")

        dps = store.get_decision_points("s1")
        self.assertEqual(len(dps), 1)
        self.assertEqual(dps[0]["target_label"], "view_file")

        conn.close()

    def test_session_io_dual_persistence(self):
        sid = "sess_dual_001"
        io = SessionIO(self.home, sid)

        record = {"conversations": [{"from": "human", "value": "run test"}]}
        dps = [
            {"decision_type": "choice", "state": {}, "decision": {}, "target_label": "run_command"},
            {"decision_type": "noul", "state": {}, "decision": {}, "target_label": "terminate"},
        ]

        io.save_trajectory(
            turn_n=1,
            model="claude-3-5-sonnet",
            completed=True,
            trajectory=record,
            decision_points=dps,
            metadata={"origin": "test"},
        )

        # Verify DB counts
        self.assertEqual(io.trajectory_count(), 1)
        self.assertEqual(io.decision_point_count(), 2)

        # Verify per-session JSONL files
        session_traj_file = self.home / "sessions" / sid / "trajectories.jsonl"
        session_dec_file = self.home / "sessions" / sid / "decision_samples.jsonl"
        self.assertTrue(session_traj_file.exists())
        self.assertTrue(session_dec_file.exists())

        lines = session_traj_file.read_text().strip().split("\n")
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data["turn_n"], 1)

        dec_lines = session_dec_file.read_text().strip().split("\n")
        self.assertEqual(len(dec_lines), 2)

        # Verify global pool files
        global_traj_file = self.home / "trajectories" / "all_trajectories.jsonl"
        global_dec_file = self.home / "trajectories" / "all_decision_samples.jsonl"
        self.assertTrue(global_traj_file.exists())
        self.assertTrue(global_dec_file.exists())

        io.close()

    def test_export_formatters(self):
        traj_rows = [
            (
                1,
                "claude-3-5",
                1,
                json.dumps({"conversations": [{"from": "human", "value": "hello"}]}),
                json.dumps({"meta": "yes"}),
                "2026-09-21T10:00:00",
            )
        ]
        out_json = _format_trajectories("s_test", traj_rows, "json")
        self.assertIn("conversations", out_json)
        out_md = _format_trajectories("s_test", traj_rows, "md")
        self.assertIn("Trajectories — Session s_test", out_md)

        dec_rows = [
            (
                1,
                "choice",
                json.dumps({"active_tools": ["grep", "read"]}),
                json.dumps({"selected_tool": "grep"}),
                "grep",
                "2026-09-21T10:00:00",
            )
        ]
        out_d_json = _format_dataset("s_test", dec_rows, "json")
        self.assertIn("selected_tool", out_d_json)
        out_d_md = _format_dataset("s_test", dec_rows, "md")
        self.assertIn("Typed Decision Dataset — Session s_test", out_d_md)

    def test_decohere_engine_lifecycle(self):
        from plugins.context_engine.decohere import Decohere
        engine = Decohere()
        sid = "lifecycle_sess_001"
        engine.on_session_start(sid, hermes_home=str(self.home))
        engine.update_model("gpt-4o", 128000)

        turn_messages = [
            {"role": "user", "content": "list directory"},
            {
                "role": "assistant",
                "content": "<think>listing files</think>",
                "tool_calls": [{"id": "t1", "function": {"name": "list_dir", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "file1.py\nfile2.py"},
            {"role": "assistant", "content": "Here are the files."},
        ]

        # Simulate Hermes turn finalizer hook
        engine.on_turn_complete(turn_messages, usage={"total_tokens": 120})

        self.assertIsNotNone(engine._io)
        self.assertEqual(engine._io.trajectory_count(), 1)
        self.assertGreater(engine._io.decision_point_count(), 0)

        # End session
        engine.on_session_end(sid, turn_messages)

        # Check global and session files
        session_traj = self.home / "sessions" / sid / "trajectories.jsonl"
        self.assertTrue(session_traj.exists())
        global_traj = self.home / "trajectories" / "all_trajectories.jsonl"
        self.assertTrue(global_traj.exists())


if __name__ == "__main__":
    unittest.main()
