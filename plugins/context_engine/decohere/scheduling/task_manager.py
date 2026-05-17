"""Async posting scheduler. Per-session serial, cross-session parallel."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..config import LedgerConfig
from ..core.poster import post_entry
from ..core.utils import elapsed_ms, ensure_entry
from ..core.validator import validate_entry
from ..io.session_io import SessionIO
from ..types import EntryResult, Outcome
from .metrics import MetricsCollector

logger = logging.getLogger(__name__)


class TaskManager:
    """Serial queue per session (lock-gated). Independent across sessions."""

    def __init__(self, config: LedgerConfig):
        self._config = config
        self._pending: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Background event loop thread for CLI (sync) contexts
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create a background event loop for async posting."""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            pass

        if self._loop is None or self._loop.is_closed():
            import threading
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, daemon=True,
            )
            self._thread.start()
        return self._loop

    def schedule(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        io: SessionIO,
        metrics: MetricsCollector,
    ) -> None:
        """Fire async posting. Non-blocking.

        Silently skips if no event loop is running (sync contexts such as
        unit tests or CLI sessions without an async runtime).
        The placeholder is already persisted — only the LLM entry posting
        step is deferred.
        """
        try:
            loop = self._ensure_loop()
        except Exception:
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(
                self._run(session_id, messages, io, metrics), loop=loop
            )
        )

    async def _run(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        io: SessionIO,
        metrics: MetricsCollector,
    ) -> None:
        """Pure delegation. Each line does ONE thing."""
        lock, _ = ensure_entry(self._locks, session_id, asyncio.Lock)
        async with lock:
            self._pending[session_id] = self._pending.get(session_id, 0) + 1
            metrics.record_attempt()

            result = await _post_with_timeout(messages, self._config, io)

            _persist_if_ok(result, io)
            _record_outcome(result, metrics)
            _log_if_failed(result, session_id, metrics)

            if result.outcome is Outcome.OK and result.turn is not None:
                _auto_import_concepts(result.turn, session_id, io)

            self._pending[session_id] -= 1

    def cleanup(self, session_id: str) -> None:
        self._locks.pop(session_id, None)
        self._pending.pop(session_id, None)

    def pending_count(self, session_id: str) -> int:
        return self._pending.get(session_id, 0)


# ── Pure helpers ───────────────────────────────────────────────────────


async def _post_with_timeout(
    messages: list[dict[str, Any]],
    config: LedgerConfig,
    io: SessionIO | None = None,
) -> EntryResult:
    """Post entry with timeout.

    F5: on timeout, the underlying LLM call is shielded from cancellation
    and continues in background. When it eventually completes, the turn is
    persisted via a done callback — so the next turn can get a clean spec
    instead of hitting F2 fallback permanently.
    """
    t0 = time.monotonic()
    # Wrap in a Task so we can shield it from cancellation on timeout
    work_task = asyncio.ensure_future(post_entry(messages, config))
    try:
        raw = await asyncio.wait_for(
            asyncio.shield(work_task),
            timeout=config.timeout,
        )
        return EntryResult(Outcome.OK, validate_entry(raw), elapsed_ms(t0))
    except asyncio.TimeoutError:
        # F5: work continues in background; persist if it completes later
        if io is not None:
            work_task.add_done_callback(
                lambda fut: _try_persist_late(fut, io)
            )
        return EntryResult(Outcome.TIMEOUT, None, elapsed_ms(t0))
    except Exception as exc:
        logger.error(
            "Decohere: entry posting failed session=%s: %s",
            messages[-1].get("session_id", "?") if messages else "?",
            exc,
            exc_info=True,
        )
        return EntryResult(Outcome.ERROR, None, elapsed_ms(t0))


def _persist_if_ok(result: EntryResult, io: SessionIO) -> None:
    """Save turn only if posting succeeded."""
    if result.outcome is Outcome.OK and result.turn is not None:
        io.save_turn(result.turn)


def _try_persist_late(fut: asyncio.Future, io: SessionIO) -> None:
    """F5 late-completion handler: persist turn from completed background task.

    Shielded from cancellation — the done callback fires in a context where
    the parent coroutine may already be gone.  Errors are logged, not raised.
    """
    try:
        raw = fut.result()
    except Exception as exc:
        logger.warning("Decohere: late posting task failed: %s", exc)
        return
    try:
        turn = validate_entry(raw)
        io.save_turn(turn)
        logger.debug("Decohere: late posting persisted turn %s", turn.get("n"))
    except Exception as exc:
        logger.warning("Decohere: late posting validate/save failed: %s", exc)


def _record_outcome(result: EntryResult, metrics: MetricsCollector) -> None:
    """Route EntryResult to the correct metrics method."""
    dispatch = {
        Outcome.OK: metrics.record_success,
        Outcome.TIMEOUT: metrics.record_timeout,
        Outcome.ERROR: metrics.record_failure,
    }
    dispatch[result.outcome](result.elapsed_ms)


def _log_if_failed(
    result: EntryResult,
    session_id: str,
    metrics: MetricsCollector,
) -> None:
    """Log warning only on failure."""
    if result.outcome is Outcome.TIMEOUT:
        logger.warning("Decohere: entry posting timeout session=%s", session_id)
    elif result.outcome is Outcome.ERROR:
        logger.warning(
            "Decohere: entry posting failed session=%s (rate: %s)",
            session_id,
            metrics.failure_rate(),
        )


def _auto_import_concepts(
    turn: dict[str, object],
    session_id: str,
    io,
) -> None:
    """Auto-import concepts from a successfully posted turn into shared_store."""
    concepts = turn.get("concepts_and_definitions", []) or []
    if not concepts:
        return

    try:
        # Resolve hermes_home from io
        import os
        home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
        from pathlib import Path
        from plugins.context_engine.decohere.knowledge.shared_store import SharedStore

        store = SharedStore(Path(home))
        try:
            turn_n = int(turn.get("n", 0))
            for c in concepts:
                if isinstance(c, dict):
                    term = c.get("term", "")
                    definition = c.get("definition", "")
                    if term:
                        store.add_concept(
                            term, definition, session_id, turn_n,
                            imported_by="auto",
                        )
        finally:
            store.close()
    except Exception:
        pass  # Best-effort — don't block posting on import failure
