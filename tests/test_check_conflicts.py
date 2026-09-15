#!/usr/bin/env python3
"""Tests for scripts/check_conflicts.py — registry-derived _TYPE_CONFIG, the resolve / ownership
step, the ``is_existing`` rule and the conflict scan (description and binding verdicts)."""

import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import artifact_utils  # noqa: E402
import check_conflicts  # noqa: E402
import type_registry  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "check_conflicts.py")
REG = type_registry.load(extra_roots=[], env={})

JIRA_ENV = {"JIRA_SERVER": "https://jira.example.com", "JIRA_USER": "u", "JIRA_TOKEN": "t"}

# (tasks dir, originals dir, id field, sample local id, sample jira id) per type — the literal
# layout every artifact on disk follows today.
LAYOUT = {
    "rfe": ("rfe-tasks", "rfe-originals", "rfe_id", "RFE-001", "RHAIRFE-1595"),
    "initiative": (
        "initiatives",
        "initiative-originals",
        "initiative_id",
        "INIT-001",
        "RHOAIENG-12345",
    ),
}


def _task(artifacts, type_name, item_id, status="Draft", extra=""):
    tasks_dir, _, id_field, _, _ = LAYOUT[type_name]
    path = artifacts / tasks_dir / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\n{id_field}: {item_id}\ntitle: T\npriority: Major\nstatus: {status}\n{extra}---\n"
        "\nbody\n"
    )
    return path


def _original(artifacts, type_name, item_id):
    _, originals_dir, _, _, _ = LAYOUT[type_name]
    path = artifacts / originals_dir / f"{item_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {item_id}: T\n\nbody\n")
    return path


def _pair(type_name):
    """The descriptor (project, issue_type) pair — what the fetched issue must show."""
    desc = REG.get(type_name)
    return desc.get("identity.jira.project"), desc.get("identity.jira.issue_type")


def _fields(project, issue_type, description="x"):
    """A fetched ``fields`` mapping carrying the project and issuetype witnesses (D9)."""
    return {
        "description": description,
        "project": {"key": project},
        "issuetype": {"name": issue_type},
    }


def _fake_check(outcomes, seen=None):
    """A ``check_description_conflict`` stand-in: ``outcomes`` maps key -> (has_conflict,
    fields) or an exception to raise; records (key, originals dir, extra_fields) in ``seen``."""

    def fake(server, user, token, key, original_path, extra_fields=None):
        if seen is not None:
            seen.append((key, os.path.basename(os.path.dirname(original_path)), extra_fields))
        outcome = outcomes[key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return fake


def _dropin_root(tmp_path):
    """A minimal third type under a drop-in root (registry env seam, dev/test only)."""
    root = tmp_path / "types"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "type.yaml").write_text(
        textwrap.dedent(
            """\
            schema_version: 1
            type: docs
            identity:
              tracker: jira
              jira: {project: DOCS, issue_type: Task, key_prefixes: ["DOCS-"]}
              local_prefix: "DOC-"
              id_field: doc_id
            dirs: {tasks: artifacts/doc-tasks, originals: artifacts/doc-originals}
            """
        )
    )
    return str(root)


@pytest.fixture
def no_override(monkeypatch):
    """No binding override or shorthand leaks in from the developer's environment."""
    for var in list(os.environ):
        if var.startswith(type_registry.BINDING_ENV_PREFIX):
            monkeypatch.delenv(var)
    for var in ("JIRA_PROJECT", "JIRA_ISSUE_TYPE"):
        monkeypatch.delenv(var, raising=False)


