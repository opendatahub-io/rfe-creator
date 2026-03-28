#!/usr/bin/env python3
"""CLI for reading, writing, and inspecting artifact frontmatter.

Skills call this script instead of writing YAML by hand, ensuring
schema-validated frontmatter on all artifact files.

Usage:
    # Show schema for a file type
    python3 scripts/frontmatter.py schema rfe-task
    python3 scripts/frontmatter.py schema rfe-review
    python3 scripts/frontmatter.py schema strat-task
    python3 scripts/frontmatter.py schema strat-review

    # Set/update frontmatter on a file (validates before writing)
    python3 scripts/frontmatter.py set artifacts/rfe-tasks/RFE-001-foo.md \\
        --rfe_id RFE-001 --title "My RFE" --priority Major --size M \\
        --status Draft

    # Set nested fields with dot notation
    python3 scripts/frontmatter.py set artifacts/rfe-reviews/RFE-001-foo-review.md \\
        --rfe_id RFE-001 --score 9 --pass true --recommendation submit \\
        --feasibility feasible --revised false --needs_attention false \\
        --scores.what 2 --scores.why 1 --scores.open_to_how 2 \\
        --scores.not_a_task 2 --scores.right_sized 2

    # Read and validate frontmatter from a file
    python3 scripts/frontmatter.py read artifacts/rfe-tasks/RFE-001-foo.md

    # Rebuild the rfes.md index from all task and review files
    python3 scripts/frontmatter.py rebuild-index [--artifacts-dir artifacts]
"""

import argparse
import json
import os
import sys

from artifact_utils import (
    SCHEMAS,
    get_schema_yaml,
    read_frontmatter,
    read_frontmatter_validated,
    write_frontmatter,
    update_frontmatter,
    rebuild_index,
    ValidationError,
)


def _coerce_value(value_str, field_spec):
    """Coerce a CLI string value to the correct type based on field spec."""
    field_type = field_spec.get("type", "string")

    if field_type == "bool":
        if value_str.lower() in ("true", "1", "yes"):
            return True
        if value_str.lower() in ("false", "0", "no"):
            return False
        raise ValueError(f"Cannot convert '{value_str}' to bool")

    if field_type == "int":
        return int(value_str)

    if field_type == "string":
        if value_str.lower() == "null" or value_str.lower() == "none":
            return None
        return value_str

    return value_str


def _detect_schema_type(path):
    """Detect schema type from file path."""
    if "/rfe-reviews/" in path or "rfe-reviews/" in path:
        return "rfe-review"
    if "/rfe-tasks/" in path or "rfe-tasks/" in path:
        return "rfe-task"
    if "/strat-tasks/" in path or "strat-tasks/" in path:
        return "strat-task"
    if "/strat-reviews/" in path or "strat-reviews/" in path:
        return "strat-review"
    return None


def cmd_schema(args):
    """Print the schema for a file type."""
    try:
        yaml_str = get_schema_yaml(args.schema_type)
        print(yaml_str)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_read(args):
    """Read and display frontmatter from a file."""
    if not os.path.exists(args.file):
        print(f"Error: {args.file} not found", file=sys.stderr)
        sys.exit(1)

    schema_type = args.schema_type or _detect_schema_type(args.file)

    if schema_type:
        try:
            data, _ = read_frontmatter_validated(args.file, schema_type)
        except ValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        data, _ = read_frontmatter(args.file)
        if not data:
            print(f"Error: no frontmatter found in {args.file}",
                  file=sys.stderr)
            sys.exit(1)

    # Output as JSON for easy parsing by callers
    json.dump(data, sys.stdout, indent=2, default=str)
    print()


def cmd_set(args):
    """Set/update frontmatter fields on a file."""
    schema_type = args.schema_type or _detect_schema_type(args.file)
    if not schema_type:
        print("Error: cannot detect schema type from path. "
              "Use --schema-type.", file=sys.stderr)
        sys.exit(1)

    schema = SCHEMAS[schema_type]

    # Parse field=value pairs from remaining args
    data = {}
    for field_value in args.fields:
        if "=" not in field_value:
            print(f"Error: expected field=value, got '{field_value}'",
                  file=sys.stderr)
            sys.exit(1)

        field_name, value_str = field_value.split("=", 1)

        # Handle dot notation for nested fields (e.g., scores.what=2)
        if "." in field_name:
            parts = field_name.split(".", 1)
            parent_name, child_name = parts
            if parent_name not in schema:
                print(f"Error: unknown field '{parent_name}'",
                      file=sys.stderr)
                sys.exit(1)
            parent_spec = schema[parent_name]
            if parent_spec.get("type") != "dict":
                print(f"Error: '{parent_name}' is not a dict field",
                      file=sys.stderr)
                sys.exit(1)
            nested_fields = parent_spec.get("fields", {})
            if child_name not in nested_fields:
                print(f"Error: unknown field '{field_name}'",
                      file=sys.stderr)
                sys.exit(1)
            coerced = _coerce_value(value_str, nested_fields[child_name])
            if parent_name not in data:
                data[parent_name] = {}
            data[parent_name][child_name] = coerced
        else:
            if field_name not in schema:
                print(f"Error: unknown field '{field_name}' for schema "
                      f"'{schema_type}'", file=sys.stderr)
                sys.exit(1)
            data[field_name] = _coerce_value(value_str, schema[field_name])

    if os.path.exists(args.file):
        try:
            update_frontmatter(args.file, data, schema_type)
        except ValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            write_frontmatter(args.file, data, schema_type)
        except ValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"OK: {args.file}")


def cmd_rebuild_index(args):
    """Rebuild rfes.md index from frontmatter."""
    content = rebuild_index(args.artifacts_dir)
    print(f"Rebuilt {args.artifacts_dir}/rfes.md")


def main():
    parser = argparse.ArgumentParser(
        description="Artifact frontmatter CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # schema
    p_schema = subparsers.add_parser("schema",
                                     help="Show schema for a file type")
    p_schema.add_argument("schema_type",
                          choices=list(SCHEMAS.keys()),
                          help="Schema type to display")
    p_schema.set_defaults(func=cmd_schema)

    # read
    p_read = subparsers.add_parser("read",
                                   help="Read frontmatter from a file")
    p_read.add_argument("file", help="Path to the markdown file")
    p_read.add_argument("--schema-type", dest="schema_type",
                        choices=list(SCHEMAS.keys()),
                        help="Schema type (auto-detected from path if omitted)")
    p_read.set_defaults(func=cmd_read)

    # set
    p_set = subparsers.add_parser(
        "set", help="Set/update frontmatter fields")
    p_set.add_argument("file", help="Path to the markdown file")
    p_set.add_argument("fields", nargs="+",
                       help="Fields as field=value pairs. Use dot notation "
                            "for nested fields (e.g., scores.what=2)")
    p_set.add_argument("--schema-type", dest="schema_type",
                       choices=list(SCHEMAS.keys()),
                       help="Schema type (auto-detected from path if omitted)")
    p_set.set_defaults(func=cmd_set)

    # rebuild-index
    p_rebuild = subparsers.add_parser("rebuild-index",
                                     help="Rebuild rfes.md index")
    p_rebuild.add_argument("--artifacts-dir", default="artifacts",
                           help="Artifacts directory (default: artifacts)")
    p_rebuild.set_defaults(func=cmd_rebuild_index)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
