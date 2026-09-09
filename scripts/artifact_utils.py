"""Artifact schema definitions, frontmatter read/write/validate, and index rebuilding.

Owns all structured metadata for RFE artifacts. Scripts and skills use this
module instead of regex-parsing markdown prose.

Frontmatter is stored as YAML between --- delimiters at the top of markdown files.

Per-type facts — the id field's name and grammar, the artifact dirs, the score fields
and the extra schema fields — come from the work-item type registry
(``types/<name>/type.yaml`` through ``type_registry``). SCHEMAS and the scan / rename /
parse entry points are projections over it; the historical per-type names
(scan_task_files, rename_to_jira_key, parse_child_artifact and their initiative twins)
remain as thin wrappers over the generics that take a ``type_registry.Descriptor``.
"""

import copy
import os
import re
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry

_TYPES = type_registry.load()


def read_ids_file(path):
    """Read RFE IDs from a file (one per line), deduped, order-preserved.

    Mirrors the format written by `state.py write-ids`. Lets scripts accept
    an --ids-file argument instead of forcing skills to use $(...) command
    substitution, which triggers headless permission denials.
    """
    if not os.path.isfile(path):
        print(
            f"IDs file not found: {path} — was it persisted in a prior step?",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        with open(path) as f:
            ids = [line.strip() for line in f if line.strip()]
    except OSError as e:
        print(f"Could not read IDs file {path}: {e}", file=sys.stderr)
        sys.exit(1)
    return list(dict.fromkeys(ids))


def resolve_ids(positional, ids_file):
    """Combine positional IDs with --ids-file IDs, deduped, order-preserved.

    Positional IDs come first so explicit args take precedence in ordering.
    """
    combined = list(positional or [])
    if ids_file:
        combined.extend(read_ids_file(ids_file))
    return list(dict.fromkeys(combined))


# ─── Schema Definitions ────────────────────────────────────────────────────────

# Each schema is a dict of field_name -> field_spec.
# field_spec keys:
#   type:     "string" | "int" | "bool" | "dict"
#   required: bool (default False)
#   enum:     list of allowed values (optional)
#   pattern:  regex pattern the value must match (optional, strings only)
#   default:  default value when not provided (optional)
#   fields:   nested schema for type="dict" (optional)
#
# SCHEMAS holds "<type>-task" and "<type>-review" for every registered type, in registry
# order (rfe first): that key order is the `frontmatter.py schema` choices order. The base
# fields and the shared vocabularies below are the artifact contract, identical for every
# type; what differs per type is read from its descriptor — identity.id_field (the id
# field's NAME), identity.local_id_pattern + the tracker key_prefixes (its grammar),
# conventions.parent_key_patterns, schema.task.priority.enum, schema.task.extra_fields
# (rfe: size), schema.review.score_fields (scores / before_scores) and
# schema.review.extra_fields (initiative: alignment). tests/test_schemas_golden.py pins
# the derived dicts byte for byte to tests/data/schemas-golden.json.

_STATUS_ENUM = ["Draft", "Ready", "Submitted", "Archived"]
_RECOMMENDATION_ENUM = ["submit", "revise", "split", "reject", "autorevise_reject"]
_FEASIBILITY_ENUM = ["feasible", "infeasible", "indeterminate"]

# The descriptor fields a type must declare to get a task and a review schema. Both shipped
# types declare them all (validate_types.py gate 1 and tests/test_schemas_golden.py hold that).
# A drop-in root (RFE_CREATOR_EXTRA_TYPES, dev/test only) may register a partial descriptor,
# and the registry loader does not run the JSON-Schema gate — such a type is simply absent
# from SCHEMAS (and from frontmatter.py's path table) instead of breaking the import of every
# script that uses this module. The tolerance is for drop-ins only: a type under the
# registry's own root (types/) is built unconditionally, so an edit that drops one of these
# facts from a shipped descriptor fails this import with the loader's KeyError naming the
# field, instead of surfacing as "Unknown schema type" from the first read or write.
_SCHEMA_FACTS = (
    "identity.tracker",
    "identity.id_field",
    "identity.local_id_pattern",
    "conventions.parent_key_patterns",
    "schema.task.priority.enum",
    "schema.review.score_fields",
)

_SHIPPED_ROOT = _TYPES.root.resolve()


def _is_shipped(desc):
    """True when ``desc`` was loaded from the registry's own root rather than a drop-in root."""
    return desc.path is not None and desc.path.is_relative_to(_SHIPPED_ROOT)


def _declares_schemas(desc):
    """True when ``desc`` carries every field the task/review schemas are derived from. A
    shipped type always counts, so a missing fact fails at import (see _SCHEMA_FACTS)."""
    if _is_shipped(desc):
        return True
    return all(desc.get(dotted, None) is not None for dotted in _SCHEMA_FACTS)


def _id_pattern(desc):
    """The id field's grammar: the local draft id or a tracker key, e.g.
    ``^(RFE-\\d+|RHAIRFE-\\d+)$``. Prefixes are used verbatim (not re.escape'd), exactly as
    the hand-written literals were: upper-case letters plus the trailing dash, none of which
    is a regex metacharacter."""
    # Drop only the outer anchors: strip("^$") would also eat a pattern's own trailing
    # characters (e.g. a literal \$ before the closing anchor) and produce an invalid regex.
    alternatives = [desc.local_id_pattern.removeprefix("^").removesuffix("$")]
    alternatives += [prefix + r"\d+" for prefix in desc.key_prefixes]
    return "^(" + "|".join(alternatives) + ")$"


def _parent_key_pattern(desc):
    """``^(a|b|c)$`` over ``conventions.parent_key_patterns`` — joining reproduces today's
    literal for both shipped types (rfe: RFE-/RHAIRFE-; initiative: RHAISTRAT-/RHOAIENG-/
    INIT-, the task schema's wider list of PR-1 decision Q14; validate_batch_input.py's
    narrower batch pattern is reconciled in PR-3)."""
    return "^(" + "|".join(desc.get("conventions.parent_key_patterns")) + ")$"


def _id_fields(desc):
    """The identity fields every task and review schema opens with."""
    return {
        desc.id_field: {
            "type": "string",
            "required": True,
            "pattern": _id_pattern(desc),
        },
        # Pre-submission id, persisted at rename time. Renaming overwrites
        # the id field with the Jira key, and this is the only durable record of
        # which local draft a submitted item came from.
        "local_id": {
            "type": "string",
            "required": False,
            "pattern": desc.local_id_pattern,
            "default": None,
        },
        # Self-describing artifact fields (design-proposals/work-item-types-unified.md
        # §5). `type` names the work item type ("rfe", "initiative"); `tracker_ref`
        # is the canonical remote reference (e.g. "RHAIRFE-1595") whose grammar the
        # type's tracker binding owns. tracker_ref is read from frontmatter, never
        # re-derived from an id prefix. Both are declared WITHOUT a `default` key on
        # purpose (PR-1 decision Q4): apply_defaults writes only fields that carry
        # `default`, so existing artifacts stay byte-identical until a writer sets
        # them (PR-3 writes `type:`). No writer sets them yet.
        "type": {"type": "string", "required": False},
        "tracker_ref": {"type": "string", "required": False},
    }


def _extra_fields(desc, dotted):
    """The type's own field specs (``schema.task.extra_fields`` / ``schema.review.extra_fields``),
    deep-copied so SCHEMAS never aliases registry data. Spec key order is the descriptor's."""
    return {name: copy.deepcopy(spec) for name, spec in (desc.get(dotted, None) or {}).items()}


def _task_schema(desc):
    """The ``<type>-task`` schema. Field order: id, local_id, type, tracker_ref, title,
    priority, the type's extra fields (rfe: size), status, parent_key, original_labels."""
    schema = _id_fields(desc)
    schema["title"] = {"type": "string", "required": True}
    schema["priority"] = {
        "type": "string",
        "required": True,
        "enum": list(desc.get("schema.task.priority.enum")),
    }
    schema.update(_extra_fields(desc, "schema.task.extra_fields"))
    schema["status"] = {"type": "string", "required": True, "enum": list(_STATUS_ENUM)}
    schema["parent_key"] = {
        "type": "string",
        "required": False,
        "pattern": _parent_key_pattern(desc),
        "default": None,
    }
    schema["original_labels"] = {"type": "list", "required": False, "default": None}
    return schema


def _review_schema(desc):
    """The ``<type>-review`` schema. Field order: id, local_id, type, tracker_ref, the shared
    review fields (scores / before_scores nested over ``schema.review.score_fields``), then
    the type's extra fields appended last (initiative: alignment, whose default is the
    string "not_assessed" so an item with no RHAISTRAT parent reads the same here as in
    its alignment file)."""
    scores = {name: {"type": "int", "required": True} for name in desc.score_fields}
    schema = _id_fields(desc)
    schema.update(
        {
            "score": {"type": "int", "required": True},
            "pass": {"type": "bool", "required": True},
            "recommendation": {
                "type": "string",
                "required": True,
                "enum": list(_RECOMMENDATION_ENUM),
            },
            "feasibility": {"type": "string", "required": True, "enum": list(_FEASIBILITY_ENUM)},
            "auto_revised": {"type": "bool", "required": True, "default": False},
            "needs_attention": {"type": "bool", "required": True, "default": False},
            "scores": {"type": "dict", "required": True, "fields": scores},
            "error": {"type": "string", "required": False, "default": None},
            "before_score": {"type": "int", "required": False, "default": None},
            "needs_attention_reason": {"type": "string", "required": False, "default": None},
            "before_scores": {
                "type": "dict",
                "required": False,
                "default": None,
                "fields": copy.deepcopy(scores),
            },
        }
    )
    schema.update(_extra_fields(desc, "schema.review.extra_fields"))
    return schema


SCHEMAS = {
    f"{desc.name}-{kind}": build(desc)
    for desc in _TYPES
    if _declares_schemas(desc)
    for kind, build in (("task", _task_schema), ("review", _review_schema))
}


# ─── Validation ─────────────────────────────────────────────────────────────────


class ValidationError(Exception):
    """Raised when frontmatter fails schema validation."""

    pass


def _validate_field(name, value, spec, path=""):
    """Validate a single field against its spec. Returns list of errors."""
    errors = []
    full_name = f"{path}.{name}" if path else name

    if value is None:
        if spec.get("required", False) and "default" not in spec:
            errors.append(f"Missing required field: {full_name}")
        return errors

    expected_type = spec.get("type", "string")

    if expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{full_name}: expected string, got {type(value).__name__}")
            return errors
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"{full_name}: '{value}' not in {spec['enum']}")
        if "pattern" in spec and not re.match(spec["pattern"], value):
            errors.append(f"{full_name}: '{value}' does not match {spec['pattern']}")

    elif expected_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{full_name}: expected int, got {type(value).__name__}")

    elif expected_type == "bool":
        if not isinstance(value, bool):
            errors.append(f"{full_name}: expected bool, got {type(value).__name__}")

    elif expected_type == "list":
        if not isinstance(value, list):
            errors.append(f"{full_name}: expected list, got {type(value).__name__}")

    elif expected_type == "dict":
        if not isinstance(value, dict):
            errors.append(f"{full_name}: expected dict, got {type(value).__name__}")
            return errors
        nested_schema = spec.get("fields", {})
        # Check for unknown fields in nested dict
        for key in value:
            if key not in nested_schema:
                errors.append(f"{full_name}: unknown field '{key}'")
        # Validate nested fields
        for field_name, field_spec in nested_schema.items():
            errors.extend(_validate_field(field_name, value.get(field_name), field_spec, full_name))

    return errors


