"""Tests for hermes decohere sessions command."""

import json
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "sessions", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestSessionsCommand:
    def test_sessions_with_data(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"])
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert temp_decohere_db["session_id"] in r.stdout

    def test_sessions_json(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert isinstance(data, list) and len(data) >= 1

    def test_sessions_no_data(self, tmp_path):
        r = _run(str(tmp_path))
        assert r.returncode == 0
        assert "no sessions" in r.stdout.lower()

    def test_sessions_no_data_json(self, tmp_path):
        r = _run(str(tmp_path), "--json")
        assert r.returncode == 0
        assert json.loads(r.stdout) == []
