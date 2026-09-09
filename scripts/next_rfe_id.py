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
_RFE = _TYPES.get("rfe")

# The rfe defaults come from the rfe descriptor (design work-item-types-unified.md §10 item 2);
# other types pass --prefix/--dir explicitly (initiative-create/SKILL.md:50). The prefix is held
# dash-less on purpose: identity.local_prefix is "RFE-" and the allocator composes
# f"{prefix}-{n:03d}" itself, so the CLI value stays "RFE" as documented above.
DEFAULT_PREFIX = _RFE.local_prefix.rstrip("-")
DEFAULT_DIR = _RFE.dirs()["tasks"]


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


def count_batch_entries(path):
    """Return the number of entries in a YAML batch file."""
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, list):
        print(f"Batch file {path} must contain a YAML list", file=sys.stderr)
        sys.exit(2)
    return len(data)


def main():
    parser = argparse.ArgumentParser(description="Allocate the next available ID(s) atomically.")
    parser.add_argument("count", nargs="?", type=int, help="Number of IDs to allocate")
    parser.add_argument("--from-batch", metavar="FILE", help="YAML batch file (one ID per entry)")
    parser.add_argument(
        "--prefix", default=DEFAULT_PREFIX, help=f"ID prefix (default: {DEFAULT_PREFIX})"
    )
    parser.add_argument(
        "--dir", default=DEFAULT_DIR, help=f"Tasks directory (default: {DEFAULT_DIR})"
    )
    args = parser.parse_args()

    if args.from_batch:
        count = count_batch_entries(args.from_batch)
    elif args.count is not None:
        count = args.count
    else:
        parser.error("either count or --from-batch is required")

    if count < 1:
        print("Count must be >= 1", file=sys.stderr)
        sys.exit(2)

    tasks_dir = args.dir
    prefix = args.prefix
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
