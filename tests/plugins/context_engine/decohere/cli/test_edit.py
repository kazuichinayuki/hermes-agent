"""Tests for hermes decohere edit command."""

import json
import sqlite3
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "edit", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestEditCommand:
    def test_edit_user_intent(self, temp_decohere_db):
        r = _run(
            temp_decohere_db["hermes_home"],
            "--turn", "1", "--field", "user_intent",
            "--value", '"Changed intent"', "--confirm",
        )
        assert r.returncode == 0
        conn = sqlite3.connect(str(temp_decohere_db["db_path"]))
        row = conn.execute("SELECT entry_json FROM ledger_entries WHERE turn_n=1").fetchone()
        assert json.loads(row[0])["user_intent"] == "Changed intent"
        conn.close()

    def test_edit_nonexistent_turn(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"],
                 "--turn", "999", "--field", "user_intent", "--value", '"x"', "--confirm")
        assert r.returncode == 1

    def test_edit_nonexistent_field(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"],
                 "--turn", "1", "--field", "no_such", "--value", '"x"', "--confirm")
        assert r.returncode == 1

    def test_edit_sets_validated_false(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"],
                 "--turn", "1", "--field", "user_intent", "--value", '"updated"', "--confirm")
        assert r.returncode == 0
        conn = sqlite3.connect(str(temp_decohere_db["db_path"]))
        row = conn.execute("SELECT validated FROM ledger_entries WHERE turn_n=1").fetchone()
        assert row[0] == 0
        conn.close()
