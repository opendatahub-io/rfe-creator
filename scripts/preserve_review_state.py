"""Save and restore cumulative review state across re-assessment cycles.

Saves before_scores and revision history to a JSON state file before
re-review, then restores them after the new review file is written.

Usage:
    python3 scripts/preserve_review_state.py save <ID> [<ID> ...]
    python3 scripts/preserve_review_state.py restore <ID> [<ID> ...]
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter, update_frontmatter


REVIEWS_DIR = "artifacts/rfe-reviews"


def state_path(rfe_id):
    return os.path.join(REVIEWS_DIR, f"{rfe_id}-review-state.json")


def review_path(rfe_id):
    return os.path.join(REVIEWS_DIR, f"{rfe_id}-review.md")


def extract_revision_history(filepath):
    """Extract the ## Revision History section content from a review file."""
    with open(filepath) as f:
        content = f.read()

    # Skip frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].lstrip("\n")

    # Find ## Revision History section
    match = re.search(r"^## Revision History\s*\n(.*)", content,
                      re.MULTILINE | re.DOTALL)
    if not match:
        return ""

    section = match.group(1)

    # Trim at the next ## heading (if any)
    next_heading = re.search(r"^## ", section, re.MULTILINE)
    if next_heading:
        section = section[:next_heading.start()]

    return section.strip()


def save(rfe_id):
    """Save before_scores and revision history to a state file."""
    rpath = review_path(rfe_id)
    if not os.path.exists(rpath):
        print(f"SKIP={rfe_id} (no review file)")
        return

    data, _ = read_frontmatter(rpath)
    state = {
        "before_score": data.get("before_score"),
        "before_scores": data.get("before_scores"),
        "auto_revised": data.get("auto_revised"),
        "revision_history": extract_revision_history(rpath),
    }

    spath = state_path(rfe_id)
    with open(spath, "w") as f:
        json.dump(state, f, indent=2)

    print(f"SAVED={rfe_id}")


def restore(rfe_id):
    """Restore before_scores and revision history from the state file."""
    spath = state_path(rfe_id)
    if not os.path.exists(spath):
        print(f"SKIP={rfe_id} (no state file)")
        return

    with open(spath) as f:
        state = json.load(f)

    rpath = review_path(rfe_id)
    if not os.path.exists(rpath):
        print(f"SKIP={rfe_id} (no review file to restore into)")
        return

    # Restore before_scores and auto_revised via frontmatter
    fm_updates = {}
    if state.get("before_score") is not None:
        fm_updates["before_score"] = state["before_score"]
    if state.get("before_scores"):
        fm_updates["before_scores"] = state["before_scores"]
    if state.get("auto_revised") is not None:
        fm_updates["auto_revised"] = state["auto_revised"]

    if fm_updates:
        update_frontmatter(rpath, fm_updates, "rfe-review")

    # Restore revision history
    saved_history = state.get("revision_history", "").strip()
    if saved_history:
        with open(rpath) as f:
            content = f.read()

        # Find ## Revision History and prepend saved history
        marker = "## Revision History"
        idx = content.find(marker)
        if idx != -1:
            after_marker = idx + len(marker)
            # Get current revision history (new pass content)
            current_after = content[after_marker:]
            # Rebuild: marker + saved history + new content
            content = (content[:after_marker] + "\n" +
                       saved_history + "\n" + current_after.lstrip("\n"))
            with open(rpath, "w") as f:
                f.write(content)

    os.remove(spath)
    print(f"RESTORED={rfe_id}")


def main():
    if len(sys.argv) < 3:
        print("Usage: preserve_review_state.py save|restore <ID> [<ID> ...]",
              file=sys.stderr)
        sys.exit(2)

    action = sys.argv[1]
    ids = sys.argv[2:]

    if action == "save":
        for rfe_id in ids:
            save(rfe_id)
    elif action == "restore":
        for rfe_id in ids:
            restore(rfe_id)
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
