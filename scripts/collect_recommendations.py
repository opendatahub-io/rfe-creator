#!/usr/bin/env python3
"""Group RFE IDs by review recommendation or reassess status."""

import argparse
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import ValidationError, read_frontmatter, resolve_ids

ARTIFACTS_DIR = os.path.join(os.getcwd(), "artifacts")


def _review_dir(entry_type):
    """Return the review directory for the given type."""
    subdir = "initiative-reviews" if entry_type == "initiative" else "rfe-reviews"
    return os.path.join(ARTIFACTS_DIR, subdir)


def _read_review_data(path):
    """Read a review's frontmatter, tolerating a missing file or corrupt block.

    Returns the frontmatter dict, or None if the file is absent or its
    frontmatter is unparseable. read_frontmatter raises ValidationError on a bad
    YAML block (see artifact_utils), so every collection mode goes through here
    and treats a malformed review as an error/no-op rather than letting one bad
    file crash the whole batch.
    """
    if not os.path.exists(path):
        return None
    try:
        data, _ = read_frontmatter(path)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError):
        return None
    return data


def collect_default(ids, entry_type="rfe"):
    """Group IDs by recommendation field."""
    groups = {"SUBMIT": [], "SPLIT": [], "REVISE": [], "REJECT": [], "ERRORS": []}
    review_dir = _review_dir(entry_type)
    for item_id in ids:
        data = _read_review_data(os.path.join(review_dir, f"{item_id}-review.md"))
        if data is None or data.get("error"):
            groups["ERRORS"].append(item_id)
            continue
        rec = data.get("recommendation", "").upper()
        if rec == "AUTOREVISE_REJECT":
            rec = "REJECT"
        if rec in groups:
            groups[rec].append(item_id)
        else:
            groups["ERRORS"].append(item_id)
    for key, vals in groups.items():
        print(f"{key}={','.join(vals)}")


def collect_reassess(ids, entry_type="rfe"):
    """Collect IDs needing reassessment (auto_revised=true, pass=false)."""
    reassess, done = [], []
    review_dir = _review_dir(entry_type)
    for item_id in ids:
        data = _read_review_data(os.path.join(review_dir, f"{item_id}-review.md"))
        # A missing or corrupt review is not reassessable — bucket it as done so
        # the run continues; --errors is the mode that surfaces it.
        if data and data.get("auto_revised") and not data.get("pass"):
            reassess.append(item_id)
        else:
            done.append(item_id)
    print(f"REASSESS={','.join(reassess)}")
    print(f"DONE={','.join(done)}")


def collect_errors(ids, entry_type="rfe"):
    """Collect IDs with non-null error field, missing files, or corrupt reviews."""
    error_ids = []
    review_dir = _review_dir(entry_type)
    for item_id in ids:
        data = _read_review_data(os.path.join(review_dir, f"{item_id}-review.md"))
        if data is None or data.get("error"):
            error_ids.append(item_id)
    print(f"ERRORS={','.join(error_ids)}")


def main():
    parser = argparse.ArgumentParser(description="Group IDs by review recommendation.")
    parser.add_argument("ids", nargs="*", help="IDs to check")
    parser.add_argument(
        "--ids-file", help="Read IDs from a file (one per line) instead of positional args"
    )
    parser.add_argument(
        "--reassess", action="store_true", help="Collect re-assess candidates instead"
    )
    parser.add_argument("--errors", action="store_true", help="Collect IDs with error field set")
    parser.add_argument(
        "--type",
        choices=["rfe", "initiative"],
        default="rfe",
        help="Entry type (default: rfe)",
    )
    args = parser.parse_args()

    ids = resolve_ids(args.ids, args.ids_file)
    if not ids:
        parser.error("no IDs provided (pass positionally or via --ids-file)")

    if args.errors:
        collect_errors(ids, entry_type=args.type)
    elif args.reassess:
        collect_reassess(ids, entry_type=args.type)
    else:
        collect_default(ids, entry_type=args.type)


if __name__ == "__main__":
    main()
