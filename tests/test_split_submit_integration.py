#!/usr/bin/env python3
"""Integration tests for split_submit.py recovery against the jira-emulator.

The create-to-link window (RHAIFIRST-570): a process death between
create_issue and the link/comment used to mint a child no future run could
find, so the next run duplicated it. Recovery is now id-based (marker labels,
id-bearing comments) instead of positional, so sibling changes between runs
cannot misalign it either.
"""

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import split_submit
import type_registry
from jira_utils import add_comment, get_comments, get_issue, text_to_adf_paragraph
from split_submit import (
    SPLIT_CONFIG,
    _child_fingerprint,
    _child_marker_label,
    discover_state,
    phase1_persist,
    phase2_create_link,
)


def _marker_for(art_dir, child_id, parent_key="RHAIRFE-1000"):
    config = SPLIT_CONFIG["rfe"]
    fp = _child_fingerprint(config, f"{art_dir}/rfe-tasks/{child_id}.md")
    return _child_marker_label("rfe-creator", parent_key, child_id, fp)


SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "split_submit.py")

PARENT_KEY = "RHAIRFE-1000"
PARENT_DESC = "Original parent content."


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


PARENT_TASK = f"""\
---
rfe_id: {PARENT_KEY}
title: Parent RFE
priority: Major
status: Archived
---

## Problem Statement

{PARENT_DESC}
"""

CHILD_TASK = """\
---
rfe_id: {child_id}
title: {title}
priority: Major
status: Ready
parent_key: RHAIRFE-1000
---

## Problem Statement

Content for {child_id}.
"""


@pytest.fixture
def art_dir(tmp_path):
    for d in ["rfe-tasks", "rfe-reviews", "rfe-originals"]:
        os.makedirs(tmp_path / d)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield str(tmp_path)
    os.chdir(orig)


def _setup_parent(jira, art_dir, child_ids=("RFE-001", "RFE-002")):
    """Archived parent + children task files, and the parent in Jira.

    The original file matches the live description so the conflict check
    passes, mirroring a real fetched parent.
    """
    jira.create(PARENT_KEY, "Parent RFE", PARENT_DESC)
    _write(f"{art_dir}/rfe-tasks/{PARENT_KEY}.md", PARENT_TASK)
    _write(f"{art_dir}/rfe-originals/{PARENT_KEY}.md", PARENT_DESC)
    children = []
    for cid in child_ids:
        title = f"Child {cid}"
        path = f"{art_dir}/rfe-tasks/{cid}.md"
        _write(path, CHILD_TASK.format(child_id=cid, title=title))
        children.append((cid, title, "Major", path))
    return children


def _run_split(art_dir, url):
    env = {**os.environ, "JIRA_SERVER": url, "JIRA_USER": "admin", "JIRA_TOKEN": "admin"}
    return subprocess.run(
        [sys.executable, SCRIPT, PARENT_KEY, "--artifacts-dir", art_dir],
        capture_output=True,
        text=True,
        env=env,
    )


