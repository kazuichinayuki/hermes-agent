"""Frozen dataclasses for health check results."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RangeCheck:
    ok: bool
    expected: int
    actual: int
    delta: int


@dataclass(frozen=True)
class PersistCheck:
    ok: bool
    turn_n: int
    found: bool


@dataclass(frozen=True)
class CompressSnapshot:
    readiness: str
    turn_count: int
    pending_turn_n: int | None
    branch: str  # "spec" | "fallback" | "legacy" | "empty"
    estimated_tokens: int
    turn_numbers: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class SecurityEvent:
    kind: str
    session_id: str
    turn_n: int
    detail: str


@dataclass(frozen=True)
class AuditEntry:
    turn_n: int
    model: str
    provider: str
    attempt_at: str
    outcome: str
    elapsed_ms: float
    validated: bool