def validate(data, schema_type):
    """Validate frontmatter data against a schema.

    Args:
        data: dict of frontmatter fields
        schema_type: a key of SCHEMAS ("rfe-task", "rfe-review", ...)

    Returns:
        list of error strings (empty if valid)

    Raises:
        ValueError: if schema_type is unknown
    """
    if schema_type not in SCHEMAS:
        raise ValueError(f"Unknown schema type: {schema_type}. Valid types: {list(SCHEMAS.keys())}")

    schema = SCHEMAS[schema_type]
    errors = []

    # Check for unknown top-level fields
    for key in data:
        if key not in schema:
            errors.append(f"Unknown field: {key}")

    # Validate each defined field
    for field_name, field_spec in schema.items():
        errors.extend(_validate_field(field_name, data.get(field_name), field_spec))

    return errors


def apply_defaults(data, schema_type):
    """Apply default values for missing optional fields.

    Modifies data in-place and returns it.
    """
    schema = SCHEMAS[schema_type]
    for field_name, field_spec in schema.items():
        if field_name not in data and "default" in field_spec:
            data[field_name] = field_spec["default"]
        if field_spec.get("type") == "dict" and field_name in data:
            nested = data[field_name]
            if isinstance(nested, dict):
                for nested_name, nested_spec in field_spec.get("fields", {}).items():
                    if nested_name not in nested and "default" in nested_spec:
                        nested[nested_name] = nested_spec["default"]
    return data