def _search_keys(url, jql):
    req = urllib.request.Request(
        f"{url}/rest/api/3/search/jql?jql={urllib.parse.quote(jql, safe='')}&fields=summary"
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return sorted(issue["key"] for issue in data.get("issues", []))


def _split_links(url, parent_key):
    issue = get_issue(url, "admin", "admin", parent_key, ["issuelinks"])
    return sorted(
        (link.get("outwardIssue") or link.get("inwardIssue"))["key"]
        for link in issue["fields"].get("issuelinks", [])
        if link.get("type", {}).get("name") == "Work item split"
        and (link.get("outwardIssue") or link.get("inwardIssue"))
    )


class TestFullRun:
    def test_creates_links_confirms_and_closes(self, art_dir, jira):
        _setup_parent(jira, art_dir)

        marker = _marker_for(art_dir, "RFE-001")
        r = _run_split(art_dir, jira.url)
        assert r.returncode == 0, r.stderr + r.stdout

        created = _search_keys(jira.url, "labels = rfe-creator-split-result")
        assert len(created) == 2
        assert _split_links(jira.url, PARENT_KEY) == created
        # Marker labels present: the from-birth recovery signal.
        marked = _search_keys(jira.url, f'labels = "{marker}"')
        assert len(marked) == 1
        # Parent closed.
        issue = get_issue(jira.url, "admin", "admin", PARENT_KEY, ["status"])
        assert issue["fields"]["status"]["statusCategory"]["key"] == "done"
        # Artifacts renamed to the Jira keys.
        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")


class TestCreateToLinkWindow:
    """AC: a process killed between create_issue and the link/comment leaves
    a child that the next run finds rather than duplicates."""

    def _crash_run(self, art_dir, jira, children, patch_target, monkeypatch):
        """Run phase 1+2 in-process with one call patched to die."""
        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(jira.url, "admin", "admin", PARENT_KEY, children, state, config, False)

        calls = {"n": 0}
        original = getattr(split_submit, patch_target)

        def dying(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("process died in the window")
            return original(*a, **k)

        monkeypatch.setattr(split_submit, patch_target, dying)
        with pytest.raises(RuntimeError):
            phase2_create_link(
                jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
            )
        monkeypatch.setattr(split_submit, patch_target, original)

    def test_death_after_create_is_found_by_marker_label(self, art_dir, jira, monkeypatch):
        """Death BEFORE the link and the comment: the marker label is the
        only signal, and it exists from the instant the child does."""
        children = _setup_parent(jira, art_dir)
        self._crash_run(art_dir, jira, children, "create_issue_link", monkeypatch)

        # One child exists in Jira, unlinked, unconfirmed — the old recovery
        # could not see it and would have duplicated it.
        assert len(_search_keys(jira.url, "labels = rfe-creator-split-result")) == 1
        assert _split_links(jira.url, PARENT_KEY) == []

        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        assert "RFE-001" in state.phase2_done
        assert state.phase2_done["RFE-001"]["linked"] is False

        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
        )
        created = _search_keys(jira.url, "labels = rfe-creator-split-result")
        assert len(created) == 2, "recovery duplicated the orphaned child"
        assert _split_links(jira.url, PARENT_KEY) == created

    def test_death_after_link_completes_the_confirmation(self, art_dir, jira, monkeypatch):
        """Death between the link and the comment: found via the link, and
        recovery posts the missing confirmation instead of skipping silently."""
        children = _setup_parent(jira, art_dir)
        self._crash_run(art_dir, jira, children, "add_comment", monkeypatch)

        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        assert state.phase2_done["RFE-001"]["linked"] is True
        assert state.phase2_done["RFE-001"]["commented"] is False

        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
        )
        assert len(_search_keys(jira.url, "labels = rfe-creator-split-result")) == 2
        comments = get_comments(jira.url, "admin", "admin", PARENT_KEY)
        confirmations = [
            c for c in comments if "Created as" in split_submit._extract_adf_text(c.get("body", {}))
        ]
        assert len(confirmations) == 2


