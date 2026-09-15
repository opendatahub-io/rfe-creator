#!/usr/bin/env python3
"""Tests for scripts/split_submit.py — guardrails and ADF output."""

import io
import os
import subprocess
import sys
import urllib.error

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import split_submit
import type_registry
from artifact_utils import read_frontmatter_validated
from split_submit import (
    EXIT_PER_PARENT,
    EXIT_SYSTEMIC,
    SPLIT_CONFIG,
    SubmissionState,
    _classify_exit,
    build_split_summary_adf,
    phase3_close,
)

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "split_submit.py")
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
EXTRA_TYPES = os.path.join(os.path.dirname(__file__), "fixtures", "types")


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


PARENT_TASK = """\
---
rfe_id: RHAIRFE-1000
title: Parent RFE
priority: Major
status: Archived
---

## Problem Statement

Original parent content.
"""

CHILD_TASK = """\
---
rfe_id: RFE-{num:03d}
title: Child RFE {num}
priority: Major
status: Ready
parent_key: RHAIRFE-1000
---

## Problem Statement

Child {num} content.
"""


def _run_split_submit(artifacts_dir, parent_key="RHAIRFE-1000"):
    """Run split_submit.py --dry-run and return result."""
    env = {
        **os.environ,
        "JIRA_SERVER": "",
        "JIRA_USER": "",
        "JIRA_TOKEN": "",
    }
    return subprocess.run(
        [sys.executable, SCRIPT, parent_key, "--dry-run", "--artifacts-dir", artifacts_dir],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def art_dir(tmp_path):
    """Create a minimal artifacts directory."""
    for d in ["rfe-tasks", "rfe-reviews"]:
        os.makedirs(tmp_path / d)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


class TestMaxLeafChildren:
    def test_exits_code_2_when_over_limit(self, art_dir):
        """More than MAX_LEAF_CHILDREN → exit code 2."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 8):  # 7 children > 6 limit
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        assert result.returncode == 2
        assert "Refusing to submit" in result.stderr
        assert "requires human review" in result.stderr
        assert "7 leaf children" in result.stderr

    def test_accepts_at_limit(self, art_dir):
        """Exactly MAX_LEAF_CHILDREN → proceeds (no exit code 2)."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 7):  # 6 children = limit
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        # Should not exit with code 2 (may fail for other reasons
        # in dry-run without Jira creds, but NOT the cap)
        assert result.returncode != 2
        assert "Refusing to submit" not in result.stderr

    def test_accepts_under_limit(self, art_dir):
        """Fewer than MAX_LEAF_CHILDREN → proceeds."""
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, 4):  # 3 children
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", CHILD_TASK.format(num=i))

        result = _run_split_submit(art_dir)
        assert result.returncode != 2
        assert "Refusing to submit" not in result.stderr


class TestSplitSummaryAdf:
    def test_produces_inline_cards(self):
        """Summary ADF uses inlineCard nodes for child keys."""
        state = SubmissionState()
        state.phase2_done = {
            "RFE-001": {"key": "RHAIRFE-100", "linked": True, "commented": True},
            "RFE-002": {"key": "RHAIRFE-101", "linked": True, "commented": True},
        }
        children = [
            ("RFE-001", "First child", "Major", "/fake/path1"),
            ("RFE-002", "Second child", "Major", "/fake/path2"),
        ]
        from split_submit import SPLIT_CONFIG

        rfe_config = SPLIT_CONFIG["rfe"]
        adf = build_split_summary_adf("https://jira.example.com", children, state, 2, rfe_config)

        # Top-level structure
        assert adf["type"] == "doc"
        content = adf["content"]
        assert len(content) == 3  # paragraph, bulletList, paragraph
        assert content[1]["type"] == "bulletList"

        # Correct number of list items
        items = content[1]["content"]
        assert len(items) == 2

        # Each item has inlineCard with correct URL
        for i, item in enumerate(items):
            para = item["content"][0]
            inline_card = para["content"][0]
            assert inline_card["type"] == "inlineCard"
            expected_key = state.phase2_done[children[i][0]]["key"]
            assert inline_card["attrs"]["url"] == f"https://jira.example.com/browse/{expected_key}"

    def test_strips_trailing_slash(self):
        """Trailing slash on server URL does not produce double slash."""
        from split_submit import SPLIT_CONFIG

        rfe_config = SPLIT_CONFIG["rfe"]
        state = SubmissionState()
        state.phase2_done = {"RFE-001": {"key": "RHAIRFE-100", "linked": True, "commented": True}}
        children = [("RFE-001", "Child", "Major", "/fake/path")]
        adf = build_split_summary_adf("https://jira.example.com/", children, state, 1, rfe_config)

        item = adf["content"][1]["content"][0]
        url = item["content"][0]["content"][0]["attrs"]["url"]
        assert "//" not in url.replace("https://", "")


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(b""))