def get_schema_yaml(schema_type):
    """Return the schema definition as a YAML string for display."""
    if schema_type not in SCHEMAS:
        raise ValueError(f"Unknown schema type: {schema_type}. Valid types: {list(SCHEMAS.keys())}")

    schema = SCHEMAS[schema_type]
    output = {"required": {}, "optional": {}}

    for name, spec in schema.items():
        entry = {"type": spec["type"]}
        if "enum" in spec:
            entry["enum"] = spec["enum"]
        if "pattern" in spec:
            entry["pattern"] = spec["pattern"]
        if "default" in spec:
            entry["default"] = spec["default"]
        if spec.get("type") == "dict" and "fields" in spec:
            entry["fields"] = {}
            for fname, fspec in spec["fields"].items():
                fentry = {"type": fspec["type"]}
                if "enum" in fspec:
                    fentry["enum"] = fspec["enum"]
                entry["fields"][fname] = fentry

        if spec.get("required", False):
            output["required"][name] = entry
        else:
            output["optional"][name] = entry

    return yaml.dump(output, default_flow_style=False, sort_keys=False)


# ─── Frontmatter Read/Write ────────────────────────────────────────────────────

# The closing delimiter must be anchored to the start of a line, and the block
# body must be allowed to be empty. `(.*?\n)` requires at least one newline, so
# on an empty block (`---\n---\n`) it consumes the closing delimiter and keeps
# expanding to the next `---` in the body — swallowing markdown horizontal rules
# and score tables into the YAML string. `[ \t]*` rather than `\s*` so a blank
# line after the closing delimiter stays in the body.
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*\r?\n", re.DOTALL | re.MULTILINE)


