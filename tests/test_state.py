#!/usr/bin/env python3
"""Tests for scripts/state.py."""
import os
import subprocess
import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "state.py")


def run_state(*args):
    """Run state.py and return (stdout, stderr, returncode)."""
    result = subprocess.run(
        ["python3", SCRIPT, *args],
        capture_output=True, text=True,
    )
    return result.stdout, result.stderr, result.returncode


@pytest.fixture
def tmp_dir(tmp_path):
    """Run tests from a temp directory to isolate state files."""
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


class TestInitAndRead:
    def test_init_creates_file(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "headless=true", "batch_size=5")
        out, _, rc = run_state("read", "tmp/config.yaml")
        assert rc == 0
        assert "headless: true" in out
        assert "batch_size: 5" in out

    def test_init_overwrites(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "a=1")
        run_state("init", "tmp/config.yaml", "b=2")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert "a: 1" not in out
        assert "b: 2" in out


class TestSet:
    def test_set_adds_new_key(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "a=1")
        run_state("set", "tmp/config.yaml", "b=2")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert "a: 1" in out
        assert "b: 2" in out

    def test_set_updates_existing_key_in_place(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "batch_size=5", "current_batch=0")
        run_state("set", "tmp/config.yaml", "current_batch=1")
        run_state("set", "tmp/config.yaml", "current_batch=2")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert out.count("current_batch") == 1
        assert "current_batch: 2" in out
        assert "batch_size: 5" in out

    def test_set_preserves_key_order(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "a=1", "b=2", "c=3")
        run_state("set", "tmp/config.yaml", "b=updated")
        out, _, _ = run_state("read", "tmp/config.yaml")
        lines = [l for l in out.strip().split("\n") if l]
        assert lines[0] == "a: 1"
        assert lines[1] == "b: updated"
        assert lines[2] == "c: 3"

    def test_set_value_with_equals(self, tmp_dir):
        run_state("init", "tmp/config.yaml")
        run_state("set", "tmp/config.yaml", "start_time=2026-04-01T18:20:38Z")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert "start_time: 2026-04-01T18:20:38Z" in out

    def test_set_creates_directory(self, tmp_dir):
        run_state("set", "tmp/config.yaml", "a=1")
        out, _, rc = run_state("read", "tmp/config.yaml")
        assert rc == 0
        assert "a: 1" in out


class TestSetDefault:
    def test_sets_when_absent(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "headless=true")
        run_state("set-default", "tmp/config.yaml", "cycle=0")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert "cycle: 0" in out

    def test_skips_when_present(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "cycle=2")
        run_state("set-default", "tmp/config.yaml", "cycle=0")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert out.count("cycle") == 1
        assert "cycle: 2" in out

    def test_compression_reentry_safe(self, tmp_dir):
        """Simulates the exact failure: counter set, incremented, then re-entered."""
        run_state("init", "tmp/config.yaml", "headless=true")
        run_state("set-default", "tmp/config.yaml", "reassess_cycle=0")
        run_state("set", "tmp/config.yaml", "reassess_cycle=1")
        # Compression re-entry: init code runs again
        run_state("set-default", "tmp/config.yaml", "reassess_cycle=0")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert "reassess_cycle: 1" in out  # must NOT reset to 0


class TestWriteAndReadIds:
    def test_round_trip(self, tmp_dir):
        run_state("write-ids", "tmp/ids.txt", "RHAIRFE-1001", "RHAIRFE-1002", "RHAIRFE-1003")
        out, _, rc = run_state("read-ids", "tmp/ids.txt")
        assert rc == 0
        assert out.strip() == "RHAIRFE-1001 RHAIRFE-1002 RHAIRFE-1003"

    def test_deduplicates(self, tmp_dir):
        run_state("write-ids", "tmp/ids.txt", "RHAIRFE-1001", "RHAIRFE-1002", "RHAIRFE-1001")
        out, _, _ = run_state("read-ids", "tmp/ids.txt")
        assert out.strip() == "RHAIRFE-1001 RHAIRFE-1002"

    def test_empty_id_list(self, tmp_dir):
        run_state("write-ids", "tmp/ids.txt")
        out, _, rc = run_state("read-ids", "tmp/ids.txt")
        assert rc == 0
        assert out.strip() == ""

    def test_write_overwrites(self, tmp_dir):
        run_state("write-ids", "tmp/ids.txt", "A", "B")
        run_state("write-ids", "tmp/ids.txt", "C", "D")
        out, _, _ = run_state("read-ids", "tmp/ids.txt")
        assert out.strip() == "C D"


class TestErrorHandling:
    def test_read_missing_file(self, tmp_dir):
        _, err, rc = run_state("read", "tmp/nonexistent.yaml")
        assert rc != 0
        assert "State file not found" in err

    def test_read_ids_missing_file(self, tmp_dir):
        _, err, rc = run_state("read-ids", "tmp/nonexistent.txt")
        assert rc != 0
        assert "State file not found" in err


class TestTimestamp:
    def test_returns_iso8601(self, tmp_dir):
        import re
        out, _, rc = run_state("timestamp")
        assert rc == 0
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", out.strip())

    def test_round_trips_through_set(self, tmp_dir):
        run_state("init", "tmp/config.yaml")
        ts, _, _ = run_state("timestamp")
        run_state("set", "tmp/config.yaml", f"start_time={ts.strip()}")
        out, _, _ = run_state("read", "tmp/config.yaml")
        assert f"start_time: {ts.strip()}" in out


class TestClean:
    def test_clean_removes_and_recreates(self, tmp_dir):
        run_state("init", "tmp/config.yaml", "a=1")
        run_state("write-ids", "tmp/ids.txt", "X")
        run_state("clean")
        assert os.path.isdir("tmp")
        assert not os.path.exists("tmp/config.yaml")
        assert not os.path.exists("tmp/ids.txt")
