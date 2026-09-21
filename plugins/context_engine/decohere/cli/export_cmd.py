"""hermes decohere export — export session data to JSON / Markdown / YAML."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ._shared import (
    format_timestamp,
    open_db,
    parse_json_field,
    resolve_hermes_home,
    resolve_session,
)


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "export",
        help="Export session data to JSON / Markdown / YAML",
        description="Export all ledger entries to a file or stdout.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--format", choices=["json", "md", "yaml"], default="md",
                       help="Output format (default: md)")
    parser.add_argument("--output", help="Output file path (default: stdout)")
    parser.add_argument(
        "--layer",
        choices=["l1", "l2", "full", "trajectory", "dataset"],
        default="full",
        help="Detail level or target layer: l1, l2, full (ledger), trajectory (ShareGPT records), dataset (typed decision points)",
    )


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)
        conn = open_db(db_path, readonly=True)

        if args.layer == "trajectory":
            try:
                rows = conn.execute(
                    "SELECT turn_n, model, completed, trajectory_json, metadata_json, created_at "
                    "FROM trajectories ORDER BY turn_n"
                ).fetchall()
            except Exception:
                rows = []
            conn.close()

            if not rows:
                print(f"Session {sid}: no trajectories found")
                return 0

            output = _format_trajectories(sid, rows, args.format)
            total_items = len(rows)
            label = "trajectories"

        elif args.layer == "dataset":
            try:
                rows = conn.execute(
                    "SELECT turn_n, decision_type, state_json, decision_json, target_label, created_at "
                    "FROM decision_points ORDER BY id"
                ).fetchall()
            except Exception:
                rows = []
            conn.close()

            if not rows:
                print(f"Session {sid}: no decision points found")
                return 0

            output = _format_dataset(sid, rows, args.format)
            total_items = len(rows)
            label = "decision points"

        else:
            rows = conn.execute(
                "SELECT turn_n, entry_json, posted_at, validated "
                "FROM ledger_entries ORDER BY turn_n"
            ).fetchall()
            conn.close()

            if not rows:
                print(f"Session {sid}: no entries to export")
                return 0

            if args.format == "json":
                output = _format_json(sid, rows, args.layer)
            elif args.format == "yaml":
                output = _format_yaml(sid, rows, args.layer)
            else:
                output = _format_markdown(sid, rows, args.layer)
            total_items = len(rows)
            label = "turns"

        if args.output:
            out_path = Path(args.output).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output, encoding="utf-8")
            print(f"✓ Exported {total_items} {label} → {out_path} "
                  f"({format_size(len(output.encode('utf-8')))})")
        else:
            print(output)

        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _format_json(sid: str, rows, layer: str) -> str:
    turns = []
    for r in rows:
        # r = (turn_n, entry_json, posted_at, validated)
        entry = parse_json_field(r[1])
        turn_data = {
            "turn_n": r[0],
            "posted_at": r[2],
            "validated": bool(r[3]),
        }
        if entry:
            if layer == "l1":
                turn_data["entry"] = {
                    "relevant_metadata": entry.get("relevant_metadata"),
                    "reference_documentation": entry.get("reference_documentation"),
                    "user_intent": entry.get("user_intent"),
                }
            elif layer == "l2":
                turn_data["entry"] = {
                    k: entry.get(k) for k in (
                        "concepts_and_definitions", "narrative",
                        "decisions_and_rationale", "procedures",
                        "insights_and_learnings",
                    ) if k in entry
                }
            else:
                turn_data["entry"] = entry
        turns.append(turn_data)

    result = {
        "session_id": sid,
        "exported_at": format_timestamp(None).replace("—", json.dumps(None)),
        "turn_count": len(rows),
        "turns": turns,
    }
    import datetime as dt_mod
    result["exported_at"] = dt_mod.datetime.now(dt_mod.timezone.utc).isoformat()
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def _format_yaml(sid: str, rows, layer: str) -> str:
    try:
        import yaml
    except ImportError:
        return _format_json(sid, rows, layer)
    data = json.loads(_format_json(sid, rows, layer))
    return yaml.dump(data, allow_unicode=True, default_flow_style=False)


def _format_markdown(sid: str, rows, layer: str) -> str:
    import datetime as dt_mod
    now = dt_mod.datetime.now(dt_mod.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# Decohere Ledger Export — {sid}",
        f"Exported: {now} | {len(rows)} turns",
        "",
    ]

    for r in rows:
        # r = (turn_n, entry_json, posted_at, validated)
        entry = parse_json_field(r[1], {})
        turn_n = r[0]
        tools = entry.get("tools", []) or []
        files = entry.get("files_touched", []) or []
        task = entry.get("relevant_metadata", {}).get("task", "")

        lines.append(f"## Turn {turn_n}")
        lines.append(f"**Tools:** {', '.join(tools) if tools else 'none'}")
        lines.append(f"**Files:** {', '.join(files) if files else 'none'}")
        if task:
            lines.append(f"**Task:** {task}")
        lines.append("")

        if layer in ("l2", "full"):
            # Concepts
            concepts = entry.get("concepts_and_definitions", []) or []
            if concepts:
                lines.append("### Concepts")
                for c in concepts:
                    if isinstance(c, dict):
                        lines.append(f"- **{c.get('term', '?')}:** {c.get('definition', '')}")
                lines.append("")

            # Narrative
            narrative = entry.get("narrative", {}) or {}
            if narrative.get("summary"):
                lines.append("### Narrative")
                lines.append(narrative["summary"])
                lines.append("")

            # Decisions
            decisions = entry.get("decisions_and_rationale", []) or []
            if decisions:
                lines.append("### Decisions")
                for d in decisions:
                    if isinstance(d, dict):
                        lines.append(f"- **{d.get('decision', '')}**")
                        if d.get("rationale"):
                            lines.append(f"  → {d['rationale']}")
                lines.append("")

            # Insights
            insights = entry.get("insights_and_learnings", []) or []
            if insights:
                lines.append("### Insights")
                for i in insights:
                    if isinstance(i, str):
                        lines.append(f"- {i}")
                lines.append("")

        # User intent
        intent = entry.get("user_intent", "")
        if intent and layer in ("l2", "full"):
            lines.append("### User Intent")
            lines.append(intent)
            lines.append("")

        if layer == "full":
            cr = entry.get("critical_reflection", {}) or {}
            if any(cr.values()):
                lines.append("### Critical Reflection")
                improvements = cr.get("improvement_directions", []) or []
                if improvements:
                    for imp in improvements:
                        lines.append(f"- {imp}")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _format_trajectories(sid: str, rows, fmt: str) -> str:
    records = []
    for r in rows:
        traj = parse_json_field(r[3], {})
        meta = parse_json_field(r[4], {})
        records.append({
            "session_id": sid,
            "turn_n": r[0],
            "model": r[1],
            "completed": bool(r[2]),
            "trajectory": traj,
            "metadata": meta,
            "created_at": r[5],
        })

    if fmt == "json":
        return json.dumps(records, indent=2, ensure_ascii=False)
    elif fmt == "yaml":
        try:
            import yaml
            return yaml.dump(records, allow_unicode=True, default_flow_style=False)
        except ImportError:
            return json.dumps(records, indent=2, ensure_ascii=False)
    else:
        lines = [f"# Trajectories — Session {sid}", f"Total: {len(records)} records", ""]
        for rec in records:
            lines.append(f"## Turn {rec['turn_n']} (Model: {rec['model'] or 'unknown'}, Completed: {rec['completed']})")
            convs = rec.get("trajectory", {}).get("conversations", [])
            for c in convs:
                role = c.get("from", "unknown").upper()
                value = c.get("value", "")
                lines.append(f"### {role}")
                lines.append(value)
                lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)


def _format_dataset(sid: str, rows, fmt: str) -> str:
    samples = []
    for r in rows:
        state = parse_json_field(r[2], {})
        decision = parse_json_field(r[3], {})
        samples.append({
            "session_id": sid,
            "turn_n": r[0],
            "decision_type": r[1],
            "state": state,
            "decision": decision,
            "target_label": r[4],
            "created_at": r[5],
        })

    if fmt == "json":
        return json.dumps(samples, indent=2, ensure_ascii=False)
    elif fmt == "yaml":
        try:
            import yaml
            return yaml.dump(samples, allow_unicode=True, default_flow_style=False)
        except ImportError:
            return json.dumps(samples, indent=2, ensure_ascii=False)
    else:
        lines = [f"# Typed Decision Dataset — Session {sid}", f"Total: {len(samples)} decision points", ""]
        for s in samples:
            lines.append(f"### Turn {s['turn_n']} | Type: `{s['decision_type']}` | Target: `{s['target_label']}`")
            lines.append(f"- **State Tools:** {', '.join(s.get('state', {}).get('active_tools', []))}")
            lines.append(f"- **Decision:** `{json.dumps(s.get('decision', {}), ensure_ascii=False)}`")
            lines.append("")
        return "\n".join(lines)