def _yaml_error_message(path, yaml_str, exc):
    """Describe a frontmatter parse failure in one actionable line.

    PyYAML's own traceback is unreadable in agent logs. Lead with the file and
    the offending line so the message survives log truncation, and say how to
    avoid it — the usual cause is a hand-written block whose free-text value
    contains an unquoted ':'.
    """
    problem = getattr(exc, "problem", None) or str(exc)
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return f"Invalid YAML frontmatter in {path}: {problem}"

    lines = yaml_str.splitlines()
    source = lines[mark.line].strip() if 0 <= mark.line < len(lines) else ""
    near = f" near {source!r}" if source else ""
    return (
        f"Invalid YAML frontmatter in {path}, line {mark.line + 1}{near} — {problem}. "
        f"Set frontmatter with scripts/frontmatter.py rather than by hand; "
        f"values containing ':' must be quoted."
    )


def _looks_like_frontmatter_block(text):
    """Does `text` (the region between two `---` lines) hold real frontmatter,
    rather than body prose a stray `---` rule was mistaken for?

    Valid frontmatter parses cleanly to a mapping, so that is the fast path (and
    covers list- or nested-map values). The repair path must also recognise a
    *malformed* mapping — the unquoted-colon corruption this feature targets — so
    a fallback accepts a contiguous run of `key: value` lines. Anything else
    means the matched `---` is almost certainly a body horizontal rule, so the
    region is body and must be preserved, not stripped:

    - a blank line (frontmatter this tool writes has none),
    - a top-level `- ` sequence item not under a key (never valid frontmatter for
      these schemas, and the mixed map+sequence shape below is invalid YAML),
    - a prose line or a markdown heading (`#` is a YAML comment).
    """
    try:
        if isinstance(yaml.safe_load(text), dict):
            return True
    except yaml.YAMLError:
        pass  # fall through to the malformed-but-recoverable mapping check
    saw_key = False
    for line in text.splitlines():
        if not line.strip():
            return False  # blank line — we have run past the real block into body
        if line[0] in " \t":
            continue  # indented continuation of a value
        if re.match(r"[^\s:#][^:]*:(\s|$)", line):
            saw_key = True
            continue
        return False  # prose, a markdown heading, or a top-level "- " list item
    return saw_key


def _body_without_frontmatter(path):
    """Return the markdown body, ignoring a leading frontmatter block.

    The delimiters are matched by regex, so the body is recoverable even when the
    YAML between them does not parse. But a lone `---` horizontal rule in the body
    can be mistaken for the closing delimiter, so the leading block is stripped
    only when it is empty or actually looks like a YAML mapping. Otherwise the
    whole content is returned unchanged — better to leave a stale block stacked in
    the body than to silently drop real content sitting above a body rule.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content
    region = match.group(1)
    if region.strip() == "" or _looks_like_frontmatter_block(region):
        return content[match.end() :]
    return content


def read_frontmatter(path):
    """Read and parse YAML frontmatter from a markdown file.

    Returns:
        (data_dict, body_string) — frontmatter as dict, remainder as string.
        Returns ({}, full_content) if no frontmatter found. A genuinely empty
        block (`---\\n---\\n`) is valid frontmatter with no fields, so it returns
        ({}, body) with the delimiters consumed — otherwise write_frontmatter
        would leave a stale block sitting in the body. A block that is present
        but not a mapping (a bare scalar, or comment/heading-only content that
        parses to null) returns ({}, full_content): the closing `---` we matched
        may be a body horizontal rule, so the safe choice is to treat the whole
        file as body rather than drop the region above the delimiter.

    Raises:
        ValidationError: if the frontmatter block is present but unparseable.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_str = match.group(1)
    body = content[match.end() :]

    if yaml_str.strip() == "":
        # A genuinely empty block is valid frontmatter with no fields. Consume
        # the delimiters so a later write replaces it rather than stacking a
        # second block on top. Only a *blank* block is consumed: content that
        # merely parses to null is not — a markdown heading is a YAML comment, so
        # `---\n## Heading\n---\nbody` also yields None, and the closing `---`
        # there may be a body horizontal rule. Consuming up to it would silently
        # drop the lines above.
        return {}, body

    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise ValidationError(_yaml_error_message(path, yaml_str, exc)) from exc
    if not isinstance(data, dict):
        # Present but not a mapping (a bare scalar/list, or comment-only content
        # that parsed to None). Preserve the whole file — see the docstring.
        return {}, content

    _migrate_fields(data)
    return data, body


_FIELD_MIGRATIONS = {
    "revised": "auto_revised",
}


def _migrate_fields(data, schema_type=None):
    """Rename deprecated frontmatter fields to current names."""
    for old, new in _FIELD_MIGRATIONS.items():
        if old in data and new not in data:
            data[new] = data.pop(old)


