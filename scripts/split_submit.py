#!/usr/bin/env python3
"""Resilient split-submission of child RFEs or Initiatives to Jira.

Submits children produced by /rfe.split or /initiative-split to Jira with
proper linking and parent closure. Designed to be idempotent and resumable —
uses Jira comments as the durable store for content and progress tracking.

Reads all structured metadata from YAML frontmatter on task files.
Identifies parent (status: Archived, id matches parent key) and children
(parent_key matches parent's id) from frontmatter.

Usage:
    python scripts/split_submit.py RHAIRFE-XXXX [--dry-run] [--artifacts-dir DIR]
    python scripts/split_submit.py RHOAIENG-XXXX --type initiative [--dry-run]

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import os
import re
import sys
import urllib.error
from functools import partial

# Ensure progress output is visible immediately when stdout is redirected
# to a file or pipe (Python defaults to full buffering in that case).
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry  # noqa: E402
from artifact_utils import (  # noqa: E402
    ValidationError,
    find_review_file,
    parse_child,
    read_frontmatter_validated,
    rebuild_index,
    rename_to_tracker_key,
    scan_tasks,
)
from jira_utils import (  # noqa: E402
    add_comment,
    add_labels,
    archival_comment_adf,
    check_description_conflict,
    create_issue,
    create_issue_link,
    do_transition,
    get_comments,
    get_issue,
    get_transitions,
    markdown_to_adf,
    require_env,
    search_issues,
    text_to_adf_paragraph,
)

MAX_LEAF_CHILDREN = 6

# Exit-code contract consumed by submit.py's split loop (RHAIFIRST-571).
# 2 and 3 predate this and stay: leaf-cap refusal and Jira conflict.
EXIT_PER_PARENT = 4  # this parent failed; others are unaffected — continue
EXIT_SYSTEMIC = 5  # Jira itself is unusable (auth, outage) — abort the loop
EXIT_USAGE = 64  # argparse would exit 2, which reads as the leaf cap


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on usage errors — indistinguishable from the leaf-cap
    refusal to the caller. Route usage errors to EX_USAGE instead."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _classify_exit(exc):
    """Map an escaped exception to the per-parent/systemic exit code.

    jira_utils.api_call_with_retry is a RETRY classifier, not a fatal one:
    it re-raises 401/403/400/404 immediately and 429/5xx/URLError after
    three attempts. What reaches here is one undifferentiated bucket, so
    classify by what the failure says about the NEXT parent: an expired
    token or an unreachable instance fails every parent identically
    (systemic), while a 400 on one child's field or a local artifact
    problem says nothing about the others (per-parent).
    """
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return EXIT_SYSTEMIC
        if exc.code == 429 or exc.code >= 500:
            # 429/502/503/504 are only reachable after api_call_with_retry
            # exhausted its attempts; a bare 500 (and any other 5xx) is
            # re-raised immediately — but it is still a server-side fault,
            # and misreading an instance-wide 500 as per-parent fans out
            # requests and quarantine flags across the whole batch
            # (CodeRabbit). A payload-specific 500 aborting one night is
            # the cheaper error: the parent is untouched and retried.
            return EXIT_SYSTEMIC
        return EXIT_PER_PARENT
    if isinstance(exc, urllib.error.URLError):
        return EXIT_SYSTEMIC
    return EXIT_PER_PARENT


# The work-item type registry (types/<name>/type.yaml), read once at import; every
# per-type value below is a projection of a descriptor (design work-item-types-unified.md
# §10 item 2). Deliberately the DESCRIPTOR values, not the effective binding: deployment
# overrides land with resolve() in a later PR.
_TYPES = type_registry.load()


def _split_config(desc):
    """Project one descriptor onto the SPLIT_CONFIG entry the phases read.

    The four callables keep the ``(artifacts_dir, ...)`` signatures the per-type wrappers
    had: scan_tasks / rename_to_tracker_key / parse_child are bound to ``desc``, and
    find_review_file routes by the child id, which the task schema constrains to this
    type's ``local_id_pattern`` or ``key_prefixes`` — so it lands on ``dirs.reviews`` too.
    """
    dirs = desc.dirs("bare")
    alignment = desc.get("conventions.labels.alignment", None)
    return {
        "project": desc.get("identity.jira.project"),
        "issue_type": desc.get("identity.jira.issue_type"),
        "comment_marker": desc.get("conventions.comment_prefix"),
        "label_prefix": desc.get("conventions.label_prefix"),
        "entity_name": desc.get("display.entity"),
        "entity_name_plural": desc.get("display.entity_plural"),
        "id_field": desc.id_field,
        "reviews_dir": dirs["reviews"],
        "review_schema": f"{desc.name}-review",
        "originals_dir": dirs["originals"],
        "scan_fn": partial(scan_tasks, desc=desc),
        "rename_fn": partial(rename_to_tracker_key, desc=desc),
        "parse_child_fn": partial(parse_child, desc=desc),
        "find_review_fn": find_review_file,
        "do_rebuild_index": desc.get("index.enabled"),
        "alignment_labels": dict(alignment) if alignment is not None else None,
    }


SPLIT_CONFIG = {name: _split_config(_TYPES.get(name)) for name in _TYPES.names()}


# The Jira-side facts of the split transaction that SPLIT_CONFIG never carried (its key
# set is a contract shared with the tests): the link type between a parent and its
# children, and how the parent is closed once they exist. Keyed by the (project,
# issue_type) binding pair every SPLIT_CONFIG entry carries, so a phase recovers them from
# its ``config`` alone. validate_types.py keeps the pair unique per registered type, but
# the registry does not run that gate at load, so the invariant is enforced here where it
# is relied on: a second type with the same pair would otherwise silently replace the
# first's facts. Read with a None default: both are optional in the schema and a
# registered type that never splits must not break the import (main() refuses to split a
# parent of such a type before any scan or Jira call).
def _tracker_facts(types):
    facts, owner = {}, {}
    for desc in types:
        pair = (desc.get("identity.jira.project"), desc.get("identity.jira.issue_type"))
        if pair in owner:
            raise type_registry.RegistryError(
                f"split_submit: types {owner[pair]!r} and {desc.name!r} share the "
                f"(project, issue_type) binding {pair}; the split link type and the "
                "close-superseded state are recovered by that pair, so it must be unique"
            )
        owner[pair] = desc.name
        facts[pair] = {
            "split_link_type": desc.get("identity.jira.split_link_type", None),
            "close_superseded": desc.get("identity.jira.state_map.close_superseded", None),
        }
    return facts


_TRACKER = _tracker_facts(_TYPES)


def _tracker(config):
    """The ``_TRACKER`` entry of the type ``config`` projects."""
    return _TRACKER[(config["project"], config["issue_type"])]


def _missing_split_facts(config):
    """Names of the binding facts a split of this type needs but its descriptor omits.

    Empty for both shipped types. A type may legitimately omit them (it never splits);
    the refusal belongs to the moment a parent of that type is submitted for a split.
    """
    facts = _tracker(config)
    close = facts["close_superseded"] or {}
    missing = []
    if not facts["split_link_type"]:
        missing.append("identity.jira.split_link_type")
    # phase3_close matches the target status and sets the resolution on the transition.
    for key in ("transition", "resolution"):
        if not close.get(key):
            missing.append(f"identity.jira.state_map.close_superseded.{key}")
    return missing


def _feasibility_labels(label_prefix):
    return {
        "feasible": f"{label_prefix}-feasibility-pass",
        "infeasible": f"{label_prefix}-feasibility-fail",
        "indeterminate": f"{label_prefix}-feasibility-unknown",
    }


# ─── Recovery / State Detection ──────────────────────────────────────────────


def _extract_adf_text(node):
    """Recursively extract plain text from an ADF node."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_extract_adf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    return _extract_adf_text(node.get("content", []))


