#!/usr/bin/env python3
"""Tests for scripts/verify_phase.py — failure handling for both pipeline types.

Every phase treats a missing output the same way: write error frontmatter, drop
the ID from the active set. Also pins the phase→path maps against
check_review_progress.PHASE_CHECKS.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from check_review_progress import PHASE_CHECKS  # noqa: E402
from verify_phase import (  # noqa: E402
    _INITIATIVE_PHASE_OUTPUT,
    _RFE_PHASE_OUTPUT,
    verify,
)

SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts"))


@pytest.fixture
def workdir(tmp_path):
    """A scratch working directory that can still shell out to scripts/.

    verify() writes error frontmatter by invoking `python3 scripts/frontmatter.py`
    relative to cwd, and swallows any exception from it — without the symlink
    the write would silently no-op and the assertions would pass vacuously.
    """
    os.symlink(SCRIPTS_DIR, tmp_path / "scripts")
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


def _write_ids(*ids):
    with open("ids.txt", "w") as f:
        for id_ in ids:
            f.write(f"{id_}\n")
    return "ids.txt"


def _read_ids():
    with open("ids.txt") as f:
        return [line.strip() for line in f if line.strip()]


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _failed(capsys):
    """Parse the FAILED= line off stdout."""
    line = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("FAILED=")][-1]
    return [x for x in line[len("FAILED=") :].split(",") if x]


REVIEW_FM = "---\nrfe_id: RHAIRFE-1\nscore: 7\n---\nBody\n"


# ── Degenerate inputs ─────────────────────────────────────────────────────────


class TestNoIds:
    def test_missing_ids_file(self, workdir, capsys):
        verify("fetch", "nope.txt")
        assert _failed(capsys) == []

    def test_empty_ids_file(self, workdir, capsys):
        _write_ids()
        verify("fetch", "ids.txt")
        assert _failed(capsys) == []

    def test_unknown_phase_exits(self, workdir):
        _write_ids("RHAIRFE-1")
        with pytest.raises(SystemExit) as exc:
            verify("banana", "ids.txt")
        assert exc.value.code == 1


# ── Non-review phases act too ─────────────────────────────────────────────────


class TestNonReviewPhasesAlsoDropTheId:
    """fetch/assess degrade the same way review does — one bad ID, not a batch.

    The alternative (report and leave the ID in the active set) hands the next
    phase's pre_script an ID with no input file; `prep_assess.py` exits 1 on a
    missing task file and `_run_script` turns that into a pipeline-wide exit.
    """

    def test_fetch_reports_the_missing_id(self, workdir, capsys):
        _write_ids("RHAIRFE-1", "RHAIRFE-2")
        _write("artifacts/rfe-tasks/RHAIRFE-2.md", "task\n")
        verify("fetch", "ids.txt")
        assert _failed(capsys) == ["RHAIRFE-1"]

    def test_fetch_drops_the_failed_id(self, workdir, capsys):
        _write_ids("RHAIRFE-1", "RHAIRFE-2")
        _write("artifacts/rfe-tasks/RHAIRFE-2.md", "task\n")
        verify("fetch", "ids.txt")
        assert _read_ids() == ["RHAIRFE-2"]

    def test_fetch_records_the_phase_in_the_error(self, workdir, capsys):
        from artifact_utils import read_frontmatter

        _write_ids("RHAIRFE-1")
        verify("fetch", "ids.txt")
        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1-review.md")
        assert data["error"] == "fetch_failed"
        assert data["needs_attention"] is True

    def test_assess_drops_the_failed_id(self, workdir, capsys):
        _write_ids("RHAIRFE-1")
        verify("assess", "ids.txt")
        assert _failed(capsys) == ["RHAIRFE-1"]
        assert _read_ids() == []

    def test_assess_records_the_phase_in_the_error(self, workdir, capsys):
        from artifact_utils import read_frontmatter

        _write_ids("RHAIRFE-1")
        verify("assess", "ids.txt")
        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1-review.md")
        assert data["error"] == "assess_failed"

    def test_present_output_is_not_a_failure(self, workdir, capsys):
        _write_ids("RHAIRFE-1")
        _write("tmp/rfe-assess/single/RHAIRFE-1.result.md", "result\n")
        verify("assess", "ids.txt")
        assert _failed(capsys) == []
        assert _read_ids() == ["RHAIRFE-1"]

    def test_a_clean_phase_leaves_no_review_files(self, workdir, capsys):
        _write_ids("RHAIRFE-1")
        _write("tmp/rfe-assess/single/RHAIRFE-1.result.md", "result\n")
        verify("assess", "ids.txt")
        assert not os.path.exists("artifacts/rfe-reviews/RHAIRFE-1-review.md")


# ── Review phase: write error frontmatter and drop the ID ─────────────────────


class TestReviewPhaseActs:
    def test_missing_review_is_reported(self, workdir, capsys):
        _write_ids("RHAIRFE-1")
        verify("review", "ids.txt")
        assert _failed(capsys) == ["RHAIRFE-1"]

    def test_missing_review_gets_error_frontmatter(self, workdir, capsys):
        from artifact_utils import read_frontmatter

        _write_ids("RHAIRFE-1")
        verify("review", "ids.txt")
        data, _ = read_frontmatter("artifacts/rfe-reviews/RHAIRFE-1-review.md")
        assert data["rfe_id"] == "RHAIRFE-1"
        assert data["error"] == "review_failed"
        assert data["score"] == 0
        assert data["needs_attention"] is True
        assert data["scores"]["not_a_task"] == 0

    def test_failed_id_leaves_the_active_set(self, workdir, capsys):
        _write_ids("RHAIRFE-1", "RHAIRFE-2")
        _write("artifacts/rfe-reviews/RHAIRFE-2-review.md", REVIEW_FM)
        verify("review", "ids.txt")
        assert _read_ids() == ["RHAIRFE-2"]

    def test_healthy_ids_are_preserved_in_order(self, workdir, capsys):
        _write_ids("RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3")
        for id_ in ("RHAIRFE-1", "RHAIRFE-3"):
            _write(f"artifacts/rfe-reviews/{id_}-review.md", REVIEW_FM)
        verify("review", "ids.txt")
        assert _read_ids() == ["RHAIRFE-1", "RHAIRFE-3"]

    def test_scored_review_is_not_a_failure(self, workdir, capsys):
        _write_ids("RHAIRFE-1")
        _write("artifacts/rfe-reviews/RHAIRFE-1-review.md", REVIEW_FM)
        verify("review", "ids.txt")
        assert _failed(capsys) == []
        assert _read_ids() == ["RHAIRFE-1"]

    def test_review_without_a_score_is_a_failure(self, workdir, capsys):
        """A file the agent created but never scored still counts as failed."""
        _write_ids("RHAIRFE-1")
        _write("artifacts/rfe-reviews/RHAIRFE-1-review.md", "---\nrfe_id: RHAIRFE-1\n---\nBody\n")
        verify("review", "ids.txt")
        assert _failed(capsys) == ["RHAIRFE-1"]

    def test_unparsable_review_is_a_failure(self, workdir, capsys):
        _write_ids("RHAIRFE-1")
        _write("artifacts/rfe-reviews/RHAIRFE-1-review.md", "---\n: : :\n---\nBody\n")
        verify("review", "ids.txt")
        assert _failed(capsys) == ["RHAIRFE-1"]
        assert _read_ids() == []


# ── Initiative type ───────────────────────────────────────────────────────────


class TestInitiativeType:
    def test_fetch_looks_in_the_initiatives_dir(self, workdir, capsys):
        _write_ids("INIT-001", "INIT-002")
        _write("artifacts/initiatives/INIT-002.md", "task\n")
        verify("fetch", "ids.txt", "initiative")
        assert _failed(capsys) == ["INIT-001"]

    def test_rfe_task_file_does_not_satisfy_an_initiative(self, workdir, capsys):
        _write_ids("INIT-001")
        _write("artifacts/rfe-tasks/INIT-001.md", "task\n")
        verify("fetch", "ids.txt", "initiative")
        assert _failed(capsys) == ["INIT-001"]

    def test_error_frontmatter_uses_the_initiative_schema(self, workdir, capsys):
        from artifact_utils import read_frontmatter

        _write_ids("INIT-001")
        verify("review", "ids.txt", "initiative")
        data, _ = read_frontmatter("artifacts/initiative-reviews/INIT-001-review.md")
        assert data["initiative_id"] == "INIT-001"
        assert data["error"] == "review_failed"
        # scope, not not_a_task — the two types score different criteria.
        assert data["scores"]["scope"] == 0
        assert "not_a_task" not in data["scores"]

    def test_alignment_is_verifiable(self, workdir, capsys):
        """Initiatives have a phase RFEs do not."""
        _write_ids("INIT-001")
        verify("alignment", "ids.txt", "initiative")
        assert _failed(capsys) == ["INIT-001"]
        assert _read_ids() == []

    def test_fetch_failure_writes_to_the_initiative_reviews_dir(self, workdir, capsys):
        _write_ids("INIT-001")
        verify("fetch", "ids.txt", "initiative")
        assert os.path.exists("artifacts/initiative-reviews/INIT-001-review.md")
        assert not os.path.exists("artifacts/rfe-reviews/INIT-001-review.md")


# ── Cross-module invariant ────────────────────────────────────────────────────


class TestAgreesWithProgressChecker:
    """verify() and check_id() must test the same path for the same phase.

    cmd_next_action's pre-filter is built from check_id(); if the two maps
    drifted apart, verify() would fail IDs the pipeline considers healthy.
    """

    @pytest.mark.parametrize("phase", sorted(_RFE_PHASE_OUTPUT))
    def test_rfe_paths_match(self, phase):
        assert _RFE_PHASE_OUTPUT[phase]("X-1") == PHASE_CHECKS[phase]("X-1")

    @pytest.mark.parametrize("phase", sorted(_INITIATIVE_PHASE_OUTPUT))
    def test_initiative_paths_match(self, phase):
        assert _INITIATIVE_PHASE_OUTPUT[phase]("X-1") == PHASE_CHECKS[f"initiative-{phase}"]("X-1")


# ── Registry derivation ───────────────────────────────────────────────────────


class TestDerivedFromRegistry:
    """The phase tables and the error-stub tail are projections of types/<t>/type.yaml.

    A drop-in type (RFE_CREATOR_EXTRA_TYPES) therefore appears in _TYPE_CONFIG with its own
    dirs, id field, dimension rows and score fields — nothing in verify_phase names a type.
    """

    def test_shipped_tables_are_the_descriptor_projection(self):
        import type_registry
        from verify_phase import _TYPE_CONFIG

        reg = type_registry.load(extra_roots=[], env={})
        assert list(_TYPE_CONFIG) == reg.names()
        for desc in reg:
            tc = _TYPE_CONFIG[desc.name]
            dirs = desc.dirs()
            assert tc["reviews_dir"] == dirs["reviews"]
            assert tc["id_field"] == desc.id_field
            assert tc["score_fields"] == [f"scores.{f}=0" for f in desc.score_fields]
            assert tc["phases"]["fetch"]("X-1") == f"{dirs['tasks']}/X-1.md"
            assert tc["phases"]["assess"]("X-1") == "tmp/rfe-assess/single/X-1.result.md"
            for dim in desc.get("pipeline.dimensions"):
                assert tc["phases"][dim["name"]]("X-1") == f"{dirs['reviews']}/X-1-{dim['name']}.md"

    def test_a_drop_in_type_gets_its_own_rows(self, tmp_path):
        import json
        import subprocess
        import sys

        import yaml

        with open(os.path.join(SCRIPTS_DIR, "..", "types", "rfe", "type.yaml")) as f:
            data = yaml.safe_load(f)
        data["type"] = "widget"
        data["identity"]["id_field"] = "widget_id"
        data["dirs"] = {
            "tasks": "artifacts/widgets",
            "originals": "artifacts/widget-originals",
            "reviews": "artifacts/widget-reviews",
        }
        data["pipeline"]["dimensions"] = [
            {"name": "feasibility", "prompt": "x.md", "blocking": True},
            {"name": "security", "prompt": "y.md", "blocking": False},
        ]
        data["schema"]["review"]["score_fields"] = ["what", "risk"]
        root = tmp_path / "extra"
        (root / "widget").mkdir(parents=True)
        with open(root / "widget" / "type.yaml", "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)

        probe = (
            "import json, verify_phase as v; tc = v._TYPE_CONFIG['widget']; "
            "print(json.dumps({'types': list(v._TYPE_CONFIG), 'id_field': tc['id_field'], "
            "'score_fields': tc['score_fields'], 'reviews_dir': tc['reviews_dir'], "
            "'phases': {p: fn('W-1') for p, fn in tc['phases'].items()}}))"
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("RFE_CREATOR_") and k not in ("CI", "GITHUB_ACTIONS")
        }
        env["RFE_CREATOR_EXTRA_TYPES"] = str(root)
        env["PYTHONPATH"] = SCRIPTS_DIR
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, env=env, check=True
        )
        got = json.loads(result.stdout)
        assert got["types"] == ["rfe", "initiative", "widget"]
        assert got["id_field"] == "widget_id"
        assert got["score_fields"] == ["scores.what=0", "scores.risk=0"]
        assert got["reviews_dir"] == "artifacts/widget-reviews"
        assert got["phases"] == {
            "fetch": "artifacts/widgets/W-1.md",
            "assess": "tmp/rfe-assess/single/W-1.result.md",
            "feasibility": "artifacts/widget-reviews/W-1-feasibility.md",
            "security": "artifacts/widget-reviews/W-1-security.md",
            "review": "artifacts/widget-reviews/W-1-review.md",
            "split": "artifacts/widget-reviews/W-1-split-status.yaml",
        }
