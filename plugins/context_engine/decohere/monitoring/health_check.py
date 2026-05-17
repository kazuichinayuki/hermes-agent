"""Decohere data quality monitoring and auto-fix engine.

Five checkers + fix engine + CLI entry point.

Usage:
    python -m plugins.context_engine.decohere.monitoring.health_check
        [--session <id>] [--fix] [--output <path>] [--json]

Checks:
    1. Completeness  — all 9 required fields present
    2. FTS5 sync     — concepts_fts matches ledger_entries
    3. Orphan msgs   — raw_messages without matching ledger entry
    4. JSON validity  — entry_json parseable
    5. Range verify   — message_range vs raw_messages count
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = [
    "reference_documentation",
    "relevant_metadata",
    "concepts_and_definitions",
    "narrative",
    "user_intent",
    "decisions_and_rationale",
    "procedures",
    "insights_and_learnings",
    "critical_reflection",
]

L1_DEFAULTS = {
    "reference_documentation": (),
    "relevant_metadata": {"task": "", "reference_class": ""},
}

L2_DEFAULTS = {
    "concepts_and_definitions": (),
    "narrative": {"summary": "", "cross_references": ()},
    "user_intent": "",
    "decisions_and_rationale": (),
    "procedures": (),
    "insights_and_learnings": (),
    "critical_reflection": {
        "ignored_perspectives": (), "logical_gaps": (), "improvement_directions": (),
    },
}

ALL_DEFAULTS = {**L1_DEFAULTS, **L2_DEFAULTS}


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    fixes_applied: list[str] = field(default_factory=list)


@dataclass
class HealthReport:
    session_id: str
    total_turns: int
    total_raw: int
    checks: list[CheckResult]
    fixes_total: int = 0
    fixed_turns: list[int] = field(default_factory=list)
    corrupted_turns: list[int] = field(default_factory=list)
    skipped_turns: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Checkers (read-only)
# ---------------------------------------------------------------------------

def check_completeness(conn: sqlite3.Connection) -> CheckResult:
    """Verify all 9 required fields exist in each entry, default values where needed."""
    issues: list[dict] = []
    rows = conn.execute("SELECT turn_n, entry_json FROM ledger_entries").fetchall()
    for turn_n, entry_json in rows:
        entry = _parse(entry_json)
        missing = []
        for field in REQUIRED_FIELDS:
            if field not in entry or entry[field] is None:
                missing.append(field)
        if missing:
            issues.append({"turn_n": turn_n, "missing_fields": missing})
    return CheckResult(
        name="completeness",
        passed=len(issues) == 0,
        details={"issues": issues, "total_checked": len(rows)},
    )


def check_fts5_sync(conn: sqlite3.Connection) -> CheckResult:
    """Verify concepts_fts rows match ledger_entries concepts_and_definitions."""
    issues: list[dict] = []

    # FTS5 orphan rows (no matching ledger entry)
    try:
        orphans = conn.execute(
            "SELECT rowid FROM concepts_fts WHERE rowid NOT IN "
            "(SELECT turn_n FROM ledger_entries)"
        ).fetchall()
        for r in orphans:
            issues.append({"type": "orphan_fts5", "rowid": r[0]})
    except sqlite3.OperationalError:
        pass

    # Missing FTS5 rows (ledger entry has concepts but no FTS5 row)
    rows = conn.execute("SELECT turn_n, entry_json FROM ledger_entries").fetchall()
    for turn_n, entry_json in rows:
        entry = _parse(entry_json)
        concepts = entry.get("concepts_and_definitions", []) or []
        if concepts:
            fts_row = conn.execute(
                "SELECT COUNT(*) FROM concepts_fts WHERE rowid = ?", (turn_n,)
            ).fetchone()
            if fts_row and fts_row[0] == 0:
                issues.append({"type": "missing_fts5", "turn_n": turn_n})

    return CheckResult(
        name="fts5_sync",
        passed=len(issues) == 0,
        details={"issues": issues},
    )


def check_orphan_messages(conn: sqlite3.Connection) -> CheckResult:
    """Detect raw_messages with no matching ledger entry span."""
    entry_count = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
    if entry_count == 0:
        return CheckResult(name="orphan_messages", passed=True, details={})

    # Collect all store_id ranges from ledger entries
    rows = conn.execute("SELECT entry_json FROM ledger_entries").fetchall()
    covered: set[int] = set()
    for (entry_json,) in rows:
        entry = _parse(entry_json)
        msg_range = entry.get("message_range", [])
        if msg_range and len(msg_range) >= 2:
            covered.update(range(int(msg_range[0]), int(msg_range[-1]) + 1))

    # Check raw_messages outside covered ranges
    total_raw = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    orphan_count = 0
    if total_raw > 0:
        raw_rows = conn.execute("SELECT store_id FROM raw_messages").fetchall()
        orphan_count = sum(1 for (sid,) in raw_rows if sid not in covered)

    return CheckResult(
        name="orphan_messages",
        passed=orphan_count == 0,
        details={"orphan_count": orphan_count, "total_raw": total_raw},
    )


def check_json_validity(conn: sqlite3.Connection) -> CheckResult:
    """Check each entry_json is valid JSON, flag corrupted ones."""
    corrupted: list[int] = []
    rows = conn.execute("SELECT turn_n, entry_json FROM ledger_entries").fetchall()
    for turn_n, entry_json in rows:
        entry = _parse(entry_json)
        if entry is None:
            corrupted.append(turn_n)
    return CheckResult(
        name="json_validity",
        passed=len(corrupted) == 0,
        details={"corrupted_turns": corrupted, "total_checked": len(rows)},
    )


def check_message_range(conn: sqlite3.Connection) -> CheckResult:
    """Verify message_range spans match raw_messages count."""
    issues: list[dict] = []
    rows = conn.execute("SELECT turn_n, entry_json FROM ledger_entries").fetchall()
    for turn_n, entry_json in rows:
        entry = _parse(entry_json)
        if not entry:
            continue
        msg_range = entry.get("message_range", [])
        if msg_range and len(msg_range) >= 2:
            expected = msg_range[-1] - msg_range[0] + 1
            # Count actual raw messages that fall in this range
            actual = conn.execute(
                "SELECT COUNT(*) FROM raw_messages WHERE store_id >= ? AND store_id <= ?",
                (msg_range[0], msg_range[-1]),
            ).fetchone()[0]
            if actual != expected:
                issues.append({
                    "turn_n": turn_n,
                    "expected": expected,
                    "actual": actual,
                })
    return CheckResult(
        name="message_range",
        passed=len(issues) == 0,
        details={"issues": issues},
    )


# ---------------------------------------------------------------------------
# Fix engine (read-write)
# ---------------------------------------------------------------------------

def fix_completeness(conn: sqlite3.Connection) -> list[int]:
    """Fill missing fields with default values. Returns fixed turn numbers."""
    fixed: list[int] = []
    rows = conn.execute("SELECT turn_n, entry_json FROM ledger_entries").fetchall()
    for turn_n, entry_json in rows:
        entry = _parse(entry_json)
        if not entry:
            continue
        needs_fix = False
        for field in REQUIRED_FIELDS:
            if field not in entry or entry[field] is None:
                entry[field] = ALL_DEFAULTS[field]
                needs_fix = True
        if needs_fix:
            conn.execute(
                "UPDATE ledger_entries SET entry_json = ?, validated = 0 WHERE turn_n = ?",
                (json.dumps(entry, ensure_ascii=False), turn_n),
            )
            fixed.append(turn_n)
    return fixed


def fix_fts5_sync(conn: sqlite3.Connection) -> list[int]:
    """Rebuild FTS5: remove orphans, add missing. Returns fixed turn numbers."""
    fixed: list[int] = []

    # Remove orphans (contentless FTS5: use special INSERT)
    try:
        orphans = conn.execute(
            "SELECT rowid FROM concepts_fts WHERE rowid NOT IN "
            "(SELECT turn_n FROM ledger_entries)"
        ).fetchall()
        for (rowid,) in orphans:
            conn.execute(
                "INSERT INTO concepts_fts(concepts_fts, rowid, term, definition) "
                "VALUES('delete', ?, '', '')",
                (rowid,),
            )
    except sqlite3.OperationalError:
        pass

    # Add missing
    rows = conn.execute("SELECT turn_n, entry_json FROM ledger_entries").fetchall()
    for turn_n, entry_json in rows:
        entry = _parse(entry_json)
        if not entry:
            continue
        concepts = entry.get("concepts_and_definitions", []) or []
        if concepts:
            existing = conn.execute(
                "SELECT COUNT(*) FROM concepts_fts WHERE rowid = ?", (turn_n,)
            ).fetchone()[0]
            if existing == 0:
                for c in concepts:
                    if isinstance(c, dict):
                        conn.execute(
                            "INSERT INTO concepts_fts (rowid, term, definition) VALUES (?, ?, ?)",
                            (turn_n, c.get("term", ""), c.get("definition", "")),
                        )
                fixed.append(turn_n)
    return fixed


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(report: HealthReport, output_path: str | None, as_json: bool) -> None:
    """Write health report to file or stdout."""
    if as_json:
        data = {
            "session_id": report.session_id,
            "total_turns": report.total_turns,
            "total_raw": report.total_raw,
            "fixes_total": report.fixes_total,
            "fixed_turns": report.fixed_turns,
            "corrupted_turns": report.corrupted_turns,
            "checks": [
                {"name": c.name, "passed": c.passed, "details": c.details}
                for c in report.checks
            ],
        }
        output = json.dumps(data, indent=2, ensure_ascii=False)
    else:
        lines = [
            f"# Decohere Health Report — {report.session_id}",
            f"Turns: {report.total_turns} | Raw messages: {report.total_raw}",
            f"Fixes applied: {report.fixes_total}",
            "",
            "## Check Results",
            "",
        ]
        for c in report.checks:
            status = "✓ PASS" if c.passed else "✗ FAIL"
            lines.append(f"- **{c.name}**: {status}")
            for k, v in c.details.items():
                if isinstance(v, list) and v:
                    lines.append(f"  - {k}: {len(v)} issue(s)")
        if report.fixed_turns:
            lines.append(f"\n## Fixed Turns\n{', '.join(str(t) for t in report.fixed_turns)}")
        if report.corrupted_turns:
            lines.append(f"\n## Corrupted Turns (skipped)\n{', '.join(str(t) for t in report.corrupted_turns)}")
        output = "\n".join(lines)

    if output_path:
        p = Path(output_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(output, encoding="utf-8")
    else:
        print(output)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_health_check(
    session_id: str | None = None,
    hermes_home: str | None = None,
    fix: bool = False,
    output_path: str | None = None,
    as_json: bool = False,
) -> int:
    """Run all health checks. Returns exit code (0=pass, 1=issues found)."""
    # Resolve paths
    from plugins.context_engine.decohere.cli._shared import (
        resolve_hermes_home,
        resolve_session,
    )
    import os

    home_path = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    if hermes_home:
        home_path = hermes_home
    home = Path(home_path).expanduser()

    try:
        sid, db_path = resolve_session(home, session_id)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Open DB (read-write if fixing)
    if fix:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)

    try:
        # Run all checks
        checks = [
            check_completeness(conn),
            check_fts5_sync(conn),
            check_orphan_messages(conn),
            check_json_validity(conn),
            check_message_range(conn),
        ]

        # Fix if requested
        fixes_total = 0
        fixed_turns: list[int] = []
        if fix:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for c in checks:
                    if c.name == "completeness" and not c.passed:
                        ft = fix_completeness(conn)
                        fixed_turns.extend(ft)
                        c.fixes_applied = [f"filled {len(ft)} entries"]
                    elif c.name == "fts5_sync" and not c.passed:
                        ft = fix_fts5_sync(conn)
                        fixed_turns.extend(ft)
                        c.fixes_applied = [f"synced {len(ft)} entries"]
                conn.commit()
                fixes_total = len(set(fixed_turns))
                # Re-check after fixing
                checks = [
                    check_completeness(conn),
                    check_fts5_sync(conn),
                    check_orphan_messages(conn),
                    check_json_validity(conn),
                    check_message_range(conn),
                ]
            except Exception:
                conn.rollback()
                raise

        # Build report
        total_turns = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        total_raw = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
        corrupted = [d["turn_n"] for c in checks if c.name == "json_validity"
                     for d in c.details.get("corrupted_turns", [])]

        report = HealthReport(
            session_id=sid,
            total_turns=total_turns,
            total_raw=total_raw,
            checks=checks,
            fixes_total=fixes_total,
            fixed_turns=sorted(set(fixed_turns)),
            corrupted_turns=corrupted,
        )

        generate_report(report, output_path, as_json)

    finally:
        conn.close()

    # Exit code: 1 if any corruption found (unfixable)
    if corrupted:
        return 1
    all_pass = all(c.passed for c in checks)
    return 0 if all_pass else 1


def _parse(raw: str) -> dict | None:
    """Safe JSON parse. Returns None on corruption."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="Decohere data quality health check and auto-fix.",
    )
    p.add_argument("--session", help="Session ID (default: most recently modified)")
    p.add_argument("--home", help="Hermes home path")
    p.add_argument("--fix", action="store_true", help="Auto-fix issues where possible")
    p.add_argument("--output", help="Output report file path")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    args = p.parse_args()

    sys.exit(run_health_check(
        session_id=args.session,
        hermes_home=args.home,
        fix=args.fix,
        output_path=args.output,
        as_json=args.json,
    ))
