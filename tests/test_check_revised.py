#!/usr/bin/env python3
"""Tests for scripts/check_revised.py — content comparison between original and task files."""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import check_revised  # noqa: E402
import type_registry  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "check_revised.py")
GENERIC_REVIEW_SKELETON = ".claude/skills/rfe-review/prompts/review-agent.md"
REVIEW_PROMPT_SURFACES = [
    ".claude/skills/rfe.review/prompts/review-agent.md",  # legacy, until PR-5c
    ".claude/skills/initiative-review/prompts/review-agent.md",  # legacy, until PR-5c
    "rfe:generic",
    "initiative:generic",
]


def _set_commands(text):
    """Every `python3 scripts/frontmatter.py set` command, backslash continuations joined."""
    lines, out, i = text.splitlines(), [], 0
    while i < len(lines):
        if "frontmatter.py set" in lines[i]:
            command = lines[i]
            while command.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                command = command.rstrip()[:-1] + " " + lines[i].strip()
            out.append(command)
        i += 1
    return out


def _review_prompt(surface):
    """(text, id_field) of a review-agent prompt surface: a legacy file, or the generic
    skeleton rendered for a type with its launch block (`type_registry.py launch-vars`)."""
    if surface.endswith(":generic"):
        t = surface.split(":")[0]
        desc = type_registry.load(extra_roots=[], env={}).get(t)
        with open(os.path.join(REPO_ROOT, GENERIC_REVIEW_SKELETON)) as f:
            text = f.read()
        for key, value in type_registry.launch_vars(desc, "review"):
            text = text.replace("{" + key + "}", value)
        return text, desc.id_field
    with open(os.path.join(REPO_ROOT, surface)) as f:
        return f.read(), ("rfe_id" if "/rfe." in surface else "initiative_id")


FM_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "frontmatter.py")


