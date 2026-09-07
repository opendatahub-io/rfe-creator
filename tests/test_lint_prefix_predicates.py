#!/usr/bin/env python3
"""Tests for scripts/lint_prefix_predicates.py — the anti-regression prefix lint.

design-proposals/work-item-types-unified.md §3.3 gate 1 / ADR A.1: no NEW literal
work-item key prefix may appear in ``scripts/**/*.py`` code outside the registry.
The prefix set is the Q6 union derived from the shipped descriptors; existing sites
are grandfathered by a per-file COUNT baseline (tests/data/prefix_predicate_baseline.json)
that may only shrink.

Scanner tests use ``scan_source`` and tmp trees; ratchet tests drive ``main(argv)``
in-process against tmp baselines (never the shipped one, which only
``--update-baseline`` may rewrite, and only into a tmp path here).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import lint_prefix_predicates as lint  # noqa: E402
import type_registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
TYPES_ROOT = REPO_ROOT / "types"
SCRIPT = "scripts/lint_prefix_predicates.py"
BASELINE = REPO_ROOT / "tests" / "data" / "prefix_predicate_baseline.json"

# Q6 union over the shipped descriptors, in the lint's own order (longest first, then
# alphabetical): key_prefixes, local_prefix, parent_key_patterns stems, snapshot.prefix,
# snapshot.report_prefix.
EXPECTED_PREFIXES = [
    "initiative-snapshot-",
    "initiative-run-",
    "issue-snapshot-",
    "RHAISTRAT-",
    "RHOAIENG-",
    "RHAIRFE-",
    "INIT-",
    "RFE-",
]

PLANTED = """\
def check(k, n):
    if k.startswith("RHAIRFE-"):
        return True
    name = f"RFE-{n:03d}"
    pattern = r'^INIT-\\d+$'
    glob = 'issue-snapshot-' + "*.yaml"
    return name, pattern, glob
