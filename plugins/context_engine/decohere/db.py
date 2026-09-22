"""SQLite bootstrap helpers for decohere.

Creates and migrates the decohere-specific database stored under
``<hermes_home>/sessions/<session_id>/decohere.db``.

Follows the LCM plugin pattern: self-contained storage, no dependency
on hermes_state.py.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4
SQLITE_BUSY_TIMEOUT_MS = 30_000


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply PRAGMA settings to a new connection."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist. Idempotent."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS raw_messages (
            store_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            role        TEXT NOT NULL,
            content     TEXT,
            tool_name   TEXT,
            tool_call_id TEXT,
            timestamp   REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );

        CREATE INDEX IF NOT EXISTS idx_raw_role
            ON raw_messages(role);

        CREATE TABLE IF NOT EXISTS ledger_entries (
            turn_n      INTEGER PRIMARY KEY,
            entry_json   TEXT NOT NULL,
            posted_at  REAL NOT NULL DEFAULT (unixepoch('subsec')),
            validated   INTEGER NOT NULL DEFAULT 0
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS concepts_fts
            USING fts5(term, definition, tokenize='unicode61');

        CREATE TABLE IF NOT EXISTS session_state (
            key   TEXT NOT NULL,
            value TEXT,
            scope TEXT NOT NULL DEFAULT 'session',
            updated_at REAL NOT NULL DEFAULT (unixepoch('subsec')),
            PRIMARY KEY (key, scope)
        );

        CREATE TABLE IF NOT EXISTS trajectories (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT NOT NULL,
            turn_n              INTEGER,
            model               TEXT,
            completed           INTEGER NOT NULL DEFAULT 1,
            trajectory_json     TEXT NOT NULL,
            metadata_json       TEXT,
            created_at          REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );

        CREATE INDEX IF NOT EXISTS idx_trajectories_session
            ON trajectories(session_id);

        CREATE TABLE IF NOT EXISTS decision_points (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT NOT NULL,
            turn_n              INTEGER,
            decision_type       TEXT NOT NULL,
            state_json          TEXT NOT NULL,
            decision_json       TEXT NOT NULL,
            target_label        TEXT NOT NULL,
            created_at          REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );

        CREATE INDEX IF NOT EXISTS idx_decision_session
            ON decision_points(session_id);
        CREATE INDEX IF NOT EXISTS idx_decision_type
            ON decision_points(decision_type);

        CREATE TABLE IF NOT EXISTS nogood_clauses (
            clause_id       TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL,
            predicate       TEXT NOT NULL,
            tool_name       TEXT NOT NULL,
            pattern_json    TEXT NOT NULL,
            reason          TEXT NOT NULL,
            scope           TEXT NOT NULL DEFAULT 'session',
            hit_count       INTEGER NOT NULL DEFAULT 0,
            created_at      REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );

        CREATE INDEX IF NOT EXISTS idx_nogood_tool
            ON nogood_clauses(tool_name);
        CREATE INDEX IF NOT EXISTS idx_nogood_session
            ON nogood_clauses(session_id);

        CREATE TABLE IF NOT EXISTS tool_results_archive (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            tool_name       TEXT NOT NULL,
            raw_result      TEXT NOT NULL,
            state           TEXT NOT NULL,
            created_at      REAL NOT NULL DEFAULT (unixepoch('subsec'))
        );

        CREATE INDEX IF NOT EXISTS idx_archive_session
            ON tool_results_archive(session_id);
        """
    )


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 if uninitialized."""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT value FROM metadata WHERE key = 'schema_version'"
    ).fetchone()
    if not row or row[0] is None:
        return 0
    return int(row[0])


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Write the schema version marker."""
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )


def run_migrations(conn: sqlite3.Connection) -> None:
    """Ensure schema is current. Idempotent — safe to call every session start."""
    ensure_schema(conn)
    current = get_schema_version(conn)
    if current >= SCHEMA_VERSION:
        return

    logger.info(
        "decohere: migrating DB from schema %d to %d", current, SCHEMA_VERSION
    )
    if current < 2:
        # v2: Convert concepts_fts from contentless (content='') to regular FTS5.
        # Contentless FTS5 doesn't support DELETE; regular FTS5 does.
        # The table must be dropped and recreated without content=''.
        conn.execute("DROP TABLE IF EXISTS concepts_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE concepts_fts "
            "USING fts5(term, definition, tokenize='unicode61')"
        )
        # Repopulate from existing ledger_entries
        import json
        for turn_n, entry_json in conn.execute(
            "SELECT turn_n, entry_json FROM ledger_entries"
        ).fetchall():
            try:
                data = json.loads(entry_json)
            except (json.JSONDecodeError, TypeError):
                continue
            base = turn_n * 1000
            for idx, c in enumerate(data.get("concepts_and_definitions", []) or []):
                if isinstance(c, dict):
                    conn.execute(
                        "INSERT INTO concepts_fts (rowid, term, definition) "
                        "VALUES (?, ?, ?)",
                        (base + idx, c.get("term", ""), c.get("definition", "")),
                    )
    if current < 3:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trajectories (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          TEXT NOT NULL,
                turn_n              INTEGER,
                model               TEXT,
                completed           INTEGER NOT NULL DEFAULT 1,
                trajectory_json     TEXT NOT NULL,
                metadata_json       TEXT,
                created_at          REAL NOT NULL DEFAULT (unixepoch('subsec'))
            );
            CREATE INDEX IF NOT EXISTS idx_trajectories_session
                ON trajectories(session_id);

            CREATE TABLE IF NOT EXISTS decision_points (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          TEXT NOT NULL,
                turn_n              INTEGER,
                decision_type       TEXT NOT NULL,
                state_json          TEXT NOT NULL,
                decision_json       TEXT NOT NULL,
                target_label        TEXT NOT NULL,
                created_at          REAL NOT NULL DEFAULT (unixepoch('subsec'))
            );
            CREATE INDEX IF NOT EXISTS idx_decision_session
                ON decision_points(session_id);
            CREATE INDEX IF NOT EXISTS idx_decision_type
                ON decision_points(decision_type);
            """
        )
    if current < 4:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nogood_clauses (
                clause_id       TEXT PRIMARY KEY,
                session_id      TEXT NOT NULL,
                predicate       TEXT NOT NULL,
                tool_name       TEXT NOT NULL,
                pattern_json    TEXT NOT NULL,
                reason          TEXT NOT NULL,
                scope           TEXT NOT NULL DEFAULT 'session',
                hit_count       INTEGER NOT NULL DEFAULT 0,
                created_at      REAL NOT NULL DEFAULT (unixepoch('subsec'))
            );
            CREATE INDEX IF NOT EXISTS idx_nogood_tool
                ON nogood_clauses(tool_name);
            CREATE INDEX IF NOT EXISTS idx_nogood_session
                ON nogood_clauses(session_id);

            CREATE TABLE IF NOT EXISTS tool_results_archive (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT NOT NULL,
                tool_name       TEXT NOT NULL,
                raw_result      TEXT NOT NULL,
                state           TEXT NOT NULL,
                created_at      REAL NOT NULL DEFAULT (unixepoch('subsec'))
            );
            CREATE INDEX IF NOT EXISTS idx_archive_session
                ON tool_results_archive(session_id);
            """
        )
    set_schema_version(conn, SCHEMA_VERSION)
