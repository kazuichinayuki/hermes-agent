"""Tests for hermes decohere list command."""

import json
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "list", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestListCommand:
    def test_list_all(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"])
        assert r.returncode == 0

    def test_list_with_limit(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--limit", "3")
        assert r.returncode == 0

    def test_list_json(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, list) and len(data) >= 1

    def test_nonexistent_session(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--session", "no_such")
        assert r.returncode == 1