class TestSiblingChangesBetweenRuns:
    """AC: recovery no longer depends on child ordering."""

    def test_inserted_sibling_does_not_misalign(self, art_dir, jira, monkeypatch):
        children = _setup_parent(jira, art_dir)
        marker_001 = _marker_for(art_dir, "RFE-001")
        # Die on the second create: child RFE-001 is fully done.
        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(jira.url, "admin", "admin", PARENT_KEY, children, state, config, False)
        original = split_submit.create_issue
        calls = {"n": 0}

        def dying(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("died before the second child")
            return original(*a, **k)

        monkeypatch.setattr(split_submit, "create_issue", dying)
        with pytest.raises(RuntimeError):
            phase2_create_link(
                jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
            )
        monkeypatch.setattr(split_submit, "create_issue", original)

        # Between runs: a new sibling that sorts FIRST, re-numbering every
        # positional index — the shape that used to misalign recovery.
        _write(
            f"{art_dir}/rfe-tasks/RFE-000.md",
            CHILD_TASK.format(child_id="RFE-000", title="Child RFE-000"),
        )

        r = _run_split(art_dir, jira.url)
        assert r.returncode == 0, r.stderr + r.stdout

        # RFE-001 not duplicated; three children total, all linked.
        assert len(_search_keys(jira.url, f'labels = "{marker_001}"')) == 1
        created = _search_keys(jira.url, "labels = rfe-creator-split-result")
        assert len(created) == 3
        assert _split_links(jira.url, PARENT_KEY) == created


class TestStaleMarkerLabels:
    """Marker labels persist forever and local ids recycle across workspaces —
    adoption must be parent-scoped and title-guarded (review findings)."""

    def test_fresh_split_does_not_adopt_another_parents_children(self, art_dir, jira, tmp_path):
        """Run N split RHAIRFE-1000 -> children keep their marker labels.
        Run N+1 (fresh workspace) splits RHAIRFE-2000 reusing local ids
        RFE-001/RFE-002 — it must create ITS OWN children, not adopt run N's
        (reproduced live against the id-only label scheme in review)."""
        _setup_parent(jira, art_dir)
        r = _run_split(art_dir, jira.url)
        assert r.returncode == 0, r.stderr + r.stdout
        run_n_children = _search_keys(jira.url, "labels = rfe-creator-split-result")
        assert len(run_n_children) == 2

        # Fresh workspace for a DIFFERENT parent, same local ids.
        ws2 = tmp_path / "run-n-plus-1"
        for d in ["rfe-tasks", "rfe-reviews", "rfe-originals"]:
            os.makedirs(ws2 / d)
        jira.create("RHAIRFE-2000", "Other parent", "Other content.")
        _write(
            str(ws2 / "rfe-tasks/RHAIRFE-2000.md"),
            "---\nrfe_id: RHAIRFE-2000\ntitle: Other parent\n"
            "priority: Major\nstatus: Archived\n---\n\nOther content.\n",
        )
        _write(str(ws2 / "rfe-originals/RHAIRFE-2000.md"), "Other content.")
        for cid in ("RFE-001", "RFE-002"):
            # Deliberately IDENTICAL titles to run N's children: the title
            # guard must not be what saves us here — only the parent-scoped
            # label keeps the search from seeing run N's children at all.
            _write(
                str(ws2 / f"rfe-tasks/{cid}.md"),
                "---\nrfe_id: " + cid + "\ntitle: Child " + cid + "\n"
                "priority: Major\nstatus: Ready\n"
                "parent_key: RHAIRFE-2000\n---\n\nDifferent content.\n",
            )

        env = {**os.environ, "JIRA_SERVER": jira.url, "JIRA_USER": "admin", "JIRA_TOKEN": "admin"}
        r = subprocess.run(
            [sys.executable, SCRIPT, "RHAIRFE-2000", "--artifacts-dir", str(ws2)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr + r.stdout

        # Four distinct children exist; run N's were not adopted or re-linked.
        all_children = _search_keys(jira.url, "labels = rfe-creator-split-result")
        assert len(all_children) == 4
        assert set(_split_links(jira.url, "RHAIRFE-2000")).isdisjoint(run_n_children)

    def test_resplit_with_new_decomposition_is_not_bound_to_stale_child(
        self, art_dir, jira, monkeypatch
    ):
        """Same parent, quarantine cleared, re-split with DIFFERENT scope
        reusing the same local id: the stale child carries the matching
        marker label but the wrong title — the title guard must refuse it."""
        children = _setup_parent(jira, art_dir, child_ids=("RFE-001",))
        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(jira.url, "admin", "admin", PARENT_KEY, children, state, config, False)
        original = split_submit.create_issue_link

        def dying(*a, **k):
            raise RuntimeError("died before link")

        monkeypatch.setattr(split_submit, "create_issue_link", dying)
        with pytest.raises(RuntimeError):
            phase2_create_link(
                jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
            )
        monkeypatch.setattr(split_submit, "create_issue_link", original)

        # New decomposition: same local id, different title.
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            CHILD_TASK.format(child_id="RFE-001", title="Reworked scope"),
        )
        new_children = [("RFE-001", "Reworked scope", "Major", f"{art_dir}/rfe-tasks/RFE-001.md")]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, new_children, config)
        assert "RFE-001" not in state.phase2_done, "stale child adopted despite title mismatch"

        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, new_children, state, art_dir, config, False
        )
        # Fresh child created for the new scope; the stale one stays unlinked.
        assert len(_search_keys(jira.url, "labels = rfe-creator-split-result")) == 2
        assert len(_split_links(jira.url, PARENT_KEY)) == 1

    def test_resplit_same_title_different_content_is_not_adopted(self, art_dir, jira, monkeypatch):
        """A re-split can reuse the same parent, id AND title for different
        scope — the title is too weak an identity (CodeRabbit on #169). The
        content fingerprint in the marker label must reject the stale child."""
        children = _setup_parent(jira, art_dir, child_ids=("RFE-001",))
        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(jira.url, "admin", "admin", PARENT_KEY, children, state, config, False)

        def dying(*a, **k):
            raise RuntimeError("died before link")

        monkeypatch.setattr(split_submit, "create_issue_link", dying)
        with pytest.raises(RuntimeError):
            phase2_create_link(
                jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
            )
        monkeypatch.undo()

        # Same id, same title — different body.
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            CHILD_TASK.format(child_id="RFE-001", title="Child RFE-001").replace(
                "Content for RFE-001.", "Entirely different scope."
            ),
        )
        new_children = [("RFE-001", "Child RFE-001", "Major", f"{art_dir}/rfe-tasks/RFE-001.md")]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, new_children, config)
        assert "RFE-001" not in state.phase2_done, "stale child adopted despite content change"

        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, new_children, state, art_dir, config, False
        )
        assert len(_search_keys(jira.url, "labels = rfe-creator-split-result")) == 2
        assert len(_split_links(jira.url, PARENT_KEY)) == 1

    def test_dry_run_completion_writes_nothing(self, art_dir, jira, monkeypatch):
        """Discovery runs whenever credentials exist. A dry run that finds a
        partially-applied child must report what it WOULD complete — not
        link, comment, or rename (review finding: write-under-dry-run)."""
        children = _setup_parent(jira, art_dir, child_ids=("RFE-001",))
        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(jira.url, "admin", "admin", PARENT_KEY, children, state, config, False)

        def dying(*a, **k):
            raise RuntimeError("died before link")

        monkeypatch.setattr(split_submit, "create_issue_link", dying)
        with pytest.raises(RuntimeError):
            phase2_create_link(
                jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
            )
        monkeypatch.undo()

        comments_before = len(get_comments(jira.url, "admin", "admin", PARENT_KEY))

        env = {**os.environ, "JIRA_SERVER": jira.url, "JIRA_USER": "admin", "JIRA_TOKEN": "admin"}
        r = subprocess.run(
            [sys.executable, SCRIPT, PARENT_KEY, "--dry-run", "--artifacts-dir", art_dir],
            capture_output=True,
            text=True,
            env=env,
        )
        assert r.returncode == 0, r.stderr + r.stdout
        assert "Would link" in r.stdout

        assert _split_links(jira.url, PARENT_KEY) == []
        assert len(get_comments(jira.url, "admin", "admin", PARENT_KEY)) == comments_before
        assert os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md"), "dry run renamed artifacts"


