#!/usr/bin/env python3
"""Fetch a Jira issue and print its fields as JSON.

Lightweight read utility for skills that need to fetch issues when the
Atlassian MCP server is unavailable. Outputs JSON to stdout for the
calling skill to parse.

Usage:
    python3 scripts/fetch_issue.py RHAIRFE-1234 \\
        [--fields summary,description,comment,priority,labels,status] \\
        [--markdown]

    # Fetch everything and write all artifact files at once
    python3 scripts/fetch_issue.py RHAIRFE-1234 --fetch-all artifacts

Environment variables:
    JIRA_SERVER  Jira server URL (e.g. https://mysite.atlassian.net)
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token

Exit codes:
    0  Success
    1  API/network/script error, or (--fetch-all) a fetched issue whose
       (project, issue type) is not the resolved type's binding — nothing
       is written and stderr names the type to re-run with, if any — or a
       RFE_CREATOR_BINDING_* override that binds the resolved type to
       another registered type's pair, or the bare JIRA_PROJECT /
       JIRA_ISSUE_TYPE shorthand, which the artifact layer does not honour
       (both refused before the fetch)
    2  Missing JIRA credentials (caller should try MCP fallback)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import type_registry
from jira_utils import adf_to_markdown, get_comments, get_issue, require_env

_TYPES = type_registry.load()


def _desc_to_markdown(desc_raw):
    """Convert a raw description field (ADF dict or string) to markdown."""
    if isinstance(desc_raw, dict):
        return adf_to_markdown(desc_raw).strip()
    elif desc_raw is not None:
        return str(desc_raw).strip()
    return ""


def _format_comment_date(iso_date):
    """Format an ISO timestamp to a human-readable date string."""
    # Jira dates look like "2025-01-15T10:30:00.000+0000"
    if not iso_date:
        return "Unknown date"
    return iso_date[:10]


# The --fetch-all request list: the pre-registry fields, then the two witnesses of the
# post-fetch verification below — issuetype (PR-1) and project (PR-3c, D9: requested
# explicitly, never inferred from the key stem). Request-only widening: nothing written by
# _fetch_all reads either, so the artifact bytes do not depend on them.
FETCH_ALL_FIELDS = [
    "summary",
    "description",
    "priority",
    "labels",
    "status",
    "issuetype",
    "project",
]


def _fetched_pair(fields):
    """``(project key, issue type name, missing)`` from a fetched issue's ``fields``: the
    ``project.key`` / ``issuetype.name`` witnesses and the names (``project`` / ``issuetype``)
    of the ones the response does not carry (absent, null, or without a key / name)."""
    project = fields.get("project")
    issuetype = fields.get("issuetype")
    project_key = project.get("key") if isinstance(project, dict) else None
    type_name = issuetype.get("name") if isinstance(issuetype, dict) else None
    missing = [
        name for name, value in (("project", project_key), ("issuetype", type_name)) if not value
    ]
    return project_key, type_name, missing


def verify_binding(issue_key, fields, type_name, binding, env=None):
    """Post-fetch verification (design §5 self-describing artifacts, PR-3c D9).

    The fetched ``(project.key, issuetype.name)`` pair must be one of the pairs the resolved
    type accepts for ``issue_key`` (``Descriptor.accepted_pairs``): its EFFECTIVE binding
    ``(project, issue_type)`` — ``binding`` is what ``type_registry.resolve`` returned, so a
    ``RFE_CREATOR_BINDING_<TYPE>_*`` override is honoured — plus, for a key carrying one of the
    type's descriptor prefixes, the descriptor pair (an item created before the override is
    still the type's own). Returns ``None`` on a match and otherwise the one-line refusal to
    print: it names the key, the fetched pair, the accepted pair(s) and the resolved type, and —
    when exactly one OTHER registered type's effective binding owns the fetched pair — the
    ``re-run with --type <t>`` hint (best-effort: a type whose ``RFE_CREATOR_BINDING_*``
    variables fail the grammar cannot be offered and is skipped; ``main`` refuses such an
    environment before the fetch, a direct caller merely loses the hint). The resolved type
    itself is never offered: its descriptor pair is already accepted for a key that carries a
    descriptor prefix, and for any other key re-running with the same type would fail the same
    way. A response without a ``project`` or ``issuetype`` witness cannot be verified and is
    refused the same way (fail closed), naming the missing field.
    """
    if env is None:
        env = os.environ
    accepted = _TYPES.get(type_name).accepted_pairs(binding, issue_key)
    expected_text = type_registry.render_pairs(accepted)
    project_key, issue_type, missing = _fetched_pair(fields)
    if missing:
        return (
            f"Error: cannot verify {issue_key} against the resolved type {type_name} binding "
            f"{expected_text}: the fetched issue has no {' or '.join(missing)} field; "
            f"nothing written"
        )
    if (project_key, issue_type) in accepted:
        return None
    message = (
        f"Error: {issue_key} is ({project_key}, {issue_type}) in Jira but the resolved type "
        f"{type_name} binds {expected_text}; nothing written"
    )
    owners = []
    for name in _TYPES.names():
        if name == type_name:
            continue
        try:
            other = _TYPES.get(name).binding(env)
        except type_registry.RegistryError:
            # A malformed RFE_CREATOR_BINDING_<OTHER>_* variable: that type cannot be offered
            # as the hint, and its error is not this run's to raise (the resolved type's own
            # variables were validated by resolve) — the refusal stands without the hint.
            continue
        if (other.get("project"), other.get("issue_type")) == (project_key, issue_type):
            owners.append(name)
    if len(owners) == 1:
        message += f" - re-run with --type {owners[0]}"
    return message


def _fetch_all(issue_key, artifacts_dir, server, user, token, type_name="rfe", binding=None):
    """Fetch issue and write all artifact files for one work-item type.

    The artifact layout is the ``type_name`` descriptor's (design
    work-item-types-unified.md §10 item 2): the task and original directories
    are ``dirs.tasks`` / ``dirs.originals``, the frontmatter id field is
    ``identity.id_field`` and the ``<KEY>-comments.md`` companion is written
    only when ``companions.comments`` is true. Returns 0 on success, 1 on error.

    Before anything is written the fetched issue is verified against ``binding`` — the
    type's effective ``(project, issue_type)`` as ``type_registry.resolve`` returned it to
    ``main``; a direct caller that passes none gets the type's own effective binding — and a
    mismatch (or a response that cannot be verified) writes no task, original or comments
    file: one line on stderr, return 1 (``verify_binding``). The task file is written first and
    its frontmatter set by ``scripts/frontmatter.py``; when that step fails the task file is
    removed again before returning 1, so a failed fetch never leaves a body-only task file for
    the fetch barrier to accept (the original and the companion are not yet written then).
    """
    desc = _TYPES.get(type_name)
    if binding is None:
        # The typed RFE_CREATOR_BINDING_* overlay only: the bare JIRA_PROJECT / JIRA_ISSUE_TYPE
        # shorthand is not honoured by the artifact layer this writes into (main refuses it).
        binding = desc.binding(os.environ)
    dirs = desc.dirs(form="bare")
    tasks_dir = os.path.join(artifacts_dir, dirs["tasks"])
    originals_dir = os.path.join(artifacts_dir, dirs["originals"])

    # Fetch issue fields
    try:
        issue = get_issue(server, user, token, issue_key, fields=list(FETCH_ALL_FIELDS))
    except Exception as e:
        print(f"Error fetching issue {issue_key}: {e}", file=sys.stderr)
        return 1

    fields = issue.get("fields", {})
    refusal = verify_binding(issue_key, fields, type_name, binding)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 1

    os.makedirs(tasks_dir, exist_ok=True)
    os.makedirs(originals_dir, exist_ok=True)
    desc_md = _desc_to_markdown(fields.get("description"))

    # Extract field values. The "Major" fallback below and status=Ready in the
    # frontmatter are shared pipeline conventions, not type facts: every type's
    # task schema carries the same priority vocabulary and status enum
    # (artifact_utils._STATUS_ENUM), so they stay literal here rather than
    # being read from the descriptor.
    summary = fields.get("summary", "")
    priority_obj = fields.get("priority")
    priority = priority_obj.get("name", "Major") if isinstance(priority_obj, dict) else "Major"
    labels = fields.get("labels", [])
    labels_str = ",".join(labels) if labels else "null"

    # Write task file (description body)
    task_path = os.path.join(tasks_dir, f"{issue_key}.md")
    with open(task_path, "w", encoding="utf-8") as f:
        f.write(desc_md + "\n")

    # Set frontmatter via frontmatter.py. The two self-describing fields (design §5,
    # PR-3c) come last: `type` names the work-item type the layout belongs to and
    # `tracker_ref` the issue this artifact was fetched from — appended after the
    # pre-migration fields, never reordered (D7).
    fm_args = [
        sys.executable,
        "scripts/frontmatter.py",
        "set",
        task_path,
        f"{desc.id_field}={issue_key}",
        f"title={summary}",
        f"priority={priority}",
        "status=Ready",
        f"original_labels={labels_str}",
        f"type={desc.name}",
        f"tracker_ref={issue_key}",
    ]
    result = subprocess.run(fm_args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error setting frontmatter: {result.stderr.strip()}", file=sys.stderr)
        # The body-only task file must not survive: the fetch barrier accepts a task file that
        # merely exists, so leaving it would pass a frontmatter-less artifact downstream instead
        # of the fetch_failed stub. Nothing else is on disk yet (the original and the comments
        # companion are written only after this point), so removing it restores "nothing
        # written".
        os.remove(task_path)
        return 1

    # Write original description (deterministic baseline for conflict
    # detection)
    orig_path = os.path.join(originals_dir, f"{issue_key}.md")
    with open(orig_path, "w", encoding="utf-8") as f:
        f.write(desc_md + "\n")

    written = [task_path, orig_path]

    # Fetch and write comments — only for types whose fetch step produces the
    # companion (companions.comments); no comment request is made otherwise.
    if desc.get("companions.comments"):
        try:
            comments = get_comments(server, user, token, issue_key)
        except Exception as e:
            print(f"Error fetching comments for {issue_key}: {e}", file=sys.stderr)
            return 1

        comments_path = os.path.join(tasks_dir, f"{issue_key}-comments.md")
        with open(comments_path, "w", encoding="utf-8") as f:
            f.write(f"# Comments: {issue_key}\n\n")
            if not comments:
                f.write("No comments found.\n")
            else:
                for c in comments:
                    author = c.get("author", {}).get("displayName", "Unknown")
                    date = _format_comment_date(c.get("created", ""))
                    body = c.get("body", {})
                    if isinstance(body, dict):
                        body = adf_to_markdown(body).strip()
                    elif body is not None:
                        body = str(body).strip()
                    else:
                        body = ""
                    f.write(f"## {author} — {date}\n\n{body}\n\n")
        written.append(comments_path)

    print(f"OK: wrote {', '.join(written)}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("issue_key", help="Jira issue key (e.g. RHAIRFE-1234)")

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--fields",
        default=None,
        help="Comma-separated list of fields to fetch "
        "(default: summary,description,priority,"
        "labels,status,issuetype). "
        "Use 'comment' to also fetch comments.",
    )
    mode_group.add_argument(
        "--fetch-all",
        metavar="ARTIFACTS_DIR",
        help="Fetch issue and write all artifact files "
        "(rfe-tasks, rfe-originals, comments) to "
        "the given directory.",
    )
    # default=None: an absent flag reaches type_registry.resolve as "no signal" and lands on the
    # grandfathered legacy default rung (rfe) silently; an explicit --type is rung 1 and prints
    # the D3 line. The rendered default is unchanged.
    parser.add_argument(
        "--type",
        choices=_TYPES.choices(),
        default=None,
        help="Work-item type whose artifact layout --fetch-all "
        "writes: task and original directories, frontmatter "
        "id field and comments companion come from "
        "types/<type>/type.yaml (default: rfe). Not used by "
        "the other modes.",
    )

    parser.add_argument(
        "--markdown",
        action="store_true",
        help="Convert ADF fields (description, comments) to markdown strings in the output",
    )
    parser.add_argument(
        "--write-original",
        metavar="DIR",
        help="Write the description as markdown to "
        "DIR/<issue_key>.md. If JIRA creds are "
        "available, refetches via REST API and uses "
        "adf_to_markdown for deterministic output. "
        "If not, copies DIR/<issue_key>.input.md "
        "as a fallback.",
    )
    args = parser.parse_args()

    server, user, token = require_env()

    # --fetch-all mode: script does everything
    if args.fetch_all:
        if not all([server, user, token]):
            print(
                "Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars "
                "required for --fetch-all mode.",
                file=sys.stderr,
            )
            sys.exit(2)
        # Design §5 ladder: --type (rung 1) else the legacy default (rfe). The key is NOT an
        # id signal here (D9: the project witness comes from the fetched issue, never from
        # the key stem), so the result is never ambiguous. The binding on the resolution is
        # the effective one (§3.2.1) — what the fetched issue is verified against.
        try:
            resolution = type_registry.resolve(_TYPES, explicit_type=args.type, env=os.environ)
            # D3: the resolve line only when a non-default rung decided, and never on stdout.
            if resolution.rung != type_registry.LEGACY_DEFAULT_RUNG:
                print(resolution.line(), file=sys.stderr)
            # §3.2.1 g (runtime twin of gate-1 rule 1): the effective binding must be the
            # resolved type's OWN. An override that binds it to another registered type's
            # pair is refused here, before the fetch — otherwise the post-fetch check would
            # pass an issue of that other type into this type's layout. No override: silent.
            type_registry.assert_registered_binding(
                resolution.desc, env=os.environ, registry=_TYPES, shorthand=True
            )
            # The bare JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is a resolve-CLI verdict only:
            # the artifact layer this writes into reads binding() without it. Refused before
            # the fetch.
            type_registry.assert_not_shorthand(resolution.type_name, resolution.binding)
        except type_registry.RegistryError as exc:
            print(f"Error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
            sys.exit(1)
        rc = _fetch_all(
            args.issue_key,
            args.fetch_all,
            server,
            user,
            token,
            resolution.type_name,
            resolution.binding,
        )
        sys.exit(rc)

    # --write-original-only mode: no --fields means caller just wants
    # the original description snapshot written to disk.
    if args.write_original and not args.fields:
        os.makedirs(args.write_original, exist_ok=True)
        orig_path = os.path.join(args.write_original, f"{args.issue_key}.md")
        base, ext = os.path.splitext(orig_path)
        input_path = base + ".input" + ext
        if all([server, user, token]):
            issue = get_issue(server, user, token, args.issue_key, fields=["description"])
            desc_md = _desc_to_markdown(issue.get("fields", {}).get("description"))
            with open(orig_path, "w", encoding="utf-8") as f:
                f.write(desc_md + "\n")
            if os.path.exists(input_path):
                os.remove(input_path)
        elif os.path.exists(input_path):
            shutil.copy2(input_path, orig_path)
            os.remove(input_path)
        else:
            print(
                f"Warning: no JIRA creds and no {input_path}, skipping --write-original",
                file=sys.stderr,
            )
        return

    # Default fields when not in write-original-only mode
    if not args.fields:
        args.fields = "summary,description,priority,labels,status,issuetype"

    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.", file=sys.stderr)
        sys.exit(1)

    requested = [f.strip() for f in args.fields.split(",")]
    fetch_comments = "comment" in requested
    api_fields = [f for f in requested if f != "comment"]

    # Fetch the issue
    issue = get_issue(
        server, user, token, args.issue_key, fields=api_fields if api_fields else None
    )

    # Build output
    fields = issue.get("fields", {})
    output = {
        "key": issue.get("key"),
        "fields": {},
    }

    for field_name in api_fields:
        value = fields.get(field_name)
        # Convert ADF description to markdown if requested
        if args.markdown and field_name == "description" and isinstance(value, dict):
            value = adf_to_markdown(value).strip()
        output["fields"][field_name] = value

    # Fetch comments separately if requested
    if fetch_comments:
        comments = get_comments(server, user, token, args.issue_key)
        output["comments"] = []
        for c in comments:
            body = c.get("body", {})
            if args.markdown and isinstance(body, dict):
                body = adf_to_markdown(body).strip()
            output["comments"].append(
                {
                    "author": c.get("author", {}).get("displayName", "Unknown"),
                    "created": c.get("created", ""),
                    "body": body,
                }
            )

    # Write original description snapshot for conflict detection
    if args.write_original:
        desc_md = _desc_to_markdown(fields.get("description"))
        os.makedirs(args.write_original, exist_ok=True)
        orig_path = os.path.join(args.write_original, f"{args.issue_key}.md")
        with open(orig_path, "w", encoding="utf-8") as f:
            f.write(desc_md + "\n")

    json.dump(output, sys.stdout, indent=2)
    print()  # trailing newline


if __name__ == "__main__":
    main()
