#!/usr/bin/env python3
"""Check for concurrent Jira modifications before submitting.

Compares the current Jira description against the original snapshot saved
at fetch time. If they differ, someone modified the issue in Jira since we
last fetched it, and submitting would overwrite their changes.

The same fetch verifies that each issue still is what the resolved type
binds: its ``(project, issuetype)`` must be the type's effective
``(project, issue_type)`` or — for a key carrying one of the type's
descriptor prefixes, an item created before a deployment override — the
descriptor pair (design work-item-types-unified.md §3.2.1, §5). A mismatch
is reported like a conflict — the item must not be updated as this type —
and never as a traceback. The remote key of a task is its ``tracker_ref``,
else its id.

Usage:
    python3 scripts/check_conflicts.py [--type rfe|initiative] [--artifacts-dir DIR]

Exit codes:
    0  No conflicts — safe to submit
    1  Conflicts detected — submission should be blocked
    2  Error (missing env vars, API failure, a type whose tracker binding is
       malformed, not its own or sourced from the JIRA_PROJECT / JIRA_ISSUE_TYPE
       shorthand, a task whose tracker_ref another type owns)

Output:
    CONFLICT_COUNT=N
    For each conflict:
      CONFLICT: <id> — modified in Jira since last fetch
      CONFLICT: <id> — is (<project>, <issue type>) in Jira but the resolved
                       type <type> binds (<project>, <issue type>)
    If no conflicts:
      OK: no conflicts detected

Environment variables:
    JIRA_SERVER  Jira server URL
    JIRA_USER    Jira username/email
    JIRA_TOKEN   Jira API token
"""

import argparse
import os
import sys

import type_registry
from artifact_utils import scan_tasks
from jira_utils import check_description_conflict, require_env

_TYPES = type_registry.load()

# Derived from the type registry (design work-item-types-unified.md §10 item 2): the bare dir
# form (Q13) and identity.id_field. The task tree itself is read by artifact_utils.scan_tasks
# over the same descriptor, so every registered type — shipped or drop-in — is scanned from
# its own dirs.tasks. Which of its tasks are EXISTING issues is decided per run, against the
# resolved type's effective binding (``_is_existing``), not by a table built at import.
_TYPE_CONFIG = {
    name: {
        "originals_dir": _TYPES.get(name).dirs(form="bare")["originals"],
        "id_field": _TYPES.get(name).id_field,
    }
    for name in _TYPES.names()
}


class ForeignTrackerRefError(ValueError):
    """A task's ``tracker_ref`` names a tracker key the resolved type does not own."""


def _owned(key, key_prefixes):
    """``key`` starts with one of the effective ``key_prefixes`` (the resolved type owns it)."""
    return isinstance(key, str) and key.startswith(tuple(p for p in key_prefixes if p))


def _is_existing(task_data, item_id, type_name, key_prefixes):
    """Is this task an existing tracker issue of the resolved type? (design §5, PR-3c)

    ``tracker_ref`` in the task frontmatter is the canonical remote reference and decides
    when present: owned by the resolved type — a prefix match on the EFFECTIVE
    ``key_prefixes`` union (the overridden write prefix first, the descriptor prefixes kept
    as read prefixes) — means existing; owned by anything else is a ``ForeignTrackerRefError``,
    never an update and never a create. A pre-migration artifact without the field falls
    back to the id itself: existing when the id carries one of the same prefixes.
    """
    ref = task_data.get("tracker_ref")
    if isinstance(ref, str) and ref:
        if not _owned(ref, key_prefixes):
            raise ForeignTrackerRefError(
                f"{item_id}: tracker_ref {ref!r} is not owned by the resolved type "
                f"{type_name} (key prefixes {', '.join(p for p in key_prefixes if p)}); "
                f"a task of another type is never updated as this one — fix the artifact "
                f"or pass the type that owns it"
            )
        return True
    return _owned(item_id, key_prefixes)


