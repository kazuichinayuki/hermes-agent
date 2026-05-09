"""Decohere context engine plugin. Entry layer — orchestration only.

Matches the ContextEngine ABC signature exactly.
Hermes calls compress() before each LLM turn — that's where both
posting AND context building happen.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.context_engine import ContextEngine

from .config import LedgerConfig
from .context.builder import build_fallback_context, build_raw_context, build_ledger_context
from .context.classifier import check_readiness, should_skip_entry
from .context.formatter import format_entry_layer, format_proc_layer
from .context.placeholder import build_placeholder
from .core.extractor import last_turn_messages, mechanical_fields
from .io.session_io import SessionIO
from .monitoring.reporter import HealthReporter
from .scheduling.metrics import MetricsCollector
from .scheduling.task_manager import TaskManager

logger = logging.getLogger(__name__)


class Decohere(ContextEngine):
    """Structured ledger entries as context. Thin coordinator.

    Delegates to:
    - monitoring.*        → health checks, logging
    - scheduling.*        → async tasks, metrics
    - context.*           → classification, placeholder, builder, formatter
    - io.SessionIO        → all reads/writes

    This class contains ZERO business logic. Every method delegates.
    """

    name = "decohere"

    # ── Token state (read by run_agent.py) ──
    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 200_000
    context_length: int = 0
    compression_count: int = 0

    # ── Compaction parameters ──
    threshold_percent: float = 1.0
    protect_first_n: int = 0
    protect_last_n: int = 0

    def __init__(self, context_length: int = 200_000):
        self.context_length = context_length
        self._session_id: str | None = None
        self._io: SessionIO | None = None
        self._tasks: TaskManager | None = None
        self._metrics: MetricsCollector | None = None
        self._health: HealthReporter | None = None
        self._cfg: LedgerConfig | None = None
        self._last_compressed_turns: int = 0

    # ── Lifecycle ──────────────────────────────────────────────────────

    def on_session_start(self, session_id: str, **kwargs):
        """Initialize all layers from hermes_home — the only external anchor."""
        hermes_home = kwargs.get("hermes_home", "~/.hermes")
        home = Path(hermes_home).expanduser()

        self._session_id = session_id
        self._cfg = LedgerConfig.from_aux_config(
            self._read_aux_config(home / "config.yaml")
        )
        self._io = SessionIO(home, session_id)
        self._tasks = TaskManager(self._cfg)
        self._metrics = MetricsCollector()
        self._health = HealthReporter(self._io, self._metrics)
        self._health.snapshot_session_start(session_id, kwargs.get("platform", ""))

    def on_session_end(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        if self._health and self._tasks:
            self._health.snapshot_session_end(
                session_id, self._tasks.pending_count(session_id)
            )
            self._tasks.cleanup(session_id)
        if self._io:
            self._io.close()

    def on_session_reset(self):
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0
        self._last_compressed_turns = 0

    # ── Token tracking ─────────────────────────────────────────────────

    def update_from_response(self, usage: dict[str, int]) -> None:
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_total_tokens = usage.get("total_tokens", 0)

    # ── Context management ─────────────────────────────────────────────

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """True only when there are ledger entries to inject as context.

        First turn has no prior entries — let raw messages flow through.
        Subsequent turns with existing entries trigger compression to swap
        raw history for structured L1+L2 ledger context.
        """
        if self._io is None:
            return False
        return self._io.is_v2() and self._io.turn_count() > 0

    def compress(
        self,
        messages: list[dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> list[dict[str, Any]]:
        """Main entry point. Called BEFORE each LLM turn.

        Phase 1: extract last turn → placeholder → async posting
        Phase 2: read existing specs → build L1+L2 → return
        """
        sid = self._session_id
        if not sid or not self._io:
            return messages

        # Filter out any None messages (can happen after session splits)
        messages = [m for m in messages if m is not None]

        # ── Post-turn processing: ALWAYS run (write placeholder, fire async posting) ──
        turn_msgs = last_turn_messages(messages)
        should_skip = should_skip_entry(turn_msgs)
        mechanical = mechanical_fields(turn_msgs)
        msg_range = self._io.compute_range(turn_msgs)
        placeholder = build_placeholder(
            turn_n=self._io.turn_count() + 1,
            message_range=msg_range,
            mechanical=mechanical,
            skipped=should_skip,
        )
        range_ok = self._health.verify_range(msg_range, len(turn_msgs))
        self._io.save_turn(placeholder)
        persist_ok = self._health.verify_persisted(placeholder["n"])
        if not range_ok.ok or not persist_ok.ok:
            self._metrics.record_degraded()
        if not should_skip:
            self._tasks.schedule(sid, turn_msgs, self._io, self._metrics)

        # ── Context building: only replace history if there are NEW turns to inject ──
        current_turns = self._io.turn_count()
        if current_turns <= self._last_compressed_turns:
            return messages

        self._last_compressed_turns = current_turns
        turns = self._io.get_turns()
        readiness = check_readiness(turns, self._io.turn_count())

        if readiness.state == "legacy":
            self._health.snapshot_compress(readiness, len(messages))
            return build_raw_context(messages)
        if readiness.state == "empty":
            self._health.snapshot_compress(readiness, 0)
            return []

        result = (
            build_ledger_context(list(readiness.turns), self._cfg.max_turns)
            if readiness.state == "ready"
            else build_fallback_context(
                turns=list(readiness.turns),
                max_turns=self._cfg.max_turns,
                last_turn_msgs=self._io.get_raw_messages(),
            )
        )

        included_turns = _extract_turn_numbers(result)
        self._health.snapshot_compress(
            readiness, len(messages), turn_numbers=tuple(included_turns),
        )
        self.compression_count += 1
        return result

    # ── Status ─────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, object]:
        return {
            "last_prompt_tokens": self.last_prompt_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, self.last_prompt_tokens / self.context_length * 100)
                if self.context_length else 0
            ),
            "compression_count": self.compression_count,
            "ledger_entries": self._io.turn_count() if self._io else 0,
        }

    def update_model(self, model: str, context_length: int,
                     base_url: str = "", api_key: str = "", provider: str = ""):
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)

    # ── Config bootstrap ───────────────────────────────────────────────

    @staticmethod
    def _read_aux_config(config_path: Path) -> dict | None:
        """Read auxiliary config from config.yaml via hermes_home anchor."""
        import yaml
        try:
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("auxiliary", {})
        except Exception:
            return None


def _extract_turn_numbers(messages: list[dict[str, Any]]) -> list[int]:
    """Extract turn numbers from L1/L2 context messages for audit trail."""
    import re
    numbers = set()
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            for m in re.finditer(r"\[Turn (\d+)\]", content):
                numbers.add(int(m.group(1)))
    return sorted(numbers)
