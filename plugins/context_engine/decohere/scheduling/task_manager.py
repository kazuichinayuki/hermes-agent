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
        self._locks: dict[str, asyncio.Lock] = {}
        self._pending: dict[str, int] = {}
        self._config = config

    def schedule(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        io: SessionIO,
        metrics: MetricsCollector,
    ) -> None:
        """Fire async posting. Non-blocking."""
        asyncio.create_task(self._run(session_id, messages, io, metrics))

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

            result = await _post_with_timeout(messages, self._config)

            _persist_if_ok(result, io)
            _record_outcome(result, metrics)
            _log_if_failed(result, session_id, metrics)

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
) -> EntryResult:
    """Post entry with timeout."""
    t0 = time.monotonic()
    try:
        raw = await asyncio.wait_for(
            post_entry(messages, config),
            timeout=config.timeout,
        )
        return EntryResult(Outcome.OK, validate_entry(raw), elapsed_ms(t0))
    except asyncio.TimeoutError:
        return EntryResult(Outcome.TIMEOUT, None, elapsed_ms(t0))
    except Exception:
        return EntryResult(Outcome.ERROR, None, elapsed_ms(t0))


def _persist_if_ok(result: EntryResult, io: SessionIO) -> None:
    """Save turn only if posting succeeded."""
    if result.outcome is Outcome.OK and result.turn is not None:
        io.save_turn(result.turn)


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
