#!/usr/bin/env python3
"""Validate a batch YAML input file before /rfe-speedrun (any registered type) processes it.

Catches malformed entries (missing prompt, bad priority, wrong types,
duplicate prompts, unknown fields) before the expensive multi-agent
create/auto-fix/submit pipeline runs.

Usage:
    python3 scripts/validate_batch_input.py <path> [--strict] [--type <type>]

Batch root forms (design work-item-types-unified.md §5 rung 2; PR-3b):
    - a bare list of entries (legacy form) — the type is --type, else the
      grandfathered default rfe;
    - a mapping with exactly the keys ``type`` and ``items`` (mapping form) —
      the type is ``type:``; --type may be omitted and, when given, must agree
      with it (PR-3 D1).
    An entry carrying its own ``type`` key is an error in either form: a run is
    single-typed, so a mixed batch must be split by type (PR-3 D2).

The file is read ONCE through ``type_registry.read_batch`` (the one parser —
a pipe or /dev/stdin works) and the type is decided by ``type_registry.resolve``
(the one ladder) over the parsed items: an item that is not a mapping is this
validator's ``entry N: must be a mapping`` error, never an id signal
(``items_are_ids=False``), and only the type verdict is taken
(``binding=False``) — the environment's JIRA_PROJECT / JIRA_ISSUE_TYPE
shorthand is never read and no RFE_CREATOR_BINDING_* value is validated here.
The per-type rules come from the resolved type's descriptor
(types/<type>/type.yaml): the priority vocabulary is schema.task.priority.enum;
the known fields are the shared base plus batch.extra_fields; ``parent_key`` is
checked against conventions.parent_key_patterns (the same join as the task
schema — Descriptor.parent_key_pattern_effective, so under a
RFE_CREATOR_BINDING_<TYPE>_PROJECT override the effective write prefix is an
alternative too, and a malformed value falls back to the descriptor join) only
when the type declares the field — for any other type it is an unknown field (a
warning), never a pattern error. Two deliberate differences
from the pre-PR-3b validator, both on malformed input: an rfe entry with a
malformed ``parent_key`` was a pattern error and is now the unknown-field
warning only (--strict still blocks it), and an initiative batch newly accepts
local ``INIT-`` parents (the task schema always did).

Exit codes:
    0  Valid (no errors; warnings allowed unless --strict)
    1  Invalid — errors found, or warnings found with --strict. A type error of
       the batch itself (unknown mapping type, --type disagreeing with type:, a
       per-item type key, an ambiguous type) is one ``ERROR: batch: ...`` line
    2  Usage/file error (file missing or unreadable, not valid YAML, root
       neither a list nor a {type, items} mapping)

Output (stdout is a machine-read protocol — nothing else is printed there):
    ERROR_COUNT=N
    WARNING_COUNT=N
    ERROR: entry <i>: <message>       (one per error)
    ERROR: batch: <message>           (a type error of the batch — the only error)
    WARNING: entry <i>: <message>     (one per warning)
    VALID=true|false

Stderr: ``TYPE RESOLVED: <type> (--type)`` or ``(batch type)`` when the type
came from a non-default rung (PR-3 D3) — exactly one line, without a binding
clause (the binding is not evaluated here). The legacy default — a bare list
with no --type — prints nothing anywhere beyond the protocol, so that
invocation is byte-identical to main on stdout and stderr whatever the
environment; an explicit --type keeps main's stdout and adds that one stderr
line. Usage errors print ``ERROR: ...`` on stderr.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry

_TYPES = type_registry.load()
LEGACY_DEFAULT_TYPE = type_registry.LEGACY_DEFAULT_TYPE

# Keep in sync with the batch YAML format documented in
# .claude/skills/rfe-speedrun/SKILL.md (Mode A). Since the skill runs this
# validator with --strict, a field missing here becomes a blocking warning
# for anyone who adds a new batch field without updating both places.
# The base set is shared by every type; each type adds its descriptor's batch.extra_fields.
BASE_KNOWN_FIELDS = frozenset({"prompt", "priority", "labels", "clarifying_context"})


def _priority_enum(desc):
    """The type's schema.task.priority.enum; a drop-in without a task schema keeps the
    grandfathered vocabulary (the legacy default type's enum)."""
    enum = desc.get("schema.task.priority.enum", None)
    if enum is None and LEGACY_DEFAULT_TYPE in _TYPES:
        enum = _TYPES.get(LEGACY_DEFAULT_TYPE).get("schema.task.priority.enum", None)
    return list(enum or [])


# Per-type vocabularies, keyed by type name in registry order (rfe first).
KNOWN_FIELDS = {
    name: set(BASE_KNOWN_FIELDS) | set(_TYPES.get(name).get("batch.extra_fields", None) or [])
    for name in _TYPES.names()
}
ALLOWED_PRIORITIES = {name: _priority_enum(_TYPES.get(name)) for name in _TYPES.names()}
# conventions.parent_key_patterns (unanchored alternatives, rendered in the error text) and the
# anchored alternation Descriptor.parent_key_pattern_effective joins from them — the same string
# the <type>-task schema carries (artifact_utils), so batch and task agree by construction (Q14).
# Under a project override (RFE_CREATOR_BINDING_<TYPE>_PROJECT) the effective write prefix joins
# first, so an entry whose parent was fetched under the override validates; with no override the
# join is Descriptor.parent_key_pattern byte for byte, and so it is under a malformed override
# (the writers report the value; this validator takes the type verdict only).


def _parent_key_pattern(desc):
    try:
        return desc.parent_key_pattern_effective()
    except type_registry.RegistryError:
        return desc.parent_key_pattern


PARENT_KEY_PATTERNS = {
    name: list(_TYPES.get(name).get("conventions.parent_key_patterns", None) or [])
    for name in _TYPES.names()
}
PARENT_KEY_PATTERN = {name: _parent_key_pattern(_TYPES.get(name)) for name in _TYPES.names()}
_PARENT_KEY_RE = {
    name: re.compile(pattern) if pattern else None for name, pattern in PARENT_KEY_PATTERN.items()
}


def _rules_type(entry_type):
    """The registered type whose vocabulary applies; anything else falls back to the legacy
    default (``validate_entries`` is also called as a library function)."""
    return entry_type if entry_type in KNOWN_FIELDS else LEGACY_DEFAULT_TYPE


def validate_entries(entries, entry_type="rfe"):
    """Validate a list of batch entries. Returns (errors, warnings) lists of strings."""
    if not entries:
        return ["batch input must contain at least one entry"], []

    rules = _rules_type(entry_type)
    known_fields = KNOWN_FIELDS[rules]
    priorities = ALLOWED_PRIORITIES[rules]
    parent_alternatives = PARENT_KEY_PATTERNS[rules]
    parent_re = _PARENT_KEY_RE[rules]
    errors = []
    warnings = []
    seen_prompts = {}

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry {i}: must be a mapping, got {type(entry).__name__}")
            continue

        prompt = entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"entry {i}: 'prompt' is required and must be a non-empty string")
        else:
            key = prompt.strip().lower()
            seen_prompts.setdefault(key, []).append(i)

        if "priority" in entry and entry["priority"] not in priorities:
            errors.append(f"entry {i}: 'priority' {entry['priority']!r} is not one of {priorities}")

        if "labels" in entry:
            labels = entry["labels"]
            if not isinstance(labels, list):
                errors.append(f"entry {i}: 'labels' must be a list")
            elif any(not isinstance(label, str) or not label.strip() for label in labels):
                errors.append(f"entry {i}: 'labels' entries must be non-empty strings")

        if "clarifying_context" in entry and not isinstance(entry["clarifying_context"], str):
            errors.append(f"entry {i}: 'clarifying_context' must be a string")

        # The pattern rule applies only to a type that declares parent_key as a batch field;
        # elsewhere the key is simply unknown (warning below), never a pattern error.
        if "parent_key" in entry and "parent_key" in known_fields:
            pk = entry["parent_key"]
            if not isinstance(pk, str) or (parent_re is not None and not parent_re.match(pk)):
                if parent_alternatives:
                    errors.append(
                        f"entry {i}: 'parent_key' must match one of "
                        f"{', '.join(parent_alternatives)}"
                    )
                else:
                    errors.append(f"entry {i}: 'parent_key' must be a string")

        unknown = set(entry) - known_fields
        for field in sorted(unknown):
            warnings.append(f"entry {i}: unknown field '{field}'")

    for key, indices in seen_prompts.items():
        if len(indices) > 1:
            warnings.append(f"entries {indices}: duplicate prompt {key!r}")

    return errors, warnings


