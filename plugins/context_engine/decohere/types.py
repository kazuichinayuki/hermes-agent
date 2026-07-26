"""Immutable data containers. No methods with side-effects.

All state passed through frozen dataclasses or enum dispatch.
Zero I/O, zero mutation, zero side-effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ── Mechanical extraction results ──────────────────────────────────────


@dataclass(frozen=True)
class MechanicalFields:
    """Mechanically extracted from raw messages — no LLM involvement."""

    tools: tuple = field(default_factory=tuple)
    files_touched: tuple = field(default_factory=tuple)


# ── Placeholder (pre-posting) ───────────────────────────────────────


@dataclass(frozen=True)
class Placeholder:
    """Written synchronously before async posting begins."""

    turn_n: int
    message_range: tuple
    mechanical: MechanicalFields
    entry_skipped: bool


# ── Readiness check result ─────────────────────────────────────────────


@dataclass(frozen=True)
class Readiness:
    """Snapshot of session readiness for context building."""

    state: str  # "ready" | "pending" | "legacy" | "empty"
    turns: tuple
    pending_turn_n: int | None


# ── Entry result ──────────────────────────────────────────────────


class Outcome(Enum):
    """Posting outcome states. Single source of truth."""

    OK = "ok"
    TIMEOUT = "timeout"
    ERROR = "error"


class BudgetTier(Enum):
    """Memory budget tiers for query-aware dynamic allocation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class EntryResult:
    """Outcome of async entry posting.

    Carries validated turn + timing. Used by TaskManager._run
    to route to metrics/logging/persistence without if-else branching.
    """

    outcome: Outcome
    turn: dict | None
    elapsed_ms: float


# ── Monitoring snapshots ───────────────────────────────────────────────


@dataclass(frozen=True)
class RangeCheck:
    """Result of message_range verification against raw store."""

    ok: bool
    expected: int
    actual: int
    delta: int


@dataclass(frozen=True)
class PersistCheck:
    """Result of placeholder persistence verification."""

    ok: bool
    turn_n: int
    found: bool


@dataclass(frozen=True)
class CompressSnapshot:
    """Record of which compress branch was taken + which turns in context."""

    readiness: str
    turn_count: int
    pending_turn_n: int | None
    branch: str  # "spec" | "fallback" | "legacy" | "empty"
    estimated_tokens: int
    turn_numbers: tuple = field(default_factory=tuple)


# ── Security / audit ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SecurityEvent:
    """Record of security-relevant operations. Written to audit log."""

    kind: str
    session_id: str
    turn_n: int
    detail: str


@dataclass(frozen=True)
class AuditEntry:
    """Per-entry audit record."""

    turn_n: int
    model: str
    provider: str
    attempt_at: str
    outcome: str
    elapsed_ms: float
    validated: bool