class TestClassifyExit:
    """The exit-code contract submit.py's loop policy relies on: systemic
    failures (dead auth, outage) fail every parent identically; everything
    else says nothing about the next parent (RHAIFIRST-571)."""

    @pytest.mark.parametrize("code", [401, 403, 429, 500, 501, 502, 503, 504, 521])
    def test_systemic_http_codes(self, code):
        """Any 5xx is a server-side fault: a bare 500 is re-raised by
        api_call_with_retry without retrying, and misreading an
        instance-wide 500 as per-parent fans out across the batch
        (CodeRabbit on #170)."""
        assert _classify_exit(_http_error(code)) == EXIT_SYSTEMIC

    @pytest.mark.parametrize("code", [400, 404, 409, 422])
    def test_per_parent_http_codes(self, code):
        assert _classify_exit(_http_error(code)) == EXIT_PER_PARENT

    def test_network_error_is_systemic(self):
        assert _classify_exit(urllib.error.URLError("no route")) == EXIT_SYSTEMIC

    def test_local_errors_are_per_parent(self):
        assert _classify_exit(ValueError("bad artifact")) == EXIT_PER_PARENT
        assert _classify_exit(FileNotFoundError("gone")) == EXIT_PER_PARENT


class TestUsageExitCode:
    def test_malformed_argv_exits_64_not_2(self):
        """argparse's default exit 2 reads as the leaf-cap refusal to
        submit.py — usage errors must be distinguishable."""
        r = subprocess.run(
            [sys.executable, SCRIPT, "--no-such-flag"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 64


# ─── Registry projection (design work-item-types-unified.md §10 item 2) ──────


# The SPLIT_CONFIG entry shape the phases and the integration tests read — frozen.
SPLIT_CONFIG_KEYS = [
    "type",
    "project",
    "issue_type",
    "comment_marker",
    "label_prefix",
    "entity_name",
    "entity_name_plural",
    "id_field",
    "reviews_dir",
    "review_schema",
    "originals_dir",
    "scan_fn",
    "rename_fn",
    "parse_child_fn",
    "find_review_fn",
    "do_rebuild_index",
    "alignment_labels",
]
TYPES = split_submit._TYPES.names()


def _clean_env(**extra):
    """The developer's registry seams and the headless/CI markers stay out of subprocess
    runs (a CI runner's ``CI`` would otherwise gate RFE_CREATOR_EXTRA_TYPES)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("RFE_CREATOR_") and k not in type_registry.HEADLESS_MARKER_VARS
    }
    env.update({"JIRA_SERVER": "", "JIRA_USER": "", "JIRA_TOKEN": ""})
    env.update(extra)
    return env


def _fm(fields, body="Body.\n"):
    return "---\n" + yaml.safe_dump(fields, sort_keys=False) + "---\n\n" + body


def _task_fields(desc, item_id, **extra):
    fields = {desc.id_field: item_id, "title": "Child", "priority": "Major", "status": "Ready"}
    fields.update(extra)
    return fields


def _review_fields(desc, item_id, **extra):
    fields = {
        desc.id_field: item_id,
        "score": 9,
        "pass": True,
        "recommendation": "submit",
        "feasibility": "feasible",
        "auto_revised": False,
        "needs_attention": False,
        "scores": {name: 2 for name in desc.score_fields},
    }
    fields.update(extra)
    return fields


@pytest.fixture(params=TYPES, ids=TYPES)
def desc(request):
    return split_submit._TYPES.get(request.param)


class TestSplitConfigProjection:
    """SPLIT_CONFIG is a projection of the type registry: one entry per registered type in
    ``choices()`` order, the frozen key shape, and callables bound to the type's descriptor
    that keep the ``(artifacts_dir, ...)`` signatures of the per-type wrappers they replace."""

    def test_one_entry_per_registered_type_with_the_frozen_key_order(self):
        assert list(SPLIT_CONFIG) == split_submit._TYPES.choices()
        for config in SPLIT_CONFIG.values():
            assert list(config) == SPLIT_CONFIG_KEYS

    def test_scalar_values_project_the_descriptor(self, desc):
        config = SPLIT_CONFIG[desc.name]
        jira = desc.get("identity.jira")
        bare = desc.dirs("bare")
        assert config["type"] == desc.name
        assert (config["project"], config["issue_type"]) == (jira["project"], jira["issue_type"])
        assert config["comment_marker"] == desc.get("conventions.comment_prefix")
        assert config["label_prefix"] == desc.get("conventions.label_prefix")
        assert config["entity_name"] == desc.get("display.entity")
        assert config["entity_name_plural"] == desc.get("display.entity_plural")
        assert config["id_field"] == desc.id_field
        assert (config["reviews_dir"], config["originals_dir"]) == (
            bare["reviews"],
            bare["originals"],
        )
        assert config["review_schema"] == f"{desc.name}-review"
        assert config["do_rebuild_index"] is desc.get("index.enabled")
        assert config["alignment_labels"] == desc.get("conventions.labels.alignment", None)

    def test_callables_are_bound_to_the_type(self, desc, tmp_path):
        config = SPLIT_CONFIG[desc.name]
        bare = desc.dirs("bare")
        local, key = f"{desc.local_prefix}001", f"{desc.write_prefix}4321"
        task = tmp_path / bare["tasks"] / f"{local}.md"
        _write(str(task), _fm(_task_fields(desc, local), "## Problem\n\nChild body.\n"))
        _write(str(tmp_path / bare["tasks"] / f"{local}-comments.md"), "# Comments\n")
        review = tmp_path / bare["reviews"] / f"{local}-review.md"
        _write(str(review), _fm(_review_fields(desc, local)))

        # scan_fn: this type's dirs.tasks, validated as <type>-task, companions skipped
        tasks = config["scan_fn"](str(tmp_path))
        assert [(os.path.basename(p), d[desc.id_field]) for p, d in tasks] == [
            (f"{local}.md", local)
        ]

        # parse_child_fn: (title, priority, full_markdown, cleaned_markdown)
        title, priority, full, cleaned = config["parse_child_fn"](str(task))
        assert (title, priority) == ("Child", "Major")
        assert "Child body." in full and "Child body." in cleaned

        # find_review_fn: this type's dirs.reviews, by local id and by tracker key
        assert config["find_review_fn"](str(tmp_path), local) == str(review)
        assert config["find_review_fn"](str(tmp_path), f"{desc.local_prefix}999") is None

        # rename_fn: task + companions + review move to the tracker key
        config["rename_fn"](str(tmp_path), local, key)
        renamed_task = tmp_path / bare["tasks"] / f"{key}.md"
        renamed_review = tmp_path / bare["reviews"] / f"{key}-review.md"
        assert renamed_task.is_file() and renamed_review.is_file() and not task.exists()
        assert (tmp_path / bare["tasks"] / f"{key}-comments.md").is_file()
        data, _ = read_frontmatter_validated(str(renamed_task), f"{desc.name}-task")
        assert (data[desc.id_field], data["status"], data["local_id"]) == (key, "Submitted", local)
        assert config["find_review_fn"](str(tmp_path), key) == str(renamed_review)

    def test_a_third_type_projects_without_code_changes(self):
        reg = type_registry.load(
            root=os.path.join(REPO_ROOT, "types"), extra_roots=[EXTRA_TYPES], env={}
        )
        config = split_submit._split_config(reg.get("epic"))
        assert list(config) == SPLIT_CONFIG_KEYS
        assert config["type"] == "epic"
        assert (config["project"], config["issue_type"]) == ("RHAI", "Epic")
        assert (config["entity_name"], config["entity_name_plural"]) == ("Epic", "Epics")
        assert (config["id_field"], config["reviews_dir"], config["review_schema"]) == (
            "epic_id",
            "epic-reviews",
            "epic-review",
        )
        assert config["do_rebuild_index"] is False and config["alignment_labels"] is None
        callables = ("scan_fn", "rename_fn", "parse_child_fn", "find_review_fn")
        assert all(callable(config[k]) for k in callables)

    def test_drop_in_type_appears_in_the_config_and_the_type_choices(self, tmp_path):
        env = _clean_env(RFE_CREATOR_EXTRA_TYPES=EXTRA_TYPES)
        r = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=env
        )
        assert r.returncode == 0, r.stderr
        assert "{rfe,epic,initiative}" in r.stdout
        # The epic fixture never splits (no split_link_type, state_map {}), so a split of one
        # is refused up front — before the scan that would otherwise report no epic files.
        r = subprocess.run(
            [sys.executable, SCRIPT, "RHAI-1", "--dry-run", "--type", "epic"]
            + ["--artifacts-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == split_submit.EXIT_SYSTEMIC
        assert "type 'epic' declares no identity.jira.split_link_type" in r.stderr
        assert "it cannot be split." in r.stderr
        assert "No epic files found" not in r.stderr


class TestTrackerFactsFromTheBinding:
    """The split link type and the close-superseded transition/resolution come from the
    type's Jira binding (identity.jira.split_link_type / state_map.close_superseded),
    recovered from a SPLIT_CONFIG entry through its ``type`` name (PR-3 D12) — never through
    the (project, issue_type) pair, which a deployment override changes."""

    def test_tracker_projects_the_binding(self, desc):
        facts = split_submit._tracker_for(SPLIT_CONFIG[desc.name])
        assert facts["split_link_type"] == desc.get("identity.jira.split_link_type")
        assert facts["close_superseded"] == desc.get("identity.jira.state_map.close_superseded")
        assert facts["split_link_type"]
        assert facts["close_superseded"]["transition"] and facts["close_superseded"]["resolution"]

    def test_tracker_is_keyed_by_type_name(self, desc):
        # D12: the facts survive a binding override because the lookup never sees the pair.
        assert list(split_submit._TRACKER) == split_submit._TYPES.names()
        env = {
            type_registry.binding_env_var(desc.name, "PROJECT"): "KONFLUX",
            type_registry.binding_env_var(desc.name, "ISSUE_TYPE"): "Story",
        }
        overridden = split_submit._split_config(desc, desc.binding(env))
        assert (overridden["project"], overridden["issue_type"]) == ("KONFLUX", "Story")
        assert split_submit._tracker_for(overridden) is split_submit._TRACKER[desc.name]
        assert split_submit._tracker_for(overridden) == split_submit._tracker_for(
            SPLIT_CONFIG[desc.name]
        )
        assert split_submit._missing_split_facts(overridden) == []

    def test_inspect_child_derives_linked_from_the_binding_link_type(
        self, desc, tmp_path, monkeypatch
    ):
        config = SPLIT_CONFIG[desc.name]
        parent, key = f"{desc.write_prefix}1000", f"{desc.write_prefix}1001"
        child = tmp_path / f"{desc.local_prefix}001.md"
        _write(str(child), _fm(_task_fields(desc, f"{desc.local_prefix}001")))
        live = {}

        def fake_get_issue(server, user, token, issue_key, fields=None):
            assert issue_key == key
            link = {"type": {"name": live["link"]}, "inwardIssue": {"key": parent}}
            return {"fields": {"summary": "Child", "description": None, "issuelinks": [link]}}

        monkeypatch.setattr(split_submit, "get_issue", fake_get_issue)
        live["link"] = desc.get("identity.jira.split_link_type")
        seen = split_submit._inspect_child("s", "u", "t", key, str(child), parent, config)
        assert seen["linked"] is True and seen["title"] == "Child"
        live["link"] = "Relates"
        seen = split_submit._inspect_child("s", "u", "t", key, str(child), parent, config)
        assert seen["linked"] is False

    def _closable(self, desc):
        parent = f"{desc.write_prefix}1000"
        child_id = f"{desc.local_prefix}001"
        children = [(child_id, "Child", "Major", "/fake/path")]
        state = SubmissionState()
        state.phase2_done = {
            child_id: {"key": f"{desc.write_prefix}1001", "linked": True, "commented": True}
        }
        return parent, children, state

    def test_phase3_dry_run_plans_the_binding_transition(self, desc, capsys):
        config = SPLIT_CONFIG[desc.name]
        close = desc.get("identity.jira.state_map.close_superseded")
        parent, children, state = self._closable(desc)
        phase3_close("s", "u", "t", parent, children, state, config, dry_run=True)
        out = capsys.readouterr().out
        assert f"Would label {parent} with {config['label_prefix']}-split-original" in out
        assert (
            f"Would transition {parent} to {close['transition']} "
            f"(resolution: {close['resolution']})"
        ) in out

    def test_phase3_matches_the_target_status_case_insensitively(self, desc, monkeypatch, capsys):
        config = SPLIT_CONFIG[desc.name]
        close = desc.get("identity.jira.state_map.close_superseded")
        parent, children, state = self._closable(desc)
        calls = {}
        transitions = [
            {"id": "1", "name": "Start", "to": {"name": "In Progress"}},
            {"id": "9", "name": "Retire", "to": {"name": close["transition"].upper()}},
        ]
        monkeypatch.setattr(
            split_submit, "add_labels", lambda s, u, t, k, labels: calls.update(labels=labels)
        )
        monkeypatch.setattr(split_submit, "get_transitions", lambda s, u, t, k: transitions)
        monkeypatch.setattr(
            split_submit,
            "do_transition",
            lambda s, u, t, k, tid, fields=None: calls.update(tid=tid, fields=fields),
        )
        monkeypatch.setattr(
            split_submit, "add_comment", lambda s, u, t, k, adf: calls.update(adf=adf)
        )
        phase3_close("https://j", "u", "t", parent, children, state, config, dry_run=False)
        assert calls["labels"] == [f"{config['label_prefix']}-split-original"]
        assert calls["tid"] == "9"
        assert calls["fields"] == {"resolution": {"name": close["resolution"]}}
        assert calls["adf"]["type"] == "doc"
        assert f"Transitioned {parent} to {close['transition']} ({close['resolution']})" in (
            capsys.readouterr().out
        )

    def test_phase3_warns_with_the_binding_status_when_unreachable(self, desc, monkeypatch, capsys):
        config = SPLIT_CONFIG[desc.name]
        close = desc.get("identity.jira.state_map.close_superseded")
        parent, children, state = self._closable(desc)
        monkeypatch.setattr(split_submit, "add_labels", lambda *a: None)
        monkeypatch.setattr(
            split_submit,
            "get_transitions",
            lambda *a: [{"id": "1", "name": "Start", "to": {"name": "In Progress"}}],
        )
        monkeypatch.setattr(
            split_submit, "do_transition", lambda *a, **k: pytest.fail("must not transition")
        )
        phase3_close("https://j", "u", "t", parent, children, state, config, dry_run=False)
        err = capsys.readouterr().err
        assert f"No '{close['transition']}' transition found" in err
        assert "Skipping parent closure." in err


class TestTypeChoices:
    def test_type_choices_are_the_registry_choices(self):
        r = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=_clean_env()
        )
        assert r.returncode == 0
        assert "{" + ",".join(split_submit._TYPES.choices()) + "}" in r.stdout

    def test_unknown_type_is_a_usage_error(self):
        r = subprocess.run(
            [sys.executable, SCRIPT, "X-1", "--type", "nope"],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        assert r.returncode == 64
        assert "invalid choice: 'nope'" in r.stderr


INITIATIVE_PARENT = """\
---
initiative_id: RHOAIENG-2000
title: Parent Initiative
priority: Critical
status: Archived
parent_key: RHAISTRAT-77
---

## Objective

Original parent initiative content.
"""

INITIATIVE_CHILD = """\
---
initiative_id: INIT-{num:03d}
title: Child Initiative {num}
priority: Normal
status: Ready
parent_key: RHOAIENG-2000
---

## Objective

Child initiative {num} objective.
"""


class TestInitiativeDryRun:
    def test_plans_the_initiative_binding_end_to_end(self, tmp_path):
        """--type initiative resolves every per-type value from the initiative descriptor:
        project, label prefix, alignment labels, link type, closure — and no index rebuild."""
        art = str(tmp_path)
        _write(f"{art}/initiatives/RHOAIENG-2000.md", INITIATIVE_PARENT)
        for i in (1, 2):
            _write(f"{art}/initiatives/INIT-{i:03d}.md", INITIATIVE_CHILD.format(num=i))
        desc = split_submit._TYPES.get("initiative")
        review = _review_fields(
            desc, "INIT-001", auto_revised=True, needs_attention=True, alignment="weak"
        )
        review["needs_attention_reason"] = "Weak alignment"
        _write(f"{art}/initiative-reviews/INIT-001-review.md", _fm(review))

        r = subprocess.run(
            [sys.executable, SCRIPT, "RHOAIENG-2000", "--dry-run", "--type", "initiative"]
            + ["--artifacts-dir", art],
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        assert r.returncode == 0, r.stderr
        out = r.stdout
        assert "Split submission: RHOAIENG-2000 -> 2 children" in out
        assert "Phase 1: Persisting child Initiative content to parent comments..." in out
        assert "Would create RHOAIENG ticket for child 1/2: Child Initiative 1" in out
        assert (
            "Labels: initiative-auto-created, initiative-split-result, "
            "initiative-split-child-rhoaieng-2000-init-001-"
        ) in out
        assert (
            "initiative-auto-revised, initiative-needs-attention, "
            "initiative-autofix-rubric-pass, initiative-feasibility-pass, "
            "initiative-alignment-weak"
        ) in out
        assert "Would link to RHOAIENG-2000 via 'Work item split'" in out
        assert "Would post needs-attention comment" in out
        assert "Would label RHOAIENG-2000 with initiative-split-original" in out
        assert "Would transition RHOAIENG-2000 to Closed (resolution: Obsolete)" in out
        assert out.rstrip().endswith("Done.")
        assert not os.path.exists(f"{art}/rfes.md")


class TestSplitFactsGuards:
    """identity.jira.split_link_type and state_map.close_superseded are optional in the schema
    (a type that never splits omits them), so their absence is refused at the moment a parent
    of that type is submitted for a split — before any scan or Jira call. A second type on a
    shipped type's (project, issue_type) pair no longer breaks the import: ownership of the
    EFFECTIVE binding is proven at run time (type_registry.assert_registered_binding, D12)."""

    def test_shipped_types_miss_nothing(self, desc):
        assert split_submit._missing_split_facts(SPLIT_CONFIG[desc.name]) == []

    def test_missing_facts_are_named_one_by_one(self, monkeypatch):
        config = {"type": "memo", "project": "MEMO", "issue_type": "Memo"}
        tracker = dict(split_submit._TRACKER)
        monkeypatch.setattr(split_submit, "_TRACKER", tracker)

        tracker["memo"] = {"split_link_type": None, "close_superseded": None}
        assert split_submit._missing_split_facts(config) == [
            "identity.jira.split_link_type",
            "identity.jira.state_map.close_superseded.transition",
            "identity.jira.state_map.close_superseded.resolution",
        ]
        tracker["memo"] = {"split_link_type": "Work item split", "close_superseded": {}}
        assert split_submit._missing_split_facts(config) == [
            "identity.jira.state_map.close_superseded.transition",
            "identity.jira.state_map.close_superseded.resolution",
        ]
        tracker["memo"] = {
            "split_link_type": "Work item split",
            "close_superseded": {"transition": "Closed"},
        }
        assert split_submit._missing_split_facts(config) == [
            "identity.jira.state_map.close_superseded.resolution",
        ]

    def _run_memo(self, root, tmp_path):
        return subprocess.run(
            [sys.executable, SCRIPT, "MEMO-1", "--dry-run", "--type", "memo"]
            + ["--artifacts-dir", str(tmp_path / "artifacts")],
            capture_output=True,
            text=True,
            env=_clean_env(RFE_CREATOR_EXTRA_TYPES=root),
        )

    def test_a_type_without_split_facts_is_refused_before_scanning(self, drop_in_root, tmp_path):
        root = drop_in_root.memo(
            drop=("identity.jira.split_link_type", "identity.jira.state_map.close_superseded")
        )
        r = self._run_memo(root, tmp_path)
        assert r.returncode == split_submit.EXIT_SYSTEMIC, r.stderr
        assert (
            "Error: type 'memo' declares no identity.jira.split_link_type / "
            "identity.jira.state_map.close_superseded.transition / "
            "identity.jira.state_map.close_superseded.resolution; it cannot be split."
        ) in r.stderr
        assert "No memo files found" not in r.stderr
        assert r.stdout == ""

    def test_a_type_with_split_facts_passes_the_guard(self, drop_in_root, tmp_path):
        r = self._run_memo(drop_in_root.memo(), tmp_path)
        assert r.returncode == split_submit.EXIT_PER_PARENT, r.stderr
        assert "No memo files found" in r.stderr
        assert "cannot be split" not in r.stderr

    def test_two_types_on_one_binding_pair_are_refused_at_run_time(self, drop_in_root, tmp_path):
        # A plain rfe copy: the same (RHAIRFE, Feature Request). The import no longer polices
        # the pair (_TRACKER is keyed by type name, D12); a split of EITHER type is refused by
        # the ownership check before any scan or Jira call, with the usage/config exit code.
        root = drop_in_root.add("rfe2")
        env = _clean_env(RFE_CREATOR_EXTRA_TYPES=root)
        r = subprocess.run(
            [sys.executable, SCRIPT, "--help"], capture_output=True, text=True, env=env
        )
        assert r.returncode == 0, r.stderr
        assert "{rfe,initiative,rfe2}" in r.stdout
        for chosen, other in (("rfe2", "rfe"), ("rfe", "rfe2")):
            r = subprocess.run(
                [sys.executable, SCRIPT, "RHAIRFE-1", "--dry-run", "--type", chosen]
                + ["--artifacts-dir", str(tmp_path)],
                capture_output=True,
                text=True,
                env=env,
            )
            assert r.returncode == split_submit.EXIT_USAGE, r.stderr
            assert r.stdout == ""
            assert r.stderr.splitlines() == [
                f"TYPE RESOLVED: {chosen} (--type)",
                f"Error: {chosen}: effective binding ('jira', 'RHAIRFE', 'Feature Request') "
                f"(source: descriptor) is the binding registered for type '{other}'; a tracker "
                f"binding must be owned by exactly one type (design §3.3 rule 1) — fix the "
                f"override or pass --type {other}",
            ]


# ─── Effective binding (design §3.2.1; PR-3c, the writers) ───────────────────


def _override_env(desc, **fields):
    """``RFE_CREATOR_BINDING_<TYPE>_<FIELD>`` variables for ``desc``."""
    return {type_registry.binding_env_var(desc.name, name): value for name, value in fields.items()}


def _config_with(desc, **fields):
    """SPLIT_CONFIG's entry for ``desc`` re-projected over an overridden effective binding —
    what main() builds from ``resolve()``: ``_split_config(resolution.desc, resolution.binding)``.
    """
    return split_submit._split_config(desc, desc.binding(_override_env(desc, **fields)))


class TestEffectiveBinding:
    """main() re-projects the resolved type over ``desc.binding(env)``: project and issue type
    — and with them the children's Jira project, the marker-label JQL and the ``<PROJECT>-DRY``
    sentinel (§3.2.1 d) — come from the EFFECTIVE binding; everything else is the descriptor's.
    With no override set every value equals the import-time SPLIT_CONFIG entry."""

    def test_split_config_without_a_binding_is_the_import_time_entry(self, desc):
        fresh = split_submit._split_config(desc)
        for key in SPLIT_CONFIG_KEYS:
            if key in ("scan_fn", "rename_fn", "parse_child_fn"):
                assert fresh[key].func is SPLIT_CONFIG[desc.name][key].func
                assert fresh[key].keywords["desc"] is desc
            else:
                assert fresh[key] == SPLIT_CONFIG[desc.name][key], key
        # No override in the environment: the effective binding projects the same entry.
        effective = split_submit._split_config(desc, desc.binding({}))
        assert (effective["project"], effective["issue_type"]) == (
            fresh["project"],
            fresh["issue_type"],
        )

    def test_split_config_takes_project_and_issue_type_from_the_binding(self, desc):
        config = _config_with(desc, PROJECT="KONFLUX", ISSUE_TYPE="Story")
        assert list(config) == SPLIT_CONFIG_KEYS
        assert config["type"] == desc.name
        assert (config["project"], config["issue_type"]) == ("KONFLUX", "Story")
        base = SPLIT_CONFIG[desc.name]
        for key in SPLIT_CONFIG_KEYS:
            if key in ("project", "issue_type"):
                continue
            if key in ("scan_fn", "rename_fn", "parse_child_fn"):
                assert config[key].func is base[key].func
                assert config[key].keywords["desc"] is desc
            else:
                assert config[key] == base[key], key
        # The project alone: the descriptor issue type stays.
        only_project = _config_with(desc, PROJECT="KONFLUX")
        assert (only_project["project"], only_project["issue_type"]) == (
            "KONFLUX",
            desc.get("identity.jira.issue_type"),
        )

    def _child(self, desc, tmp_path):
        bare = desc.dirs("bare")
        child_id = f"{desc.local_prefix}001"
        path = tmp_path / bare["tasks"] / f"{child_id}.md"
        _write(str(path), _fm(_task_fields(desc, child_id), "## Problem\n\nChild body.\n"))
        return child_id, [(child_id, "Child", "Major", str(path))]

    def test_dry_run_sentinel_is_binding_derived(self, desc, tmp_path, capsys):
        child_id, children = self._child(desc, tmp_path)
        parent = f"{desc.write_prefix}1000"
        for config, sentinel in (
            (SPLIT_CONFIG[desc.name], f"{desc.get('identity.jira.project')}-DRY"),
            (_config_with(desc, PROJECT="KONFLUX"), "KONFLUX-DRY"),
        ):
            state = SubmissionState()
            state.phase1_done = {child_id: "dry-run"}
            split_submit.phase2_create_link(
                "s", "u", "t", parent, children, state, str(tmp_path), config, dry_run=True
            )
            assert state.phase2_done[child_id] == {
                "key": sentinel,
                "linked": True,
                "commented": True,
            }
            assert f"Would create {config['project']} ticket for child 1/1: Child" in (
                capsys.readouterr().out
            )

    def test_marker_label_search_uses_the_effective_project(self, desc, tmp_path, monkeypatch):
        child_id, children = self._child(desc, tmp_path)
        parent = f"{desc.write_prefix}1000"
        seen = []
        monkeypatch.setattr(split_submit, "get_comments", lambda *a: [])
        monkeypatch.setattr(
            split_submit,
            "get_issue",
            lambda *a, **k: {"fields": {"issuelinks": [], "status": {"statusCategory": {}}}},
        )
        monkeypatch.setattr(
            split_submit, "search_issues", lambda s, u, t, jql, fields: seen.append(jql) or []
        )
        for config, project in (
            (SPLIT_CONFIG[desc.name], desc.get("identity.jira.project")),
            (_config_with(desc, PROJECT="KONFLUX"), "KONFLUX"),
        ):
            state = split_submit.discover_state("s", "u", "t", parent, children, config)
            assert state.phase2_done == {}
            expected_marker = split_submit._child_marker_label(
                config["label_prefix"],
                parent,
                child_id,
                split_submit._child_fingerprint(config, children[0][3]),
            )
            assert seen[-1] == f'project = "{project}" AND labels in ("{expected_marker}")'

    def test_children_are_created_in_the_effective_project_and_issue_type(
        self, desc, tmp_path, monkeypatch
    ):
        child_id, children = self._child(desc, tmp_path)
        parent = f"{desc.write_prefix}1000"
        config = _config_with(desc, PROJECT="KONFLUX", ISSUE_TYPE="Story")
        created, links, comments = [], [], []

        def fake_create_issue(server, user, token, project, issue_type, summary, *a, **k):
            created.append((project, issue_type, summary, k.get("labels")))
            return f"{project}-7"

        monkeypatch.setattr(split_submit, "create_issue", fake_create_issue)
        monkeypatch.setattr(
            split_submit, "create_issue_link", lambda s, u, t, *rest: links.append(rest)
        )
        monkeypatch.setattr(
            split_submit, "add_comment", lambda s, u, t, key, adf: comments.append((key, adf))
        )
        state = SubmissionState()
        state.phase1_done = {child_id: "c1"}
        split_submit.phase2_create_link(
            "s", "u", "t", parent, children, state, str(tmp_path), config, dry_run=False
        )
        ((project, issue_type, summary, labels),) = created
        assert (project, issue_type, summary) == ("KONFLUX", "Story", "Child")
        # The labels are exactly today's: the label prefix and marker never carry the project.
        label_prefix = config["label_prefix"]
        assert labels[:2] == [f"{label_prefix}-auto-created", f"{label_prefix}-split-result"]
        assert labels[2].startswith(f"{label_prefix}-split-child-{parent.lower()}-")
        assert links == [(desc.get("identity.jira.split_link_type"), parent, "KONFLUX-7")]
        assert state.phase2_done[child_id] == {
            "key": "KONFLUX-7",
            "linked": True,
            "commented": True,
        }
        assert [key for key, _ in comments] == [parent]


class TestEffectiveBindingCli:
    """The resolve / ownership step at the start of main(): silent and byte-identical for the
    legacy default (no --type, no override), one stderr line for an explicit --type (D3), the
    plan re-targeted by an override, and a refusal — one ``Error:`` line, EXIT_USAGE, before any
    artifact or Jira access — when the effective binding is malformed or another type's."""

    def _tree(self, art_dir, children=2):
        _write(f"{art_dir}/rfe-tasks/RHAIRFE-1000.md", PARENT_TASK)
        for i in range(1, children + 1):
            _write(f"{art_dir}/rfe-tasks/RFE-{i:03d}.md", CHILD_TASK.format(num=i))

    def _run(self, art_dir, *extra, **env):
        return subprocess.run(
            [sys.executable, SCRIPT, "RHAIRFE-1000", "--dry-run", "--artifacts-dir", art_dir]
            + list(extra),
            capture_output=True,
            text=True,
            env=_clean_env(**env),
        )

    def test_legacy_default_is_silent_and_an_explicit_type_prints_the_resolve_line(self, art_dir):
        self._tree(art_dir)
        silent = self._run(art_dir)
        assert silent.returncode == 0, silent.stderr
        assert silent.stderr == ""
        assert "Would create RHAIRFE ticket for child 1/2: Child RFE 1" in silent.stdout
        explicit = self._run(art_dir, "--type", "rfe")
        assert explicit.returncode == 0, explicit.stderr
        assert explicit.stderr == "TYPE RESOLVED: rfe (--type)\n"
        # D3: the line is the only difference — stdout is byte-identical.
        assert explicit.stdout == silent.stdout

    def test_an_override_plans_the_children_in_the_effective_project(self, art_dir):
        self._tree(art_dir)
        r = self._run(art_dir, RFE_CREATOR_BINDING_RFE_PROJECT="KONFLUX")
        assert r.returncode == 0, r.stderr
        assert r.stderr == ""  # the legacy default rung stays silent even under an override
        assert "Would create KONFLUX ticket for child 1/2: Child RFE 1 (priority: Major)" in (
            r.stdout
        )
        assert "Would create KONFLUX ticket for child 2/2" in r.stdout
        assert "RHAIRFE ticket" not in r.stdout
        # The parent (an RHAIRFE key, a descriptor read prefix) is still linked and closed.
        assert "Would link to RHAIRFE-1000 via 'Work item split'" in r.stdout
        assert "Would transition RHAIRFE-1000 to Closed (resolution: Obsolete)" in r.stdout
        explicit = self._run(art_dir, "--type", "rfe", RFE_CREATOR_BINDING_RFE_PROJECT="KONFLUX")
        assert explicit.stderr == "TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)\n"
        assert explicit.stdout == r.stdout

    def test_a_binding_owned_by_another_type_is_refused_before_any_access(self, tmp_path):
        # An EMPTY artifacts dir: had the scan run first, the outcome would be exit 4 and
        # "No rfe files found".
        r = self._run(
            str(tmp_path),
            RFE_CREATOR_BINDING_RFE_PROJECT="RHOAIENG",
            RFE_CREATOR_BINDING_RFE_ISSUE_TYPE="Initiative",
        )
        assert r.returncode == split_submit.EXIT_USAGE
        assert r.stdout == ""
        assert r.stderr == (
            "Error: rfe: effective binding ('jira', 'RHOAIENG', 'Initiative') (source: env) is "
            "the binding registered for type 'initiative'; a tracker binding must be owned by "
            "exactly one type (design §3.3 rule 1) — fix the override or pass --type initiative\n"
        )

    def test_the_shorthand_is_refused_before_any_access(self, tmp_path):
        # An EMPTY artifacts dir again: the refusal comes before the scan. The bare shorthand
        # reaches resolve but not the artifact layer the children are renamed by.
        r = self._run(str(tmp_path), JIRA_PROJECT="KONFLUX")
        assert r.returncode == split_submit.EXIT_USAGE
        assert r.stdout == ""
        assert r.stderr == (
            "Error: JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is not honoured by the artifact "
            "layer; set RFE_CREATOR_BINDING_RFE_PROJECT / _ISSUE_TYPE instead\n"
        )

    def test_the_parent_marker_suppresses_the_resolve_line(self, art_dir):
        # submit.py resolved the run's type and printed the line; the child it spawns per
        # parent carries RFE_CREATOR_RESOLVED_BY_PARENT and prints none (D3: one line per run).
        self._tree(art_dir)
        explicit = self._run(art_dir, "--type", "rfe")
        assert explicit.stderr == "TYPE RESOLVED: rfe (--type)\n"
        marked = self._run(art_dir, "--type", "rfe", RFE_CREATOR_RESOLVED_BY_PARENT="1")
        assert marked.returncode == 0, marked.stderr
        assert marked.stderr == ""
        assert marked.stdout == explicit.stdout

    def test_a_malformed_override_is_refused_in_main(self, monkeypatch, tmp_path, capsys):
        # In-process: the module is already imported, so this is main()'s own refusal path
        # (resolve raises RegistryError while computing the binding) and not an import error.
        for var in list(os.environ):
            if var.startswith(type_registry.BINDING_ENV_PREFIX):
                monkeypatch.delenv(var)
        monkeypatch.setenv("RFE_CREATOR_BINDING_RFE_PROJECT", "bad-key")
        monkeypatch.setattr(
            sys,
            "argv",
            ["split_submit.py", "RHAIRFE-1", "--dry-run", "--artifacts-dir", str(tmp_path)],
        )
        with pytest.raises(SystemExit) as excinfo:
            split_submit.main()
        assert excinfo.value.code == split_submit.EXIT_USAGE
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == (
            "Error: RFE_CREATOR_BINDING_RFE_PROJECT='bad-key': expected an upper-case tracker "
            "project key\n"
        )


class TestParentBindingRefusal:
    """The pre-split check of the parent (PR-3c): its fetched (project, issuetype) must be one
    of the pairs the resolved type accepts for its key — the effective pair, plus the descriptor
    pair for a key carrying a descriptor prefix (a parent fetched before an override) — else
    one Error: line and the per-parent exit code before any write."""

    def _refusal(self, key, fields, env=None):
        desc = split_submit._TYPES.get("rfe")
        return split_submit._parent_binding_refusal(key, fields, desc, desc.binding(env or {}))

    def test_the_descriptor_pair_is_accepted(self):
        fields = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Feature Request"}}
        assert self._refusal("RHAIRFE-1000", fields) is None

    def test_another_issue_type_is_refused(self):
        fields = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Epic"}}
        assert self._refusal("RHAIRFE-1000", fields) == (
            "RHAIRFE-1000 is (RHAIRFE, Epic) in Jira but the resolved type rfe binds "
            "(RHAIRFE, Feature Request); refusing to split — nothing written"
        )

    def test_missing_witnesses_fail_closed(self):
        assert self._refusal("RHAIRFE-1000", {"project": {"key": "RHAIRFE"}}) == (
            "cannot verify RHAIRFE-1000 against the resolved type rfe binding "
            "(RHAIRFE, Feature Request): the fetched issue has no issuetype field; refusing to "
            "split — nothing written"
        )
        assert "no project or issuetype field" in self._refusal("RHAIRFE-1000", None)

    def test_a_pre_override_parent_is_accepted_and_a_foreign_one_refused_under_an_override(self):
        env = {"RFE_CREATOR_BINDING_RFE_PROJECT": "KONFLUX"}
        rfe_pair = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Feature Request"}}
        assert self._refusal("RHAIRFE-1000", rfe_pair, env) is None
        konflux_pair = {"project": {"key": "KONFLUX"}, "issuetype": {"name": "Feature Request"}}
        assert self._refusal("KONFLUX-1", konflux_pair, env) is None
        konflux_epic = {"project": {"key": "KONFLUX"}, "issuetype": {"name": "Epic"}}
        assert self._refusal("KONFLUX-1", konflux_epic, env) == (
            "KONFLUX-1 is (KONFLUX, Epic) in Jira but the resolved type rfe binds "
            "(KONFLUX, Feature Request); refusing to split — nothing written"
        )
        rfe_epic = {"project": {"key": "RHAIRFE"}, "issuetype": {"name": "Epic"}}
        assert self._refusal("RHAIRFE-1000", rfe_epic, env) == (
            "RHAIRFE-1000 is (RHAIRFE, Epic) in Jira but the resolved type rfe binds "
            "(KONFLUX, Feature Request) or, for a pre-override key, (RHAIRFE, Feature Request); "
            "refusing to split — nothing written"
        )
