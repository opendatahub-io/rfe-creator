"""scripts/list_overlays.py: the allow-listed overlay discovery the feasibility prompt uses
instead of the Glob tool (absent on native Claude Code builds)."""

import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "list_overlays.py")

ACTIVE = """\
---
id: "0001"
title: KFP SDK updated to 2.16 in RHOAI 3.4
status: active
affects:
  - data-science-pipelines
  - notebooks
release:
  - "3.4"
---

## Fact
"""
PLATFORM_ALL = """\
---
id: "0002"
title: Gateway API replaces Istio ingress
status: active
affects: [platform]
release: ["all"]
---
"""
SUPERSEDED = """\
---
id: "0003"
title: Old fact
status: superseded
affects: [notebooks]
release: ["3.4"]
---
"""


def _overlays(tmp_path):
    d = tmp_path / ".context" / "architecture-context" / "overlays"
    d.mkdir(parents=True)
    (d / "README.md").write_text("# Overlays\n")
    (d / "0001-kfp.md").write_text(ACTIVE)
    (d / "0002-gateway.md").write_text(PLATFORM_ALL)
    (d / "0003-old.md").write_text(SUPERSEDED)
    (d / "notes.txt").write_text("not an overlay\n")
    return d


def _run(cwd, *args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def test_lists_active_overlays_from_the_default_dir(tmp_path):
    _overlays(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert [ln.split("\t")[1] for ln in lines] == ["0001", "0002"]
    assert lines[0].split("\t")[0] == os.path.join(
        ".context", "architecture-context", "overlays", "0001-kfp.md"
    )
    assert "release=3.4" in lines[0] and "affects=data-science-pipelines,notebooks" in lines[0]
    assert "README" not in r.stdout and "0003" not in r.stdout and "notes.txt" not in r.stdout


def test_paths_only(tmp_path):
    _overlays(tmp_path)
    r = _run(tmp_path, "--paths")
    assert r.stdout.splitlines() == [
        os.path.join(".context", "architecture-context", "overlays", "0001-kfp.md"),
        os.path.join(".context", "architecture-context", "overlays", "0002-gateway.md"),
    ]


def test_release_and_component_filters(tmp_path):
    _overlays(tmp_path)
    by_release = _run(tmp_path, "--release", "3.5").stdout.splitlines()
    assert [ln.split("\t")[1] for ln in by_release] == ["0002"]  # "all" matches, "3.4" does not
    by_component = _run(tmp_path, "--affects", "notebooks,dashboard").stdout.splitlines()
    assert [ln.split("\t")[1] for ln in by_component] == ["0001", "0002"]  # platform matches all
    none = _run(tmp_path, "--release", "3.4", "--affects", "dashboard").stdout.splitlines()
    assert [ln.split("\t")[1] for ln in none] == ["0002"]


def test_missing_directory_is_not_an_error(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0
    assert r.stdout == ""
    assert "proceeding without overlays" in r.stderr


def test_explicit_dir_and_bad_frontmatter_is_skipped(tmp_path):
    d = _overlays(tmp_path)
    (d / "0004-broken.md").write_text("---\nstatus: [unclosed\n---\n")
    r = _run(tmp_path, "--dir", str(d))
    assert r.returncode == 0
    assert [ln.split("\t")[1] for ln in r.stdout.splitlines()] == ["0001", "0002"]
    assert "0004-broken.md" in r.stderr