def _binding_mismatch(fields, type_name, binding, key=None):
    """The conflict reason when the fetched issue is not the resolved type's, else None.

    The fetched ``(project.key, issuetype.name)`` must be one of the pairs the type accepts
    for ``key`` — ``Descriptor.accepted_pairs``: the effective ``(project, issue_type)``, plus
    the descriptor pair when the key carries a descriptor read prefix (an item created before
    the override, still this type's to update); a response without either witness cannot be
    verified and is refused the same way (fail closed).
    """
    accepted = _TYPES.get(type_name).accepted_pairs(binding, key)
    expected_text = type_registry.render_pairs(accepted)
    fields = fields if isinstance(fields, dict) else {}
    project = fields.get("project")
    issuetype = fields.get("issuetype")
    project_key = project.get("key") if isinstance(project, dict) else None
    issue_type = issuetype.get("name") if isinstance(issuetype, dict) else None
    missing = [
        name for name, value in (("project", project_key), ("issuetype", issue_type)) if not value
    ]
    if missing:
        return (
            f"cannot verify against the resolved type {type_name} binding {expected_text}: "
            f"the fetched issue has no {' or '.join(missing)} field"
        )
    if (project_key, issue_type) in accepted:
        return None
    return (
        f"is ({project_key}, {issue_type}) in Jira but the resolved type {type_name} binds "
        f"{expected_text}"
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--artifacts-dir", default="artifacts", help="Artifacts directory (default: artifacts)"
    )
    # default=None: an absent flag reaches type_registry.resolve as "no signal" and lands on
    # the grandfathered legacy default rung (rfe) silently; an explicit --type is rung 1 and
    # prints the D3 line.
    parser.add_argument("--type", choices=_TYPES.choices(), default=None)
    args = parser.parse_args()

    # Design §5 ladder: --type (rung 1) else the legacy default (rfe); no id is a signal here.
    # The binding on the resolution is the EFFECTIVE one (§3.2.1) — what each fetched issue
    # is verified against and what decides which tasks are existing issues.
    try:
        resolution = type_registry.resolve(_TYPES, explicit_type=args.type, env=os.environ)
        # D3: the resolve line only when a non-default rung decided, and never on stdout
        # (stdout is the CONFLICT_COUNT protocol).
        if resolution.rung != type_registry.LEGACY_DEFAULT_RUNG:
            print(resolution.line(), file=sys.stderr)
        # §3.2.1 g (runtime twin of gate-1 rule 1): the effective binding must be the
        # resolved type's OWN; an override that selects another registered type's pair is
        # refused before any access. No override: silent.
        type_registry.assert_registered_binding(
            resolution.desc, env=os.environ, registry=_TYPES, shorthand=True
        )
        # The bare JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is a resolve-CLI verdict only: the
        # writers' artifact layer reads binding() without it, so a run under it would verify
        # against a pair the layer never minted. Refused before any access.
        type_registry.assert_not_shorthand(resolution.type_name, resolution.binding)
    except type_registry.RegistryError as exc:
        print(f"Error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        sys.exit(2)
    type_name = resolution.type_name
    binding = resolution.binding
    key_prefixes = list(binding.get("key_prefixes") or [])
    tc = _TYPE_CONFIG[type_name]

    server, user, token = require_env()
    if not all([server, user, token]):
        print("Error: JIRA_SERVER, JIRA_USER, and JIRA_TOKEN env vars required.", file=sys.stderr)
        sys.exit(2)

    originals_dir = os.path.join(args.artifacts_dir, tc["originals_dir"])

    tasks = scan_tasks(args.artifacts_dir, resolution.desc)
    jira_items = []
    for task_path, task_data in tasks:
        item_id = task_data[tc["id_field"]]
        try:
            if not _is_existing(task_data, item_id, type_name, key_prefixes):
                continue
        except ForeignTrackerRefError as exc:
            print(f"Error: {task_path}: {exc}", file=sys.stderr)
            sys.exit(2)
        if task_data.get("status") == "Archived":
            continue
        # The canonical remote reference when the artifact carries it (the id itself for a
        # pre-migration artifact — identical whenever both exist).
        issue_key = task_data.get("tracker_ref") or item_id
        original_path = os.path.join(originals_dir, f"{issue_key}.md")
        if os.path.exists(original_path):
            jira_items.append((item_id, issue_key, original_path))

    if not jira_items:
        print("CONFLICT_COUNT=0")
        print("OK: no Jira-sourced items to check")
        sys.exit(0)

    conflicts = []
    for item_id, issue_key, original_path in jira_items:
        try:
            has_conflict, fields = check_description_conflict(
                server, user, token, issue_key, original_path, extra_fields=["project", "issuetype"]
            )
        except Exception as e:
            print(f"Warning: could not fetch {item_id}: {e}", file=sys.stderr)
            continue
        # The binding verdict first: an issue that is not this type's is not this run's to
        # update whatever its description says.
        mismatch = _binding_mismatch(fields, type_name, binding, issue_key)
        if mismatch:
            conflicts.append((item_id, mismatch))
        elif has_conflict:
            conflicts.append((item_id, "modified in Jira since last fetch"))

    print(f"CONFLICT_COUNT={len(conflicts)}")
    if conflicts:
        for item_id, reason in conflicts:
            print(f"CONFLICT: {item_id} — {reason}")
        sys.exit(1)
    else:
        print("OK: no conflicts detected")
        sys.exit(0)


if __name__ == "__main__":
    main()
