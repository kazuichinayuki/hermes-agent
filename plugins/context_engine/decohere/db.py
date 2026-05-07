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

SCHEMA_VERSION = 2
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
            USING fts5(term, definition, content='', tokenize='unicode61');
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
    # Future migrations go here:
    # if current < 2: ...
    set_schema_version(conn, SCHEMA_VERSION)
