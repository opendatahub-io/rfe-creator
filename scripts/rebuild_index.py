#!/usr/bin/env python3
"""Rebuild artifacts/rfes.md index from frontmatter across task and review files.

Called at the end of batch/review/submit operations. Not called per-issue
during parallel runs — only once after all per-issue work completes.

Usage:
    python3 scripts/rebuild_index.py [--artifacts-dir artifacts]
"""

import argparse
import sys

from artifact_utils import rebuild_index


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--artifacts-dir", default="artifacts",
                        help="Artifacts directory (default: artifacts)")
    args = parser.parse_args()

    content = rebuild_index(args.artifacts_dir)
    print(f"Rebuilt {args.artifacts_dir}/rfes.md")

    # Count entries
    lines = [l for l in content.split("\n")
             if l.startswith("|") and not l.startswith("| ID")
             and not l.startswith("|---")]
    print(f"  {len(lines)} RFEs indexed")


if __name__ == "__main__":
    main()
