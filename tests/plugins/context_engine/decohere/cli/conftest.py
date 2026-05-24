"""Test fixtures for hermes decohere CLI commands."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_decohere_db():
    """Create a pre-populated decohere.db with 5 ledger entries.

    Uses the real decohere schema and test data that mirrors typical
    hermes-agent conversations.
    """
    with tempfile.TemporaryDirectory() as td:
        profile = Path(td) / "test_profile"
        sessions = profile / "sessions"
        sessions.mkdir(parents=True)
        (profile / "config.yaml").write_text(
            "auxiliary:\n  compression:\n    model: test-model\n"
        )

        session_dir = sessions / "test_session"
        session_dir.mkdir()

        db_path = session_dir / "decohere.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS raw_messages (
                store_id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL, content TEXT, tool_name TEXT,
                tool_call_id TEXT, timestamp REAL NOT NULL DEFAULT (unixepoch('subsec'))
            );
            CREATE TABLE IF NOT EXISTS ledger_entries (
                turn_n INTEGER PRIMARY KEY,
                entry_json TEXT NOT NULL,
                posted_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
                validated INTEGER NOT NULL DEFAULT 0
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts
                USING fts5(term, definition, content='', tokenize='unicode61');
            """
        )

        turns = [
            {
                "n": 1,
                "message_range": [0, 8],
                "entry_skipped": False,
                "tools": ["vision_analyze"],
                "files_touched": ["~/.hermes/config.yaml"],
                "reference_documentation": (),
                "relevant_metadata": {
                    "task": "vision debug",
                    "reference_class": "config",
                },
                "concepts_and_definitions": [
                    {"term": "context window", "definition": "The maximum token capacity of an LLM's input"},
                    {"term": "truncation", "definition": "Cutting off output at a token limit before the LLM finishes"},
                ],
                "narrative": {
                    "summary": "Fixed vision truncation by raising max_tokens in the read_file output parser.",
                    "cross_references": (),
                },
                "user_intent": "Fix the image analysis truncation issue",
                "decisions_and_rationale": [
                    {"decision": "Set max_tokens to 4096 for vision_analyze", "rationale": "Previous 1024 was too low for detailed image descriptions"},
                ],
                "procedures": [
                    {"action": "Modified model_tools.py vision_analyze handler"},
                ],
                "insights_and_learnings": [
                    "Vision models need higher token limits than text models for equivalent detail",
                ],
                "critical_reflection": {
                    "ignored_perspectives": (),
                    "logical_gaps": (),
                    "improvement_directions": ("Add per-tool token budget configuration",),
                },
            },
            {
                "n": 2,
                "message_range": [9, 16],
                "entry_skipped": False,
                "tools": ["patch", "terminal"],
                "files_touched": ["~/.hermes/config.yaml"],
                "reference_documentation": (
                    {"url": "https://example.com/decohere-config", "title": "Decohere Config Reference"},
                ),
                "relevant_metadata": {
                    "task": "config fix",
                    "reference_class": "config",
                },
                "concepts_and_definitions": [
                    {"term": "compression threshold", "definition": "Token budget at which compression triggers (0.0-1.0)"},
                ],
                "narrative": {
                    "summary": "Set compression threshold to 0.35 in config.yaml after discovering the default 1.0 was too high.",
                    "cross_references": (),
                },
                "user_intent": "Fix the compression threshold so decohere actually compresses",
                "decisions_and_rationale": [
                    {"decision": "threshold = 0.35", "rationale": "Leaves 65% of context for raw conversation, 35% for compressed ledger"},
                ],
                "procedures": [
                    {"action": "Patched config.yaml compression.threshold from 1.0 to 0.35"},
                ],
                "insights_and_learnings": [
                    "Default threshold of 1.0 means 'never compress' — basically a no-op",
                ],
                "critical_reflection": {
                    "ignored_perspectives": ("What about models with small context windows?",),
                    "logical_gaps": (),
                    "improvement_directions": ("Auto-detect optimal threshold from model context size",),
                },
            },
            {
                "n": 3,
                "message_range": [17, 26],
                "entry_skipped": False,
                "tools": ["web_search", "web_extract"],
                "files_touched": [],
                "reference_documentation": (
                    {"url": "https://docs.openai.com/codex/goal", "title": "Codex /goal Documentation"},
                ),
                "relevant_metadata": {
                    "task": "research codex /goal",
                    "reference_class": "research",
                },
                "concepts_and_definitions": [
                    {"term": "Codex /goal", "definition": "OpenAI Codex CLI autonomous task execution loop — plan→act→test→review"},
                    {"term": "Ralph loop", "definition": "Verification loop pattern where Codex self-checks output against measurable criteria"},
                ],
                "narrative": {
                    "summary": "Researched Codex CLI v0.128.0 /goal feature. Confirmed persistence across terminal restart and model switch. Extracted prompt template structure.",
                    "cross_references": (2,),
                },
                "user_intent": "Research Codex /goal to see if it can automate decohere scheme building",
                "decisions_and_rationale": [
                    {"decision": "Use Codex /goal for decohere CLI scheme", "rationale": "Measurable stop conditions (pytest exit code) match perfectly"},
                    {"decision": "Don't replicate /goal in Hermes", "rationale": "delegate_task already covers similar ground"},
                ],
                "procedures": [
                    {"action": "web_extract codex docs"},
                    {"action": "Compare with Hermes delegate_task patterns"},
                ],
                "insights_and_learnings": [
                    "/goal's persistence is the killer feature — survives model switches",
                    "Hard cap on time/tokens prevents runaway loops",
                ],
                "critical_reflection": {
                    "ignored_perspectives": (),
                    "logical_gaps": (),
                    "improvement_directions": ("Test /goal on large task token consumption",),
                },
            },
            {
                "n": 4,
                "message_range": [27, 34],
                "entry_skipped": False,
                "tools": ["read_file", "search_files"],
                "files_touched": [
                    "plugins/context_engine/decohere/db.py",
                    "plugins/context_engine/decohere/store.py",
                ],
                "reference_documentation": (),
                "relevant_metadata": {
                    "task": "code review",
                    "reference_class": "architecture",
                },
                "concepts_and_definitions": [
                    {"term": "decohere", "definition": "Context engine plugin that maintains structured ledger entries across turns"},
                    {"term": "ledger entries", "definition": "Per-turn structured summaries stored in SQLite with FTS5 indexing"},
                ],
                "narrative": {
                    "summary": "Reviewed decohere plugin architecture: db.py for schema, store.py for CRUD, SessionIO for lifecycle. Identified gap: no CLI for data inspection.",
                    "cross_references": (1, 2),
                },
                "user_intent": "Understand decohere codebase structure before building CLI",
                "decisions_and_rationale": [
                    {"decision": "Build CLI as argparse subcommand under hermes decohere", "rationale": "Matches hermes cron pattern, users type it daily"},
                ],
                "procedures": [
                    {"action": "Read db.py to understand schema"},
                    {"action": "Read store.py for CRUD operations"},
                    {"action": "Map out CLI command structure"},
                ],
                "insights_and_learnings": [
                    "WAL mode enables concurrent read access — perfect for read-only CLI commands",
                ],
                "critical_reflection": {
                    "ignored_perspectives": (),
                    "logical_gaps": ("Need to handle corrupted JSON entries gracefully",),
                    "improvement_directions": ("Add vacuum command for orphan cleanup",),
                },
            },
            {
                "n": 5,
                "message_range": [35, 42],
                "entry_skipped": False,
                "tools": ["patch", "terminal"],
                "files_touched": [
                    "plugins/context_engine/decohere/__init__.py",
                ],
                "reference_documentation": (),
                "relevant_metadata": {
                    "task": "refactor",
                    "reference_class": "bugfix",
                },
                "concepts_and_definitions": [
                    {"term": "should_compress", "definition": "Decohere's gate function — returns True when compression should run"},
                    {"term": "placeholder", "definition": "Minimal turn entry written synchronously before async LLM posting completes"},
                ],
                "narrative": {
                    "summary": "Fixed should_compress deadlock bug where it returned False on turn 1, preventing the placeholder write and keeping turn_count at 0 forever.",
                    "cross_references": (1, 2),
                },
                "user_intent": "Fix the decohere should_compress deadlock so it activates correctly",
                "decisions_and_rationale": [
                    {"decision": "should_compress always returns True when IO is ready", "rationale": "compress() must run every turn to write placeholders; context building is separately guarded"},
                ],
                "procedures": [
                    {"action": "Modified should_compress to return True when session IO is initialized"},
                    {"action": "Added _last_compressed_turns guard to context building"},
                ],
                "insights_and_learnings": [
                    "Separating 'should run this turn' from 'should rebuild context' eliminates a whole category of ordering bugs",
                ],
                "critical_reflection": {
                    "ignored_perspectives": ("What about sessions with no tool calls?",),
                    "logical_gaps": (),
                    "improvement_directions": ("Add metrics for compression skip rate",),
                },
            },
        ]

        for i, turn in enumerate(turns):
            entry_json = json.dumps(turn, ensure_ascii=False)
            conn.execute(
                "INSERT INTO ledger_entries (turn_n, entry_json, posted_at, validated) "
                "VALUES (?, ?, unixepoch('subsec'), ?)",
                (turn["n"], entry_json, 1 if i < 3 else 0),
            )
            for c in turn.get("concepts_and_definitions", []):
                if isinstance(c, dict):
                    conn.execute(
                        "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
                        (turn["n"], c.get("term", ""), c.get("definition", "")),
                    )

        # Add some raw_messages
        for i in range(50):
            conn.execute(
                "INSERT INTO raw_messages (role, content) VALUES (?, ?)",
                ("user" if i % 2 == 0 else "assistant", f"Message {i}"),
            )

        conn.commit()
        conn.close()

        yield {
            "hermes_home": profile,
            "session_id": "test_session",
            "db_path": db_path,
            "turn_count": 5,
            "raw_count": 50,
        }
