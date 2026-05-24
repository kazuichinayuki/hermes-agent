#!/usr/bin/env python3
"""Batch repost — retroactively fill all placeholder entries across all sessions.

Usage:
    python3 batch_repost.py [--dry-run] [--limit N] [--resume]

Scans all sessions in ~/.hermes/sessions/, finds entries without concepts,
and feeds them through the LLM posting pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path


import os

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
BATCH_SIZE = 3  # concurrent sessions
COOLDOWN = 0.5  # seconds between batches to avoid rate limits


def discover_sessions() -> list[Path]:
    """Return all decohere.db paths with placeholder entries (no concepts)."""
    sessions_dir = HERMES_HOME / "sessions"
    if not sessions_dir.is_dir():
        print("No sessions directory found.")
        return []

    results: list[Path] = []
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        db = sd / "decohere.db"
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(str(db))
            has_placeholder = False
            for (entry_json,) in conn.execute("SELECT entry_json FROM ledger_entries"):
                if not entry_json:
                    has_placeholder = True
                    break
                try:
                    entry = json.loads(entry_json)
                except (json.JSONDecodeError, TypeError):
                    has_placeholder = True
                    break
                concepts = entry.get("concepts_and_definitions") or []
                if not concepts:
                    has_placeholder = True
                    break
            conn.close()
            if has_placeholder:
                results.append(db)
        except Exception:
            continue
    return results


def session_summary(db_path: Path) -> dict:
    """Quick summary of a session for progress display."""
    conn = sqlite3.connect(str(db_path))
    total = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
    empty = conn.execute(
        "SELECT COUNT(*) FROM ledger_entries WHERE "
        "entry_json NOT LIKE '%concepts_and_definitions%' "
        "OR entry_json LIKE '%\"concepts_and_definitions\": []%' "
        "OR entry_json LIKE '%\"concepts_and_definitions\":()%'"
    ).fetchone()[0]
    fts = 0
    try:
        fts = conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0]
    except Exception:
        pass
    conn.close()
    return {
        "session_id": db_path.parent.name,
        "total": total,
        "empty": empty,
        "fts": fts,
    }


async def repost_session(db_path: Path) -> tuple[int, int]:
    """Repost all placeholder entries in one session. Returns (reposted, concepts_added)."""
    from plugins.context_engine.decohere.config import LedgerConfig
    from plugins.context_engine.decohere.core.poster import post_entry
    from plugins.context_engine.decohere.core.validator import validate_entry
    from plugins.context_engine.decohere.cli.repost_cmd import _rebuild_concepts_fts

    sid = db_path.parent.name

    # Read entries
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT turn_n, entry_json FROM ledger_entries ORDER BY turn_n"
    ).fetchall()

    # Find placeholders and collect raw messages
    tasks: list[tuple[int, list[dict]]] = []
    for turn_n, entry_json in rows:
        try:
            entry = json.loads(entry_json) if entry_json else {}
        except (json.JSONDecodeError, TypeError):
            entry = {}
        if entry.get("concepts_and_definitions"):
            continue  # already has concepts — skip
        msg_range = entry.get("message_range", [])
        if not msg_range or len(msg_range) < 2:
            continue
        msgs = conn.execute(
            "SELECT role, content, tool_name FROM raw_messages "
            "WHERE store_id >= ? AND store_id <= ? ORDER BY store_id",
            (msg_range[0], msg_range[-1]),
        ).fetchall()
        if not msgs:
            continue
        messages = []
        for role, content, tool_name in msgs:
            msg = {"role": role, "content": content}
            if tool_name:
                msg["tool_name"] = tool_name
            messages.append(msg)
        tasks.append((turn_n, messages))
    conn.close()

    if not tasks:
        return 0, 0

    # Load config
    config_path = HERMES_HOME / "config.yaml"
    aux_cfg, comp_cfg = LedgerConfig._read_config_static(config_path)
    config = LedgerConfig.from_aux_config(aux_cfg, compression=comp_cfg)

    posted = 0
    concepts_added = 0

    for turn_n, messages in tasks:
        try:
            raw = await post_entry(messages, config)
            result = validate_entry(raw)
        except Exception as e:
            print(f"    Turn {turn_n}: LLM call FAILED — {e}", file=sys.stderr)
            continue

        # Save
        conn2 = sqlite3.connect(str(db_path))
        conn2.execute("PRAGMA busy_timeout=30000")
        try:
            entry_json = json.dumps(result, ensure_ascii=False)
            conn2.execute(
                "UPDATE ledger_entries SET entry_json = ?, validated = 1 WHERE turn_n = ?",
                (entry_json, turn_n),
            )
            _rebuild_concepts_fts(conn2, turn_n, result)
            conn2.commit()
            n = len(result.get("concepts_and_definitions", []) or [])
            concepts_added += n
            posted += 1
        except Exception as e:
            print(f"    Turn {turn_n}: SAVE FAILED — {e}", file=sys.stderr)
        finally:
            conn2.close()

    return posted, concepts_added


async def _repost_batch(db_paths: list[Path]) -> list[tuple[Path, int, int]]:
    """Repost a batch of sessions concurrently."""
    coros = [repost_session(p) for p in db_paths]
    results = await asyncio.gather(*coros, return_exceptions=True)
    out = []
    for path, res in zip(db_paths, results):
        if isinstance(res, Exception):
            print(f"  {path.parent.name}: ERROR — {res}", file=sys.stderr)
            out.append((path, 0, 0))
        else:
            out.append((path, res[0], res[1]))
    return out


async def main():
    parser = argparse.ArgumentParser(description="Batch repost decohere placeholder entries")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--limit", type=int, default=0, help="Max sessions to process (0=all)")
    parser.add_argument("--resume", type=str, default="", help="Resume from session ID")
    args = parser.parse_args()

    print("Scanning sessions...", flush=True)
    all_dbs = discover_sessions()

    if args.resume:
        all_dbs = [p for p in all_dbs if p.parent.name >= args.resume]

    if args.limit and args.limit > 0:
        all_dbs = all_dbs[:args.limit]

    print(f"Found {len(all_dbs)} sessions with placeholder entries.\n")

    if args.dry_run:
        for db in all_dbs[:20]:
            s = session_summary(db)
            print(f"  {s['session_id']}: {s['empty']}/{s['total']} placeholders, {s['fts']} FTS rows")
        if len(all_dbs) > 20:
            print(f"  ... and {len(all_dbs) - 20} more")
        total_empty = sum(session_summary(db)["empty"] for db in all_dbs)
        print(f"\nTotal: {len(all_dbs)} sessions, ~{total_empty} entries to repost")
        return 0

    # Process in batches
    total_posted = 0
    total_concepts = 0
    t0 = time.monotonic()

    for i in range(0, len(all_dbs), BATCH_SIZE):
        batch = all_dbs[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(all_dbs) + BATCH_SIZE - 1) // BATCH_SIZE

        names = [p.parent.name for p in batch]
        print(f"[{batch_num}/{total_batches}] {', '.join(names)}", flush=True)

        results = await _repost_batch(batch)
        for path, posted, concepts in results:
            if posted:
                total_posted += posted
                total_concepts += concepts
                print(f"  ✓ {path.parent.name}: {posted} entry(ies), +{concepts} concepts",
                      flush=True)

        if i + BATCH_SIZE < len(all_dbs):
            await asyncio.sleep(COOLDOWN)

    elapsed = time.monotonic() - t0
    print(f"\n{'='*60}")
    print(f"Done in {elapsed:.0f}s. Reposted {total_posted} entries across {len(all_dbs)} sessions.")
    print(f"Total concepts added: {total_concepts}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