def read_frontmatter_validated(path, schema_type):
    """Read frontmatter and validate against schema.

    Returns:
        (data_dict, body_string)

    Raises:
        ValidationError: if frontmatter fails validation
        FileNotFoundError: if file doesn't exist
    """
    data, body = read_frontmatter(path)
    if not data:
        raise ValidationError(f"No frontmatter found in {path}")

    _migrate_fields(data, schema_type)
    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed in {path}:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return data, body


def write_frontmatter(path, data, schema_type):
    """Write/update YAML frontmatter on a markdown file.

    Validates data against the schema before writing. Preserves the
    markdown body below the frontmatter. Creates the file if it doesn't
    exist (with empty body).

    Args:
        path: file path
        data: dict of frontmatter fields
        schema_type: a key of SCHEMAS ("rfe-task", "rfe-review", ...)

    Raises:
        ValidationError: if data fails schema validation
    """
    _migrate_fields(data, schema_type)
    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            "Frontmatter validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        )

    # Read existing body if file exists. The data above is already validated
    # and complete, so an unparseable existing block is simply overwritten.
    body = ""
    if os.path.exists(path):
        body = _body_without_frontmatter(path)

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    content = f"---\n{yaml_str}---\n{body}"

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def update_frontmatter(path, updates, schema_type):
    """Merge updates into existing frontmatter and rewrite.

    Reads existing frontmatter, merges updates (overwriting on conflict),
    validates, and writes back.

    Args:
        path: file path (must exist)
        updates: dict of fields to add/update
        schema_type: schema to validate against

    If the existing frontmatter block is unparseable, it is replaced rather
    than merged. This is the only repair path available: every writer goes
    through here, so refusing would leave the caller no way to fix the file
    except hand-editing YAML — which is what corrupts it in the first place.
    The validation below guards the *fields*: a partial update that does not
    amount to a complete, valid record still fails. Body content is never
    dropped — _body_without_frontmatter strips only a leading block it can
    confidently identify as frontmatter and otherwise preserves the whole file.

    Raises:
        ValidationError: if merged data fails validation
        FileNotFoundError: if file doesn't exist
    """
    try:
        data, body = read_frontmatter(path)
    except ValidationError as exc:
        print(f"Warning: replacing unparseable frontmatter — {exc}", file=sys.stderr)
        data, body = {}, _body_without_frontmatter(path)

    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value

    _migrate_fields(data, schema_type)
    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed after update in {path}:\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    content = f"---\n{yaml_str}---\n{body}"

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ─── Artifact File Discovery ───────────────────────────────────────────────────


def _is_companion_file(filename):
    """Check if a filename is a companion file (comments, removed-context)."""
    return filename.endswith(("-comments.md", "-removed-context.md")) or filename.endswith(
        "-removed-context.yaml"
    )


def _type_for(identifier):
    """The descriptor that owns ``identifier`` (``TypeRegistry.detect``), else rfe — the
    default branch of the prefix sniffs this replaces (anything that was not INIT-/RHOAIENG-
    went to the rfe dirs)."""
    return _TYPES.detect(identifier) or _TYPES.get("rfe")


def find_task_file_including_archived(
    artifacts_dir, identifier, tasks_subdir, jira_prefix, local_prefix
):
    """Find a task file by ID, including archived tasks."""
    tasks_dir = os.path.join(artifacts_dir, tasks_subdir)
    if not os.path.isdir(tasks_dir):
        return None

    for filename in sorted(os.listdir(tasks_dir)):
        if not filename.endswith(".md"):
            continue
        if _is_companion_file(filename):
            continue

        if identifier.startswith(jira_prefix):
            if filename == f"{identifier}.md":
                return os.path.join(tasks_dir, filename)

        if identifier.startswith(local_prefix):
            if filename == f"{identifier}.md" or filename.startswith(identifier + "-"):
                return os.path.join(tasks_dir, filename)

    return None


def find_artifact_file_including_archived(artifacts_dir, identifier):
    """Find an RFE task file by ID, including archived tasks (rfe-only wrapper)."""
    rfe = _TYPES.get("rfe")
    return find_task_file_including_archived(
        artifacts_dir, identifier, rfe.dirs("bare")["tasks"], rfe.write_prefix, rfe.local_prefix
    )


def find_removed_context_yaml_in(
    artifacts_dir, identifier, tasks_subdir, jira_prefix, local_prefix
):
    """Find a removed-context YAML file by ID in the given tasks subdirectory."""
    tasks_dir = os.path.join(artifacts_dir, tasks_subdir)
    if not os.path.isdir(tasks_dir):
        return None
    if identifier.startswith(jira_prefix) or identifier.startswith(local_prefix):
        path = os.path.join(tasks_dir, f"{identifier}-removed-context.yaml")
        if os.path.isfile(path):
            return path
    return None


