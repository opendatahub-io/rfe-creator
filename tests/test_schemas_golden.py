#!/usr/bin/env python3
"""artifact_utils.SCHEMAS is an artifact contract: pin the registry-derived dicts byte for byte.

tests/data/schemas-golden.json was frozen from the hand-written SCHEMAS literal (branch
feat/registry-adopt-scripts at 2bacd7b, identical to main d0e41d9) with
``json.dumps(SCHEMAS, sort_keys=False, indent=2, default=str)`` immediately before the schemas
started deriving from ``types/<name>/type.yaml``. Every frontmatter file in every results repo
was validated against exactly these specs — field names, ORDER (``frontmatter.py read`` and
``apply_defaults`` emit fields in schema order), types, enums, patterns, defaults and required
flags — so unlike the descriptor pins in tests/test_type_registry_pins.py this golden is
permanent: it must only change together with a deliberate, reviewed change of the artifact
contract, never as a side effect of a refactor or a descriptor edit.

To regenerate after such a change (and only then):
    python3 -c "import json, sys; sys.path.insert(0, 'scripts'); from artifact_utils import \\
        SCHEMAS; print(json.dumps(SCHEMAS, sort_keys=False, indent=2, default=str))" \\
        > tests/data/schemas-golden.json
"""

import difflib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from artifact_utils import SCHEMAS  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "data" / "schemas-golden.json"


def _serialise(schemas):
    return json.dumps(schemas, sort_keys=False, indent=2, default=str) + "\n"


def test_schemas_serialise_to_the_golden_bytes():
    expected = GOLDEN.read_text(encoding="utf-8")
    actual = _serialise(SCHEMAS)
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=str(GOLDEN.relative_to(GOLDEN.parents[2])),
                tofile="artifact_utils.SCHEMAS",
            )
        )
        raise AssertionError("SCHEMAS drifted from tests/data/schemas-golden.json:\n" + diff)


def test_golden_holds_no_stringified_values():
    """``default=str`` never fired: a JSON round-trip of the live dicts is the live dicts.

    Patterns are plain strings (never compiled regexes), enums are lists, defaults are
    None / False / strings — nothing in the contract needs a repr.
    """
    assert json.loads(_serialise(SCHEMAS)) == SCHEMAS
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == SCHEMAS


def test_schema_key_order_is_types_times_task_review():
    """The dict order is observable: it is the `frontmatter.py schema` choices order."""
    assert list(SCHEMAS) == ["rfe-task", "rfe-review", "initiative-task", "initiative-review"]
    assert list(json.loads(GOLDEN.read_text(encoding="utf-8"))) == list(SCHEMAS)


def test_schemas_do_not_alias_each_other():
    """Per-schema specs are independent objects (mutating one never bleeds into another)."""
    rfe_status = SCHEMAS["rfe-task"]["status"]["enum"]
    init_status = SCHEMAS["initiative-task"]["status"]["enum"]
    assert rfe_status == init_status and rfe_status is not init_status
    for name in ("rfe-review", "initiative-review"):
        assert SCHEMAS[name]["scores"]["fields"] is not SCHEMAS[name]["before_scores"]["fields"]
