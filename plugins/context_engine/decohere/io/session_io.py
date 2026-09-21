"""Session I/O. The ONLY layer that touches files/DB.

Wraps RawMessageStore and LedgerStore over a shared SQLite connection.
WAL mode allows concurrent access; check_same_thread=False is needed
because the gateway thread opens the connection but the agent thread
calls compress() which writes through compute_range() and save_turn().
Thread safety is guaranteed by WAL mode + per-session asyncio.Lock in
TaskManager, not by Python's same-thread check."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from ..db import configure_connection, ensure_schema, run_migrations
from ..store import RawMessageStore, LedgerStore, TrajectoryStore
from .state_store import StateStore

logger = logging.getLogger(__name__)


class SessionIO:
    """Encapsulates all session persistence for a single session.

    Owns the per-session SQLite database at
    ``<hermes_home>/sessions/<session_id>/decohere.db``.
    RawMessageStore, LedgerStore, and TrajectoryStore share a single connection.
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
        self._trajectories = TrajectoryStore(conn)
        self._session_id = session_id
        self._session_dir = session_dir
        self._trajectory_file = session_dir / "trajectories.jsonl"
        self._decision_file = session_dir / "decision_samples.jsonl"
        self._global_trajectories_dir = hermes_home / "trajectories"
        self._closing = False
        self._pending_writers = 0

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
        if self._closing:
            # Session has ended — this write belongs to a background task
            # that completed after the session was closed.  Drop it and
            # count down; the last writer closes the connection.
            self._pending_writers -= 1
            if self._pending_writers <= 0:
                try:
                    self._conn.close()
                except Exception:
                    pass
            return
        self._ledger.save_turn(turn)
        self._conn.commit()

    def get_turns(self) -> list[dict[str, object]]:
        return self._ledger.get_turns()

    def get_turn(self, turn_n: int) -> dict[str, object] | None:
        return self._ledger.get_turn(turn_n)

    def turn_count(self) -> int:
        return self._ledger.turn_count()

    # ── Trajectories & Decisions ──────────────────────────────────────

    def save_trajectory(
        self,
        turn_n: int,
        model: str,
        completed: bool,
        trajectory: dict[str, Any],
        decision_points: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self._closing:
            return

        # 1. Save to SQLite database
        self._trajectories.save_trajectory(
            session_id=self._session_id,
            turn_n=turn_n,
            model=model,
            completed=completed,
            trajectory=trajectory,
            metadata=metadata,
        )
        if decision_points:
            self._trajectories.save_decision_points(
                session_id=self._session_id,
                turn_n=turn_n,
                decision_points=decision_points,
            )
        self._conn.commit()

        # 2. Append to per-session JSONL files
        try:
            with open(self._trajectory_file, "a", encoding="utf-8") as f:
                rec = {
                    "session_id": self._session_id,
                    "turn_n": turn_n,
                    "model": model,
                    "completed": completed,
                    "trajectory": trajectory,
                    "metadata": metadata or {},
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("Failed to append to session trajectories.jsonl: %s", e)

        if decision_points:
            try:
                with open(self._decision_file, "a", encoding="utf-8") as f:
                    for dp in decision_points:
                        f.write(json.dumps(dp, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning("Failed to append to session decision_samples.jsonl: %s", e)

        # 3. Append to global dataset pool in <hermes_home>/trajectories/
        try:
            self._global_trajectories_dir.mkdir(parents=True, exist_ok=True)
            with open(self._global_trajectories_dir / "all_trajectories.jsonl", "a", encoding="utf-8") as f:
                rec = {
                    "session_id": self._session_id,
                    "turn_n": turn_n,
                    "model": model,
                    "completed": completed,
                    "trajectory": trajectory,
                    "metadata": metadata or {},
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            if decision_points:
                with open(self._global_trajectories_dir / "all_decision_samples.jsonl", "a", encoding="utf-8") as f:
                    for dp in decision_points:
                        f.write(json.dumps(dp, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("Failed to append to global trajectories: %s", e)

    def get_trajectories(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._trajectories.get_trajectories(session_id=self._session_id, limit=limit)

    def get_decision_points(self, decision_type: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        return self._trajectories.get_decision_points(session_id=self._session_id, decision_type=decision_type, limit=limit)

    def trajectory_count(self) -> int:
        return self._trajectories.trajectory_count()

    def decision_point_count(self) -> int:
        return self._trajectories.decision_point_count()

    # ── Session metadata ──────────────────────────────────────────────

    def is_v2(self) -> bool:
        return True

    def close(self) -> None:
        """Commit and defer actual connection close if background writers remain.

        When ``_pending_writers`` > 0 (injected by ``on_session_end`` before
        calling close), the connection stays open so in-flight decohere
        posting tasks can finish cleanly.  Each writer that arrives after
        close() is dropped and decrements the counter; the last one closes
        the connection.  When there are no pending writers the connection is
        closed immediately.
        """
        self._conn.commit()
        if self._pending_writers > 0:
            self._closing = True
        else:
            self._conn.close()