def find_removed_context_yaml(artifacts_dir, identifier):
    """Find the removed-context YAML file for a given RFE/initiative ID or Jira key."""
    desc = _type_for(identifier)
    return find_removed_context_yaml_in(
        artifacts_dir, identifier, desc.dirs("bare")["tasks"], desc.write_prefix, desc.local_prefix
    )


def render_removed_context_comment(yaml_path, preamble):
    """Read removed-context YAML and render postable blocks as markdown.

    Posts blocks with type 'genuine' or 'unclassified' (safety fallback).
    Returns empty string if no blocks qualify.
    """
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "blocks" not in data:
        return ""
    postable_types = {"genuine", "unclassified"}
    sections = []
    for block in data["blocks"]:
        if block.get("type", "unclassified") in postable_types:
            heading = block.get("heading", "")
            content = block.get("content", "")
            sections.append(f"## {heading}\n{content}")
    if not sections:
        return ""
    return preamble + "\n\n" + "\n\n".join(sections)


def find_review_file(artifacts_dir, identifier):
    """Find the review file for a given RFE/initiative ID or Jira key.

    Looks in the reviews directory of the type that owns ``identifier`` (rfe when no
    type does) for {identifier}-review.md.
    """
    reviews_dir = os.path.join(artifacts_dir, _type_for(identifier).dirs("bare")["reviews"])

    if not os.path.isdir(reviews_dir):
        return None

    target = f"{identifier}-review.md"
    review_path = os.path.join(reviews_dir, target)
    if os.path.exists(review_path):
        return review_path

    return None


def _scan_dir(directory, schema_type, accept):
    """(path, validated frontmatter) for every file in ``directory`` that ``accept`` admits,
    in sorted filename order; files without valid frontmatter are skipped with a warning."""
    if not os.path.isdir(directory):
        return []

    results = []
    for filename in sorted(os.listdir(directory)):
        if not accept(filename):
            continue

        path = os.path.join(directory, filename)
        try:
            data, _ = read_frontmatter_validated(path, schema_type)
            results.append((path, data))
        except (ValidationError, Exception) as e:
            print(f"Warning: skipping {filename}: {e}", file=sys.stderr)

    return results


def scan_tasks(artifacts_dir, desc):
    """Scan the task files of one work-item type and return their frontmatter.

    Args:
        artifacts_dir: path to artifacts directory
        desc: a ``type_registry.Descriptor``; its ``dirs.tasks`` is scanned and every
            ``.md`` that is not a companion file is validated against ``<type>-task``.

    Returns:
        list of (path, frontmatter_dict) tuples, sorted by the type's id field.
        Files without valid frontmatter are skipped with a warning.
    """
    tasks_dir = os.path.join(artifacts_dir, desc.dirs("bare")["tasks"])
    results = _scan_dir(
        tasks_dir,
        f"{desc.name}-task",
        lambda filename: filename.endswith(".md") and not _is_companion_file(filename),
    )
    id_field = desc.id_field
    return sorted(results, key=lambda x: x[1].get(id_field, ""))


def scan_task_files(artifacts_dir):
    """Scan all RFE task files — ``scan_tasks`` for the rfe type (sorted by rfe_id)."""
    return scan_tasks(artifacts_dir, _TYPES.get("rfe"))


def scan_initiative_task_files(artifacts_dir):
    """Scan all Initiative task files — ``scan_tasks`` for the initiative type (sorted by
    initiative_id)."""
    return scan_tasks(artifacts_dir, _TYPES.get("initiative"))


def scan_reviews(artifacts_dir, desc):
    """Scan the ``*-review.md`` files of one work-item type and return their frontmatter.

    Returns:
        list of (path, frontmatter_dict) tuples in filename order.
        Files without valid frontmatter are skipped with a warning.
    """
    reviews_dir = os.path.join(artifacts_dir, desc.dirs("bare")["reviews"])
    return _scan_dir(
        reviews_dir, f"{desc.name}-review", lambda filename: filename.endswith("-review.md")
    )


def scan_review_files(artifacts_dir):
    """Scan all RFE review files — ``scan_reviews`` for the rfe type (rebuild_index's input)."""
    return scan_reviews(artifacts_dir, _TYPES.get("rfe"))


# ─── File Renaming (post-submit) ───────────────────────────────────────────────


def _rename_error_label(desc):
    """The prefix of the rename ValueError text: the per-type function name the message has
    always carried — "rename_to_jira_key" for rfe, "rename_initiative_to_jira_key" for
    initiative. A grandfathered, name-keyed projection (like pipeline.poll_prefix being ''
    for rfe): the rfe name has no type infix, every other type gets rename_<type>_to_jira_key.
    """
    return "rename_to_jira_key" if desc.name == "rfe" else f"rename_{desc.name}_to_jira_key"


