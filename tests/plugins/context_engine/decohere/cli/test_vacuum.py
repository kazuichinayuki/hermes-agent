"""Tests for hermes decohere vacuum command."""

import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "vacuum", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestVacuumCommand:
    def test_vacuum_dry_run(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--dry-run")
        assert r.returncode == 0

    def test_vacuum_confirm(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--confirm")
        assert r.returncode == 0
