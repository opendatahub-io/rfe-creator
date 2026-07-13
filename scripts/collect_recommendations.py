#!/usr/bin/env python3
"""Group RFE IDs by review recommendation or reassess status."""

import argparse
import glob
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import read_frontmatter, resolve_ids

ARTIFACTS_DIR = os.path.join(os.getcwd(), "artifacts")


def collect_default(ids):
    """Group IDs by recommendation field."""
    groups = {"SUBMIT": [], "SPLIT": [], "REVISE": [], "REJECT": [], "ERRORS": []}
    for rfe_id in ids:
        path = os.path.join(ARTIFACTS_DIR, "rfe-reviews", f"{rfe_id}-review.md")
        if not os.path.exists(path):
            groups["ERRORS"].append(rfe_id)
            continue
        data, _ = read_frontmatter(path)
        if data.get("error"):
            groups["ERRORS"].append(rfe_id)
            continue
        rec = data.get("recommendation", "").upper()
        if rec == "AUTOREVISE_REJECT":
            rec = "REJECT"
        if rec in groups:
            groups[rec].append(rfe_id)
        else:
            groups["ERRORS"].append(rfe_id)
    for key, vals in groups.items():
        print(f"{key}={','.join(vals)}")


def collect_reassess(ids):
    """Collect IDs needing reassessment (auto_revised=true, pass=false)."""
    reassess, done = [], []
    for rfe_id in ids:
        path = os.path.join(ARTIFACTS_DIR, "rfe-reviews", f"{rfe_id}-review.md")
        if not os.path.exists(path):
            done.append(rfe_id)
            continue
        data, _ = read_frontmatter(path)
        if data.get("auto_revised") and not data.get("pass"):
            reassess.append(rfe_id)
        else:
            done.append(rfe_id)
    print(f"REASSESS={','.join(reassess)}")
    print(f"DONE={','.join(done)}")


def collect_errors(ids):
    """Collect IDs with non-null error field or missing review files."""
    error_ids = []
    for rfe_id in ids:
        path = os.path.join(ARTIFACTS_DIR, "rfe-reviews", f"{rfe_id}-review.md")
        if not os.path.exists(path):
            # Missing review file is an error — the pipeline failed to produce output
            error_ids.append(rfe_id)
            continue
        try:
            data, _ = read_frontmatter(path)
        except (OSError, UnicodeError, yaml.YAMLError):
            error_ids.append(rfe_id)
            continue
        if data.get("error"):
            error_ids.append(rfe_id)
    print(f"ERRORS={','.join(error_ids)}")


def discover_ids_from_reviews():
    """Glob review files and extract IDs from filenames."""
    review_dir = os.path.join(ARTIFACTS_DIR, "rfe-reviews")
    pattern = os.path.join(review_dir, "*-review.md")
    ids = []
    for path in sorted(glob.glob(pattern)):
        basename = os.path.basename(path)
        # DRAFT-001-review.md -> DRAFT-001
        # PROJ-1234-review.md -> PROJ-1234
        rfe_id = basename.rsplit("-review.md", 1)[0]
        ids.append(rfe_id)
    return ids


def main():
    parser = argparse.ArgumentParser(description="Group RFE IDs by review recommendation.")
    parser.add_argument("ids", nargs="*", help="RFE IDs to check")
    parser.add_argument(
        "--ids-file", help="Read RFE IDs from a file (one per line) instead of positional args"
    )
    parser.add_argument(
        "--from-reviews",
        action="store_true",
        help="Discover IDs by globbing artifacts/rfe-reviews/*-review.md",
    )
    parser.add_argument(
        "--reassess", action="store_true", help="Collect re-assess candidates instead"
    )
    parser.add_argument("--errors", action="store_true", help="Collect IDs with error field set")
    args = parser.parse_args()

    if args.from_reviews:
        ids = discover_ids_from_reviews()
        if not ids:
            print("ERROR: no review files found in artifacts/rfe-reviews/", file=sys.stderr)
            sys.exit(1)
    else:
        ids = resolve_ids(args.ids, args.ids_file)
        if not ids:
            parser.error("no RFE IDs provided (pass positionally or via --ids-file)")

    if args.errors:
        collect_errors(ids)
    elif args.reassess:
        collect_reassess(ids)
    else:
        collect_default(ids)


if __name__ == "__main__":
    main()
