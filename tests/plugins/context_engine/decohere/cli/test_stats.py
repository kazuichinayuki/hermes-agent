"""Tests for hermes decohere stats command."""

import json
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "stats", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestStatsCommand:
    def test_stats_table(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"])
        assert r.returncode == 0
        assert "Entries" in r.stdout

    def test_stats_json(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["entry_count"] == 5
        assert data["raw_count"] == 50
        assert data["validated"] == 3
        assert data["pending"] == 2
