#!/usr/bin/env python3
"""Post-barrier verification for agent phases.

Checks that expected output files exist for each ID after a phase
completes. Missing outputs are treated as agent failures — error
frontmatter is written and the ID is removed from the active set.

The phase→path maps below must stay in step with
check_review_progress.PHASE_CHECKS.

Usage:
    python3 scripts/verify_phase.py --phase assess --ids-file tmp/pipeline-active-ids.txt
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry
from artifact_utils import read_frontmatter

_TYPES = type_registry.load()

# Type-neutral assess staging dir (design §10: kept byte-stable for every type).
ASSESS_STAGING = "tmp/rfe-assess/single"


def _phase_output(desc):
    """phase -> (id -> expected output path) for one type: ``dirs`` x the pipeline phases,
    plus one ``<reviews>/<id>-<name>.md`` row per ``pipeline.dimensions`` entry."""
    dirs = desc.dirs()
    table = {
        "fetch": lambda id: f"{dirs['tasks']}/{id}.md",
        "assess": lambda id: f"{ASSESS_STAGING}/{id}.result.md",
    }
    for dimension in desc.get("pipeline.dimensions", []):
        name = dimension["name"]
        table[name] = lambda id, name=name: f"{dirs['reviews']}/{id}-{name}.md"
    table["review"] = lambda id: f"{dirs['reviews']}/{id}-review.md"
    table["split"] = lambda id: f"{dirs['reviews']}/{id}-split-status.yaml"
    return table


_PHASE_OUTPUT = {name: _phase_output(_TYPES.get(name)) for name in _TYPES.names()}
# Per-type aliases kept for their existing importers (tests/test_verify_phase.py).
_RFE_PHASE_OUTPUT = _PHASE_OUTPUT["rfe"]
_INITIATIVE_PHASE_OUTPUT = _PHASE_OUTPUT["initiative"]

_TYPE_CONFIG = {
    name: {
        "phases": _PHASE_OUTPUT[name],
        "reviews_dir": _TYPES.get(name).dirs()["reviews"],
        "id_field": _TYPES.get(name).id_field,
        "review_schema": f"{name}-review",
        # The varying tail of the error-stub command below (the fixed fields are pipeline
        # vocabulary, not type facts).
        "score_fields": [f"scores.{field}=0" for field in _TYPES.get(name).score_fields],
    }
    for name in _TYPES.names()
}


def verify(phase, ids_file, pipeline_type="rfe"):
    tc = _TYPE_CONFIG[pipeline_type]
    phase_output = tc["phases"]

    ids = []
    if os.path.exists(ids_file):
        with open(ids_file) as f:
            ids = [line.strip() for line in f if line.strip()]

    if not ids:
        print("FAILED=")
        return

    output_fn = phase_output.get(phase)
    if not output_fn:
        print(f"Unknown phase: {phase}", file=sys.stderr)
        sys.exit(1)

    failed = []
    for rfe_id in ids:
        path = output_fn(rfe_id)
        exists = os.path.exists(path)

        # For review phase, also check that score is set
        if exists and phase == "review":
            try:
                data, _ = read_frontmatter(path)
                if data.get("score") is None:
                    exists = False
            except Exception:
                exists = False

        if not exists:
            failed.append(rfe_id)

    if failed:
        for rfe_id in failed:
            review_path = f"{tc['reviews_dir']}/{rfe_id}-review.md"
            error_msg = f"{phase}_failed"
            try:
                cmd = [
                    "python3",
                    "scripts/frontmatter.py",
                    "set",
                    review_path,
                    f"{tc['id_field']}={rfe_id}",
                    f"error={error_msg}",
                    "score=0",
                    "pass=false",
                    "recommendation=revise",
                    "feasibility=feasible",
                    "auto_revised=false",
                    "needs_attention=true",
                    f"needs_attention_reason=Agent failed: {error_msg}",
                ] + tc["score_fields"]
                subprocess.run(cmd, check=True, capture_output=True)
            except Exception:
                pass

        failed_set = set(failed)
        remaining = [id_ for id_ in ids if id_ not in failed_set]
        with open(ids_file, "w") as f:
            for id_ in remaining:
                f.write(f"{id_}\n")

    print(f"FAILED={','.join(failed)}")


def main():
    parser = argparse.ArgumentParser(description="Post-barrier verification for agent phases")
    parser.add_argument("--type", choices=_TYPES.choices(), default="rfe")
    parser.add_argument("--phase", required=True, help="Phase to verify")
    parser.add_argument("--ids-file", required=True, help="File containing IDs to check")
    args = parser.parse_args()
    verify(args.phase, args.ids_file, args.type)


if __name__ == "__main__":
    main()