class TestRerunIsANoOp:
    def test_second_run_after_success_changes_nothing(self, art_dir, jira):
        """After the rename loop, children are listed under their Jira keys:
        the confirmation comments must still map (via the created key), or a
        resume re-posts archival comments and re-adopts children."""
        _setup_parent(jira, art_dir)
        r = _run_split(art_dir, jira.url)
        assert r.returncode == 0, r.stderr + r.stdout
        comments_after_first = len(get_comments(jira.url, "admin", "admin", PARENT_KEY))
        children_after_first = _search_keys(jira.url, "labels = rfe-creator-split-result")

        r = _run_split(art_dir, jira.url)
        assert r.returncode == 0, r.stderr + r.stdout

        assert len(get_comments(jira.url, "admin", "admin", PARENT_KEY)) == comments_after_first
        assert _search_keys(jira.url, "labels = rfe-creator-split-result") == children_after_first


class TestSystemicClassification:
    def test_dead_auth_exits_systemic_without_fanout(self, art_dir):
        """Every call answered 401: one classified message, exit 5, and only
        a handful of requests — not a per-child fan-out (RHAIFIRST-571)."""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        counter = {"n": 0}

        class Deny(BaseHTTPRequestHandler):
            def _deny(self):
                counter["n"] += 1
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = _deny  # noqa: N815
            do_POST = _deny  # noqa: N815
            do_PUT = _deny  # noqa: N815

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Deny)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            _write(f"{art_dir}/rfe-tasks/{PARENT_KEY}.md", PARENT_TASK)
            _write(f"{art_dir}/rfe-originals/{PARENT_KEY}.md", PARENT_DESC)
            for cid in ("RFE-001", "RFE-002"):
                _write(
                    f"{art_dir}/rfe-tasks/{cid}.md",
                    CHILD_TASK.format(child_id=cid, title=f"Child {cid}"),
                )
            r = _run_split(art_dir, url)
        finally:
            server.shutdown()

        assert r.returncode == 5, r.stderr + r.stdout
        assert "systemic Jira failure" in r.stderr
        # Conflict check (1) + discovery (1): far below a per-child fan-out.
        assert counter["n"] <= 4, f"{counter['n']} requests"