def _report(errors, warnings, strict):
    """Print the stdout protocol and exit (0 valid, 1 invalid)."""
    print(f"ERROR_COUNT={len(errors)}")
    print(f"WARNING_COUNT={len(warnings)}")
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARNING: {message}")

    valid = not errors and not (strict and warnings)
    print(f"VALID={'true' if valid else 'false'}")
    sys.exit(0 if valid else 1)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="Path to the batch YAML input file")
    parser.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors (nonzero exit)"
    )
    # default=None (not "rfe") so the ladder can tell an explicit --type (rung 1) from a
    # mapping-form type: (rung 2); absent both, resolve() returns the legacy default silently.
    parser.add_argument(
        "--type",
        choices=_TYPES.choices(),
        default=None,
        help="Entry type to validate (default: rfe)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"ERROR: file not found: {args.path}", file=sys.stderr)
        sys.exit(2)

    # The file's own shape (unreadable, invalid YAML, a root that is neither form) is a usage
    # error; the ladder's verdicts on its content (type) go through the protocol below.
    try:
        _batch_type, entries = type_registry.read_batch(args.path)
    except type_registry.ResolveError as exc:
        print(f"ERROR: {exc.args[0]}", file=sys.stderr)
        sys.exit(2)

    # The parsed items go in as batch_items (the file is not re-read: a pipe cannot be), a bare
    # string item is validate_entries' error rather than an id signal (items_are_ids=False),
    # and only the type verdict is wanted (binding=False: no override is read or validated).
    try:
        resolution = type_registry.resolve(
            _TYPES,
            explicit_type=args.type,
            batch=args.path,
            batch_items=(_batch_type, entries),
            items_are_ids=False,
            env=os.environ,
            binding=False,
        )
    except type_registry.ResolveError as exc:
        _report([f"batch: {exc.args[0]}"], [], args.strict)
    except (type_registry.RegistryError, KeyError) as exc:
        # Not a verdict on the batch: an invalid binding override in the environment or a
        # drop-in descriptor the ladder cannot bind — a configuration (usage) error.
        print(f"ERROR: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        sys.exit(2)
    if resolution.ambiguous:
        candidates = ", ".join(resolution.candidates)
        _report(
            [f"batch: ambiguous type (candidates: {candidates}) - pass --type"], [], args.strict
        )
    # D3: the resolve line only when a non-default rung decided, and never on stdout.
    if resolution.rung != type_registry.LEGACY_DEFAULT_RUNG:
        print(resolution.line(), file=sys.stderr)

    errors, warnings = validate_entries(entries, entry_type=resolution.type_name)
    _report(errors, warnings, args.strict)


if __name__ == "__main__":
    main()