def _inspect_child(server, user, token, child_key, artifact_path, parent_key, config):
    """One GET: does the live child match the artifact, and is it linked?

    Returns {"content_matches", "title", "linked"}. The content comparison is
    the same normalize-and-compare check_description_conflict uses: the local
    side is the cleaned markdown phase 2 would submit, the live side is the
    child's ADF description rendered back to markdown. `linked` is DERIVED
    from the live issuelinks rather than assumed from the signal that found
    the child (CodeRabbit: a confirmation comment implies the link only for
    comments this tool wrote in order) — a missing link is then healed by
    phase 2's completion path instead of silently skipped.
    """
    from jira_utils import adf_to_markdown, normalize_for_compare

    _, _, _, cleaned = config["parse_child_fn"](artifact_path)
    split_link_type = _tracker(config)["split_link_type"]
    issue = get_issue(server, user, token, child_key, ["description", "summary", "issuelinks"])
    fields = issue.get("fields", {})
    desc_raw = fields.get("description")
    if isinstance(desc_raw, dict):
        live = normalize_for_compare(adf_to_markdown(desc_raw))
    elif desc_raw is None:
        live = ""
    else:
        live = normalize_for_compare(str(desc_raw))
    linked = any(
        link.get("type", {}).get("name") == split_link_type
        and parent_key
        in (
            (link.get("inwardIssue") or {}).get("key"),
            (link.get("outwardIssue") or {}).get("key"),
        )
        for link in fields.get("issuelinks", [])
    )
    return {
        "content_matches": normalize_for_compare(cleaned) == live,
        "title": fields.get("summary", ""),
        "linked": linked,
    }


def _child_fingerprint(config, artifact_path):
    """Short content fingerprint of the child's cleaned markdown body."""
    import hashlib

    _, _, _, cleaned = config["parse_child_fn"](artifact_path)
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]


