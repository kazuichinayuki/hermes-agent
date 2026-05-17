"""hermes decohere repost — retroactively fill placeholder entries.

Reads raw_messages from any session, feeds them through the LLM posting
pipeline, and writes back completed entries.  No active session needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from ._shared import (
    open_db,
    parse_json_field,
    resolve_hermes_home,
    resolve_session,
)


def _rebuild_concepts_fts(
    conn, turn_n: int, result: dict
) -> None:
    """Rebuild FTS5 rows for one turn. Handles both contentless (v1) and regular (v2).

    v2 rowid convention: turn_n * 1000 + concept_index.
    """
    concepts = result.get("concepts_and_definitions", []) or []
    base = turn_n * 1000

    # Delete all concepts for this turn (v2: bounded range)
    try:
        conn.execute(
            "DELETE FROM concepts_fts WHERE rowid >= ? AND rowid < ?",
            (base, base + 1000),
        )
    except Exception:
        # Contentless FTS5 (v1) — delete one-by-one
        for idx in range(len(concepts) + 1):
            try:
                conn.execute(
                    "INSERT INTO concepts_fts(concepts_fts, rowid, term, definition) "
                    "VALUES('delete', ?, '', '')",
                    (turn_n + idx,),
                )
            except Exception:
                pass

    for idx, c in enumerate(concepts):
        if not isinstance(c, dict) or not c.get("term"):
            continue
        term = c["term"]
        definition = c.get("definition", "")
        rowid = base + idx
        try:
            # Regular FTS5 (v2)
            conn.execute(
                "INSERT INTO concepts_fts(rowid, term, definition) VALUES (?, ?, ?)",
                (rowid, term, definition),
            )
        except Exception:
            # Contentless FTS5 (v1)
            conn.execute(
                "INSERT INTO concepts_fts(concepts_fts, rowid, term, definition) "
                "VALUES('replace', ?, ?, ?)",
                (turn_n, term, definition),
            )


def register_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "repost",
        help="Retroactively fill placeholder entries with LLM content",
        description="Read raw_messages from any session and run LLM posting to fill entries.",
    )
    parser.add_argument("--profile", help="Use a specific profile")
    parser.add_argument("--home", help="Directly specify hermes home path")
    parser.add_argument("--session", help="Session ID (default: most recently modified)")
    parser.add_argument("--turn", type=int, help="Repost a specific turn only")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be reposted without doing it")
    parser.add_argument("--output", help="Write LLM result to JSON file instead of DB")


def run(args) -> int:
    try:
        home = resolve_hermes_home(profile=args.profile, home=args.home)
        sid, db_path = resolve_session(home, args.session)
        return _repost(home, sid, db_path, args.turn, args.dry_run,
                      getattr(args, "output", None))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _repost(home: Path, sid: str, db_path: Path,
            specific_turn: int | None, dry_run: bool, output_file: str | None = None) -> int:
    conn = open_db(db_path, readonly=True)

    # Find placeholder entries
    rows = conn.execute(
        "SELECT turn_n, entry_json FROM ledger_entries ORDER BY turn_n"
    ).fetchall()

    candidates = []
    all_raw = {}  # turn_n → messages
    for turn_n, entry_json in rows:
        entry = parse_json_field(entry_json)
        if not entry:
            continue
        if specific_turn is not None and turn_n != specific_turn:
            continue
        is_placeholder = not entry.get("concepts_and_definitions")
        if is_placeholder:
            msg_range = entry.get("message_range", [])
            if msg_range and len(msg_range) >= 2:
                msgs = conn.execute(
                    "SELECT role, content, tool_name FROM raw_messages "
                    "WHERE store_id >= ? AND store_id <= ? ORDER BY store_id",
                    (msg_range[0], msg_range[-1]),
                ).fetchall()
                if msgs:
                    candidates.append(turn_n)
                    all_raw[turn_n] = msgs

    conn.close()

    if not candidates:
        print(f"Session {sid}: no placeholder entries to repost.")
        return 0

    if dry_run:
        print(f"Session {sid}: {len(candidates)} placeholder(s) would be reposted:")
        for tn in candidates:
            msgs = all_raw.get(tn, [])
            print(f"  Turn {tn}: {len(msgs)} messages")
        return 0

    # Load config — respect the user's configured compression model, don't hardcode
    from plugins.context_engine.decohere.config import LedgerConfig
    from plugins.context_engine.decohere.core.poster import post_entry
    from plugins.context_engine.decohere.core.validator import validate_entry

    config_path = home / "config.yaml"
    aux_cfg, comp_cfg = LedgerConfig._read_config_static(config_path)
    config = LedgerConfig.from_aux_config(aux_cfg, compression=comp_cfg)

    posted = 0
    for turn_n in candidates:
        msgs = all_raw.get(turn_n, [])
        messages = []
        for role, content, tool_name in msgs:
            msg = {"role": role, "content": content}
            if tool_name:
                msg["tool_name"] = tool_name
            messages.append(msg)

        print(f"  Turn {turn_n}: posting {len(messages)} messages...", end=" ", flush=True)

        try:
            async def _post():
                raw = await post_entry(messages, config)
                return validate_entry(raw)

            result = asyncio.run(_post())
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        # Output mode: write to file instead of DB
        if output_file:
            out_path = Path(output_file).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            concepts = len(result.get("concepts_and_definitions", []) or [])
            print(f"OK ({concepts} concepts) → {out_path}")
            posted += 1
            continue

        # Save back
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            entry_json = json.dumps(result, ensure_ascii=False)
            conn.execute(
                "UPDATE ledger_entries SET entry_json = ?, validated = 1 WHERE turn_n = ?",
                (entry_json, turn_n),
            )
            # Rebuild FTS5 — handle both contentless (v1) and regular (v2)
            _rebuild_concepts_fts(conn, turn_n, result)
            conn.commit()
            concepts = len(result.get("concepts_and_definitions", []) or [])
            print(f"OK ({concepts} concepts)")
            posted += 1
        except Exception as e:
            print(f"SAVE FAILED: {e}")
        finally:
            conn.close()

    print(f"\n✓ Reposted {posted}/{len(candidates)} entries in session {sid}")
    return 0
