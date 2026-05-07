"""Health reporting. Depends on io.SessionIO + scheduling.MetricsCollector."""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import replace

from ..io.session_io import SessionIO
from ..scheduling.metrics import MetricsCollector
from .checks import check_message_range, check_persisted_turn, classify_compress
from .snapshots import CompressSnapshot, PersistCheck, RangeCheck

logger = logging.getLogger(__name__)


class HealthReporter:
    """Assembles health snapshots and logs warnings.

    No business logic — doesn't judge readiness, doesn't assemble context.
    """

    def __init__(self, io: SessionIO, metrics: MetricsCollector):
        self._io = io
        self._metrics = metrics
        self._last_compress: CompressSnapshot | None = None

    def verify_range(self, actual_range: tuple, msg_count: int) -> RangeCheck:
        """Cross-check message_range against raw store."""
        expected = self._io.raw_count() - msg_count
        result = check_message_range(actual_range[0], expected)
        if not result.ok:
            logger.error(
                "Decohere: RANGE MISMATCH expected=%d actual=%d delta=%d",
                result.expected, result.actual, result.delta,
            )
        return result

    def verify_persisted(self, expected_n: int) -> PersistCheck:
        """Verify placeholder write succeeded."""
        turns = tuple(self._io.get_turns())
        result = check_persisted_turn(turns, expected_n)
        if not result.ok:
            logger.error("Decohere: Turn %d NOT PERSISTED", expected_n)
        return result

    def snapshot_compress(
        self,
        readiness,
        msg_count: int,
        turn_numbers: tuple = (),
    ) -> None:
        """Record which compress branch was taken."""
        self._last_compress = replace(
            classify_compress(readiness),
            turn_numbers=turn_numbers,
        )
        logger.info(
            "Decohere: compress readiness=%s turns=%d pending=%s branch=%s tokens=%d",
            self._last_compress.readiness,
            self._last_compress.turn_count,
            self._last_compress.pending_turn_n,
            self._last_compress.branch,
            self._last_compress.estimated_tokens,
        )

    def build_health_response(self) -> dict:
        """Assemble /spec-health response."""
        return {
            "posting": self._metrics.snapshot(),
            "last_compress": (
                dataclasses.asdict(self._last_compress)
                if self._last_compress
                else None
            ),
        }

    def snapshot_session_start(self, session_id: str, platform: str) -> None:
        """Log full session state on init."""
        logger.info(
            "Decohere: session=%s platform=%s turns=%d raw=%d",
            session_id, platform,
            self._io.turn_count(),
            self._io.raw_count(),
        )

    def snapshot_session_end(self, session_id: str, pending: int) -> None:
        """Log session close. Warns if entries were abandoned."""
        if pending > 0:
            logger.warning(
                "Decohere: session=%s ended with %d pending postings",
                session_id, pending,
            )
        logger.info("Decohere: session=%s ended", session_id)