def _child_marker_label(label_prefix, parent_key, child_id, fingerprint):
    """Deterministic per-(parent, child, content) label applied at creation.

    This is the recovery signal that exists from the instant the child does:
    a process death between create_issue and the link/comment used to mint a
    ticket no future run could find (RHAIFIRST-570). It carries the child's
    local id, so recovery maps by identity instead of position; the PARENT
    key, because local ids are workspace-scoped and restart at 001 in every
    fresh CI run while labels persist forever (an id-only label let a new
    split adopt a stale child of a DIFFERENT parent — reproduced live in
    review); and a CONTENT fingerprint, because a re-split can legitimately
    reuse the same parent, id and even title for different scope, and the
    title is too weak an identity to prevent adopting a stale child whose
    body is wrong (CodeRabbit on #169). Adoption therefore requires the
    exact label — same parent, id and content — plus the title guard.
    Within-run retries see identical artifacts and adopt; a cross-run
    re-split regenerates content and deliberately does NOT adopt: minting a
    fresh child and leaving the stale one for the human the quarantine
    comment already points at beats silently binding wrong content.
    """
    return f"{label_prefix}-split-child-{parent_key.lower()}-{child_id.lower()}-{fingerprint}"


class SubmissionState:
    """Tracks progress of the split submission, keyed by child local id.

    phase2_done values are dicts {"key", "linked", "commented"} so recovery
    can finish a partially-applied child (created but never linked, or linked
    but never confirmed) instead of only skipping fully-done ones.
    """

    def __init__(self):
        self.phase1_done = {}  # child_id -> comment ID
        self.phase2_done = {}  # child_id -> {"key", "linked", "commented"}
        self.parent_closed = False
        self.total_children = 0
        self.parent_components = []  # inherited by children
        self.parent_labels = []  # non-automation labels inherited
        self.parent_parent_key = None  # Jira parent (e.g. RHAISTRAT) inherited
        self.parent_reporter_id = None  # original reporter preserved on children


