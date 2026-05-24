"""Tests for hermes decohere export command."""

import json
import subprocess

HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [HERMES_BIN, "decohere", "export", *args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestExportCommand:
    def test_export_json(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--format", "json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert data["turn_count"] == 5

    def test_export_markdown(self, temp_decohere_db):
        r = _run(temp_decohere_db["hermes_home"], "--format", "md")
        assert r.returncode == 0
        assert "Decohere Ledger Export" in r.stdout

    def test_export_to_file(self, temp_decohere_db, tmp_path):
        out = tmp_path / "export.md"
        r = _run(temp_decohere_db["hermes_home"], "--format", "md", "--output", str(out))
        assert r.returncode == 0
        assert out.exists()