def _review_to_rename(reviews_dir, item_id, desc):
    """The review file ``rename_to_tracker_key`` renames, or None.

    rfe tolerates legacy slug-suffixed names (``RFE-001-slug-review.md``): the first
    ``{item_id}-*-review.md`` in directory order is taken, as rename_to_jira_key always
    did. Every other type renames exactly ``{item_id}-review.md``, as
    rename_initiative_to_jira_key always did. The tolerance is not a descriptor fact, so it
    stays keyed on the type name until the PR-3 resolution ladder (frontmatter ``type:`` on
    every artifact) decides whether legacy names are honoured for every type or retired.
    """
    if desc.name == "rfe":
        for filename in list(os.listdir(reviews_dir)):
            if filename.startswith(item_id + "-") and filename.endswith("-review.md"):
                return os.path.join(reviews_dir, filename)
        return None
    old_review = os.path.join(reviews_dir, f"{item_id}-review.md")
    return old_review if os.path.isfile(old_review) else None


def rename_to_tracker_key(artifacts_dir, item_id, tracker_key, desc):
    """Rename a submitted item's files from its local id to its tracker key.

    ``RFE-NNN.md`` -> ``RHAIRFE-NNNN.md`` (``INIT-NNN.md`` -> ``RHOAIENG-NNNN.md``): the
    task file, its companion files and the review file under the type's ``dirs``. The
    task file's frontmatter becomes ``{<id_field>: tracker_key, status: Submitted,
    local_id: item_id}`` and the review file's ``{<id_field>: tracker_key, local_id:
    item_id}``.

    Args:
        artifacts_dir: path to artifacts directory
        item_id: e.g. "RFE-001" — must match ``identity.local_id_pattern``
        tracker_key: e.g. "RHAIRFE-1600" — must be the type's write prefix + digits
        desc: a ``type_registry.Descriptor``
    """
    # Both ids become path components below. item_id comes from validated
    # frontmatter, but tracker_key arrives from a Jira API response — reject
    # anything that is not the documented shape before touching the fs.
    # The key is checked against the WRITE prefix (key_prefixes[0]) alone, not the
    # key_prefixes union design §10 item 2 names for the forked pairs' guards: the key an
    # item is renamed TO is always the one its tracker binding creates under. Identical
    # today (both shipped types declare a single prefix); PR-3, where key_prefixes gains
    # read prefixes, decides whether a rename target may carry one of those.
    label = _rename_error_label(desc)
    if not re.fullmatch(desc.local_id_pattern, item_id):
        raise ValueError(f"{label}: invalid local id {item_id!r}")
    if not re.fullmatch(desc.write_prefix + r"\d+", tracker_key):
        raise ValueError(f"{label}: invalid Jira key {tracker_key!r}")

    dirs = desc.dirs("bare")
    tasks_dir = os.path.join(artifacts_dir, dirs["tasks"])
    reviews_dir = os.path.join(artifacts_dir, dirs["reviews"])
    id_field = desc.id_field

    # Rename task file and companions
    if os.path.isdir(tasks_dir):
        for filename in list(os.listdir(tasks_dir)):
            if not (filename == f"{item_id}.md" or filename.startswith(item_id + "-")):
                continue
            if not (filename.endswith(".md") or filename.endswith(".yaml")):
                continue

            old_path = os.path.join(tasks_dir, filename)

            # Companions must be matched before the fallback: anything that
            # reaches the else branch is renamed to {tracker_key}.md, which is
            # also the main task file's new name, so an unhandled companion
            # silently overwrites it. The suffix map is type-agnostic: a
            # -comments.md is renamed for every type (companions.comments only
            # says whether the fetch step produces one).
            if filename.endswith("-comments.md"):
                new_name = f"{tracker_key}-comments.md"
            elif filename.endswith("-removed-context.yaml"):
                new_name = f"{tracker_key}-removed-context.yaml"
            elif filename.endswith("-removed-context.md"):
                new_name = f"{tracker_key}-removed-context.md"
            else:
                new_name = f"{tracker_key}.md"

            new_path = os.path.join(tasks_dir, new_name)
            os.rename(old_path, new_path)

            # Update frontmatter on main task file
            if new_name == f"{tracker_key}.md":
                update_frontmatter(
                    new_path,
                    {id_field: tracker_key, "status": "Submitted", "local_id": item_id},
                    f"{desc.name}-task",
                )

    # Rename review file
    if os.path.isdir(reviews_dir):
        old_review = _review_to_rename(reviews_dir, item_id, desc)
        if old_review is not None:
            new_review = os.path.join(reviews_dir, f"{tracker_key}-review.md")
            os.rename(old_review, new_review)
            update_frontmatter(
                new_review, {id_field: tracker_key, "local_id": item_id}, f"{desc.name}-review"
            )


def rename_to_jira_key(artifacts_dir, rfe_id, jira_key):
    """Rename RFE-NNN.md files to RHAIRFE-NNNN.md after submission —
    ``rename_to_tracker_key`` for the rfe type.

    Args:
        artifacts_dir: path to artifacts directory
        rfe_id: e.g. "RFE-001"
        jira_key: e.g. "RHAIRFE-1600"
    """
    rename_to_tracker_key(artifacts_dir, rfe_id, jira_key, _TYPES.get("rfe"))