def discover_state(server, user, token, parent_key, expected_children, config):
    """Determine submission progress from Jira: comments, links, marker labels.

    Recovery is id-based: every signal maps to the child's LOCAL id, never to
    its position in the children list — sibling changes between runs used to
    re-number positional indices and silently misalign recovery. Legacy
    positional comments (written before RHAIFIRST-570) are still understood,
    mapped through the current ordering as a best effort.

    Signal precedence per child: confirmation comment (implies created,
    linked and confirmed — the comment is posted last), then a `Work item
    split` link (created and linked, confirmation lost), then the per-child
    marker label (created only — the death-after-create window). Later
    signals never overwrite earlier ones.
    """
    state = SubmissionState()
    state.total_children = len(expected_children)
    marker = re.escape(config["comment_marker"])
    split_link_type = _tracker(config)["split_link_type"]
    ids_in_order = [child_id for (child_id, _, _, _) in expected_children]
    id_by_title = {title: child_id for (child_id, title, _, _) in expected_children}
    title_by_id = {child_id: title for (child_id, title, _, _) in expected_children}
    path_by_id = {child_id: path for (child_id, _, _, path) in expected_children}

    def _adopt_confirmed(child_id, created_key, comment_id):
        """Adopt a confirmation-comment match only when the live child's
        content is the artifact this run would submit.

        The comment signal was the one adoption path with no content check:
        on a cross-run re-split it silently bound attempt 1's confirmed
        child (old content) into attempt 2's decomposition. Refusal falls
        through to the marker search and a fresh create — the same
        prefer-a-visible-duplicate-over-silent-misbinding rule the marker
        path already applies. The GET is recovery-only and per adopted
        child, at most the leaf cap.
        """
        if child_id not in path_by_id:
            # A comment naming a child that no longer exists (removed in a
            # re-split) must not inflate phase2_done — phase3's count guard
            # compares its size against the CURRENT total.
            return
        try:
            live = _inspect_child(
                server, user, token, created_key, path_by_id[child_id], parent_key, config
            )
        except Exception as e:
            print(
                f"  Warning: could not verify {created_key} against {child_id}'s "
                f"artifact ({e}) — not adopting it",
                file=sys.stderr,
            )
            return
        if not live["content_matches"] or live["title"] != title_by_id.get(child_id):
            print(
                f"  Warning: {created_key} is confirmed for {child_id} but its "
                f"content or title does not match the current artifact — not "
                f"adopting it",
                file=sys.stderr,
            )
            return
        state.phase2_done[child_id] = {
            "key": created_key,
            # Derived, not assumed: a forged or out-of-order comment must not
            # make phase 2 skip the link — a missing one is completed there.
            "linked": live["linked"],
            "commented": True,
        }
        state.phase1_done.setdefault(child_id, comment_id)

    def _id_at(idx):
        """Map a legacy positional index to a child id, best effort."""
        return ids_in_order[idx - 1] if 1 <= idx <= len(ids_in_order) else None

    # 1. Scan comments for comment markers (new id-based format first,
    #    legacy positional format second — the formats are disjoint).
    comments = get_comments(server, user, token, parent_key)
    for comment in comments:
        body_text = _extract_adf_text(comment.get("body", {}))

        archival_match = re.search(rf"{marker} Split child (\S+) \(\d+ of \d+\):", body_text)
        legacy_archival = re.search(rf"{marker} Split child (\d+) of (\d+):", body_text)
        if archival_match or legacy_archival:
            if archival_match:
                child_id = archival_match.group(1)
            else:
                # Positional legacy comment: only trustworthy when the child
                # count it recorded still matches — a shrunk or regrown set
                # re-numbers positions and would misbind (caught in review).
                child_id = (
                    _id_at(int(legacy_archival.group(1)))
                    if int(legacy_archival.group(2)) == len(ids_in_order)
                    else None
                )
            if child_id:
                state.phase1_done[child_id] = comment["id"]
            continue

        confirm_match = re.search(
            rf"{marker} Created as (\S+) for (\S+), linked to parent", body_text
        )
        if confirm_match:
            created_key, child_id = confirm_match.group(1), confirm_match.group(2)
            if child_id not in set(ids_in_order) and created_key in set(ids_in_order):
                # The artifact was already renamed to the Jira key by a run
                # that died after the rename: the current scan lists the
                # child under its KEY, not its local id. Without this, the
                # resume would re-post its archival comment and re-adopt it
                # through the link signal.
                child_id = created_key
            if child_id not in state.phase2_done:
                _adopt_confirmed(child_id, created_key, comment["id"])
            continue

        legacy_confirm = re.search(
            rf"{marker} Created as (\S+),.*\(ref: child (\d+) of (\d+)\)",
            body_text,
        )
        if legacy_confirm:
            child_id = (
                _id_at(int(legacy_confirm.group(2)))
                if int(legacy_confirm.group(3)) == len(ids_in_order)
                else None
            )
            if child_id and child_id not in state.phase2_done:
                _adopt_confirmed(child_id, legacy_confirm.group(1), comment["id"])
            continue

    # 2. Check issue links, components, labels, and Jira parent
    issue = get_issue(
        server,
        user,
        token,
        parent_key,
        ["issuelinks", "status", "components", "labels", "parent", "reporter"],
    )
    for link in issue.get("fields", {}).get("issuelinks", []):
        if link.get("type", {}).get("name") != split_link_type:
            continue
        # Real Jira renders the child as outwardIssue on the parent; the
        # jira-emulator renders it as inwardIssue. Check both ends — the
        # title guard keeps a parent that is itself a split child (its own
        # inward link points at ITS parent) from matching here.
        other = link.get("outwardIssue") or link.get("inwardIssue")
        if not other:
            continue
        child_key = other["key"]
        child_summary = other.get("fields", {}).get("summary", "")
        child_id = id_by_title.get(child_summary)
        if child_id and child_id not in state.phase2_done:
            # Same content guard as the comment path: the title alone would
            # adopt a stale child from an earlier attempt whose body no
            # longer matches the artifact this run would submit.
            try:
                live = _inspect_child(
                    server, user, token, child_key, path_by_id[child_id], parent_key, config
                )
                matches = live["content_matches"]
            except Exception as e:
                print(
                    f"  Warning: could not verify {child_key} against {child_id}'s "
                    f"artifact ({e}) — not adopting it",
                    file=sys.stderr,
                )
                continue
            if not matches:
                print(
                    f"  Warning: {child_key} is linked and matches {child_id}'s title "
                    f"but not its content — not adopting it",
                    file=sys.stderr,
                )
                continue
            state.phase2_done[child_id] = {
                "key": child_key,
                "linked": True,
                "commented": False,
            }

    # 2b. Marker-label search: the only signal that exists from the instant
    #     the child does. Finds children whose creating process died before
    #     the link and the comment — previously undiscoverable, so the next
    #     run minted a duplicate (RHAIFIRST-570).
    label_prefix = config["label_prefix"]
    unmapped = [cid for cid in ids_in_order if cid not in state.phase2_done]
    if unmapped:
        markers = [
            _child_marker_label(
                label_prefix, parent_key, cid, _child_fingerprint(config, path_by_id[cid])
            )
            for cid in unmapped
        ]
        marker_by_label = dict(zip(markers, unmapped))
        # Quote every literal: label values embed parent_key and child ids
        # that come from frontmatter/CLI, and an unquoted `)` or `AND` would
        # change the query semantics (CWE-943). The values themselves cannot
        # contain quotes — ids are schema-validated key patterns.
        quoted = ", ".join(f'"{m}"' for m in markers)
        jql = f'project = "{config["project"]}" AND labels in ({quoted})'
        try:
            found = search_issues(server, user, token, jql, "labels,issuelinks,summary")
        except Exception as e:
            # Best-effort net: a search failure must not turn recovery into
            # an abort — the comment and link signals above still stand.
            print(f"  Warning: marker-label search failed: {e}", file=sys.stderr)
            found = []
        for hit in found:
            hit_labels = hit.get("fields", {}).get("labels", [])
            hit_ids = [marker_by_label[m] for m in hit_labels if m in marker_by_label]
            if len(hit_ids) != 1:
                continue
            child_id = hit_ids[0]
            if child_id in state.phase2_done:
                continue
            # Title guard, on top of the content-bound label: adoption
            # happens only when parent, id, content AND title all match —
            # i.e. the same artifact this run would submit.
            if hit.get("fields", {}).get("summary", "") != title_by_id.get(child_id):
                print(
                    f"  Warning: {hit['key']} carries the marker for {child_id} but "
                    f"its title does not match the expected child — not adopting it",
                    file=sys.stderr,
                )
                continue
            linked = any(
                link.get("type", {}).get("name") == split_link_type
                and parent_key
                in (
                    (link.get("inwardIssue") or {}).get("key"),
                    (link.get("outwardIssue") or {}).get("key"),
                )
                for link in hit.get("fields", {}).get("issuelinks", [])
            )
            state.phase2_done[child_id] = {
                "key": hit["key"],
                "linked": linked,
                "commented": False,
            }

    # Capture parent's components and non-automation labels for inheritance
    label_prefix = config["label_prefix"]
    state.parent_components = [c["name"] for c in issue.get("fields", {}).get("components", [])]
    state.parent_labels = [
        label
        for label in issue.get("fields", {}).get("labels", [])
        if not label.startswith(f"{label_prefix}-")
    ]
    jira_parent = issue.get("fields", {}).get("parent")
    if jira_parent:
        state.parent_parent_key = jira_parent.get("key")
    reporter = issue.get("fields", {}).get("reporter")
    if reporter:
        state.parent_reporter_id = reporter.get("accountId")

    # 3. Check parent status
    status_cat = issue.get("fields", {}).get("status", {}).get("statusCategory", {}).get("key", "")
    state.parent_closed = status_cat == "done"

    return state


