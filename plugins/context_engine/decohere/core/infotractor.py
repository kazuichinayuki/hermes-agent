"""Infotractor: Information-Theoretic Environmental Feedback Distillation.

Intercepts raw tool results in Hermes `transform_tool_result` hook:
1. Deconstructs feedback into the Four Observation States:
   - COMMITMENT: Verified positive invariant / passing assertion
   - HYPOBRANCH: Active exploratory branch under hypothesis testing
   - CONFLICT: Violated assumption / dead-end -> Extracts NogoodClause for CDCL
   - REDULOG: Redundant log / high-entropy waste -> Radically evaporates to 3-line diagnostic residual
2. Replaces raw tool result in session history with condensed, high-signal information,
   preserving 80%+ context window while persisting full logs out-of-band in SQLite.
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .nogood_store import NogoodClause

logger = logging.getLogger(__name__)


class ObservationState(str, Enum):
    """The four fundamental states of environmental observation."""

    COMMITMENT = "commitment"  # Verified positive invariant or passed assertion
    HYPOBRANCH = "hypobranch"  # Active exploratory hypothesis branch
    CONFLICT = "conflict"      # Violated assumption / contradictory dead-end
    REDULOG = "redulog"        # Redundant log / high-entropy waste


class Infotractor:
    """Information extractor and entropy contractor for tool feedback."""

    # Regex patterns for diagnostic extraction
    _PYTHON_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):", re.MULTILINE)
    _PYTHON_EXCEPTION_RE = re.compile(
        r"^(?P<type>[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt|Warning)): (?P<msg>.*)$",
        re.MULTILINE,
    )
    _FILE_LINE_RE = re.compile(
        r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>[A-Za-z0-9_<>]+)',
        re.MULTILINE,
    )
    _PYTEST_FAIL_RE = re.compile(r"FAILED\s+([^\s:]+)(?:::[^\s]+)?\s+-\s+(.*)", re.MULTILINE)

    def __init__(self, session_id: str = ""):
        self.session_id = session_id

    def compile(
        self,
        tool_name: str,
        args: Dict[str, Any],
        raw_result: Any,
        **kwargs: Any,
    ) -> Tuple[str, ObservationState, Optional[NogoodClause], Optional[str]]:
        """Compile raw tool feedback into condensed context, state, and optional NogoodClause.

        Returns:
            (clean_result, state, nogood_clause, full_raw_archive_if_evaporated)
        """
        raw_text = str(raw_result) if raw_result is not None else ""
        lines = raw_text.splitlines()

        # ── 1. Check for REDULOG (Redundant / High-Entropy Verbose Logs) ──
        is_traceback = bool(self._PYTHON_TRACEBACK_RE.search(raw_text))
        is_error = "error" in raw_text.lower() or "failed" in raw_text.lower() or "exception" in raw_text.lower()
        is_long = len(lines) > 25

        if (is_traceback or (is_error and is_long)) and len(lines) > 15:
            evaporated_text, diag = self._evaporate_redulog(tool_name, raw_text, lines)
            conflict_clause = self._extract_conflict(tool_name, args, raw_text, diag)
            state = ObservationState.REDULOG
            return evaporated_text, state, conflict_clause, raw_text

        # ── 2. Check for CONFLICT (Empty grep, missing file, exit code failure) ──
        conflict_clause = self._extract_conflict(tool_name, args, raw_text)
        if conflict_clause is not None:
            state = ObservationState.CONFLICT
            # If conflict produced a nogood, append sharp notification to result
            enhanced_text = (
                f"{raw_text}\n\n"
                f"[INFOTRACTOR: Conflict Identified -> Learned {conflict_clause.clause_id}]\n"
                f"Negative Constraint: {conflict_clause.reason}"
            )
            return enhanced_text, state, conflict_clause, None

        # ── 3. Check for COMMITMENT (Passed test, verified replacement, created file) ──
        if self._is_commitment(tool_name, raw_text):
            state = ObservationState.COMMITMENT
            return raw_text, state, None, None

        # ── 4. Default: HYPOBRANCH (Active exploratory branch) ──
        return raw_text, ObservationState.HYPOBRANCH, None, None

    def _evaporate_redulog(
        self,
        tool_name: str,
        raw_text: str,
        lines: List[str],
    ) -> Tuple[str, Dict[str, str]]:
        """Condense massive traceback or error spam into 3-line diagnostic residual."""
        diag: Dict[str, str] = {
            "error_type": "ExecutionError",
            "root_cause": "",
            "location": "",
        }

        # Match Python exceptions
        exc_matches = list(self._PYTHON_EXCEPTION_RE.finditer(raw_text))
        if exc_matches:
            last_exc = exc_matches[-1]
            diag["error_type"] = last_exc.group("type")
            diag["root_cause"] = last_exc.group("msg").strip()

        # Match File/Line trace
        file_matches = list(self._FILE_LINE_RE.finditer(raw_text))
        if file_matches:
            last_file = file_matches[-1]
            diag["location"] = f"{last_file.group('file')}:{last_file.group('line')} in {last_file.group('func')}()"

        # Match Pytest failure summary
        pytest_matches = list(self._PYTEST_FAIL_RE.finditer(raw_text))
        if pytest_matches:
            p = pytest_matches[0]
            diag["error_type"] = "TestFailure"
            diag["root_cause"] = f"{p.group(1)} - {p.group(2)}"

        if not diag["root_cause"] and lines:
            # Fallback: pick last non-empty line
            non_empty = [ln.strip() for ln in lines if ln.strip()]
            diag["root_cause"] = non_empty[-1] if non_empty else "Unknown error"

        orig_count = len(lines)
        evaporated = (
            f"[REDULOG EVAPORATED: {orig_count} lines condensed to diagnostic residual]\n"
            f"Error: {diag['error_type']}: {diag['root_cause']}\n"
            f"Location: {diag['location'] if diag['location'] else 'N/A'}\n"
            f"Note: Full raw log ({orig_count} lines) archived to SQLite out-of-band."
        )
        return evaporated, diag

    def _extract_conflict(
        self,
        tool_name: str,
        args: Dict[str, Any],
        raw_text: str,
        diag: Optional[Dict[str, str]] = None,
    ) -> Optional[NogoodClause]:
        """Synthesize NogoodClause if environmental feedback falsifies an assumption."""
        # Case A: grep_search with 0 matches
        if tool_name == "grep_search":
            query = args.get("Query", "")
            search_path = args.get("SearchPath", "")
            # Check for empty json or zero results
            is_empty = False
            stripped = raw_text.strip()
            if stripped in ("[]", "{}", '{"matches": []}', "0 matches found"):
                is_empty = True
            elif "No matches found" in raw_text or "Total results: 0" in raw_text:
                is_empty = True

            if is_empty and query:
                return NogoodClause.create(
                    predicate="symbol_absent",
                    tool_name="grep_search",
                    pattern={"Query": query, "SearchPath": search_path},
                    reason=f"Symbol '{query}' verified absent in '{search_path}'. Avoid repeated search.",
                )

        # Case B: file view / read file not found
        if tool_name in ("view_file", "read_file"):
            target_file = args.get("AbsolutePath", "") or args.get("TargetFile", "")
            if "not found" in raw_text.lower() or "does not exist" in raw_text.lower() or "no such file" in raw_text.lower():
                return NogoodClause.create(
                    predicate="file_not_found",
                    tool_name=tool_name,
                    pattern={("AbsolutePath" if "AbsolutePath" in args else "TargetFile"): target_file},
                    reason=f"Path '{target_file}' does not exist on filesystem.",
                )

        # Case C: CLI unrecognized flag or syntax error
        if tool_name == "run_command":
            cmd = args.get("CommandLine", "")
            if "unrecognized argument" in raw_text.lower() or "command not found" in raw_text.lower():
                return NogoodClause.create(
                    predicate="syntax_error",
                    tool_name="run_command",
                    pattern={"CommandLine": cmd},
                    reason=f"Command '{cmd[:60]}' failed due to unrecognized syntax or missing binary.",
                )

        return None

    def _is_commitment(self, tool_name: str, raw_text: str) -> bool:
        """Determine if tool feedback represents a verified positive commitment."""
        lower = raw_text.lower()
        if "passed in" in lower or "100% passing" in lower or "all tests passed" in lower:
            return True
        if "successfully created" in lower or "successfully replaced" in lower:
            return True
        return False
