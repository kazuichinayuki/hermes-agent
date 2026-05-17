"""Session I/O. The ONLY layer that touches files/DB.

Wraps RawMessageStore and LedgerStore over a shared SQLite connection.
WAL mode allows concurrent access; check_same_thread=False is needed
because the gateway thread opens the connection but the agent thread
calls compress() which writes through compute_range() and save_turn().
Thread safety is guaranteed by WAL mode + per-session asyncio.Lock in
TaskManager, not by Python's same-thread check."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..db import configure_connection, ensure_schema, run_migrations
from ..store import RawMessageStore, LedgerStore
from .state_store import StateStore


class SessionIO:
    """Encapsulates all session persistence for a single session.

    Owns the per-session SQLite database at
    ``<hermes_home>/sessions/<session_id>/decohere.db``.
    RawMessageStore and LedgerStore share a single connection.
    """

    def __init__(self, hermes_home: Path, session_id: str):
        session_dir = hermes_home / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(session_dir / "decohere.db")

        conn = sqlite3.connect(db_path, check_same_thread=False)
        configure_connection(conn)
        ensure_schema(conn)
        run_migrations(conn)
        conn.commit()

        self._conn = conn
        self._raw = RawMessageStore(conn)
        self._ledger = LedgerStore(conn)
        self._state = StateStore(conn)
        self._session_id = session_id

    # ── Raw messages ──────────────────────────────────────────────────

    def compute_range(self, messages: list[dict[str, Any]]) -> tuple[int, int]:
        result = self._raw.append(messages)
        self._conn.commit()
        return result

    def get_raw_messages(self, start: int = 0, end: int | None = None) -> list[dict[str, Any]]:
        return self._raw.get(start, end)

    def raw_count(self) -> int:
        return self._raw.count()

    # ── Ledger ─────────────────────────────────────────────────────────

    def save_turn(self, turn: dict[str, object]) -> None:
        self._ledger.save_turn(turn)
        self._conn.commit()

    def get_turns(self) -> list[dict[str, object]]:
        return self._ledger.get_turns()

    def get_turn(self, turn_n: int) -> dict[str, object] | None:
        return self._ledger.get_turn(turn_n)

    def turn_count(self) -> int:
        return self._ledger.turn_count()

    # ── Session metadata ──────────────────────────────────────────────

    def is_v2(self) -> bool:
        return True

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()