# ─── Phases ───────────────────────────────────────────────────────────────────


def phase1_persist(server, user, token, parent_key, children, state, config, dry_run):
    """Post archival comments for each child not yet persisted."""
    total = len(children)
    parse_child = config["parse_child_fn"]
    comment_marker = config["comment_marker"]

    for idx, (child_id, title, priority, artifact_path) in enumerate(children, 1):
        if child_id in state.phase1_done:
            print(f"  Phase 1: Child {child_id} ({idx}/{total}) already posted, skipping")
            continue

        _, _, full_markdown, _ = parse_child(artifact_path)
        # The header carries the child's local id so recovery maps by
        # identity, not position (RHAIFIRST-570).
        header = f"{comment_marker} Split child {child_id} ({idx} of {total}): {title}"

        if dry_run:
            print(
                f"  Phase 1: Would post archival comment for child "
                f"{child_id} ({idx}/{total}): {title} ({len(full_markdown)} chars)"
            )
            state.phase1_done[child_id] = "dry-run"
            continue

        body_adf = archival_comment_adf(header, full_markdown)
        result = add_comment(server, user, token, parent_key, body_adf)
        state.phase1_done[child_id] = result["id"]
        print(f"  Phase 1: Posted content for child {child_id} ({idx}/{total}): {title}")