"""


# ── helpers ──────────────────────────────────────────────────────────────────────


def _shipped_registry():
    return type_registry.load(root=TYPES_ROOT, extra_roots=[], env={})


def _prefixes():
    return lint.lint_prefixes(_shipped_registry())


def _write(root, rel, source):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _baseline(root, counts, name="baseline.json"):
    path = root / name
    path.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _main(root, baseline, *extra):
    """Run the lint in-process against a tmp root with the shipped registry."""
    argv = ["--root", str(root), "--baseline", str(baseline), "--types-root", str(TYPES_ROOT)]
    return lint.main(argv + list(extra))


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("RFE_CREATOR_")}
    env.update(extra)
    return env


def _cli(*args):
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_clean_env(),
    )


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "scripts").mkdir()
    return tmp_path


# ── prefix set ───────────────────────────────────────────────────────────────────


class TestPrefixSet:
    def test_q6_union_from_the_shipped_registry(self):
        assert _prefixes() == EXPECTED_PREFIXES

    def test_sort_prefixes_longest_first_then_alphabetical(self):
        assert lint.sort_prefixes({"RFE-", "RHAIRFE-", "INIT-", "RFE-"}) == [
            "RHAIRFE-",
            "INIT-",
            "RFE-",
        ]

    @pytest.mark.parametrize(
        "pattern, stem",
        [
            (r"RHAISTRAT-\d+", "RHAISTRAT-"),
            (r"^(RFE-\d+|RHAIRFE-\d+)$", "RFE-"),
            (r"^INIT-\d+$", "INIT-"),
            (r"RHAISTRAT-\d+(-BRANCH-[A-Z])?-E\d{3}", "RHAISTRAT-"),
            (r"(RFE|INIT)-\d+", ""),
            (r"\d+", ""),
            ("", ""),
            ("RFE", ""),
        ],
    )
    def test_pattern_stem(self, pattern, stem):
        assert lint.pattern_stem(pattern) == stem

    def test_empty_and_missing_values_are_skipped(self, tmp_path):
        root = tmp_path / "types"
        (root / "bare").mkdir(parents=True)
        (root / "bare" / "type.yaml").write_text(
            "type: bare\n"
            "identity: {tracker: jira, jira: {key_prefixes: ['BARE-', '']}, local_prefix: ''}\n"
            "snapshot: {prefix: '  ', report_prefix: ''}\n"
        )
        reg = type_registry.load(root=root, extra_roots=[], env={})
        assert lint.lint_prefixes(reg) == ["BARE-"]

    def test_labels_are_not_in_the_prefix_set(self):
        assert not any(p.startswith(("rfe-creator", "initiative-a")) for p in _prefixes())
        assert "initiative-" not in _prefixes()

    def test_find_prefixes_reports_each_alternative(self):
        prefixes = _prefixes()
        assert lint.find_prefixes(r"^(RFE-\d+|RHAIRFE-\d+)$", prefixes) == ("RFE-", "RHAIRFE-")

    def test_find_prefixes_nested_match_reported_once_as_the_longest(self):
        prefixes = _prefixes()
        assert lint.find_prefixes("RHAIRFE-", prefixes) == ("RHAIRFE-",)
        assert lint.find_prefixes("x RHAIRFE-1 y RFE-2", prefixes) == ("RHAIRFE-", "RFE-")

    def test_find_prefixes_is_case_sensitive(self):
        prefixes = _prefixes()
        assert lint.find_prefixes("artifacts/rfe-tasks", prefixes) == ()
        assert lint.find_prefixes("rfe-creator-split-quarantine", prefixes) == ()
        assert lint.find_prefixes("nothing here", prefixes) == ()

    def test_scan_source_is_generic_over_the_prefix_list(self):
        source = 'LABEL = "rfe-creator-split-quarantine"\n'
        assert lint.scan_source(source, _prefixes()) == []
        hits = lint.scan_source(source, ["rfe-creator-"])
        assert len(hits) == 1
        assert hits[0].prefixes == ("rfe-creator-",)


# ── scanner ──────────────────────────────────────────────────────────────────────


class TestScanner:
    def test_planted_literals_are_all_found(self):
        hits = lint.scan_source(PLANTED, _prefixes(), "scripts/x.py")
        assert [(h.line, h.prefixes) for h in hits] == [
            (2, ("RHAIRFE-",)),
            (4, ("RFE-",)),
            (5, ("INIT-",)),
            (6, ("issue-snapshot-",)),
        ]
        assert hits[0].code == 'if k.startswith("RHAIRFE-"):'
        assert hits[0].path == "scripts/x.py"

    def test_docstrings_and_comments_are_not_code(self):
        source = '''\
"""Module docstring mentions RHAIRFE-1234 and RFE-001."""


class Thing:
    """Class docstring: INIT-001."""

    def method(self):
        """Method docstring: RHOAIENG-1."""
        # a comment: issue-snapshot-2026.yaml
        return None  # RHAISTRAT-1


async def coro():
    """Async docstring: initiative-run-x."""
    return 1
'''
        assert lint.scan_source(source, _prefixes()) == []

    def test_bare_string_statement_is_code(self):
        # Only the FIRST statement of a body is a docstring; a later bare string is code.
        source = 'def f():\n    """doc"""\n    "RFE-001"\n    return 1\n'
        assert len(lint.scan_source(source, _prefixes())) == 1

    def test_implicit_concatenation_is_one_constant(self):
        source = 'x = ("RFE-"\n     "RHAIRFE-")\n'
        hits = lint.scan_source(source, _prefixes())
        assert len(hits) == 1
        assert hits[0].prefixes == ("RFE-", "RHAIRFE-")

    def test_two_prefixes_in_one_literal_is_one_occurrence(self):
        hits = lint.scan_source('P = r"^(RFE-\\d+|RHAIRFE-\\d+)$"\n', _prefixes())
        assert len(hits) == 1
        assert hits[0].prefixes == ("RFE-", "RHAIRFE-")

    def test_each_fstring_literal_part_is_its_own_constant(self):
        source = 'x = f"RFE-{a}RHAIRFE-{b}"\n'
        hits = lint.scan_source(source, _prefixes())
        assert [h.prefixes for h in hits] == [("RFE-",), ("RHAIRFE-",)]
        # Reported at the f-string's own position on every interpreter version.
        assert all((h.line, h.col) == (1, 5) for h in hits)

    def test_nested_fstring_expressions_are_scanned(self):
        source = "x = f\"{key.removeprefix('RHAIRFE-')}\"\n"
        hits = lint.scan_source(source, _prefixes())
        assert [h.prefixes for h in hits] == [("RHAIRFE-",)]

    def test_call_arguments_and_dict_keys_are_code(self):
        source = 'd = {"RHOAIENG-": 1}\nok = k.startswith(("RFE-", "INIT-"))\n'
        hits = lint.scan_source(source, _prefixes())
        assert [h.prefixes for h in hits] == [("RHOAIENG-",), ("RFE-",), ("INIT-",)]

    def test_non_string_constants_are_ignored(self):
        assert lint.scan_source("x = 1\ny = b'RFE-'\nz = None\n", _prefixes()) == []

    def test_occurrence_format(self):
        hits = lint.scan_source(PLANTED, _prefixes(), "scripts/x.py")
        line = hits[0].format()
        assert line == "scripts/x.py:2:21: RHAIRFE- in 'RHAIRFE-' | if k.startswith(\"RHAIRFE-\"):"
        long = lint.Occurrence("p.py", 1, 1, "RFE-" + "x" * 100, ("RFE-",), "code")
        assert long.format().split(" | ")[0].endswith("...")

    def test_excluded_files_and_pycache_are_not_scanned(self, tree):
        for name in sorted(lint.EXCLUDED_FILES):
            _write(tree, f"scripts/{name}", 'X = "RHAIRFE-"\n')
        _write(tree, "scripts/trackers/type_registry.py", 'X = "RHAIRFE-"\n')
        _write(tree, "scripts/__pycache__/junk.py", 'X = "RHAIRFE-"\n')
        _write(tree, "scripts/trackers/jira.py", 'X = "RHAIRFE-"\n')
        _write(tree, "scripts/notes.txt", "RHAIRFE-\n")
        files = lint.iter_scan_files(tree)
        assert [p.relative_to(tree).as_posix() for p in files] == ["scripts/trackers/jira.py"]
        results, errors = lint.scan_tree(tree, _prefixes())
        assert errors == []
        assert lint.counts_of(results) == {"scripts/trackers/jira.py": 1}

    def test_scan_tree_without_scripts_dir(self, tmp_path):
        assert lint.iter_scan_files(tmp_path) == []
        assert lint.scan_tree(tmp_path, _prefixes()) == ({}, [])

    def test_unparsable_file_is_an_error_not_a_crash(self, tree):
        _write(tree, "scripts/bad.py", "def broken(:\n")
        _write(tree, "scripts/good.py", 'X = "RFE-"\n')
        results, errors = lint.scan_tree(tree, _prefixes())
        assert lint.counts_of(results) == {"scripts/good.py": 1}
        assert len(errors) == 1 and errors[0].startswith("ERROR scripts/bad.py: cannot parse")


# ── baseline ratchet ─────────────────────────────────────────────────────────────


class TestBaselineRatchet:
    def test_shipped_baseline_is_clean(self, capsys):
        assert lint.main([]) == 0
        out = capsys.readouterr().out
        assert out.startswith("OK: ")
        assert "within baseline (prefixes: " + ", ".join(EXPECTED_PREFIXES) + ")" in out
        assert "NOTE" not in out

    def test_shipped_baseline_is_a_pure_sorted_count_map(self):
        text = BASELINE.read_text(encoding="utf-8")
        data = json.loads(text)
        assert list(data) == sorted(data)
        assert all(k.startswith("scripts/") and k.endswith(".py") for k in data)
        assert all(isinstance(v, int) and v > 0 for v in data.values())
        assert text == json.dumps(data, indent=2, sort_keys=True) + "\n"

    def test_shipped_baseline_is_tight(self):
        """Every entry equals the current count: a migrated site must also tighten the
        baseline (design §3.3: the baseline only shrinks, never carries stale headroom)."""
        results, errors = lint.scan_tree(REPO_ROOT, _prefixes())
        assert errors == []
        assert lint.counts_of(results) == lint.load_baseline(BASELINE)

    def test_update_baseline_into_a_tmp_path_reproduces_the_shipped_file(self, tmp_path, capsys):
        target = tmp_path / "baseline.json"
        assert lint.main(["--baseline", str(target), "--update-baseline"]) == 0
        assert target.read_bytes() == BASELINE.read_bytes()
        out = capsys.readouterr().out
        assert "WARNING" in out  # every entry "grew" from an absent baseline
        assert out.rstrip().endswith("occurrence(s) of " + ", ".join(EXPECTED_PREFIXES))

    def test_planted_literals_fail_with_file_line_col(self, tree, capsys):
        _write(tree, "scripts/x.py", PLANTED)
        baseline = _baseline(tree, {})
        assert _main(tree, baseline) == 1
        out = capsys.readouterr().out
        assert (
            "ERROR scripts/x.py: 4 literal prefix occurrence(s) in code, not in baseline "
            "-- read the prefix from the type registry instead"
        ) in out
        for line, prefix in ((2, "RHAIRFE-"), (4, "RFE-"), (5, "INIT-"), (6, "issue-snapshot-")):
            assert re.search(rf"^  scripts/x\.py:{line}:\d+: {prefix} in ", out, re.MULTILINE)

    def test_prefix_only_in_docstring_or_comment_passes(self, tree, capsys):
        _write(tree, "scripts/x.py", '"""RHAIRFE-1 docs."""\n# RFE-1\nX = 1\n')
        assert _main(tree, _baseline(tree, {})) == 0
        assert "0 grandfathered occurrence(s) in 0 file(s)" in capsys.readouterr().out

    def test_count_above_baseline_fails(self, tree, capsys):
        _write(tree, "scripts/a.py", 'A = "RFE-"\nB = "INIT-"\n')
        assert _main(tree, _baseline(tree, {"scripts/a.py": 1})) == 1
        out = capsys.readouterr().out
        assert "ERROR scripts/a.py: 2 literal prefix occurrence(s) in code, baseline 1" in out
        assert "  scripts/a.py:1:5: RFE- in 'RFE-' | A = \"RFE-\"" in out
        assert "  scripts/a.py:2:5: INIT- in 'INIT-' | B = \"INIT-\"" in out

    def test_count_equal_to_baseline_passes(self, tree, capsys):
        _write(tree, "scripts/a.py", 'A = "RFE-"\nB = "INIT-"\n')
        assert _main(tree, _baseline(tree, {"scripts/a.py": 2})) == 0
        assert "NOTE" not in capsys.readouterr().out

    def test_count_below_baseline_passes_with_a_note(self, tree, capsys):
        _write(tree, "scripts/a.py", 'A = "RFE-"\nB = "INIT-"\n')
        assert _main(tree, _baseline(tree, {"scripts/a.py": 3})) == 0
        out = capsys.readouterr().out
        assert "NOTE scripts/a.py: 2 < baseline 3; tighten with --update-baseline" in out
        assert out.rstrip().splitlines()[-1].startswith("OK: 1 file(s) scanned, 2 grandfathered")

    def test_baseline_entry_for_a_clean_file_is_a_note(self, tree, capsys):
        _write(tree, "scripts/a.py", "A = 1\n")
        assert _main(tree, _baseline(tree, {"scripts/a.py": 2, "scripts/gone.py": 1})) == 0
        out = capsys.readouterr().out
        assert "NOTE scripts/a.py: 0 < baseline 2" in out
        assert "NOTE scripts/gone.py: 0 < baseline 1" in out

    def test_new_file_with_occurrences_fails_even_if_others_are_fine(self, tree, capsys):
        _write(tree, "scripts/old.py", 'A = "RFE-"\n')
        _write(tree, "scripts/new.py", 'B = "RHOAIENG-"\n')
        assert _main(tree, _baseline(tree, {"scripts/old.py": 1})) == 1
        out = capsys.readouterr().out
        assert (
            "ERROR scripts/new.py: 1 literal prefix occurrence(s) in code, not in baseline" in out
        )
        assert "ERROR scripts/old.py" not in out

    def test_unparsable_file_fails(self, tree, capsys):
        _write(tree, "scripts/bad.py", "def broken(:\n")
        assert _main(tree, _baseline(tree, {})) == 1
        assert "ERROR scripts/bad.py: cannot parse" in capsys.readouterr().out

    def test_update_baseline_writes_sorted_json_idempotently(self, tree, capsys):
        _write(tree, "scripts/z.py", 'A = "RFE-"\n')
        _write(tree, "scripts/a.py", 'A = "RFE-"\nB = "INIT-"\n')
        _write(tree, "scripts/clean.py", "A = 1\n")
        baseline = tree / "nested" / "baseline.json"  # parent dirs are created
        assert _main(tree, baseline, "--update-baseline") == 0
        first = baseline.read_bytes()
        assert json.loads(first) == {"scripts/a.py": 2, "scripts/z.py": 1}
        assert first == b'{\n  "scripts/a.py": 2,\n  "scripts/z.py": 1\n}\n'
        out = capsys.readouterr().out
        assert "WARNING scripts/a.py: baseline grew 0 -> 2" in out
        assert "WARNING scripts/z.py: baseline grew 0 -> 1" in out
        assert _main(tree, baseline, "--update-baseline") == 0
        assert baseline.read_bytes() == first
        assert "WARNING" not in capsys.readouterr().out
        assert _main(tree, baseline) == 0

    def test_update_baseline_shrinks_silently_and_warns_on_growth(self, tree, capsys):
        _write(tree, "scripts/a.py", 'A = "RFE-"\nB = "INIT-"\n')
        baseline = _baseline(tree, {"scripts/a.py": 5, "scripts/gone.py": 2})
        assert _main(tree, baseline, "--update-baseline") == 0
        assert json.loads(baseline.read_text()) == {"scripts/a.py": 2}
        assert "WARNING" not in capsys.readouterr().out
        _write(tree, "scripts/a.py", 'A = "RFE-"\nB = "INIT-"\nC = "RHAIRFE-"\n')
        assert _main(tree, baseline, "--update-baseline") == 0
        assert "WARNING scripts/a.py: baseline grew 2 -> 3" in capsys.readouterr().out

    def test_update_baseline_with_a_parse_error_still_writes_but_fails(self, tree):
        _write(tree, "scripts/bad.py", "def broken(:\n")
        _write(tree, "scripts/a.py", 'A = "RFE-"\n')
        baseline = tree / "baseline.json"
        assert _main(tree, baseline, "--update-baseline") == 1
        assert json.loads(baseline.read_text()) == {"scripts/a.py": 1}

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"scripts/a.py": "1"},
            {"scripts/a.py": True},
            {"scripts/a.py": -1},
            {"scripts/a.py": 1.0},
        ],
    )
    def test_load_baseline_rejects_malformed_shapes(self, tmp_path, payload):
        path = tmp_path / "b.json"
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match="expected"):
            lint.load_baseline(path)

    def test_load_baseline_sorts_keys(self, tmp_path):
        path = tmp_path / "b.json"
        path.write_text('{"scripts/z.py": 1, "scripts/a.py": 0}')
        assert list(lint.load_baseline(path)) == ["scripts/a.py", "scripts/z.py"]

    def test_compare(self):
        counts = {"scripts/a.py": 2, "scripts/b.py": 1, "scripts/c.py": 1}
        baseline = {"scripts/a.py": 1, "scripts/b.py": 3, "scripts/d.py": 2, "scripts/e.py": 0}
        regressions, notes = lint.compare(counts, baseline)
        assert regressions == [
            lint.Regression("scripts/a.py", 2, 1),
            lint.Regression("scripts/c.py", 1, None),
        ]
        assert notes == [
            "NOTE scripts/b.py: 1 < baseline 3; tighten with --update-baseline",
            "NOTE scripts/d.py: 0 < baseline 2; tighten with --update-baseline",
        ]


# ── CLI ──────────────────────────────────────────────────────────────────────────


class TestCli:
    def test_repo_root_invocation_is_green(self):
        result = _cli()
        assert result.returncode == 0, result.stdout + result.stderr
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        expected = re.compile(
            rf"^OK: \d+ file\(s\) scanned, {sum(baseline.values())} grandfathered occurrence\(s\) "
            rf"in {len(baseline)} file\(s\) within baseline \(prefixes: "
            + re.escape(", ".join(EXPECTED_PREFIXES))
            + r"\)\n$"
        )
        assert expected.match(result.stdout), result.stdout
        assert result.stderr == ""

    def test_explicit_defaults_match_the_implicit_ones(self):
        implicit = _cli()
        explicit = _cli("--root", ".", "--baseline", "tests/data/prefix_predicate_baseline.json")
        assert (explicit.returncode, explicit.stdout) == (0, implicit.stdout)

    def test_missing_types_root_is_a_usage_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            lint.main(["--types-root", str(tmp_path / "nope")])
        assert excinfo.value.code == 2
        assert "cannot load the type registry" in capsys.readouterr().err

    def test_empty_types_root_is_a_usage_error(self, tmp_path, capsys):
        (tmp_path / "types").mkdir()
        with pytest.raises(SystemExit) as excinfo:
            lint.main(["--types-root", str(tmp_path / "types")])
        assert excinfo.value.code == 2
        assert "yields no lint prefixes" in capsys.readouterr().err

    def test_missing_baseline_is_a_usage_error(self, tree, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _main(tree, tree / "absent.json")
        assert excinfo.value.code == 2
        assert "create it with --update-baseline" in capsys.readouterr().err

    def test_malformed_baseline_is_a_usage_error(self, tree, capsys):
        bad = tree / "bad.json"
        bad.write_text("[1, 2]")
        with pytest.raises(SystemExit) as excinfo:
            _main(tree, bad)
        assert excinfo.value.code == 2
        broken = tree / "broken.json"
        broken.write_text("{not json")
        with pytest.raises(SystemExit) as excinfo:
            _main(tree, broken)
        assert excinfo.value.code == 2

    def test_root_without_scripts_dir_is_a_usage_error(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            lint.main(["--root", str(tmp_path)])
        assert excinfo.value.code == 2
        assert "has no scripts/ directory" in capsys.readouterr().err

    def test_lint_ignores_extra_types_and_binding_overrides(self, tmp_path, monkeypatch, capsys):
        extra = tmp_path / "extra" / "docs"
        extra.mkdir(parents=True)
        (extra / "type.yaml").write_text(
            "type: docs\nidentity: {tracker: jira, jira: {key_prefixes: ['RHAIDOCS-']}, "
            "local_prefix: 'DOC-'}\n"
        )
        monkeypatch.setenv("RFE_CREATOR_EXTRA_TYPES", str(tmp_path / "extra"))
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_PROJECT", "ACME")
        assert lint.main([]) == 0
        out = capsys.readouterr().out
        assert "RHAIDOCS-" not in out and "ACME-" not in out
        assert "(prefixes: " + ", ".join(EXPECTED_PREFIXES) + ")" in out

    def test_subprocess_fails_on_a_planted_literal(self, tree):
        _write(tree, "scripts/x.py", PLANTED)
        baseline = _baseline(tree, {})
        result = _cli("--root", str(tree), "--baseline", str(baseline))
        assert result.returncode == 1
        assert "ERROR scripts/x.py: 4 literal prefix occurrence(s) in code, not in baseline" in (
            result.stdout
        )
