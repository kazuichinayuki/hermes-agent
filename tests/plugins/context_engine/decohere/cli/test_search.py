"""Tests for hermes decohere search command."""

import json
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "search", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestSearchCommand:
    def test_search_concepts(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "context window")
        assert r.returncode == 0
        assert "context window" in r.stdout.lower()

    def test_search_no_results(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "xyznonexistent123")
        assert r.returncode == 0

    def test_search_json(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "decohere", "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, list)
