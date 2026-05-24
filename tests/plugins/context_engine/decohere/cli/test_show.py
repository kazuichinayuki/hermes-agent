"""Tests for hermes decohere show command."""

import json
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "show", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestShowCommand:
    def test_show_turn1(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--turn", "1")
        assert r.returncode == 0
        assert "vision debug" in r.stdout

    def test_show_layer_l1(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--turn", "3", "--layer", "l1")
        assert r.returncode == 0

    def test_show_json(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--turn", "1", "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["turn_n"] == 1

    def test_show_nonexistent(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--turn", "999")
        assert r.returncode == 1