def phase2_create_link(
    server, user, token, parent_key, children, state, artifacts_dir, config, dry_run
):
    """Create tickets, link to parent, and post confirmation comments."""
    total = len(children)
    label_prefix = config["label_prefix"]
    comment_marker = config["comment_marker"]
    project = config["project"]
    issue_type = config["issue_type"]
    entity_name = config["entity_name"]
    review_schema = config["review_schema"]
    find_review = config["find_review_fn"]
    feas_labels = _feasibility_labels(label_prefix)
    alignment_labels = config["alignment_labels"]
    split_link_type = _tracker(config)["split_link_type"]

    for idx, (child_id, title, priority, artifact_path) in enumerate(children, 1):
        done = state.phase2_done.get(child_id)
        if done and done["linked"] and done["commented"]:
            print(
                f"  Phase 2: Child {child_id} ({idx}/{total}) already created as "
                f"{done['key']}, skipping"
            )
            continue

        if done:
            # Created by a previous run that died before finishing: complete
            # the missing steps instead of minting a duplicate.
            child_key = done["key"]
            print(
                f"  Phase 2: Child {child_id} ({idx}/{total}) found as {child_key} "
                f"(linked: {done['linked']}, confirmed: {done['commented']}) — completing"
            )
            if dry_run:
                # Discovery runs whenever credentials exist, dry run or not —
                # completing here would write to Jira during a run the caller
                # expects to be read-only (caught in review).
                if not done["linked"]:
                    print(f"           Would link {child_key} to {parent_key}")
                if not done["commented"]:
                    print(f"           Would post confirmation for {child_key}")
                done["linked"] = True
                done["commented"] = True
                continue
            if not done["linked"]:
                create_issue_link(server, user, token, split_link_type, parent_key, child_key)
                print(f"           Linked {child_key} to {parent_key}")
                done["linked"] = True
            if not done["commented"]:
                confirm_text = (
                    f"{comment_marker} Created as {child_key} for {child_id}, "
                    f"linked to parent. ({idx} of {total})"
                )
                add_comment(server, user, token, parent_key, text_to_adf_paragraph(confirm_text))
                done["commented"] = True
            continue

        if child_id not in state.phase1_done:
            print(
                f"  ERROR: Child {child_id} ({idx}/{total}) has no archival comment. "
                f"Run Phase 1 first.",
                file=sys.stderr,
            )
            sys.exit(EXIT_PER_PARENT)

        _, _, _, cleaned_markdown = config["parse_child_fn"](artifact_path)
        description_adf = markdown_to_adf(cleaned_markdown)

        # Determine labels from review frontmatter. The marker label is the
        # recovery signal that exists from the instant the child does — and
        # doubles as visible local-id provenance on the ticket.
        labels = [
            f"{label_prefix}-auto-created",
            f"{label_prefix}-split-result",
            _child_marker_label(
                label_prefix, parent_key, child_id, _child_fingerprint(config, artifact_path)
            ),
        ]

        review_path = find_review(artifacts_dir, child_id)
        review_rec = None
        attn_reason = None
        feas_label = None
        align_label = None
        if review_path:
            try:
                review_data, _ = read_frontmatter_validated(review_path, review_schema)
                review_rec = review_data.get("recommendation")
                if review_data.get("auto_revised", False):
                    labels.append(f"{label_prefix}-auto-revised")
                if review_data.get("needs_attention", False):
                    labels.append(f"{label_prefix}-needs-attention")
                    attn_reason = review_data.get("needs_attention_reason")
                feas_label = feas_labels.get(review_data.get("feasibility"))
                if alignment_labels:
                    align_label = alignment_labels.get(review_data.get("alignment"))
            except (ValidationError, Exception):
                pass  # proceed without review data
        if review_rec == "submit":
            labels.append(f"{label_prefix}-autofix-rubric-pass")
        if feas_label:
            labels.append(feas_label)
        if align_label:
            labels.append(align_label)

        # Inherit non-automation labels from parent
        labels = state.parent_labels + labels

        if dry_run:
            print(
                f"  Phase 2: Would create {project} ticket for child "
                f"{idx}/{total}: {title} (priority: {priority})"
            )
            print(f"           Labels: {', '.join(labels)}")
            if state.parent_components:
                print(f"           Components: {', '.join(state.parent_components)}")
            if state.parent_parent_key:
                print(f"           Parent: {state.parent_parent_key}")
            if state.parent_reporter_id:
                print(f"           Reporter: {state.parent_reporter_id}")
            print(f"           Would link to {parent_key} via '{split_link_type}'")
            if attn_reason:
                print("           Would post needs-attention comment")
            state.phase2_done[child_id] = {
                "key": f"{project}-DRY",
                "linked": True,
                "commented": True,
            }
            continue

        # 1. Create ticket with labels, inherited components, and parent
        child_key = create_issue(
            server,
            user,
            token,
            project,
            issue_type,
            title,
            description_adf,
            priority,
            labels=labels,
            components=state.parent_components,
            parent_key=state.parent_parent_key,
            reporter_account_id=state.parent_reporter_id,
        )
        print(f"  Phase 2: Created {child_key} for child {idx}/{total}: {title}")
        print(f"           Labels: {', '.join(labels)}")
        if state.parent_parent_key:
            print(f"           Parent: {state.parent_parent_key}")

        # 2. Link to parent
        create_issue_link(server, user, token, split_link_type, parent_key, child_key)
        print(f"           Linked {child_key} to {parent_key}")

        # 3. Post confirmation comment (carries the child's local id so
        # recovery maps by identity, not position)
        confirm_text = (
            f"{comment_marker} Created as {child_key} for {child_id}, "
            f"linked to parent. ({idx} of {total})"
        )
        add_comment(server, user, token, parent_key, text_to_adf_paragraph(confirm_text))

        # 4. Post needs-attention comment on the new child ticket
        if attn_reason:
            attn_md = (
                f"*{comment_marker}* This {entity_name} has been flagged for human review:"
                f"\n\n{attn_reason}"
            )
            add_comment(server, user, token, child_key, markdown_to_adf(attn_md))
            print("           Posted needs-attention comment")

        state.phase2_done[child_id] = {"key": child_key, "linked": True, "commented": True}


def build_split_summary_adf(server, children, state, total, config):
    """Build ADF for the split summary comment with inlineCard smart links."""
    comment_marker = config["comment_marker"]
    entity_plural = config["entity_name_plural"]

    list_items = []
    for child_id, title, _, _ in children:
        child_key = state.phase2_done[child_id]["key"]
        url = f"{server.rstrip('/')}/browse/{child_key}"
        list_items.append(
            {
                "type": "listItem",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "inlineCard", "attrs": {"url": url}},
                            {"type": "text", "text": f": {title}"},
                        ],
                    }
                ],
            }
        )
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": comment_marker, "marks": [{"type": "em"}]},
                    {
                        "type": "text",
                        "text": (
                            f" This {config['entity_name']} has been split"
                            f" into {total} child {entity_plural}:"
                        ),
                    },
                ],
            },
            {"type": "bulletList", "content": list_items},
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Original content preserved in comments above."},
                ],
            },
        ],
    }