class TestLegacyComments:
    def test_positional_comments_still_map(self, art_dir, jira):
        """Comments written before the id-based format map through the
        current ordering, so in-flight legacy splits keep recovering."""
        children = _setup_parent(jira, art_dir)
        # The live child's content must match the artifact — legacy adoption
        # goes through the same content guard as everything else now.
        from artifact_utils import parse_child_artifact

        _, _, _, cleaned = parse_child_artifact(f"{art_dir}/rfe-tasks/RFE-001.md")
        jira.create("RHAIRFE-77", "Child RFE-001", cleaned)
        add_comment(
            jira.url,
            "admin",
            "admin",
            PARENT_KEY,
            text_to_adf_paragraph("[RFE Creator] Split child 1 of 2: Child RFE-001"),
        )
        add_comment(
            jira.url,
            "admin",
            "admin",
            PARENT_KEY,
            text_to_adf_paragraph(
                "[RFE Creator] Created as RHAIRFE-77, linked to parent. (ref: child 1 of 2)"
            ),
        )

        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)

        assert state.phase1_done.get("RFE-001")
        assert state.phase2_done["RFE-001"]["key"] == "RHAIRFE-77"
        # The fixture never created the link: linked is DERIVED from the
        # live issue, not assumed from the comment — phase 2 heals it.
        assert state.phase2_done["RFE-001"]["linked"] is False
        assert "RFE-002" not in state.phase2_done

    def test_confirmed_child_with_changed_content_is_not_adopted(self, art_dir, jira):
        """The confirmation comment was the one adoption path with no
        content check: a cross-run re-split silently bound attempt 1's
        confirmed child into attempt 2's decomposition. One GET per
        comment-adopted child closes it."""
        children = _setup_parent(jira, art_dir, child_ids=("RFE-001",))
        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(jira.url, "admin", "admin", PARENT_KEY, children, state, config, False)
        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
        )
        assert state.phase2_done["RFE-001"]["commented"] is True

        # Attempt 2: same id, same title, different body.
        _write(
            f"{art_dir}/rfe-tasks/RFE-001.md",
            CHILD_TASK.format(child_id="RFE-001", title="Child RFE-001").replace(
                "Content for RFE-001.", "Entirely different scope."
            ),
        )
        new_children = [("RFE-001", "Child RFE-001", "Major", f"{art_dir}/rfe-tasks/RFE-001.md")]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, new_children, config)
        assert "RFE-001" not in state.phase2_done, (
            "confirmed stale child adopted despite content change"
        )

        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, new_children, state, art_dir, config, False
        )
        assert len(_search_keys(jira.url, "labels = rfe-creator-split-result")) == 2

    def test_confirmed_child_with_matching_content_is_adopted(self, art_dir, jira):
        """The guard must not break the resume that matters: identical
        artifact -> adoption, no duplicate, no re-confirmation."""
        children = _setup_parent(jira, art_dir, child_ids=("RFE-001",))
        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(jira.url, "admin", "admin", PARENT_KEY, children, state, config, False)
        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
        )

        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)
        assert state.phase2_done["RFE-001"]["commented"] is True

        phase2_create_link(
            jira.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
        )
        assert len(_search_keys(jira.url, "labels = rfe-creator-split-result")) == 1

    def test_forged_confirmation_with_wrong_title_is_refused(self, art_dir, jira):
        """A confirmation comment can be written by anyone who can comment
        on the parent (CodeRabbit, CWE-345): a referenced issue must also
        match the expected title, not just the description."""
        children = _setup_parent(jira, art_dir, child_ids=("RFE-001",))
        from artifact_utils import parse_child_artifact

        _, _, _, cleaned = parse_child_artifact(f"{art_dir}/rfe-tasks/RFE-001.md")
        # Same description, different title.
        jira.create("RHAIRFE-666", "Totally unrelated ticket", cleaned)
        add_comment(
            jira.url,
            "admin",
            "admin",
            PARENT_KEY,
            text_to_adf_paragraph(
                "[RFE Creator] Created as RHAIRFE-666 for RFE-001, linked to parent. (1 of 1)"
            ),
        )

        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)

        assert "RFE-001" not in state.phase2_done

    def test_positional_comments_ignored_when_the_set_shrank(self, art_dir, jira):
        """A legacy comment recorded '1 of 2'; the decomposition was revised
        to ONE child. Positional mapping through the new ordering would bind
        the old ticket to different content — the total mismatch must make
        the comment inert instead (review finding)."""
        children = _setup_parent(jira, art_dir, child_ids=("RFE-001",))
        jira.create("RHAIRFE-77", "Old child A", "Old content.")
        add_comment(
            jira.url,
            "admin",
            "admin",
            PARENT_KEY,
            text_to_adf_paragraph(
                "[RFE Creator] Created as RHAIRFE-77, linked to parent. (ref: child 1 of 2)"
            ),
        )

        config = SPLIT_CONFIG["rfe"]
        state = discover_state(jira.url, "admin", "admin", PARENT_KEY, children, config)

        assert "RFE-001" not in state.phase2_done


