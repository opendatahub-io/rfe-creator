#!/usr/bin/env python3
"""Validate a batch YAML input file before /rfe.speedrun starts processing it.

Catches malformed entries (missing prompt, bad priority, wrong types,
duplicate prompts, unknown fields) before the expensive multi-agent
create/auto-fix/submit pipeline runs.

Usage:
    python3 scripts/validate_batch_input.py <path> [--strict]

Exit codes:
    0  Valid (no errors; warnings allowed unless --strict)
    1  Invalid — errors found, or warnings found with --strict
    2  Usage/file error (file missing, not valid YAML, root not a list)

Output:
    ERROR_COUNT=N
    WARNING_COUNT=N
    ERROR: entry <i>: <message>       (one per error)
    WARNING: entry <i>: <message>     (one per warning)
    VALID=true|false
"""

import argparse
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from artifact_utils import SCHEMAS

ALLOWED_PRIORITIES = SCHEMAS["rfe-task"]["priority"]["enum"]
KNOWN_FIELDS = {"prompt", "priority", "labels", "clarifying_context"}


def validate_entries(entries):
    """Validate a list of batch entries. Returns (errors, warnings) lists of strings."""
    errors = []
    warnings = []
    seen_prompts = {}

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry {i}: must be a mapping, got {type(entry).__name__}")
            continue

        prompt = entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"entry {i}: 'prompt' is required and must be a non-empty string")
        else:
            key = prompt.strip().lower()
            seen_prompts.setdefault(key, []).append(i)

        if "priority" in entry and entry["priority"] not in ALLOWED_PRIORITIES:
            errors.append(
                f"entry {i}: 'priority' {entry['priority']!r} is not one of {ALLOWED_PRIORITIES}"
            )

        if "labels" in entry and not isinstance(entry["labels"], list):
            errors.append(f"entry {i}: 'labels' must be a list")

        unknown = set(entry) - KNOWN_FIELDS
        for field in sorted(unknown):
            warnings.append(f"entry {i}: unknown field '{field}'")

    for key, indices in seen_prompts.items():
        if len(indices) > 1:
            warnings.append(f"entries {indices}: duplicate prompt {key!r}")

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="Path to the batch YAML input file")
    parser.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors (nonzero exit)"
    )
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"Error: file not found: {args.path}", file=sys.stderr)
        sys.exit(2)

    try:
        with open(args.path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"Error: invalid YAML: {e}", file=sys.stderr)
        sys.exit(2)

    if not isinstance(data, list):
        print(f"Error: batch input root must be a list, got {type(data).__name__}", file=sys.stderr)
        sys.exit(2)

    errors, warnings = validate_entries(data)

    print(f"ERROR_COUNT={len(errors)}")
    print(f"WARNING_COUNT={len(warnings)}")
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARNING: {message}")

    valid = not errors and not (args.strict and warnings)
    print(f"VALID={'true' if valid else 'false'}")
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()