def phase3_close(server, user, token, parent_key, children, state, config, dry_run):
    """Close the parent ticket with the type's close-superseded transition and resolution
    (``identity.jira.state_map.close_superseded``: Closed / Obsolete for both shipped types)."""
    if state.parent_closed:
        print("  Phase 3: Parent already closed, skipping")
        return

    total = len(children)
    label_prefix = config["label_prefix"]
    close = _tracker(config)["close_superseded"]
    # The TARGET STATUS name (matched case-insensitively below) and the resolution set on it.
    target_status, resolution = close["transition"], close["resolution"]

    if len(state.phase2_done) < total:
        missing = [cid for (cid, _, _, _) in children if cid not in state.phase2_done]
        print(
            f"  ERROR: Cannot close parent — children {missing} not yet created.", file=sys.stderr
        )
        sys.exit(EXIT_PER_PARENT)

    if dry_run:
        print(f"  Phase 3: Would label {parent_key} with {label_prefix}-split-original")
        print(
            f"  Phase 3: Would transition {parent_key} to {target_status} "
            f"(resolution: {resolution})"
        )
        print("           Would post summary comment")
        return

    # Label the parent
    add_labels(server, user, token, parent_key, [f"{label_prefix}-split-original"])
    print(f"  Phase 3: Labeled {parent_key} with {label_prefix}-split-original")

    # Find the transition whose target is the close-superseded status
    transitions = get_transitions(server, user, token, parent_key)
    closed_transition = None
    for t in transitions:
        if t["to"].get("name", "").lower() == target_status.lower():
            closed_transition = t
            break

    if not closed_transition:
        available = [t["name"] for t in transitions]
        print(
            f"  WARNING: No '{target_status}' transition found. Available: {available}",
            file=sys.stderr,
        )
        print("  Skipping parent closure.", file=sys.stderr)
        return

    # Transition with resolution
    do_transition(
        server,
        user,
        token,
        parent_key,
        closed_transition["id"],
        fields={"resolution": {"name": resolution}},
    )
    print(f"  Phase 3: Transitioned {parent_key} to {target_status} ({resolution})")

    # Post summary comment with smart-linked child keys
    summary_adf = build_split_summary_adf(server, children, state, total, config)
    add_comment(server, user, token, parent_key, summary_adf)
    print("  Phase 3: Posted summary comment")


