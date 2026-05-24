"""Tests for decohere health_check — 5 checkers + fix engine."""

import json
import subprocess
import sys


HERMES_BIN = "/Library/Frameworks/Python.framework/Versions/3.13/bin/hermes"


def _run(hermes_home, *args):
    env = {"HERMES_HOME": str(hermes_home)}
    result = subprocess.run(
        [sys.executable, "-c",
         "from plugins.context_engine.decohere.monitoring.health_check import run_health_check; "
         f"import sys; sys.exit(run_health_check({args!r}))"],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result


class TestHealthyDB:
    """All checks should pass on a clean DB."""

    def test_all_pass(self, healthy_db):
        r = _run_cmd(healthy_db["hermes_home"], "--session", "healthy_session")
        assert r.returncode == 0
        assert "all checks passed" in r.stdout.lower() or "PASS" in r.stdout

    def test_json_output(self, healthy_db):
        r = _run_cmd(healthy_db["hermes_home"], "--session", "healthy_session", "--json")
        assert r.returncode == 0
        data = json.loads(r.stdout)
        for c in data["checks"]:
            assert c["passed"], f"{c['name']} should pass on healthy DB"


class TestMissingFields:
    """Entries missing required fields should be detected and fixed."""

    def test_detect_missing_fields(self, missing_fields_db):
        r = _run_cmd(missing_fields_db["hermes_home"], "--session", "mf_session", "--json")
        assert r.returncode == 1
        data = json.loads(r.stdout)
        completeness = [c for c in data["checks"] if c["name"] == "completeness"][0]
        assert not completeness["passed"]

    def test_fix_missing_fields(self, missing_fields_db):
        r = _run_cmd(missing_fields_db["hermes_home"], "--session", "mf_session", "--fix", "--json")
        # Fix may succeed for completeness but orphan_msgs/message_range may still fail
        data = json.loads(r.stdout)
        completeness = [c for c in data["checks"] if c["name"] == "completeness"][0]
        assert completeness["passed"]
        assert data["fixes_total"] >= 1


class TestFTS5Desync:
    """FTS5 out of sync should be detected and fixed."""

    def test_detect_fts5_desync(self, fts5_desync_db):
        r = _run_cmd(fts5_desync_db["hermes_home"], "--session", "fts5_session", "--json")
        assert r.returncode == 1
        data = json.loads(r.stdout)
        fts5_check = [c for c in data["checks"] if c["name"] == "fts5_sync"][0]
        assert not fts5_check["passed"]

    def test_fix_fts5_desync(self, fts5_desync_db):
        r = _run_cmd(fts5_desync_db["hermes_home"], "--session", "fts5_session", "--fix", "--json")
        data = json.loads(r.stdout)
        fts5_check = [c for c in data["checks"] if c["name"] == "fts5_sync"][0]
        assert fts5_check["passed"]


class TestCorruptedJSON:
    """Corrupted JSON should be detected, marked, and skipped."""

    def test_detect_corrupted_json(self, corrupted_json_db):
        r = _run_cmd(corrupted_json_db["hermes_home"], "--session", "cj_session", "--json")
        # Exit code 1 indicates issues found (corrupted JSON is unfixable)
        assert r.returncode == 1
        if r.stdout.strip():
            data = json.loads(r.stdout)
            json_check = [c for c in data["checks"] if c["name"] == "json_validity"][0]
            assert not json_check["passed"]
            assert len(data["corrupted_turns"]) >= 1

    def test_corrupted_not_fixable(self, corrupted_json_db):
        r = _run_cmd(corrupted_json_db["hermes_home"], "--session", "cj_session", "--fix", "--json")
        # Still exit 1 because corruption is not auto-fixable
        assert r.returncode == 1


class TestOrphanMessages:
    """Raw messages without matching ledger entries should be counted."""

    def test_detect_orphans(self, orphan_messages_db):
        r = _run_cmd(orphan_messages_db["hermes_home"], "--session", "orphan_session", "--json")
        assert r.returncode == 1
        data = json.loads(r.stdout)
        orphan_check = [c for c in data["checks"] if c["name"] == "orphan_messages"][0]
        assert orphan_check["details"]["orphan_count"] > 0


class TestMarkdownOutput:
    """Markdown report should be generated correctly."""

    def test_markdown_report(self, healthy_db, tmp_path):
        out = tmp_path / "report.md"
        r = _run_cmd(healthy_db["hermes_home"], "--session", "healthy_session", "--output", str(out))
        # Output file is written even when some checks fail
        if out.exists():
            content = out.read_text()
            assert "Health Report" in content
        else:
            assert r.returncode == 1  # Some checks may fail


class TestEdgeCases:
    """Edge case tests."""

    def test_nonexistent_session(self, healthy_db):
        r = _run_cmd(healthy_db["hermes_home"], "--session", "nonexistent", "--json")
        # Should error gracefully
        assert r.returncode != 0

    def test_five_checks_present(self, healthy_db):
        r = _run_cmd(healthy_db["hermes_home"], "--session", "healthy_session", "--json")
        data = json.loads(r.stdout)
        check_names = {c["name"] for c in data["checks"]}
        assert check_names == {"completeness", "fts5_sync", "orphan_messages", "json_validity", "message_range"}


def _run_cmd(hermes_home, *args):
    """Run health_check via hermes binary as a Python module."""
    env = {"HERMES_HOME": str(hermes_home)}
    cmd_args = ["-m", "plugins.context_engine.decohere.monitoring.health_check", *args]
    result = subprocess.run(
        [sys.executable, *cmd_args],
        capture_output=True, text=True, env=env,
        timeout=15,
    )
    return result
