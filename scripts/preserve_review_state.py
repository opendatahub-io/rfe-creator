"""Save and restore cumulative review state across re-assessment cycles.

Saves before_scores, the auto_revised flag and revision history to a JSON
state file before re-review, then restores them after the new review file is
written. The re-review recreates the review file from scratch, so the flag
would otherwise fall back to the schema default (false) for every re-reviewed
item; submit.py derives the auto-revised Jira label from it.

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


def _is_initiative(item_id):
    return item_id.startswith("INIT-") or item_id.startswith("RHOAIENG-")


def _reviews_dir(item_id):
    return "artifacts/initiative-reviews" if _is_initiative(item_id) else "artifacts/rfe-reviews"


def _schema(item_id):
    return "initiative-review" if _is_initiative(item_id) else "rfe-review"


def state_path(item_id):
    return os.path.join(_reviews_dir(item_id), f"{item_id}-review-state.json")


def review_path(item_id):
    return os.path.join(_reviews_dir(item_id), f"{item_id}-review.md")


def extract_revision_history(filepath):
    """Extract the ## Revision History section content from a review file."""
    with open(filepath) as f:
        content = f.read()

    # Skip frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3 :].lstrip("\n")

    # Find ## Revision History section
    match = re.search(r"^## Revision History\s*\n(.*)", content, re.MULTILINE | re.DOTALL)
    if not match:
        return ""

    section = match.group(1)

    # Trim at the next ## heading (if any)
    next_heading = re.search(r"^## ", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]

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
        # Verified by check_revised.py --batch (FIXUP) before this save runs.
        "auto_revised": bool(data.get("auto_revised", False)),
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

    # Restore before_scores and the auto_revised flag via frontmatter
    fm_updates = {}
    if state.get("before_score") is not None:
        fm_updates["before_score"] = state["before_score"]
    if state.get("before_scores"):
        fm_updates["before_scores"] = state["before_scores"]
    # Only ever raise the flag: the fresh review defaults it to false, and a
    # revision that happened in an earlier cycle stays a revision. A state file
    # written before this key existed simply leaves the flag alone.
    if state.get("auto_revised"):
        fm_updates["auto_revised"] = True
    if fm_updates:
        update_frontmatter(rpath, fm_updates, _schema(rfe_id))

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
            content = (
                content[:after_marker] + "\n" + saved_history + "\n" + current_after.lstrip("\n")
            )
            with open(rpath, "w") as f:
                f.write(content)

    os.remove(spath)
    print(f"RESTORED={rfe_id}")


def main():
    if len(sys.argv) < 3:
        print("Usage: preserve_review_state.py save|restore <ID> [<ID> ...]", file=sys.stderr)
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
