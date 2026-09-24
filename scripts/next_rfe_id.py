#!/usr/bin/env python3
"""Allocate the next available ID(s) atomically.

Uses a lock file to prevent concurrent agents from picking the same IDs.

Usage:
    python3 scripts/next_rfe_id.py 3
    # RFE-012
    # RFE-013
    # RFE-014

    python3 scripts/next_rfe_id.py --prefix INIT --dir artifacts/initiatives 2
    # INIT-001
    # INIT-002

    python3 scripts/next_rfe_id.py --from-batch batch.yaml
    # allocates one ID per entry in the YAML batch file (avoids N=$(...) in skills)

Batch root forms (design work-item-types-unified.md §5 rung 2; PR-3b): a bare
list of entries (legacy form — prefix and directory come from --prefix/--dir,
else the rfe defaults, exactly as before) or a mapping with exactly the keys
``type`` and ``items`` (mapping form — prefix and directory come from that
type's descriptor; an explicit --prefix/--dir that disagrees with them is a
conflict). The file is read ONCE through ``type_registry.read_batch`` (a pipe
or /dev/stdin works) and the type decided by ``type_registry.resolve`` (the one
ladder) over the parsed items — a bare string item is counted as an entry, not
read as an id (``items_are_ids=False``), and only the type verdict is taken
(``binding=False``: JIRA_PROJECT / RFE_CREATOR_BINDING_* are neither read nor
validated here, as before) — so an unknown mapping type (reported with the
registered list), a per-item ``type`` key (split the batch by type — a run is
single-typed, PR-3 D2) and every other shape or registry error exit 2 with the
reason on stderr: stdout is ids only. ``TYPE RESOLVED: <type> (batch type)`` is
printed on stderr for the mapping form only (PR-3 D3); the legacy form prints
nothing there, whatever the environment.
"""

import argparse
import fcntl
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry

_TYPES = type_registry.load()
_RFE = _TYPES.get(type_registry.LEGACY_DEFAULT_TYPE)


def type_defaults(desc):
    """``(prefix, tasks dir)`` for a type — the dash-less ``identity.local_prefix`` and
    ``dirs.tasks``: the allocator composes ``f"{prefix}-{n:03d}"`` itself, so ``RFE-`` is held
    as ``RFE`` (the CLI value documented above)."""
    return desc.local_prefix.rstrip("-"), desc.dirs()["tasks"]


# The rfe defaults come from the rfe descriptor (design work-item-types-unified.md §10 item 2);
# other types pass --prefix/--dir explicitly (the NEXT_ID_FLAGS launch var) or declare
# themselves through the batch mapping form.
DEFAULT_PREFIX, DEFAULT_DIR = type_defaults(_RFE)


def get_highest_number(tasks_dir, prefix):
    """Scan tasks_dir for the highest PREFIX-NNN number."""
    highest = 0
    for path in glob.glob(os.path.join(tasks_dir, f"{prefix}-*.md")):
        basename = os.path.basename(path)
        match = re.match(rf"{re.escape(prefix)}-(\d+)", basename)
        if match:
            num = int(match.group(1))
            if num > highest:
                highest = num
    return highest


def load_batch(path):
    """Return ``(batch type or None, resolution, entries)`` for a batch file, or exit 2.

    ``type_registry.read_batch`` parses the root once (legacy list -> type ``None``; mapping
    form -> its ``type:``) and ``type_registry.resolve`` runs the ladder over that parsed pair
    (``batch_items`` — the file is not re-read), so an unknown type, a per-item ``type`` key
    (D2) and the shape errors are decided in one place. String items are entries to count, not
    ids (``items_are_ids=False``); the binding is not evaluated (``binding=False``). This
    script has no protocol for content errors: every ``RegistryError`` (``ResolveError``
    included) is exit 2 with the message on stderr, before anything is allocated.
    """
    try:
        batch_type, entries = type_registry.read_batch(path)
        resolution = type_registry.resolve(
            _TYPES,
            batch=path,
            batch_items=(batch_type, entries),
            items_are_ids=False,
            env=os.environ,
            binding=False,
        )
    except type_registry.RegistryError as exc:
        print(exc.args[0] if exc.args else exc, file=sys.stderr)
        sys.exit(2)
    return batch_type, resolution, entries


def _conflicts(path, resolution, prefix, tasks_dir):
    """The explicit --prefix/--dir values that disagree with the batch type's descriptor."""
    type_prefix, type_dir = type_defaults(resolution.desc)
    found = []
    if prefix is not None and prefix != type_prefix:
        found.append(f"--prefix {prefix} (the {resolution.type_name} prefix is {type_prefix})")
    if tasks_dir is not None and os.path.normpath(tasks_dir) != os.path.normpath(type_dir):
        found.append(f"--dir {tasks_dir} (the {resolution.type_name} tasks dir is {type_dir})")
    return found


def main():
    parser = argparse.ArgumentParser(description="Allocate the next available ID(s) atomically.")
    parser.add_argument("count", nargs="?", type=int, help="Number of IDs to allocate")
    parser.add_argument("--from-batch", metavar="FILE", help="YAML batch file (one ID per entry)")
    # default=None so an explicit flag can be told from the default: a mapping-form batch
    # supplies both values and an explicit flag that disagrees with it is a conflict; the
    # rfe defaults (rendered in the help text) fill in after parsing, as before.
    parser.add_argument("--prefix", default=None, help=f"ID prefix (default: {DEFAULT_PREFIX})")
    parser.add_argument("--dir", default=None, help=f"Tasks directory (default: {DEFAULT_DIR})")
    args = parser.parse_args()

    prefix = args.prefix
    tasks_dir = args.dir
    if args.from_batch:
        batch_type, resolution, entries = load_batch(args.from_batch)
        count = len(entries)
        if batch_type is not None:
            conflicts = _conflicts(args.from_batch, resolution, prefix, tasks_dir)
            if conflicts:
                print(
                    f"{args.from_batch}: type: {resolution.type_name} disagrees with "
                    f"{'; '.join(conflicts)}; both are explicit, so neither is guessed — drop "
                    f"the flag(s) or make them agree (PR-3 D1)",
                    file=sys.stderr,
                )
                sys.exit(2)
            prefix, tasks_dir = type_defaults(resolution.desc)
            # D3: the resolve line for the mapping form only, never on stdout.
            print(resolution.line(), file=sys.stderr)
    elif args.count is not None:
        count = args.count
    else:
        parser.error("either count or --from-batch is required")

    if count < 1:
        print("Count must be >= 1", file=sys.stderr)
        sys.exit(2)

    if prefix is None:
        prefix = DEFAULT_PREFIX
    if tasks_dir is None:
        tasks_dir = DEFAULT_DIR
    lock_file = os.path.join(tasks_dir, ".id-lock")

    os.makedirs(tasks_dir, exist_ok=True)

    lock_fd = open(lock_file, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        highest = get_highest_number(tasks_dir, prefix)
        for i in range(count):
            new_id = f"{prefix}-{highest + 1 + i:03d}"
            placeholder = os.path.join(tasks_dir, f"{new_id}.md")
            open(placeholder, "a").close()
            print(new_id)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
