#!/usr/bin/env python3
"""Reconcile the reviews of a batch before COLLECT routes them (AISDLC-33).

Two deterministic repairs that close the gaps the reassess loop leaves open:

1. **Re-apply saved review state.** REASSESS_RESTORE re-applies ``before_score``,
   ``before_scores``, ``auto_revised`` and the revision history after a re-review
   and keeps the state file. A review agent that finishes late — the same wave's
   agent writing a second time, or a previous cycle's agent — can rewrite the
   review afterwards and drop them again (seen twice on 2026-09-17). Restoring
   once more here, after every wave of the batch is over, makes the final review
   carry them whatever landed in between. The state file is removed.

2. **Flag what stays failing.** After the reassess cap nothing revises an item
   again in this batch, yet only the revise agent's prompt set ``needs_attention``
   for an item it could not fix — one of three cap-exhausted items shipped
   unflagged on 2026-09-18. Every review still at ``recommendation: revise`` with
   ``pass: false`` gets ``needs_attention=true`` and, when the agent left none, a
   reason naming the criteria scored 0.

Usage:
    python3 scripts/reconcile_reviews.py --type <t> [--cycles N] [--keep-state] <ID> [<ID> ...]

``--keep-state`` leaves the state files in place after re-applying them: the
COLLECT and SPLIT_CORRECTION_CHECK reconciles use it, because a review agent can
keep writing for minutes after its wave (seen after COLLECT on 2026-09-18), and
the final reconcile at REPORT — over every id of the run, without the flag —
re-applies once more and removes them, so any write that lands before the run
report is generated is undone.

Prints ``RESTORED=<ids>``, ``FLAGGED=<ids>`` and ``RECONCILE_ERRORS=<ids>`` (one
line each; a ``RECONCILE_ERROR <id>: <why>`` detail line per failed item). Never
exits non-zero for a per-item problem: this runs inside pipeline_state.py's
COLLECT decision, where a failing script aborts the run; the caller marks the
errored ids ``error: reconcile_failed`` so routing treats them as errors.
"""

import argparse
import contextlib
import io
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preserve_review_state as prs  # noqa: E402
import type_registry  # noqa: E402
from artifact_utils import ValidationError, read_frontmatter, update_frontmatter  # noqa: E402

_TYPES = type_registry.load()
PASS_THRESHOLD = 7


def _read_review(path):
    if not os.path.exists(path):
        return None
    try:
        data, _ = read_frontmatter(path)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError):
        return None
    return data if isinstance(data, dict) else None


def failure_reason(data, desc, cycles):
    """Why the review still fails, in the criterion labels the reports use."""
    labels = desc.get("reporting.criterion_labels", {}) or {}
    scores = data.get("scores") or {}
    zeros = [str(labels.get(f, f)) for f in desc.score_fields if scores.get(f) == 0]
    if zeros:
        what = f"{', '.join(zeros)} scored 0/2"
    else:
        what = f"score {data.get('score')}/10 below the pass threshold of {PASS_THRESHOLD}"
    if data.get("auto_revised"):
        n = max(int(cycles or 0), 1)
        cycles_text = f"{n} reassess cycle{'s' if n != 1 else ''}"
        return f"Still failing after auto-revision ({cycles_text}): {what}."
    return f"Failing and not auto-revised: {what}."


def reconcile(ids, type_name, cycles=0, keep_state=False):
    desc = _TYPES.get(type_name)
    reviews_dir = desc.dirs()["reviews"]
    schema = f"{type_name}-review"
    restored, flagged, errored = [], [], []
    for item_id in ids:
        try:
            prs.validate_item_id(item_id)
            review = os.path.join(reviews_dir, f"{item_id}-review.md")
            if os.path.exists(prs.state_path(item_id)) and os.path.exists(review):
                # restore() prints its own RESTORED=<id>; only this script's summary line
                # may reach pipeline_state's line parser.
                with contextlib.redirect_stdout(io.StringIO()):
                    prs.restore(item_id, keep_state=keep_state)  # idempotent
                restored.append(item_id)
            data = _read_review(review)
            if data is None or data.get("error"):
                continue
            if data.get("recommendation") != "revise" or data.get("pass"):
                continue
            updates = {}
            if not data.get("needs_attention"):
                updates["needs_attention"] = True
            if not str(data.get("needs_attention_reason") or "").strip():
                updates["needs_attention_reason"] = failure_reason(data, desc, cycles)
            if updates:
                update_frontmatter(review, updates, schema)
                flagged.append(item_id)
        except Exception as exc:  # one bad item must not abort the batch
            errored.append(item_id)
            print(f"RECONCILE_ERROR {item_id}: {' '.join(str(exc).split())}")
    print(f"RESTORED={','.join(restored)}")
    print(f"FLAGGED={','.join(flagged)}")
    print(f"RECONCILE_ERRORS={','.join(errored)}")
    return restored, flagged


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe", help="Work item type")
    parser.add_argument("--cycles", type=int, default=0, help="Reassess cycles the batch ran")
    parser.add_argument(
        "--keep-state",
        action="store_true",
        help="Leave the state files for a later reconcile (COLLECT); REPORT runs without it",
    )
    parser.add_argument("ids", nargs="+", metavar="ID")
    args = parser.parse_args(argv)
    reconcile(args.ids, args.type, args.cycles, keep_state=args.keep_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