# ─── Overridden project (design §3.2.1; PR-3c, the writers) ──────────────────

OVERRIDE_PROJECT = "KONFLUX"
OVERRIDE_VAR = type_registry.binding_env_var("rfe", "PROJECT")


def _override_config():
    """SPLIT_CONFIG's rfe entry re-projected over the effective binding of a deployment whose
    rfe project is overridden to KONFLUX — the config main() builds from ``resolve()``."""
    desc = split_submit._TYPES.get("rfe")
    return split_submit._split_config(desc, desc.binding({OVERRIDE_VAR: OVERRIDE_PROJECT}))


class TestOverriddenProject:
    """``RFE_CREATOR_BINDING_RFE_PROJECT=KONFLUX``: the children are created in the EFFECTIVE
    project with the descriptor issue type, the recovery signals and labels are exactly today's
    (they never carry the project), and the parent — an RHAIRFE issue, owned through the
    descriptor read prefix — is linked and closed as before. The emulator has no KONFLUX project
    until an issue with that key stem is imported (tests/conftest.py JiraHelper.create
    auto-creates the project from the key's stem)."""

    @pytest.fixture
    def konflux(self, jira):
        jira.create(f"{OVERRIDE_PROJECT}-1", "Seed", "Seeds the overridden project.")
        return jira

    def _run(self, art_dir, url, *extra):
        env = {
            **os.environ,
            "JIRA_SERVER": url,
            "JIRA_USER": "admin",
            "JIRA_TOKEN": "admin",
            OVERRIDE_VAR: OVERRIDE_PROJECT,
        }
        return subprocess.run(
            [sys.executable, SCRIPT, PARENT_KEY, "--artifacts-dir", art_dir, *extra],
            capture_output=True,
            text=True,
            env=env,
        )

    def _split_results(self, url, project):
        return _search_keys(url, f"project = {project} AND labels = rfe-creator-split-result")

    def test_children_are_created_in_the_effective_project(self, art_dir, konflux):
        _setup_parent(konflux, art_dir)
        marker = _marker_for(art_dir, "RFE-001")

        r = self._run(art_dir, konflux.url)
        assert r.returncode == 0, r.stderr + r.stdout
        assert r.stderr == ""  # no --type: the legacy default rung stays silent (D3)
        assert "Phase 2: Created KONFLUX-" in r.stdout

        created = self._split_results(konflux.url, OVERRIDE_PROJECT)
        assert len(created) == 2
        assert all(key.startswith(f"{OVERRIDE_PROJECT}-") for key in created)
        assert self._split_results(konflux.url, "RHAIRFE") == []
        for key in created:
            fields = get_issue(
                konflux.url, "admin", "admin", key, ["issuetype", "labels", "project"]
            )
            fields = fields["fields"]
            assert fields["project"]["key"] == OVERRIDE_PROJECT
            assert fields["issuetype"]["name"] == "Feature Request"
            assert {"rfe-creator-auto-created", "rfe-creator-split-result"} <= set(fields["labels"])
        # The marker label is today's: label prefix, parent, local id, fingerprint — no project.
        assert len(_search_keys(konflux.url, f'labels = "{marker}"')) == 1
        # Linked to and closed the RHAIRFE parent.
        assert _split_links(konflux.url, PARENT_KEY) == created
        issue = get_issue(konflux.url, "admin", "admin", PARENT_KEY, ["status"])
        assert issue["fields"]["status"]["statusCategory"]["key"] == "done"
        # Artifacts renamed to the effective keys.
        for key in created:
            assert os.path.exists(f"{art_dir}/rfe-tasks/{key}.md"), key
        assert not os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")

    def test_rerun_under_the_override_is_a_no_op(self, art_dir, konflux):
        _setup_parent(konflux, art_dir)
        r = self._run(art_dir, konflux.url)
        assert r.returncode == 0, r.stderr + r.stdout
        comments_after_first = len(get_comments(konflux.url, "admin", "admin", PARENT_KEY))
        created = self._split_results(konflux.url, OVERRIDE_PROJECT)

        r = self._run(art_dir, konflux.url)
        assert r.returncode == 0, r.stderr + r.stdout
        assert len(get_comments(konflux.url, "admin", "admin", PARENT_KEY)) == comments_after_first
        assert self._split_results(konflux.url, OVERRIDE_PROJECT) == created

    def test_dry_run_plans_the_effective_project_and_writes_nothing(self, art_dir, konflux):
        _setup_parent(konflux, art_dir)
        r = self._run(art_dir, konflux.url, "--dry-run")
        assert r.returncode == 0, r.stderr + r.stdout
        assert "Would create KONFLUX ticket for child 1/2: Child RFE-001" in r.stdout
        assert self._split_results(konflux.url, OVERRIDE_PROJECT) == []
        assert self._split_results(konflux.url, "RHAIRFE") == []
        issue = get_issue(konflux.url, "admin", "admin", PARENT_KEY, ["status"])
        assert issue["fields"]["status"]["statusCategory"]["key"] != "done"
        assert os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")

    def test_recovery_searches_the_effective_project(self, art_dir, konflux, monkeypatch):
        """Death after create: the marker-label search that finds the orphan runs over the
        EFFECTIVE project — the descriptor project would never see a KONFLUX child."""
        children = _setup_parent(konflux, art_dir, child_ids=("RFE-001",))
        config = _override_config()
        state = discover_state(konflux.url, "admin", "admin", PARENT_KEY, children, config)
        phase1_persist(konflux.url, "admin", "admin", PARENT_KEY, children, state, config, False)

        def dying(*a, **k):
            raise RuntimeError("died before link")

        monkeypatch.setattr(split_submit, "create_issue_link", dying)
        with pytest.raises(RuntimeError):
            phase2_create_link(
                konflux.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
            )
        monkeypatch.undo()
        (orphan,) = self._split_results(konflux.url, OVERRIDE_PROJECT)
        assert orphan.startswith(f"{OVERRIDE_PROJECT}-")

        state = discover_state(konflux.url, "admin", "admin", PARENT_KEY, children, config)
        assert state.phase2_done["RFE-001"] == {"key": orphan, "linked": False, "commented": False}
        # The descriptor binding's config (project RHAIRFE) cannot see it: the JQL is
        # binding-derived, not a constant.
        unaware = discover_state(
            konflux.url, "admin", "admin", PARENT_KEY, children, SPLIT_CONFIG["rfe"]
        )
        assert "RFE-001" not in unaware.phase2_done

        phase2_create_link(
            konflux.url, "admin", "admin", PARENT_KEY, children, state, art_dir, config, False
        )
        assert self._split_results(konflux.url, OVERRIDE_PROJECT) == [orphan]
        assert _split_links(konflux.url, PARENT_KEY) == [orphan]