def rename_initiative_to_jira_key(artifacts_dir, initiative_id, jira_key):
    """Rename INIT-NNN.md files to RHOAIENG-NNNN.md after submission —
    ``rename_to_tracker_key`` for the initiative type."""
    rename_to_tracker_key(artifacts_dir, initiative_id, jira_key, _TYPES.get("initiative"))


# ─── Index Rebuilding ───────────────────────────────────────────────────────────


def rebuild_index(artifacts_dir):
    """Rebuild artifacts/rfes.md from frontmatter across task and review files.

    The index is the rfe type's (``index.enabled``; the initiative descriptor has none):
    scans rfe-tasks/ for task metadata and rfe-reviews/ for review scores and generates
    a summary table. The Size column is the rfe task schema's extra field.

    Returns:
        The generated markdown string.
    """
    id_field = _TYPES.get("rfe").id_field
    tasks = scan_task_files(artifacts_dir)
    reviews = scan_review_files(artifacts_dir)

    # Build review lookup by id
    review_by_id = {}
    for _, review_data in reviews:
        review_by_id[review_data[id_field]] = review_data

    lines = [
        "# RFE Summary",
        "",
        "| ID | Title | Priority | Size | Score | Rec | Status |",
        "|-----|-------|----------|------|-------|-----|--------|",
    ]

    for _, task_data in tasks:
        rfe_id = task_data[id_field]
        title = task_data.get("title", "Untitled")
        priority = task_data.get("priority", "—")
        size = task_data.get("size") or "—"
        status = task_data.get("status", "—")

        review = review_by_id.get(rfe_id)
        if review:
            score = f"{review['score']}/10"
            rec = review["recommendation"]
        else:
            score = "—"
            rec = "—"

        # Strikethrough archived entries
        if status == "Archived":
            lines.append(
                f"| ~~{rfe_id}~~ | ~~{title}~~ "
                f"| ~~{priority}~~ | ~~{size}~~ | ~~{score}~~ "
                f"| ~~{rec}~~ | {status} |"
            )
        else:
            lines.append(
                f"| {rfe_id} | {title} | {priority} | {size} | {score} | {rec} | {status} |"
            )

    content = "\n".join(lines) + "\n"

    rfes_path = os.path.join(artifacts_dir, "rfes.md")
    with open(rfes_path, "w", encoding="utf-8") as f:
        f.write(content)

    return content


# ─── Legacy Compatibility ──────────────────────────────────────────────────────


def parse_child(path, desc):
    """Parse a child item markdown file of one work-item type.

    Returns: (title, priority, full_markdown, cleaned_markdown)
    - full_markdown: original content (for archival comment)
    - cleaned_markdown: metadata stripped (for Jira description)

    Title and priority are read from frontmatter. rfe alone keeps the pre-frontmatter
    markdown fallbacks parse_child_artifact always had: a ``# RFE-NNN: Title`` heading
    (built from ``identity.local_prefix``) when the title is missing or empty, a
    ``**Priority**: X`` line when the priority is, else "Untitled" / "Normal". Every other
    type reads ``data.get("title", "Untitled")`` / ``data.get("priority", "Normal")`` as
    parse_child_initiative always did. The fallback is not a descriptor fact, so it stays
    keyed on the type name until the PR-3 resolution ladder (frontmatter ``type:`` on
    every artifact) decides whether legacy markdown children are parsed for every type or
    the fallback is retired.
    """
    from jira_utils import strip_metadata

    with open(path, encoding="utf-8") as f:
        content = f.read()

    data, _ = read_frontmatter(path)

    if desc.name == "rfe":
        if data.get("title"):
            title = data["title"]
        else:
            heading = r"^#\s+" + desc.local_prefix + r"\d+:\s+(.+)$"
            title_match = re.match(heading, content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else "Untitled"

        if data.get("priority"):
            priority = data["priority"]
        else:
            priority_match = re.search(r"^\*\*Priority\*\*:\s*(.+)$", content, re.MULTILINE)
            priority = priority_match.group(1).strip() if priority_match else "Normal"
    else:
        title = data.get("title", "Untitled")
        priority = data.get("priority", "Normal")

    cleaned = strip_metadata(content)
    return title, priority, content, cleaned


def parse_child_artifact(path):
    """Parse a child RFE markdown file — ``parse_child`` for the rfe type.

    Returns: (title, priority, full_markdown, cleaned_markdown)
    """
    return parse_child(path, _TYPES.get("rfe"))


def parse_child_initiative(path):
    """Parse a child Initiative markdown file — ``parse_child`` for the initiative type.

    Returns: (title, priority, full_markdown, cleaned_markdown)
    """
    return parse_child(path, _TYPES.get("initiative"))
