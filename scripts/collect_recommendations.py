#!/usr/bin/env python3
"""Group RFE IDs by review recommendation or reassess status."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import read_frontmatter

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


def main():
    parser = argparse.ArgumentParser(
        description="Group RFE IDs by review recommendation.")
    parser.add_argument("ids", nargs="+", help="RFE IDs to check")
    parser.add_argument("--reassess", action="store_true",
                        help="Collect re-assess candidates instead")
    args = parser.parse_args()

    if args.reassess:
        collect_reassess(args.ids)
    else:
        collect_default(args.ids)


if __name__ == "__main__":
    main()
