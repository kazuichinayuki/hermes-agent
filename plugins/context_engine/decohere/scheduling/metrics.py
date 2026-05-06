"""Derivation metrics. In-memory, reset on engine restart."""

from __future__ import annotations


class MetricsCollector:
    """Per-session derivation metrics."""

    def __init__(self):
        self.attempted: int = 0
        self.succeeded: int = 0
        self.failed: int = 0
        self.timed_out: int = 0
        self.degraded: int = 0
        self.total_elapsed_ms: float = 0.0

    def record_attempt(self) -> None:
        self.attempted += 1

    def record_success(self, elapsed_ms: float) -> None:
        self.succeeded += 1
        self.total_elapsed_ms += elapsed_ms

    def record_failure(self, elapsed_ms: float) -> None:
        self.failed += 1
        self.total_elapsed_ms += elapsed_ms

    def record_timeout(self, elapsed_ms: float) -> None:
        self.timed_out += 1
        self.total_elapsed_ms += elapsed_ms

    def record_degraded(self) -> None:
        self.degraded += 1

    def failure_rate(self) -> str:
        if self.attempted == 0:
            return "0/0"
        return f"{self.failed + self.timed_out}/{self.attempted}"

    def success_rate(self) -> float:
        if self.attempted == 0:
            return 1.0
        return self.succeeded / self.attempted

    def snapshot(self) -> dict:
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "timed_out": self.timed_out,
            "degraded": self.degraded,
            "failure_rate": self.failure_rate(),
            "success_rate": round(self.success_rate(), 3),
            "avg_latency_ms": (
                round(self.total_elapsed_ms / self.attempted, 1)
                if self.attempted > 0
                else 0
            ),
        }