class TestTypeConfigDerivesFromTheRegistry:
    def test_values_are_the_pre_registry_literals(self):
        # Behaviour-neutral migration: same keys, same order, same values. The scanner is no
        # longer a per-type entry: main() calls artifact_utils.scan_tasks with the descriptor.
        # Since PR-3c the write prefix is no longer an entry either: which tasks are existing
        # issues is decided per run against the resolved type's EFFECTIVE binding.
        assert check_conflicts._TYPE_CONFIG == {
            "rfe": {"originals_dir": "rfe-originals", "id_field": "rfe_id"},
            "initiative": {"originals_dir": "initiative-originals", "id_field": "initiative_id"},
        }
        assert list(check_conflicts._TYPE_CONFIG) == ["rfe", "initiative"]
        for config in check_conflicts._TYPE_CONFIG.values():
            assert list(config) == ["originals_dir", "id_field"]
        assert not hasattr(check_conflicts, "_SCAN_FNS")

    @pytest.mark.parametrize("type_name", ["rfe", "initiative"])
    def test_main_scans_the_selected_type_through_the_generic(
        self, no_override, monkeypatch, tmp_path, type_name
    ):
        # The scan is artifact_utils.scan_tasks over the --type descriptor — the same call the
        # per-type wrappers make, so the rows are what scan_task_files /
        # scan_initiative_task_files returned before the pair collapsed.
        for var, value in JIRA_ENV.items():
            monkeypatch.setenv(var, value)
        calls = []

        def recording_scan(artifacts_dir, desc):
            calls.append((artifacts_dir, desc.name))
            return artifact_utils.scan_tasks(artifacts_dir, desc)

        monkeypatch.setattr(check_conflicts, "scan_tasks", recording_scan)
        monkeypatch.setattr(
            sys,
            "argv",
            ["check_conflicts.py", "--type", type_name, "--artifacts-dir", str(tmp_path)],
        )
        with pytest.raises(SystemExit) as excinfo:
            check_conflicts.main()
        assert excinfo.value.code == 0
        assert calls == [(str(tmp_path), type_name)]

    @pytest.mark.parametrize("type_name", REG.names())
    def test_projection_from_the_descriptor(self, type_name):
        desc = REG.get(type_name)
        tc = check_conflicts._TYPE_CONFIG[type_name]
        assert tc["originals_dir"] == desc.dirs(form="bare")["originals"]
        assert tc["id_field"] == desc.id_field
        assert "jira_prefix" not in tc

    def test_type_choices_are_the_registry_choices(self):
        result = subprocess.run(["python3", SCRIPT, "--help"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "--type {rfe,initiative}" in result.stdout

    def test_a_drop_in_type_is_offered_and_scanned(self, tmp_path):
        # A third registered type used to be refused (exit 2, "no task scanner is registered")
        # because the scanners were a forked pair; the generic scans its dirs.tasks like any other.
        root = _dropin_root(tmp_path)
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("RFE_CREATOR_")},
            **JIRA_ENV,
            "RFE_CREATOR_EXTRA_TYPES": root,
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": root,
        }
        result = subprocess.run(
            ["python3", SCRIPT, "--help"], capture_output=True, text=True, env=env
        )
        assert "--type {rfe,docs,initiative}" in result.stdout
        result = subprocess.run(
            ["python3", SCRIPT, "--type", "docs", "--artifacts-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert result.stdout == "CONFLICT_COUNT=0\nOK: no Jira-sourced items to check\n"
        # D3: an explicit --type is rung 1 and prints the resolve line — on stderr, never on
        # the CONFLICT_COUNT protocol.
        assert result.stderr == "TYPE RESOLVED: docs (--type)\n"


class TestIsExisting:
    """The design §5 rule: frontmatter ``tracker_ref`` owned by the resolved type, else — for a
    pre-migration artifact without the field — membership in the effective key-prefix union."""

    PREFIXES = ["RHAIRFE-"]

    def test_owned_tracker_ref_is_existing(self):
        task = {"rfe_id": "RHAIRFE-1", "tracker_ref": "RHAIRFE-1"}
        assert check_conflicts._is_existing(task, "RHAIRFE-1", "rfe", self.PREFIXES) is True

    def test_foreign_tracker_ref_is_a_hard_error_not_a_create(self):
        task = {"rfe_id": "RHAIRFE-1", "tracker_ref": "RHOAIENG-1"}
        with pytest.raises(check_conflicts.ForeignTrackerRefError) as exc:
            check_conflicts._is_existing(task, "RHAIRFE-1", "rfe", self.PREFIXES)
        assert str(exc.value) == (
            "RHAIRFE-1: tracker_ref 'RHOAIENG-1' is not owned by the resolved type rfe (key "
            "prefixes RHAIRFE-); a task of another type is never updated as this one — fix the "
            "artifact or pass the type that owns it"
        )
        # A local id is not a tracker key either: the field names a remote reference only.
        with pytest.raises(check_conflicts.ForeignTrackerRefError):
            check_conflicts._is_existing(
                {"rfe_id": "RFE-001", "tracker_ref": "RFE-001"}, "RFE-001", "rfe", self.PREFIXES
            )

    def test_legacy_artifact_falls_back_to_the_key_prefix_union(self):
        assert check_conflicts._is_existing(
            {"rfe_id": "RHAIRFE-1"}, "RHAIRFE-1", "rfe", ["RHAIRFE-"]
        )
        assert not check_conflicts._is_existing(
            {"rfe_id": "RFE-001"}, "RFE-001", "rfe", ["RHAIRFE-"]
        )
        # Empty and null tracker_ref count as absent (frontmatter.py writes null for "none yet").
        for absent in ("", None):
            task = {"rfe_id": "RHAIRFE-1", "tracker_ref": absent}
            assert check_conflicts._is_existing(task, "RHAIRFE-1", "rfe", ["RHAIRFE-"])
        # A read prefix (the descriptor's, kept under a project override) counts too.
        union = ["KONFLUX-", "RHAIRFE-"]
        assert check_conflicts._is_existing({"rfe_id": "KONFLUX-5"}, "KONFLUX-5", "rfe", union)
        assert check_conflicts._is_existing({"rfe_id": "RHAIRFE-5"}, "RHAIRFE-5", "rfe", union)
        task = {"rfe_id": "RHAIRFE-5", "tracker_ref": "RHAIRFE-5"}
        assert check_conflicts._is_existing(task, "RHAIRFE-5", "rfe", union)

    def test_the_prefixes_are_the_effective_binding_union(self):
        desc = REG.get("rfe")
        env = {type_registry.binding_env_var("rfe", "PROJECT"): "KONFLUX"}
        assert desc.binding(env)["key_prefixes"] == ["KONFLUX-", "RHAIRFE-"]


class TestBindingMismatch:
    BINDING = {"project": "RHAIRFE", "issue_type": "Feature Request"}

    def test_matching_pair_is_no_conflict(self):
        fields = _fields("RHAIRFE", "Feature Request")
        assert check_conflicts._binding_mismatch(fields, "rfe", self.BINDING) is None

    def test_other_pair_is_named(self):
        fields = _fields("RHOAIENG", "Initiative")
        assert check_conflicts._binding_mismatch(fields, "rfe", self.BINDING) == (
            "is (RHOAIENG, Initiative) in Jira but the resolved type rfe binds "
            "(RHAIRFE, Feature Request)"
        )

    def test_missing_witness_fails_closed(self):
        assert check_conflicts._binding_mismatch({"description": "x"}, "rfe", self.BINDING) == (
            "cannot verify against the resolved type rfe binding (RHAIRFE, Feature Request): "
            "the fetched issue has no project or issuetype field"
        )
        only_project = {"description": "x", "project": {"key": "RHAIRFE"}}
        assert "has no issuetype field" in check_conflicts._binding_mismatch(
            only_project, "rfe", self.BINDING
        )
        assert "has no project or issuetype field" in check_conflicts._binding_mismatch(
            None, "rfe", self.BINDING
        )


class TestMain:
    @pytest.fixture
    def jira(self, no_override, monkeypatch):
        for var, value in JIRA_ENV.items():
            monkeypatch.setenv(var, value)

    def _main(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["check_conflicts.py", *args])
        with pytest.raises(SystemExit) as excinfo:
            check_conflicts.main()
        return excinfo.value.code

    def test_missing_env_exits_two(self, no_override, monkeypatch, capsys, tmp_path):
        for var in JIRA_ENV:
            monkeypatch.delenv(var, raising=False)
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 2
        assert "JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required" in capsys.readouterr().err

    @pytest.mark.parametrize("type_name", ["rfe", "initiative"])
    def test_no_jira_sourced_items(self, jira, monkeypatch, capsys, tmp_path, type_name):
        _, _, _, local_id, jira_id = LAYOUT[type_name]
        _task(tmp_path, type_name, local_id)  # local id: never checked
        _task(tmp_path, type_name, jira_id)  # jira id without an original: skipped
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            lambda *a, **k: pytest.fail("no fetch expected"),
        )
        code = self._main(monkeypatch, "--type", type_name, "--artifacts-dir", str(tmp_path))
        assert code == 0
        out = capsys.readouterr()
        assert out.out == "CONFLICT_COUNT=0\nOK: no Jira-sourced items to check\n"
        assert out.err == f"TYPE RESOLVED: {type_name} (--type)\n"

    @pytest.mark.parametrize("type_name", ["rfe", "initiative"])
    def test_conflicts_are_reported_per_item(self, jira, monkeypatch, capsys, tmp_path, type_name):
        _, _, _, _, jira_id = LAYOUT[type_name]
        prefix = jira_id.rsplit("-", 1)[0] + "-"
        same, changed, archived, gone = (f"{prefix}{n}" for n in (9001, 9002, 9003, 9005))
        for item in (same, changed, gone):
            _task(tmp_path, type_name, item)
            _original(tmp_path, type_name, item)
        _task(tmp_path, type_name, archived, status="Archived")
        _original(tmp_path, type_name, archived)

        seen = []
        project, issue_type = _pair(type_name)
        outcomes = {
            same: (False, _fields(project, issue_type)),
            changed: (True, _fields(project, issue_type)),
            gone: RuntimeError("HTTP Error 404: Not Found"),
        }
        monkeypatch.setattr(
            check_conflicts, "check_description_conflict", _fake_check(outcomes, seen)
        )
        code = self._main(monkeypatch, "--type", type_name, "--artifacts-dir", str(tmp_path))
        out = capsys.readouterr()
        assert code == 1
        assert out.out == (
            f"CONFLICT_COUNT=1\nCONFLICT: {changed} — modified in Jira since last fetch\n"
        )
        assert out.err == (
            f"TYPE RESOLVED: {type_name} (--type)\n"
            f"Warning: could not fetch {gone}: HTTP Error 404: Not Found\n"
        )
        originals_dir = LAYOUT[type_name][1]
        # The one fetch requests the project and issuetype witnesses (D9) for the verdict.
        witnesses = ["project", "issuetype"]
        assert seen == [
            (same, originals_dir, witnesses),
            (changed, originals_dir, witnesses),
            (gone, originals_dir, witnesses),
        ]

    def test_no_conflicts_exits_zero(self, jira, monkeypatch, capsys, tmp_path):
        _task(tmp_path, "rfe", "RHAIRFE-1")
        _original(tmp_path, "rfe", "RHAIRFE-1")
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            _fake_check({"RHAIRFE-1": (False, _fields(*_pair("rfe")))}),
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 0
        out = capsys.readouterr()
        assert out.out == "CONFLICT_COUNT=0\nOK: no conflicts detected\n"
        assert out.err == ""  # the legacy default rung is silent (D3)

    def test_type_selects_the_tree(self, jira, monkeypatch, capsys, tmp_path):
        # An initiative tree scanned as rfe (the default) has nothing to check, and vice versa.
        _task(tmp_path, "initiative", "RHOAIENG-1")
        _original(tmp_path, "initiative", "RHOAIENG-1")
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            lambda *a, **k: pytest.fail("no fetch expected"),
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 0
        assert "no Jira-sourced items" in capsys.readouterr().out

    def test_stamped_task_is_checked_under_its_tracker_ref(
        self, jira, monkeypatch, capsys, tmp_path
    ):
        _task(tmp_path, "rfe", "RHAIRFE-1", extra="type: rfe\ntracker_ref: RHAIRFE-1\n")
        _original(tmp_path, "rfe", "RHAIRFE-1")
        seen = []
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            _fake_check({"RHAIRFE-1": (True, _fields(*_pair("rfe")))}, seen),
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 1
        assert capsys.readouterr().out == (
            "CONFLICT_COUNT=1\nCONFLICT: RHAIRFE-1 — modified in Jira since last fetch\n"
        )
        assert [key for key, _, _ in seen] == ["RHAIRFE-1"]

    def test_foreign_tracker_ref_is_a_hard_error_before_any_fetch(
        self, jira, monkeypatch, capsys, tmp_path
    ):
        path = _task(tmp_path, "rfe", "RHAIRFE-1", extra="type: rfe\ntracker_ref: RHOAIENG-1\n")
        _original(tmp_path, "rfe", "RHAIRFE-1")
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            lambda *a, **k: pytest.fail("no fetch expected"),
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 2
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == (
            f"Error: {path}: RHAIRFE-1: tracker_ref 'RHOAIENG-1' is not owned by the resolved "
            "type rfe (key prefixes RHAIRFE-); a task of another type is never updated as this "
            "one — fix the artifact or pass the type that owns it\n"
        )

    def test_binding_mismatch_is_a_conflict_for_that_id(self, jira, monkeypatch, capsys, tmp_path):
        # One fetch, one line per id: the binding verdict outranks the description verdict, an
        # issue that matches the binding keeps today's message, a response without the
        # witnesses fails closed — and nothing is ever written (the script only reads).
        for item in ("RHAIRFE-1", "RHAIRFE-2", "RHAIRFE-3", "RHAIRFE-4"):
            _task(tmp_path, "rfe", item)
            _original(tmp_path, "rfe", item)
        outcomes = {
            "RHAIRFE-1": (True, _fields("RHOAIENG", "Initiative")),
            "RHAIRFE-2": (True, _fields("RHAIRFE", "Feature Request")),
            "RHAIRFE-3": (False, {"description": "x"}),
            "RHAIRFE-4": (False, _fields("RHAIRFE", "Feature Request")),
        }
        monkeypatch.setattr(check_conflicts, "check_description_conflict", _fake_check(outcomes))
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 1
        out = capsys.readouterr()
        assert out.out == (
            "CONFLICT_COUNT=3\n"
            "CONFLICT: RHAIRFE-1 — is (RHOAIENG, Initiative) in Jira but the resolved type rfe "
            "binds (RHAIRFE, Feature Request)\n"
            "CONFLICT: RHAIRFE-2 — modified in Jira since last fetch\n"
            "CONFLICT: RHAIRFE-3 — cannot verify against the resolved type rfe binding "
            "(RHAIRFE, Feature Request): the fetched issue has no project or issuetype field\n"
        )
        assert out.err == ""

    def test_a_pre_override_item_is_accepted_under_the_override(
        self, jira, monkeypatch, capsys, tmp_path
    ):
        # RFE_CREATOR_BINDING_RFE_PROJECT=KONFLUX: an RHAIRFE issue is still owned (the
        # descriptor prefix is kept as a read prefix, so it IS checked) and, carrying the
        # descriptor prefix, its descriptor pair is accepted — an item created before the
        # override is a legitimate item of the type, not a conflict. An RHAIRFE Epic is. (An
        # in-process run keeps the task schema artifact_utils built at import, so a
        # KONFLUX-keyed task itself is exercised by the subprocess suites and the unit tests on
        # _binding_mismatch, not here.)
        monkeypatch.setenv(type_registry.binding_env_var("rfe", "PROJECT"), "KONFLUX")
        _task(tmp_path, "rfe", "RHAIRFE-1")
        _original(tmp_path, "rfe", "RHAIRFE-1")
        _task(tmp_path, "rfe", "RHAIRFE-2")
        _original(tmp_path, "rfe", "RHAIRFE-2")
        outcomes = {
            "RHAIRFE-1": (False, _fields("RHAIRFE", "Feature Request")),
            "RHAIRFE-2": (False, _fields("RHAIRFE", "Epic")),
        }
        monkeypatch.setattr(check_conflicts, "check_description_conflict", _fake_check(outcomes))
        code = self._main(monkeypatch, "--type", "rfe", "--artifacts-dir", str(tmp_path))
        assert code == 1
        out = capsys.readouterr()
        assert out.out == (
            "CONFLICT_COUNT=1\n"
            "CONFLICT: RHAIRFE-2 — is (RHAIRFE, Epic) in Jira but the resolved type rfe binds "
            "(KONFLUX, Feature Request) or, for a pre-override key, (RHAIRFE, Feature Request)\n"
        )
        assert out.err == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)\n"

    def test_the_shorthand_is_refused_before_any_access(self, jira, monkeypatch, capsys, tmp_path):
        # The bare JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand reaches resolve but not the artifact
        # layer the writers share: exit 2 with one line naming the typed variables, no scan.
        monkeypatch.setenv("JIRA_PROJECT", "KONFLUX")
        monkeypatch.setattr(
            check_conflicts, "scan_tasks", lambda *a, **k: pytest.fail("no scan expected")
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 2
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == (
            "Error: JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is not honoured by the artifact "
            "layer; set RFE_CREATOR_BINDING_RFE_PROJECT / _ISSUE_TYPE instead\n"
        )

    def test_the_remote_key_is_the_tracker_ref_not_the_id(
        self, jira, monkeypatch, capsys, tmp_path
    ):
        # rfe_id RHAIRFE-1 with tracker_ref RHAIRFE-2: fetched (and its original looked up) as
        # RHAIRFE-2 — the one remote key, as submit.py names it too; the verdict lists the id.
        _task(tmp_path, "rfe", "RHAIRFE-1", extra="type: rfe\ntracker_ref: RHAIRFE-2\n")
        _original(tmp_path, "rfe", "RHAIRFE-2")
        seen = []
        monkeypatch.setattr(
            check_conflicts,
            "check_description_conflict",
            _fake_check({"RHAIRFE-2": (True, _fields(*_pair("rfe")))}, seen),
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 1
        assert capsys.readouterr().out == (
            "CONFLICT_COUNT=1\nCONFLICT: RHAIRFE-1 — modified in Jira since last fetch\n"
        )
        assert [key for key, _, _ in seen] == ["RHAIRFE-2"]

    def test_a_binding_owned_by_another_type_exits_two_before_any_access(
        self, jira, monkeypatch, capsys, tmp_path
    ):
        monkeypatch.setenv(type_registry.binding_env_var("rfe", "PROJECT"), "RHOAIENG")
        monkeypatch.setenv(type_registry.binding_env_var("rfe", "ISSUE_TYPE"), "Initiative")
        monkeypatch.setattr(
            check_conflicts, "scan_tasks", lambda *a, **k: pytest.fail("no scan expected")
        )
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 2
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == (
            "Error: rfe: effective binding ('jira', 'RHOAIENG', 'Initiative') (source: env) is "
            "the binding registered for type 'initiative'; a tracker binding must be owned by "
            "exactly one type (design §3.3 rule 1) — fix the override or pass --type initiative\n"
        )

    def test_a_malformed_override_exits_two(self, jira, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv(type_registry.binding_env_var("rfe", "PROJECT"), "bad-key")
        code = self._main(monkeypatch, "--artifacts-dir", str(tmp_path))
        assert code == 2
        assert capsys.readouterr().err == (
            "Error: RFE_CREATOR_BINDING_RFE_PROJECT='bad-key': expected an upper-case tracker "
            "project key\n"
        )


class TestBindingMismatchAcceptsPreOverrideKeys:
    """``_binding_mismatch`` over ``Descriptor.accepted_pairs``: the effective pair always, the
    descriptor pair too for a key carrying a descriptor read prefix."""

    def _mismatch(self, fields, key, env=None):
        binding = REG.get("rfe").binding(env or {})
        return check_conflicts._binding_mismatch(fields, "rfe", binding, key)

    def test_no_override_is_the_descriptor_pair_for_every_key(self):
        rfe_pair = _fields("RHAIRFE", "Feature Request")
        assert self._mismatch(rfe_pair, "RHAIRFE-1") is None
        assert self._mismatch(_fields("RHAIRFE", "Epic"), "RHAIRFE-1") == (
            "is (RHAIRFE, Epic) in Jira but the resolved type rfe binds (RHAIRFE, Feature Request)"
        )
        # The key is optional (older callers): the effective pair alone then.
        binding = REG.get("rfe").binding({})
        assert check_conflicts._binding_mismatch(rfe_pair, "rfe", binding) is None

    def test_under_a_project_override(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        assert self._mismatch(_fields("RHAIRFE", "Feature Request"), "RHAIRFE-1", env) is None
        assert self._mismatch(_fields("KONFLUX", "Feature Request"), "KONFLUX-1", env) is None
        assert self._mismatch(_fields("KONFLUX", "Epic"), "KONFLUX-1", env) == (
            "is (KONFLUX, Epic) in Jira but the resolved type rfe binds (KONFLUX, Feature Request)"
        )
        # The descriptor pair is accepted for a descriptor-prefixed key only.
        assert self._mismatch(_fields("RHAIRFE", "Feature Request"), "KONFLUX-1", env) == (
            "is (RHAIRFE, Feature Request) in Jira but the resolved type rfe binds "
            "(KONFLUX, Feature Request)"
        )
        assert self._mismatch({"description": "x"}, "RHAIRFE-1", env) == (
            "cannot verify against the resolved type rfe binding (KONFLUX, Feature Request) or, "
            "for a pre-override key, (RHAIRFE, Feature Request): the fetched issue has no project "
            "or issuetype field"
        )
