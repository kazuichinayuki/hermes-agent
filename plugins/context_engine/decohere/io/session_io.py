"""Session I/O. The ONLY layer that touches files/DB.

Wraps RawMessageStore and LedgerStore. All persistence flows through here.
No business logic, no formatting, no computation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..store import RawMessageStore, LedgerStore


class SessionIO:
    """Encapsulates all session persistence for a single session.

    Owns the per-session SQLite database at
    ``<hermes_home>/sessions/<session_id>/decohere.db``.
    """

    def __init__(self, hermes_home: Path, session_id: str):
        session_dir = hermes_home / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        db_path = session_dir / "decohere.db"

        self._raw = RawMessageStore(db_path)
        self._ledger = LedgerStore(db_path)
        self._session_id = session_id
        self._format_version: int = 2  # Always v2 for decohere-managed sessions

    # ── Raw messages ──────────────────────────────────────────────────

    @property
    def raw(self) -> RawMessageStore:
        return self._raw

    def compute_range(self, messages: list[dict]) -> tuple[int, int]:
        """Append messages to raw store. Returns (start, end) store_id range."""
        return self._raw.append(messages)

    def get_raw_messages(self, start: int = 0, end: int | None = None) -> list[dict]:
        return self._raw.get(start, end)

    def raw_count(self) -> int:
        return self._raw.count()

    # ── Turn specs ────────────────────────────────────────────────────

    @property
    def ledger(self) -> LedgerStore:
        return self._ledger

    def save_turn(self, turn: dict) -> None:
        self._ledger.save_turn(turn)

    def get_turns(self) -> list[dict]:
        return self._ledger.get_turns()

    def get_turn(self, turn_n: int) -> dict | None:
        return self._ledger.get_turn(turn_n)

    def turn_count(self) -> int:
        return self._ledger.turn_count()

    # ── Session metadata ──────────────────────────────────────────────

    def is_v2(self) -> bool:
        """Always True — decohere manages its own sessions."""
        return True

    def close(self) -> None:
        self._raw.close()
        self._ledger.close()