# ─── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("parent_key", help="Parent Jira issue key to split")
    parser.add_argument(
        "--type",
        choices=_TYPES.choices(),
        default="rfe",
        help="Entry type (default: rfe)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print planned actions without making API calls"
    )
    parser.add_argument(
        "--artifacts-dir", default="artifacts", help="Artifacts directory (default: artifacts)"
    )
    args = parser.parse_args()
    config = SPLIT_CONFIG[args.type]

    # A type without the split facts cannot be split: refuse before any scan or Jira call
    # rather than create children and then fail to link or close. Run-wide (every parent
    # of the type fails the same way), so the exit code aborts submit.py's split loop.
    missing = _missing_split_facts(config)
    if missing:
        print(
            f"Error: type '{args.type}' declares no {' / '.join(missing)}; it cannot be split.",
            file=sys.stderr,
        )
        sys.exit(EXIT_SYSTEMIC)

    server, user, token = require_env()

    if not args.dry_run and not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.", file=sys.stderr)
        print("Set these or use --dry-run for local-only validation.", file=sys.stderr)
        sys.exit(EXIT_SYSTEMIC)

    # Scan task files to find parent and children via frontmatter
    scan_fn = config["scan_fn"]
    id_field = config["id_field"]
    entity_name = config["entity_name"]

    tasks = scan_fn(args.artifacts_dir)
    if not tasks:
        print(
            f"Error: No {entity_name.lower()} files found. Run /{entity_name.lower()}.split first.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PER_PARENT)

    # Find parent: status=Archived with matching id
    parent_task = None
    for path, data in tasks:
        if data.get("status") == "Archived" and data.get(id_field) == args.parent_key:
            parent_task = (path, data)
            break

    if not parent_task:
        print(
            f"Error: No archived parent with {id_field}={args.parent_key} found.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PER_PARENT)

    # Find leaf children: walk the tree recursively to collect all
    # non-archived descendants.  Intermediary nodes (archived local IDs
    # like RFE-017) are stepping stones — their children belong to the
    # parent for Jira linking purposes.
    tasks_by_parent = {}
    for path, data in tasks:
        pk = data.get("parent_key")
        if pk:
            tasks_by_parent.setdefault(pk, []).append((path, data))

    def _collect_leaves(parent_id):
        leaves = []
        for path, data in tasks_by_parent.get(parent_id, []):
            if data.get("status") == "Archived":
                # Intermediary — recurse into its children
                leaves.extend(_collect_leaves(data[id_field]))
            else:
                leaves.append((path, data))
        return leaves

    child_tasks = _collect_leaves(args.parent_key)

    if not child_tasks:
        print(
            f"Error: No child {entity_name}s found with parent_key={args.parent_key}.",
            file=sys.stderr,
        )
        sys.exit(EXIT_PER_PARENT)

    if len(child_tasks) > MAX_LEAF_CHILDREN:
        print(
            f"Error: {args.parent_key} has {len(child_tasks)} leaf children "
            f"(max {MAX_LEAF_CHILDREN}). Refusing to submit — requires "
            f"human review.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Build children list: (id, title, priority, artifact_path)
    children = []
    for path, data in child_tasks:
        children.append(
            (
                data[id_field],
                data["title"],
                data["priority"],
                path,
            )
        )

    print(f"Split submission: {args.parent_key} -> {len(children)} children")
    for i, (child_id, title, priority, _) in enumerate(children, 1):
        print(f"  {i}. {child_id}: {title} (Priority: {priority})")
    print()

    # Check for Jira conflicts on the parent before starting
    if not args.dry_run:
        originals_dir = config["originals_dir"]
        original_path = os.path.join(args.artifacts_dir, originals_dir, f"{args.parent_key}.md")
        try:
            has_conflict, _ = check_description_conflict(
                server, user, token, args.parent_key, original_path
            )
            if has_conflict:
                print(
                    f"Error: {args.parent_key} description was modified "
                    f"in Jira since fetch. Refusing to split — requires "
                    f"human review.",
                    file=sys.stderr,
                )
                sys.exit(3)
        except SystemExit:
            raise
        except Exception as e:
            print(f"Warning: conflict check failed for {args.parent_key}: {e}", file=sys.stderr)

    # Everything from discovery on talks to Jira. An escaped exception is
    # classified for submit.py's loop policy: per-parent failures let the
    # other parents proceed; systemic ones (dead auth, unreachable
    # instance) abort the whole split phase without fanning out
    # (RHAIFIRST-571).
    try:
        # Discover state (skip for dry-run without credentials)
        if args.dry_run and not all([server, user, token]):
            print("Dry run (no Jira credentials — skipping recovery check)")
            print()
            state = SubmissionState()
            state.total_children = len(children)
        else:
            print("Checking submission state...")
            state = discover_state(server, user, token, args.parent_key, children, config)
            if state.phase1_done:
                print(
                    f"  Phase 1: {len(state.phase1_done)}/{len(children)} archival comments found"
                )
            if state.phase2_done:
                print(f"  Phase 2: {len(state.phase2_done)}/{len(children)} tickets created")
            if state.parent_closed:
                print("  Phase 3: Parent already closed")
            if not state.phase1_done and not state.phase2_done:
                print("  Fresh start — no prior progress found")
            print()

        # Run phases
        print(f"Phase 1: Persisting child {entity_name} content to parent comments...")
        phase1_persist(server, user, token, args.parent_key, children, state, config, args.dry_run)
        print()

        print("Phase 2: Creating tickets and linking...")
        phase2_create_link(
            server,
            user,
            token,
            args.parent_key,
            children,
            state,
            args.artifacts_dir,
            config,
            args.dry_run,
        )
        print()

        print("Phase 3: Closing parent...")
        phase3_close(server, user, token, args.parent_key, children, state, config, args.dry_run)
        print()

        # Post-submit: update frontmatter and rename files. Never under dry-run:
        # recovery can adopt children with REAL keys (not the -DRY sentinel), and
        # renaming local artifacts is a write the caller did not ask for.
        rename_fn = config["rename_fn"]
        project = config["project"]
        for child_id, title, priority, artifact_path in children:
            assigned = state.phase2_done.get(child_id)
            assigned_key = assigned["key"] if assigned else None
            if args.dry_run or not assigned_key or assigned_key == f"{project}-DRY":
                continue
            if assigned_key == child_id:
                # Already renamed by a run that died between the rename and the
                # index rebuild — nothing to do.
                continue

            rename_fn(args.artifacts_dir, child_id, assigned_key)
            print(f"  {child_id}: Renamed artifacts to {assigned_key}")

        # Rebuild index (RFE only)
        if config["do_rebuild_index"]:
            rebuild_index(args.artifacts_dir)
            print(f"Done. Index rebuilt at {args.artifacts_dir}/rfes.md")
        else:
            print("Done.")
    except SystemExit:
        raise
    except Exception as e:
        code = _classify_exit(e)
        kind = "systemic Jira failure" if code == EXIT_SYSTEMIC else "per-parent failure"
        print(f"Error: {kind}: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(code)


if __name__ == "__main__":
    main()