# ─── The parent is verified before the first write (PR-3c, the writers) ─────────


class TestParentBindingIsVerifiedBeforeAnyWrite:
    """The pre-split fetch of the parent requests the two binding witnesses (and a parent with
    no original gets its own fetch): a parent whose (project, issuetype) is not the binding's —
    nor, for a descriptor-prefixed key, the descriptor pair — is refused with one Error: line and
    the per-parent exit code, and nothing is commented, labelled, linked, created or closed."""

    REFUSAL = (
        "Error: RHAIRFE-1000 is (RHAIRFE, Epic) in Jira but the resolved type rfe binds "
        "(RHAIRFE, Feature Request); refusing to split — nothing written\n"
    )

    def _epic_parent(self, jira, art_dir, original=True):
        jira.create(PARENT_KEY, "Parent RFE", PARENT_DESC, issue_type="Epic")
        _write(f"{art_dir}/rfe-tasks/{PARENT_KEY}.md", PARENT_TASK)
        if original:
            _write(f"{art_dir}/rfe-originals/{PARENT_KEY}.md", PARENT_DESC)
        for cid in ("RFE-001", "RFE-002"):
            _write(f"{art_dir}/rfe-tasks/{cid}.md", CHILD_TASK.format(child_id=cid, title=cid))

    def _assert_untouched(self, jira, art_dir, r):
        assert r.returncode == split_submit.EXIT_PER_PARENT
        assert r.stderr == self.REFUSAL
        assert "Checking submission state" not in r.stdout and "Phase 1:" not in r.stdout
        assert get_comments(jira.url, "admin", "admin", PARENT_KEY) == []
        fields = ["labels", "status", "issuelinks"]
        issue = get_issue(jira.url, "admin", "admin", PARENT_KEY, fields)
        assert issue["fields"]["labels"] == []
        assert issue["fields"]["issuelinks"] == []
        assert issue["fields"]["status"]["statusCategory"]["key"] != "done"
        assert _search_keys(jira.url, "labels = rfe-creator-split-result") == []
        assert os.path.exists(f"{art_dir}/rfe-tasks/RFE-001.md")

    def test_an_epic_parent_is_refused_and_untouched(self, art_dir, jira):
        self._epic_parent(jira, art_dir)
        self._assert_untouched(jira, art_dir, _run_split(art_dir, jira.url))

    def test_a_parent_without_an_original_gets_its_own_witness_fetch(self, art_dir, jira):
        # The conflict check makes no request without an original; the witnesses are fetched
        # on their own and the refusal is the same.
        self._epic_parent(jira, art_dir, original=False)
        self._assert_untouched(jira, art_dir, _run_split(art_dir, jira.url))

    def test_a_feature_request_parent_without_an_original_is_split(self, art_dir, jira):
        _setup_parent(jira, art_dir)
        os.remove(f"{art_dir}/rfe-originals/{PARENT_KEY}.md")
        r = _run_split(art_dir, jira.url)
        assert r.returncode == 0, r.stderr + r.stdout
        assert r.stderr == ""
        assert len(_search_keys(jira.url, "labels = rfe-creator-split-result")) == 2
