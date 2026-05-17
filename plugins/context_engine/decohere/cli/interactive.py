"""Interactive terminal UIs for decohere CLI commands.

prompt_toolkit-based list browsers and detail viewers.
All accept a read-only DB connection and never modify data
(editing is routed through the command's write path).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable


def _has_prompt_toolkit() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
        return True
    except ImportError:
        return False


def interactive_list(
    conn: sqlite3.Connection,
    session_id: str,
    limit: int = 50,
) -> None:
    """Interactive turn list browser.

    Keys: ↑↓ navigate, Enter view details, / search, q quit.
    """
    if not _has_prompt_toolkit():
        print("prompt_toolkit not installed. Install with: pip install prompt_toolkit")
        return

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import to_formatted_text
    from ._shared import parse_json_field, format_timestamp

    # Load data
    rows = conn.execute(
        "SELECT turn_n, entry_json, posted_at, validated "
        "FROM ledger_entries ORDER BY turn_n ASC LIMIT ?",
        (limit,),
    ).fetchall()

    entries = []
    for r in rows:
        entry = parse_json_field(r[1], {})
        task = (entry.get("relevant_metadata") or {}).get("task", "") or ""
        tools_raw = entry.get("tools", []) or []
        tool_names = [t.get("name", t) if isinstance(t, dict) else str(t)
                       for t in tools_raw]
        tools_str = ", ".join(tool_names[:3])
        concepts = entry.get("concepts_and_definitions")
        n_concepts = len(concepts) if isinstance(concepts, list) else 0
        validated = bool(r[3])
        entries.append({
            "turn_n": r[0],
            "task": task,
            "tools": tools_str,
            "concepts": n_concepts,
            "validated": validated,
            "posted_at": r[2],
            "entry": entry,
        })

    selected = [0]
    search_mode = [False]
    search_text = [""]

    def _render():
        lines = [("bold", f"Session: {session_id}  —  {len(entries)} turns\n")]
        lines.append(("", "  ↑↓ nav  Enter view  / search  q quit\n"))
        lines.append(("", "─" * 70 + "\n"))

        if search_mode[0]:
            lines.append(("bold", f"  Search: {search_text[0]}_ \n\n"))

        for i, e in enumerate(entries):
            # Filter by search
            if search_text[0]:
                q = search_text[0].lower()
                if (q not in e["task"].lower()
                        and q not in e["tools"].lower()):
                    continue

            prefix = "▸" if i == selected[0] else " "
            status = "✓" if e["validated"] else "◌"
            c_str = str(e["concepts"]) if e["concepts"] > 0 else "·"
            task = e["task"][:40] if e["task"] else "—"

            line = (
                f"  {prefix} T{e['turn_n']:<3} {status} "
                f"c={c_str:<2} {task:<42} "
                f"{e['tools'][:30]}\n"
            )
            lines.append(("reverse" if i == selected[0] else "", line))

        lines.append(("", "\n─" * 70 + "\n"))
        lines.append(("", f"  T{entries[selected[0]]['turn_n']} | "
                         f"{entries[selected[0]]['task'][:50]}"))
        return to_formatted_text(lines)

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        selected[0] = max(0, selected[0] - 1)

    @kb.add("down")
    def _down(event):
        selected[0] = min(len(entries) - 1, selected[0] + 1)

    @kb.add("q")
    def _quit(event):
        if search_mode[0]:
            search_mode[0] = False
            search_text[0] = ""
        else:
            event.app.exit()

    @kb.add("enter")
    def _enter(event):
        e = entries[selected[0]]
        interactive_show_entry(e["entry"], e["turn_n"], e["validated"])
        event.app.invalidate()

    @kb.add("/")
    def _search(event):
        search_mode[0] = True
        search_text[0] = ""

    @kb.add("<any>")
    def _any(event):
        if search_mode[0] and event.data and len(event.data) == 1:
            if event.data.isprintable():
                search_text[0] += event.data
        elif search_mode[0] and event.data == "backspace":
            search_text[0] = search_text[0][:-1]

    content = FormattedTextControl(_render)
    window = Window(content=content)
    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=True,
    )
    app.run()


def interactive_show_entry(
    entry: dict,
    turn_n: int,
    validated: bool = False,
) -> None:
    """Full-screen detail view of a single entry.

    Keys: q back.
    """
    if not _has_prompt_toolkit():
        return

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import to_formatted_text

    # Build detail lines
    lines = []

    status = "✓ validated" if validated else "◌ pending"
    lines.append(("bold", f"── Turn {turn_n} ── {status} " + "─" * 40 + "\n\n"))

    # L1: Spec
    tools = entry.get("tools", []) or []
    if tools:
        tool_names = [t.get("name", str(t)) if isinstance(t, dict) else str(t)
                       for t in tools]
        lines.append(("bold", "🔧 Tools\n"))
        lines.append(("", f"  {', '.join(tool_names)}\n\n"))

    files = entry.get("files_touched", []) or []
    if files:
        lines.append(("bold", "📁 Files\n"))
        for f in files:
            lines.append(("", f"  {f}\n"))
        lines.append(("", "\n"))

    refs = entry.get("reference_documentation", []) or []
    if refs:
        lines.append(("bold", "📚 References\n"))
        for r in refs:
            if isinstance(r, dict):
                lines.append(("", f"  • {r.get('source', r.get('title', str(r)))}\n"))
        lines.append(("", "\n"))

    task = (entry.get("relevant_metadata") or {}).get("task", "")
    if task:
        lines.append(("bold", "📋 Task\n"))
        lines.append(("", f"  {task}\n\n"))

    # L2: Proc
    concepts = entry.get("concepts_and_definitions")
    if concepts and isinstance(concepts, list) and len(concepts) > 0:
        lines.append(("bold", f"💡 Concepts ({len(concepts)})\n"))
        for c in concepts:
            if isinstance(c, dict):
                lines.append(("bold", f"  • {c.get('term', '?')}\n"))
                lines.append(("", f"    {c.get('definition', '')}\n"))
        lines.append(("", "\n"))

    narrative = entry.get("narrative", {}) or {}
    if narrative.get("summary"):
        lines.append(("bold", "📖 Narrative\n"))
        lines.append(("", f"  {narrative['summary']}\n\n"))

    intent = entry.get("user_intent", "")
    if intent:
        lines.append(("bold", "🎯 User Intent\n"))
        lines.append(("", f"  {intent}\n\n"))

    decisions = entry.get("decisions_and_rationale", []) or []
    if decisions:
        lines.append(("bold", "🔀 Decisions\n"))
        for d in decisions:
            if isinstance(d, dict):
                lines.append(("", f"  • {d.get('decision', '')}\n"))
                if d.get("rationale"):
                    lines.append(("", f"    → {d['rationale']}\n"))
        lines.append(("", "\n"))

    insights = entry.get("insights_and_learnings", []) or []
    if insights:
        lines.append(("bold", "✨ Insights\n"))
        for i in insights:
            lines.append(("", f"  • {i}\n"))
        lines.append(("", "\n"))

    # Critical reflection
    cr = entry.get("critical_reflection", {}) or {}
    if any(cr.values()):
        lines.append(("bold", "🔍 Critical Reflection\n"))
        for field in ("ignored_perspectives", "logical_gaps", "improvement_directions"):
            items = cr.get(field, []) or []
            if items:
                lines.append(("bold", f"  {field}:\n"))
                for item in items:
                    lines.append(("", f"    • {item}\n"))
        lines.append(("", "\n"))

    lines.append(("", "─" * 70 + "\n"))
    lines.append(("", "  q back\n"))

    formatted = to_formatted_text(lines)

    kb = KeyBindings()

    @kb.add("q")
    def _quit(event):
        event.app.exit()

    content = FormattedTextControl(formatted)
    window = Window(content=content)
    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=True,
    )
    app.run()


def interactive_knowledge_sources(hermes_home: Path) -> None:
    """Interactive knowledge source selector with checkbox toggling.

    Keys: space toggle, Enter confirm, a select all, n deselect all, q cancel.
    """
    if not _has_prompt_toolkit():
        print("prompt_toolkit not installed. Install with: pip install prompt_toolkit")
        return

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import to_formatted_text

    # Scan sessions for concepts
    sessions_dir = hermes_home / "sessions"
    sources: list[dict] = []

    if sessions_dir.is_dir():
        for sd in sorted(sessions_dir.iterdir(), reverse=True)[:30]:
            if not sd.is_dir():
                continue
            db = sd / "decohere.db"
            if not db.exists():
                continue
            try:
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                rows = conn.execute(
                    "SELECT turn_n, entry_json FROM ledger_entries ORDER BY turn_n"
                ).fetchall()
                conn.close()

                concepts = []
                import json
                for turn_n, entry_json in rows:
                    entry = json.loads(entry_json) if entry_json else {}
                    c_list = entry.get("concepts_and_definitions", []) or []
                    for c in c_list:
                        if isinstance(c, dict) and c.get("term"):
                            concepts.append({
                                "turn_n": turn_n,
                                "term": c["term"],
                                "selected": False,
                            })

                if concepts:
                    sources.append({
                        "session_id": sd.name,
                        "concepts": concepts,
                        "expanded": False,
                    })
            except Exception:
                pass

    if not sources:
        print("No sessions with concepts found.")
        return

    selected_idx = [0]
    mode = [0]  # 0=nav, 1=confirm

    def _render():
        lines = []
        lines.append(("bold", "Knowledge Sources — space toggle  Enter confirm  q cancel\n"))
        lines.append(("", "─" * 60 + "\n"))

        flat: list[tuple[int, int, dict]] = []  # (source_idx, concept_idx, concept)
        for si, s in enumerate(sources):
            lines.append(("bold", f"\n  [{'✓' if all(c['selected'] for c in s['concepts']) and s['concepts'] else ' '}] "
                                  f"{s['session_id'][:40]} "
                                  f"({len(s['concepts'])} concepts)\n"))
            for ci, c in enumerate(s["concepts"]):
                flat.append((si, ci, c))
                marker = "▸" if (si, ci) == _flat_pos(selected_idx[0], sources) else " "
                chk = "[✓]" if c["selected"] else "[ ]"
                prefix = "reverse" if (si, ci) == _flat_pos(selected_idx[0], sources) else ""
                lines.append((prefix, f"  {marker}    {chk} T{c['turn_n']:<3} {c['term'][:45]}\n"))

        total_selected = sum(
            1 for s in sources for c in s["concepts"] if c["selected"]
        )
        lines.append(("bold", f"\n  Selected: {total_selected} concepts "
                              f"from {sum(1 for s in sources if any(c['selected'] for c in s['concepts']))} sessions\n"))
        lines.append(("", "  space toggle  a all  n none  Enter import  q cancel"))

        return to_formatted_text(lines)

    def _flat_pos(idx, srcs):
        """Convert flat index to (source_idx, concept_idx)."""
        pos = 0
        for si, s in enumerate(srcs):
            for ci, c in enumerate(s["concepts"]):
                if pos == idx:
                    return (si, ci)
                pos += 1
        return (0, 0)

    def _flat_count(srcs):
        return sum(len(s["concepts"]) for s in srcs)

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        selected_idx[0] = max(0, selected_idx[0] - 1)

    @kb.add("down")
    def _down(event):
        selected_idx[0] = min(_flat_count(sources) - 1, selected_idx[0] + 1)

    @kb.add("space")
    def _toggle(event):
        si, ci = _flat_pos(selected_idx[0], sources)
        sources[si]["concepts"][ci]["selected"] = not sources[si]["concepts"][ci]["selected"]

    @kb.add("a")
    def _all(event):
        for s in sources:
            for c in s["concepts"]:
                c["selected"] = True

    @kb.add("n")
    def _none(event):
        for s in sources:
            for c in s["concepts"]:
                c["selected"] = False

    @kb.add("enter")
    def _confirm(event):
        mode[0] = 1
        event.app.exit()

    @kb.add("q")
    def _cancel(event):
        mode[0] = -1
        event.app.exit()

    content = FormattedTextControl(_render)
    window = Window(content=content)
    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=True,
    )
    app.run()

    if mode[0] == 1:
        # Import selected concepts
        _do_import(hermes_home, sources)
    elif mode[0] == -1:
        print("Cancelled.")


def _do_import(hermes_home: Path, sources: list[dict]) -> None:
    """Import selected concepts into shared knowledge store."""
    from plugins.context_engine.decohere.knowledge.shared_store import SharedStore

    store = SharedStore(hermes_home)
    count = 0
    try:
        for s in sources:
            for c in s["concepts"]:
                if c["selected"]:
                    store.add_concept(
                        term=c["term"],
                        definition="",
                        source_session=s["session_id"],
                        source_turn=c["turn_n"],
                        imported_by="user",
                    )
                    count += 1
    finally:
        store.close()

    print(f"✓ {count} concepts imported to shared knowledge.")
    print(f"  Use 'hermes decohere knowledge config' to toggle injection.")


def interactive_sessions(hermes_home: Path) -> int:
    """Interactive session browser — list sessions, pick one to explore.

    Returns 0 on success, 1 on error.
    """
    if not _has_prompt_toolkit():
        print("prompt_toolkit not installed. Install with: pip install prompt_toolkit")
        return 1

    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout, HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.formatted_text import to_formatted_text
    from ._shared import format_size, format_relative_time

    # Scan sessions
    sessions_dir = hermes_home / "sessions"
    sessions = []

    if sessions_dir.is_dir():
        for sd in sorted(sessions_dir.iterdir(), reverse=True)[:50]:
            if not sd.is_dir():
                continue
            db = sd / "decohere.db"
            if not db.exists():
                continue
            try:
                import sqlite3
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                turns = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
                msgs = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
                try:
                    concepts = conn.execute("SELECT COUNT(*) FROM concepts_fts").fetchone()[0]
                except sqlite3.OperationalError:
                    concepts = 0
                last = conn.execute("SELECT MAX(posted_at) FROM ledger_entries").fetchone()[0]
                conn.close()
                sessions.append({
                    "session_id": sd.name,
                    "turns": turns,
                    "msgs": msgs,
                    "concepts": concepts,
                    "last": last,
                    "size": db.stat().st_size,
                })
            except Exception:
                pass

    sessions.sort(key=lambda x: x.get("last", 0) or 0, reverse=True)

    selected = [0]

    def _render():
        lines = [("bold", f"Decohere Sessions — {hermes_home}\n")]
        lines.append(("", "  ↑↓ nav  Enter open  / search  q quit\n"))
        lines.append(("", "─" * 70 + "\n"))

        if not sessions:
            lines.append(("", "  (no sessions with decohere data)\n"))
            return to_formatted_text(lines)

        for i, s in enumerate(sessions):
            prefix = "▸" if i == selected[0] else " "
            sid = s["session_id"][:30]
            c_str = str(s["concepts"]) if s["concepts"] > 0 else "·"
            t_str = str(s["turns"]) if s["turns"] > 0 else "·"
            size_str = format_size(s["size"])
            time_str = format_relative_time(s.get("last"))
            line = (
                f"  {prefix} {sid:<32} T={t_str:>3} C={c_str:>3} "
                f"{size_str:>8}  {time_str}\n"
            )
            lines.append(("reverse" if i == selected[0] else "", line))

        lines.append(("", f"\n─" * 70 + "\n"))
        lines.append(("", f"  {len(sessions)} session(s)  "
                         f"Enter=open  q=quit"))
        return to_formatted_text(lines)

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        if sessions:
            selected[0] = max(0, selected[0] - 1)

    @kb.add("down")
    def _down(event):
        if sessions:
            selected[0] = min(len(sessions) - 1, selected[0] + 1)

    @kb.add("q")
    def _quit(event):
        event.app.exit()

    @kb.add("enter")
    def _enter(event):
        if not sessions:
            return
        s = sessions[selected[0]]
        db = sessions_dir / s["session_id"] / "decohere.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            interactive_list(conn, s["session_id"], limit=50)
        finally:
            conn.close()
        event.app.invalidate()

    content = FormattedTextControl(_render)
    window = Window(content=content)
    app = Application(
        layout=Layout(HSplit([window])),
        key_bindings=kb,
        full_screen=True,
    )
    app.run()
    return 0
