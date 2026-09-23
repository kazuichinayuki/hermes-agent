"""ReflectTrigger: Lightweight Decision Probe for Fast-Path vs. LLM Reflection.

Replaces the heavy Deliberation Controller / PredictiveController abstraction with
a clean, intuitive probe:
- Fast-Path (should_reflect = False): Tool completed normally -> Continue with 33ms non-autoregressive progression.
- Reflection Wakeup (should_reflect = True): Anomalous failure or broken expectation -> Awaken LLM for deep Chain-of-Thought reflection.
- Exports structured reflection decision points for training lightweight non-autoregressive heads (Laya / NeMo Relay).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReflectDecision:
    """Decision produced by ReflectTrigger after a tool completes."""

    should_reflect: bool  # True if LLM CoT reflection is needed, False if Fast Path
    trigger_score: float  # Anomaly score in [0.0, 1.0]
    reasons: List[str]  # Human-readable list of triggered signals
    entropy_estimate: float  # Estimated decision entropy for ESTR / off-policy weighting
    diagnostic_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class ReflectTrigger:
    """Probes tool execution feedback to decide whether to trigger LLM reflection."""

    DEFAULT_THRESHOLD: float = 0.35

    def __init__(self, threshold: float = DEFAULT_THRESHOLD, session_id: str = ""):
        self.threshold = threshold
        self.session_id = session_id

    def evaluate(
        self,
        tool_name: str,
        args: Dict[str, Any],
        actual_result: Any,
        duration_ms: int = 0,
    ) -> ReflectDecision:
        """Evaluate observation to decide if LLM reflection should be triggered."""
        raw_text = str(actual_result) if actual_result is not None else ""
        lower = raw_text.lower()
        reasons: List[str] = []

        # Signal 1: Explicit failure / non-zero return
        r_exit = 0.0
        if any(err_kw in lower for err_kw in ("exit code 1", "returncode=1", "error:", "failed", "fatal:")):
            r_exit = 1.0
            reasons.append("execution_error")
        elif "[nogood preemption" in lower:
            r_exit = 1.0
            reasons.append("nogood_blocked")

        # Signal 2: Search emptiness / void result
        r_empty = 0.0
        if tool_name in ("grep_search", "find_by_name"):
            stripped = raw_text.strip()
            if stripped in ("[]", "{}", '{"matches": []}', "0 matches found", ""):
                r_empty = 1.0
                reasons.append("empty_search_result")
            elif "no matches found" in lower or "total results: 0" in lower:
                r_empty = 1.0
                reasons.append("empty_search_result")

        # Signal 3: Traceback or exception crash
        r_traceback = 0.0
        if "traceback (most recent call last)" in lower or "exception:" in lower or "syntaxerror" in lower:
            r_traceback = 1.0
            reasons.append("unhandled_exception")

        # Signal 4: Duration anomaly (e.g. commands taking > 15s)
        r_duration = 0.0
        max_expected_ms = 15000 if tool_name == "run_command" else 5000
        if duration_ms > max_expected_ms:
            r_duration = min(1.0, (duration_ms - max_expected_ms) / 10000.0)
            reasons.append(f"duration_exceeded_{duration_ms}ms")

        # Weighted trigger score
        trigger_score = min(
            1.0,
            (0.40 * r_exit) + (0.35 * r_empty) + (0.25 * r_traceback) + (0.10 * r_duration),
        )

        should_reflect = trigger_score >= self.threshold

        # Shannon Entropy proxy H_t for action-space complexity
        entropy_estimate = 1.5 if tool_name == "run_command" else (1.0 if tool_name == "grep_search" else 0.5)

        diag = (
            f"score={trigger_score:.2f} (should_reflect={should_reflect}) "
            f"signals=[{', '.join(reasons) if reasons else 'all_clear'}]"
        )

        return ReflectDecision(
            should_reflect=should_reflect,
            trigger_score=trigger_score,
            reasons=reasons,
            entropy_estimate=entropy_estimate,
            diagnostic_summary=diag,
            metadata={
                "tool_name": tool_name,
                "duration_ms": duration_ms,
            },
        )


# Backward compatibility aliases
PredictiveController = ReflectTrigger
PredictiveResidual = ReflectDecision
