"""PredictiveController: Predictive Coding & Residual-Guided Deliberation Gate.

Implements Karl Friston's Predictive Processing Principle:
    ϵ_t = y_t - ŷ_t
    S_t = ||ϵ_t||_W ∈ [0.0, 1.0]

Evaluates observation in Hermes `post_tool_call` hook:
- Fast-Path (S_t <= δ): Expected outcome -> Continue with 33ms non-autoregressive execution.
- System 2 Wakeup (S_t > δ): Falsified expectation -> Awaken LLM Chain-of-Thought deliberation.
- Exports structured decision residuals (S_t, H_t, ϵ_t) for Laya / NeMo Relay offline distillation.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExpectedObservation:
    """Top-down expected sensory prior for an action a_t = (tool_name, args)."""

    expected_exit_code: int = 0
    expect_non_empty: bool = True
    expect_no_traceback: bool = True
    max_expected_duration_ms: int = 5000
    criteria: str = "normal_success"


@dataclass
class PredictiveResidual:
    """Computed prediction error residual between prior expectation and actual observation."""

    surprise_score: float  # S_t in [0.0, 1.0]
    residual_vector: Dict[str, float]  # Component residuals
    should_wake_system2: bool  # S_t > threshold
    entropy_estimate: float  # H_t estimate for ESTR/off-policy scaling
    diagnostic_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class PredictiveController:
    """Predictive Processing and Deliberation Controller."""

    DEFAULT_THRESHOLD: float = 0.35

    def __init__(self, threshold: float = DEFAULT_THRESHOLD, session_id: str = ""):
        self.threshold = threshold
        self.session_id = session_id

    def formulate_prior(self, tool_name: str, args: Dict[str, Any]) -> ExpectedObservation:
        """Formulate top-down prior expectation ŷ_t for action a_t."""
        prior = ExpectedObservation()

        if tool_name == "grep_search":
            prior.expect_non_empty = True
            prior.criteria = "matches_found"
        elif tool_name in ("view_file", "read_file"):
            prior.expect_non_empty = True
            prior.criteria = "file_readable"
        elif tool_name == "run_command":
            prior.expected_exit_code = 0
            prior.expect_no_traceback = True
            cmd = args.get("CommandLine", "")
            if "test" in cmd or "pytest" in cmd:
                prior.max_expected_duration_ms = 15000
        elif tool_name in ("replace_file_content", "write_to_file"):
            prior.criteria = "write_success"

        return prior

    def evaluate(
        self,
        tool_name: str,
        args: Dict[str, Any],
        actual_result: Any,
        duration_ms: int = 0,
    ) -> PredictiveResidual:
        """Evaluate observation y_t against prior ŷ_t, computing residual ϵ_t and surprise S_t."""
        prior = self.formulate_prior(tool_name, args)
        raw_text = str(actual_result) if actual_result is not None else ""
        lower = raw_text.lower()

        # Dimension 1: Exit code or explicit failure residual (0.0 to 1.0)
        r_exit = 0.0
        if "exit code 1" in lower or "returncode=1" in lower or "error:" in lower or "failed" in lower:
            r_exit = 1.0
        elif "[nogood preemption" in lower:
            r_exit = 1.0

        # Dimension 2: Emptiness / Void search residual (0.0 to 1.0)
        r_empty = 0.0
        if prior.expect_non_empty:
            stripped = raw_text.strip()
            if stripped in ("[]", "{}", '{"matches": []}', "0 matches found", ""):
                r_empty = 1.0
            elif "no matches found" in lower or "total results: 0" in lower:
                r_empty = 1.0

        # Dimension 3: Traceback / Exception residual (0.0 to 1.0)
        r_traceback = 0.0
        if "traceback (most recent call last)" in lower or "exception" in lower:
            r_traceback = 1.0

        # Dimension 4: Duration anomaly residual
        r_duration = 0.0
        if duration_ms > prior.max_expected_duration_ms:
            r_duration = min(1.0, (duration_ms - prior.max_expected_duration_ms) / 10000.0)

        # Weighted surprise score S_t = ||ϵ_t||_W
        w_exit = 0.4
        w_empty = 0.35
        w_traceback = 0.25
        w_duration = 0.1

        surprise_score = min(
            1.0,
            (w_exit * r_exit) + (w_empty * r_empty) + (w_traceback * r_traceback) + (w_duration * r_duration),
        )

        should_wake = surprise_score >= self.threshold

        # Shannon Entropy proxy H_t for this action decision (used in ESTR / TARL off-policy normalization)
        # Tools with wide open parameters (e.g. run_command) have higher action-space entropy than simple read
        entropy_estimate = 1.5 if tool_name == "run_command" else (1.0 if tool_name == "grep_search" else 0.5)

        residual_vector = {
            "r_exit": r_exit,
            "r_empty": r_empty,
            "r_traceback": r_traceback,
            "r_duration": r_duration,
        }

        diag = (
            f"S_t={surprise_score:.2f} (wake_system2={should_wake}) "
            f"[exit={r_exit}, empty={r_empty}, trace={r_traceback}]"
        )

        return PredictiveResidual(
            surprise_score=surprise_score,
            residual_vector=residual_vector,
            should_wake_system2=should_wake,
            entropy_estimate=entropy_estimate,
            diagnostic_summary=diag,
            metadata={
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "criteria": prior.criteria,
            },
        )
