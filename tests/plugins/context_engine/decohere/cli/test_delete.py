"""Tests for hermes decohere delete command."""

import sqlite3
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "delete", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestDeleteCommand:
    def test_delete_turn5(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--turn", "5", "--confirm")
        assert r.returncode == 0
        conn = sqlite3.connect(str(temp_decohere_db["db_path"]))
        count = conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0]
        assert count == 4
        conn.close()

    def test_delete_nonexistent(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--turn", "999", "--confirm")
        assert r.returncode == 1
