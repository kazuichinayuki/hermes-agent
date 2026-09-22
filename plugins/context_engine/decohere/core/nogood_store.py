"""NogoodStore: Conflict-Driven Clause Learning (CDCL) for Agent Tool Calls.

Maintains learned negative constraints (NogoodClauses) derived from environmental
conflicts (e.g. absent symbols, syntax errors, missing dependencies).
Executes pre-dispatch preemption in Hermes `pre_tool_call` hook:
- Returns {"action": "block", "message": ...} on matching conflict clauses
- Costs 0 tokens, 0 milliseconds, 0 child processes
- Guarantees breaking repetitive trial loops.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class NogoodClause:
    """A learned conflict clause representing an invalid or dead-end action branch."""

    clause_id: str
    predicate: str  # e.g. "symbol_absent", "file_not_found", "syntax_error", "dependency_missing"
    tool_name: str  # e.g. "grep_search", "run_command", "view_file"
    pattern: Dict[str, Any]  # args pattern to match against incoming tool args
    reason: str  # Human-readable explanation of why this action is a known dead-end
    scope: str = "session"  # "session", "global", "file"
    created_turn: int = 0
    hit_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        predicate: str,
        tool_name: str,
        pattern: Dict[str, Any],
        reason: str,
        scope: str = "session",
        created_turn: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NogoodClause:
        # Stable clause_id derived from tool_name, predicate, and sorted pattern JSON
        pattern_str = json.dumps(pattern, sort_keys=True, default=str)
        raw_sig = f"{tool_name}:{predicate}:{pattern_str}"
        clause_id = f"NG-{hashlib.sha256(raw_sig.encode()).hexdigest()[:8]}"
        return cls(
            clause_id=clause_id,
            predicate=predicate,
            tool_name=tool_name,
            pattern=pattern,
            reason=reason,
            scope=scope,
            created_turn=created_turn,
            hit_count=0,
            metadata=metadata or {},
        )

    def matches(self, tool_name: str, args: Dict[str, Any]) -> bool:
        """Check if incoming tool_name and args match this nogood clause."""
        if self.tool_name != "*" and self.tool_name != tool_name:
            return False

        # Match each key-value pair in pattern
        for k, expected_val in self.pattern.items():
            if k not in args:
                return False
            actual_val = args[k]

            if isinstance(expected_val, str) and expected_val.startswith("^") and expected_val.endswith("$"):
                # Regex match
                if not re.search(expected_val, str(actual_val)):
                    return False
            elif isinstance(expected_val, str) and "*" in expected_val:
                # Glob-like substring match
                sub = expected_val.strip("*")
                if sub not in str(actual_val):
                    return False
            else:
                # Exact match
                if str(actual_val).strip() != str(expected_val).strip():
                    return False

        return True


class NogoodStore:
    """Store and matcher for learned conflict clauses."""

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._clauses: Dict[str, NogoodClause] = {}
        self._by_tool: Dict[str, List[str]] = {}

    def add_clause(self, clause: NogoodClause) -> bool:
        """Register a new nogood clause. Returns True if newly added, False if already exists."""
        if clause.clause_id in self._clauses:
            return False

        self._clauses[clause.clause_id] = clause
        self._by_tool.setdefault(clause.tool_name, []).append(clause.clause_id)
        logger.info(
            "NogoodStore learned clause [%s] for tool '%s': %s",
            clause.clause_id,
            clause.tool_name,
            clause.reason,
        )
        return True

    def evaluate_preemption(self, tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Evaluate preemption before tool execution.

        Returns Hermes directive dict `{"action": "block", "message": "..."}`
        if action violates an active clause, or None if permitted.
        """
        # Candidate clauses: tool-specific + wildcards
        candidates = list(self._by_tool.get(tool_name, [])) + list(self._by_tool.get("*", []))
        for cid in candidates:
            clause = self._clauses.get(cid)
            if not clause:
                continue

            if clause.matches(tool_name, args):
                clause.hit_count += 1
                logger.warning(
                    "[NOGOOD PREEMPTION] Blocking tool '%s' matching clause %s: %s",
                    tool_name,
                    clause.clause_id,
                    clause.reason,
                )
                return {
                    "action": "block",
                    "message": (
                        f"[NOGOOD PREEMPTION: Blocked Dead-End Action]\n"
                        f"Clause: {clause.clause_id} ({clause.predicate})\n"
                        f"Reason: {clause.reason}\n"
                        f"Guidance: This branch has already been proven invalid or non-existent. "
                        f"Please synthesize alternative hypotheses rather than repeating this call."
                    ),
                    "clause_id": clause.clause_id,
                }

        return None

    def get_active_clauses(self) -> List[NogoodClause]:
        """Return all active learned clauses."""
        return list(self._clauses.values())

    def format_ledger_summary(self, max_clauses: int = 10) -> str:
        """Format active clauses for injection into dynamic <context_ledger>."""
        if not self._clauses:
            return ""

        clauses = list(self._clauses.values())[-max_clauses:]
        lines = ["[LEARNED CONFLICT CONSTRAINTS (NOGOOD STORE)]"]
        for c in clauses:
            lines.append(f"- [{c.clause_id}] {c.tool_name}: {c.reason}")
        return "\n".join(lines)

    def export_dict(self) -> List[Dict[str, Any]]:
        """Export all clauses as dictionaries for serialization / database storage."""
        return [asdict(c) for c in self._clauses.values()]

    def load_dict(self, records: List[Dict[str, Any]]) -> None:
        """Load clauses from dictionaries."""
        for r in records:
            clause = NogoodClause(**r)
            self.add_clause(clause)