def _write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _run(original, task):
    result = subprocess.run(
        ["python3", SCRIPT, original, task],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip(), result.returncode


@pytest.fixture
def tmp_dir(tmp_path):
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(orig)


class TestCheckRevised:
    def test_identical_content(self, tmp_dir):
        body = "## Problem\n\nSame content here.\n"
        _write("original.md", body)
        _write("task.md", body)
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=false"

    def test_different_content(self, tmp_dir):
        _write("original.md", "## Problem\n\nOriginal.\n")
        _write("task.md", "## Problem\n\nRevised and improved.\n")
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=true"

    def test_frontmatter_stripped_before_comparison(self, tmp_dir):
        """Frontmatter differences don't count — only body matters."""
        body = "## Problem\n\nSame body content.\n"
        _write("original.md", body)
        _write("task.md", f"---\nrfe_id: RHAIRFE-1234\ntitle: Test\n---\n{body}")
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=false"

    def test_whitespace_only_difference(self, tmp_dir):
        """Trailing whitespace differences are ignored (.strip())."""
        _write("original.md", "Content here.\n\n\n")
        _write("task.md", "---\nrfe_id: X\n---\nContent here.\n")
        out, rc = _run("original.md", "task.md")
        assert rc == 0
        assert out == "REVISED=false"

    def test_missing_original_file(self, tmp_dir):
        _write("task.md", "Some content.\n")
        out, rc = _run("nonexistent.md", "task.md")
        assert rc == 1
        assert "FILE_MISSING" in out

    def test_missing_task_file(self, tmp_dir):
        _write("original.md", "Some content.\n")
        out, rc = _run("original.md", "nonexistent.md")
        assert rc == 1
        assert "FILE_MISSING" in out

    def test_wrong_arg_count(self):
        result = subprocess.run(
            ["python3", SCRIPT],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2


REVIEW_TEMPLATE = """\
---
rfe_id: {rfe_id}
score: 7
pass: false
recommendation: revise
feasibility: feasible
auto_revised: {auto_revised}
needs_attention: false
scores:
  what: 2
  why: 1
  open_to_how: 2
  not_a_task: 2
  right_sized: 0
---
Review body here.
"""


def _setup_batch(tmp_path, rfe_id, original_body, task_body, auto_revised=False):
    """Create originals, tasks, and review dirs with test content."""
    originals = tmp_path / "artifacts" / "rfe-originals"
    tasks = tmp_path / "artifacts" / "rfe-tasks"
    reviews = tmp_path / "artifacts" / "rfe-reviews"
    originals.mkdir(parents=True, exist_ok=True)
    tasks.mkdir(parents=True, exist_ok=True)
    reviews.mkdir(parents=True, exist_ok=True)

    (originals / f"{rfe_id}.md").write_text(original_body)
    (tasks / f"{rfe_id}.md").write_text(
        f"---\nrfe_id: {rfe_id}\ntitle: Test\npriority: Normal\nstatus: Draft\n---\n{task_body}"
    )
    (reviews / f"{rfe_id}-review.md").write_text(
        REVIEW_TEMPLATE.format(rfe_id=rfe_id, auto_revised=str(auto_revised).lower())
    )


def _read_frontmatter(path):
    """Read YAML frontmatter from a file."""
    import yaml

    with open(path) as f:
        content = f.read()
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    return yaml.safe_load(content[3:end]) or {}


class TestBatchMode:
    def test_sets_auto_revised_true_when_content_differs(self, tmp_path):
        _setup_batch(
            tmp_path, "RHAIRFE-1001", "Original text.", "Revised text.", auto_revised=False
        )
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1001"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "RHAIRFE-1001: auto_revised False -> True" in result.stdout
        assert "UPDATED=1" in result.stdout
        # Verify frontmatter was actually changed
        review = (tmp_path / "artifacts" / "rfe-reviews" / "RHAIRFE-1001-review.md").read_text()
        assert "auto_revised: true" in review

    def test_sets_auto_revised_false_when_content_identical(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-1002", "Same content.", "Same content.", auto_revised=True)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1002"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "RHAIRFE-1002: auto_revised True -> False" in result.stdout
        assert "UPDATED=1" in result.stdout
        review = (tmp_path / "artifacts" / "rfe-reviews" / "RHAIRFE-1002-review.md").read_text()
        assert "auto_revised: false" in review

    def test_no_update_when_flag_already_correct(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-1003", "Original.", "Revised.", auto_revised=True)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1003"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "auto_revised=True (correct)" in result.stdout
        assert "UPDATED=0" in result.stdout

    def test_discovers_ids_when_none_given(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-1004", "Original.", "Changed.", auto_revised=False)
        _setup_batch(tmp_path, "RHAIRFE-1005", "Same.", "Same.", auto_revised=False)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "RHAIRFE-1004: auto_revised False -> True" in result.stdout
        assert "RHAIRFE-1005: auto_revised=False (correct)" in result.stdout
        assert "UPDATED=1" in result.stdout

    def test_skips_missing_review_file(self, tmp_path):
        """If an original+task exist but no review file, skip without error."""
        originals = tmp_path / "artifacts" / "rfe-originals"
        tasks = tmp_path / "artifacts" / "rfe-tasks"
        reviews = tmp_path / "artifacts" / "rfe-reviews"
        originals.mkdir(parents=True)
        tasks.mkdir(parents=True)
        reviews.mkdir(parents=True)
        (originals / "RHAIRFE-1006.md").write_text("Original.")
        (tasks / "RHAIRFE-1006.md").write_text(
            "---\nrfe_id: RHAIRFE-1006\ntitle: T\npriority: Normal\nstatus: Draft\n---\nChanged."
        )
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-1006"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert result.returncode == 0
        assert "UPDATED=0" in result.stdout


class TestReassessCyclePreservation:
    """Test that auto_revised flag survives reassess cycles where the
    re-review agent sets frontmatter WITHOUT including auto_revised."""

    def test_flag_survives_frontmatter_set_without_auto_revised(self, tmp_path):
        """Simulates re-review agent setting scores without auto_revised —
        the existing auto_revised=true should be preserved."""
        _setup_batch(tmp_path, "RHAIRFE-2001", "Original.", "Revised.", auto_revised=True)
        subprocess.run(
            [
                "python3",
                FM_SCRIPT,
                "set",
                str(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2001-review.md"),
                "score=9",
                "pass=true",
                "recommendation=submit",
            ],
            check=True,
            capture_output=True,
        )
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2001-review.md")
        assert fm["auto_revised"] is True

    def test_flag_clobbered_when_set_explicitly_false(self, tmp_path):
        """If something explicitly sets auto_revised=false, it sticks."""
        _setup_batch(tmp_path, "RHAIRFE-2002", "Original.", "Revised.", auto_revised=True)
        subprocess.run(
            [
                "python3",
                FM_SCRIPT,
                "set",
                str(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2002-review.md"),
                "auto_revised=false",
            ],
            check=True,
            capture_output=True,
        )
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2002-review.md")
        assert fm["auto_revised"] is False

    def test_reassess_cycle_preserves_flag(self, tmp_path):
        """End-to-end: FIXUP sets true, re-review without auto_revised
        doesn't clobber it, and check_revised confirms it stays true."""
        _setup_batch(tmp_path, "RHAIRFE-2003", "Original.", "Revised content.", auto_revised=False)

        # Step 1: FIXUP sets auto_revised=true
        subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-2003"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md")
        assert fm["auto_revised"] is True

        # Step 2: Re-review agent writes new scores WITHOUT auto_revised
        subprocess.run(
            [
                "python3",
                FM_SCRIPT,
                "set",
                str(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md"),
                "score=9",
                "pass=true",
                "recommendation=submit",
                "scores.what=2",
                "scores.why=1",
                "scores.open_to_how=2",
                "scores.not_a_task=2",
                "scores.right_sized=2",
            ],
            check=True,
            capture_output=True,
        )
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md")
        assert fm["auto_revised"] is True, "re-review agent must not clobber auto_revised"

        # Step 3: FIXUP again confirms
        subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-2003"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-2003-review.md")
        assert fm["auto_revised"] is True

    @pytest.mark.parametrize("surface", REVIEW_PROMPT_SURFACES)
    def test_review_agent_prompt_excludes_auto_revised(self, surface):
        """The review agent prompt must NOT include auto_revised in its frontmatter.py set
        call — only the revise agent and FIXUP set it. Held on the legacy prompts (until PR-5c)
        and on the generic review skeleton rendered per type (`{ID_FIELD}={ID}` renders to the
        type's id field)."""
        text, id_field = _review_prompt(surface)
        review_sets = [c for c in _set_commands(text) if f"{id_field}={{ID}}" in c]
        assert review_sets, f"{surface}: no frontmatter.py set command carries {id_field}={{ID}}"
        for command in review_sets:
            assert "auto_revised" not in command, f"{surface}: {command}"


class TestTypeArg:
    """--type is validated against the registry through the shared
    type_registry.parse_type_arg (PR-3a; pinned in tests/test_type_registry.py): an
    unregistered name exits 2 with the registered list instead of a KeyError traceback."""

    UNKNOWN = "ERROR: unknown --type 'bogus'; registered types: rfe, initiative\n"
    TRAILING = "ERROR: --type requires a value; registered types: rfe, initiative\n"

    def test_usage_choices_follow_the_registry(self):
        assert check_revised._TYPE_CHOICES == "|".join(check_revised._TYPES.choices())
        assert check_revised._TYPE_CHOICES == "rfe|initiative"

    def test_cli_unknown_type_in_batch_mode(self, tmp_path):
        result = subprocess.run(
            ["python3", SCRIPT, "--type", "bogus", "--batch"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
        )
        assert result.returncode == 2
        assert result.stderr == self.UNKNOWN
        assert result.stdout == ""

    def test_cli_unknown_type_in_pair_mode(self, tmp_dir):
        _write("original.md", "Same.\n")
        _write("task.md", "Same.\n")
        result = subprocess.run(
            ["python3", SCRIPT, "--type", "bogus", "original.md", "task.md"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert result.stderr == self.UNKNOWN

    def test_cli_trailing_type(self):
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "--type"], capture_output=True, text=True
        )
        assert result.returncode == 2
        assert result.stderr == self.TRAILING

    def test_cli_usage_lists_the_registered_types(self):
        result = subprocess.run(["python3", SCRIPT, "only-one"], capture_output=True, text=True)
        assert result.returncode == 2
        assert result.stderr == (
            "Usage: check_revised.py [--type rfe|initiative] <original> <task>\n"
            "       check_revised.py [--type rfe|initiative] --batch [ID ...]\n"
        )

    def test_cli_registered_type_still_works_in_pair_mode(self, tmp_dir):
        _write("original.md", "Original.\n")
        _write("task.md", "Revised.\n")
        result = subprocess.run(
            ["python3", SCRIPT, "--type", "initiative", "original.md", "task.md"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "REVISED=true"


class TestLowerOnly:
    """--lower-only is the last-reader guard (REPORT transition, submit.py start-up): a set
    flag on an unchanged task is lowered; nothing is ever raised (a removed-context companion
    can make task != original without a revision, so raising stays FIXUP's job)."""

    def _run(self, tmp_path, *args, cwd=None):
        return subprocess.run(
            ["python3", SCRIPT, "--batch", "--lower-only", *args],
            capture_output=True,
            text=True,
            cwd=cwd or tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )

    def test_lowers_a_set_flag_when_content_is_identical(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-3001", "Same content.", "Same content.", auto_revised=True)
        result = self._run(tmp_path, "RHAIRFE-3001")
        assert result.returncode == 0
        assert "RHAIRFE-3001: auto_revised True -> False (task body equals the original)" in (
            result.stdout
        )
        assert "LOWERED=RHAIRFE-3001" in result.stdout
        assert "UPDATED=1" in result.stdout
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-3001-review.md")
        assert fm["auto_revised"] is False

    def test_leaves_a_set_flag_when_content_differs(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-3002", "Original.", "Revised.", auto_revised=True)
        result = self._run(tmp_path, "RHAIRFE-3002")
        assert result.returncode == 0
        assert "LOWERED=\n" in result.stdout
        assert "UPDATED=0" in result.stdout
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-3002-review.md")
        assert fm["auto_revised"] is True

    def test_never_raises_an_unset_flag(self, tmp_path):
        """Differing content with the flag unset is FIXUP's call, not the guard's."""
        _setup_batch(tmp_path, "RHAIRFE-3003", "Original.", "Revised.", auto_revised=False)
        result = self._run(tmp_path, "RHAIRFE-3003")
        assert result.returncode == 0
        assert "LOWERED=\n" in result.stdout
        assert "UPDATED=0" in result.stdout
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-3003-review.md")
        assert fm["auto_revised"] is False

    def test_skips_an_id_without_an_original(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-3004", "x", "x", auto_revised=True)
        os.remove(tmp_path / "artifacts/rfe-originals/RHAIRFE-3004.md")
        result = self._run(tmp_path, "RHAIRFE-3004")
        assert result.returncode == 0
        assert "LOWERED=\n" in result.stdout
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-3004-review.md")
        assert fm["auto_revised"] is True

    def test_artifacts_dir_resolves_the_type_dirs_elsewhere(self, tmp_path):
        """submit.py runs the guard with --artifacts-dir from a cwd that has no artifacts/."""
        _setup_batch(tmp_path, "RHAIRFE-3005", "Same.", "Same.", auto_revised=True)
        _setup_batch(tmp_path, "RHAIRFE-3006", "Same.", "Same.", auto_revised=True)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        result = self._run(
            tmp_path, "--artifacts-dir", str(tmp_path / "artifacts"), cwd=elsewhere
        )  # no ids: discovered from the originals dir under --artifacts-dir
        assert result.returncode == 0
        assert "LOWERED=RHAIRFE-3005,RHAIRFE-3006" in result.stdout
        assert "UPDATED=2" in result.stdout

    def test_missing_originals_dir_is_empty_not_an_error(self, tmp_path):
        (tmp_path / "artifacts" / "rfe-reviews").mkdir(parents=True)
        result = self._run(tmp_path)
        assert result.returncode == 0
        assert "LOWERED=\n" in result.stdout
        assert "UPDATED=0" in result.stdout

    def test_artifacts_dir_requires_a_value(self, tmp_path):
        result = self._run(tmp_path, "--artifacts-dir")
        assert result.returncode == 2
        assert "--artifacts-dir requires a value" in result.stderr

    def test_default_batch_mode_still_raises_and_lowers(self, tmp_path):
        """Without --lower-only the FIXUP behaviour is unchanged (raises on a diff)."""
        _setup_batch(tmp_path, "RHAIRFE-3007", "Original.", "Revised.", auto_revised=False)
        result = subprocess.run(
            ["python3", SCRIPT, "--batch", "RHAIRFE-3007"],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )
        assert "RHAIRFE-3007: auto_revised False -> True" in result.stdout
        assert "LOWERED=" not in result.stdout


BAD_REVIEW = (
    "---\nrfe_id: {rfe_id}\nauto_revised: true\n"
    "score: [unclosed owner: jane.doe@example.com\n---\nbody\n"
)


class TestPerIdIsolation:
    """One review that cannot be read or updated must not abort the batch: every id sorted
    after it would keep a stale flag (the AISDLC-50 label would ship anyway). The skip is
    reported by exception class only — the message can quote frontmatter."""

    def _batch(self, tmp_path, *args):
        return subprocess.run(
            ["python3", SCRIPT, "--batch", *args],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": os.path.dirname(SCRIPT)},
        )

    def test_lower_only_skips_the_bad_review_and_lowers_the_rest(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-3100", "Same.", "Same.", auto_revised=True)
        (tmp_path / "artifacts/rfe-reviews/RHAIRFE-3100-review.md").write_text(
            BAD_REVIEW.format(rfe_id="RHAIRFE-3100")
        )
        _setup_batch(tmp_path, "RHAIRFE-3200", "Same.", "Same.", auto_revised=True)
        result = self._batch(tmp_path, "--lower-only", "RHAIRFE-3100", "RHAIRFE-3200")
        assert result.returncode == 0
        assert "LOWERED=RHAIRFE-3200" in result.stdout
        assert "SKIPPED=RHAIRFE-3100" in result.stdout
        assert "UPDATED=1" in result.stdout
        fm = _read_frontmatter(tmp_path / "artifacts/rfe-reviews/RHAIRFE-3200-review.md")
        assert fm["auto_revised"] is False
        skip = [ln for ln in result.stderr.splitlines() if ln.startswith("check_revised:")]
        assert len(skip) == 1 and skip[0].startswith("check_revised: skipped RHAIRFE-3100 (")
        assert "jane.doe@example.com" not in result.stderr
        assert "Traceback" not in result.stderr

    def test_default_mode_skips_the_bad_review_and_fixes_the_rest(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-3101", "Same.", "Same.", auto_revised=True)
        (tmp_path / "artifacts/rfe-reviews/RHAIRFE-3101-review.md").write_text(
            BAD_REVIEW.format(rfe_id="RHAIRFE-3101")
        )
        _setup_batch(tmp_path, "RHAIRFE-3201", "Original.", "Revised.", auto_revised=False)
        result = self._batch(tmp_path, "RHAIRFE-3101", "RHAIRFE-3201")
        assert result.returncode == 0
        assert "RHAIRFE-3201: auto_revised False -> True" in result.stdout
        assert "SKIPPED=RHAIRFE-3101" in result.stdout
        assert "UPDATED=1" in result.stdout
        assert "LOWERED=" not in result.stdout
        assert "jane.doe@example.com" not in result.stderr

    def test_clean_batch_reports_an_empty_skipped_line(self, tmp_path):
        _setup_batch(tmp_path, "RHAIRFE-3102", "Same.", "Same.", auto_revised=True)
        result = self._batch(tmp_path, "--lower-only", "RHAIRFE-3102")
        assert result.returncode == 0
        assert "SKIPPED=\n" in result.stdout
        assert result.stderr == ""

    def test_default_mode_missing_originals_dir_is_an_empty_batch(self, tmp_path):
        """The no-ids discovery path tolerates a missing originals dir in default mode too
        (it used to raise FileNotFoundError from os.listdir)."""
        (tmp_path / "artifacts" / "rfe-reviews").mkdir(parents=True)
        result = self._batch(tmp_path)
        assert result.returncode == 0
        assert "UPDATED=0" in result.stdout
        assert "SKIPPED=\n" in result.stdout
