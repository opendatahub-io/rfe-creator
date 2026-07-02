#!/usr/bin/env python3
"""Allocate the next available RFE-NNN ID(s) atomically.

Uses a lock file to prevent concurrent split agents from picking
the same IDs.

Usage:
    python3 scripts/next_rfe_id.py 3
    # RFE-012
    # RFE-013
    # RFE-014

    python3 scripts/next_rfe_id.py --from-batch batch.yaml
    # allocates one ID per entry in the YAML batch file (avoids N=$(...) in skills)
"""

import fcntl
import glob
import os
import re
import sys

TASKS_DIR = "artifacts/rfe-tasks"
LOCK_FILE = "artifacts/.rfe-id-lock"


def get_highest_rfe_number():
    """Scan artifacts/rfe-tasks/ for the highest RFE-NNN number."""
    highest = 0
    for path in glob.glob(os.path.join(TASKS_DIR, "RFE-*.md")):
        basename = os.path.basename(path)
        match = re.match(r"RFE-(\d+)", basename)
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
    if len(sys.argv) < 2:
        print("Usage: next_rfe_id.py <count> | --from-batch <batch.yaml>", file=sys.stderr)
        sys.exit(2)

    if sys.argv[1] == "--from-batch":
        if len(sys.argv) < 3:
            print("Usage: next_rfe_id.py --from-batch <batch.yaml>", file=sys.stderr)
            sys.exit(2)
        count = count_batch_entries(sys.argv[2])
    else:
        count = int(sys.argv[1])
    if count < 1:
        print("Count must be >= 1", file=sys.stderr)
        sys.exit(2)

    os.makedirs(TASKS_DIR, exist_ok=True)

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        highest = get_highest_rfe_number()
        for i in range(count):
            rfe_id = f"RFE-{highest + 1 + i:03d}"
            # Touch a placeholder so subsequent calls see it
            placeholder = os.path.join(TASKS_DIR, f"{rfe_id}.md")
            open(placeholder, "a").close()
            print(rfe_id)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    main()
