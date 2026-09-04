"""Artifact schema definitions, frontmatter read/write/validate, and index rebuilding.

Owns all structured metadata for RFE artifacts. Scripts and skills use this
module instead of regex-parsing markdown prose.

Frontmatter is stored as YAML between --- delimiters at the top of markdown files.
"""

import os
import re
import sys

import yaml


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

SCHEMAS = {
    "rfe-task": {
        "rfe_id": {
            "type": "string",
            "required": True,
            "pattern": r"^(RFE-\d+|RHAIRFE-\d+)$",
        },
        # Pre-submission id, persisted at rename time. Renaming overwrites
        # rfe_id with the Jira key, and this is the only durable record of
        # which local draft a submitted item came from.
        "local_id": {
            "type": "string",
            "required": False,
            "pattern": r"^RFE-\d+$",
            "default": None,
        },
        "title": {
            "type": "string",
            "required": True,
        },
        "priority": {
            "type": "string",
            "required": True,
            "enum": ["Blocker", "Critical", "Major", "Normal", "Minor", "Undefined"],
        },
        "size": {
            "type": "string",
            "required": False,
            "enum": ["S", "M", "L", "XL"],
            "default": None,
        },
        "status": {
            "type": "string",
            "required": True,
            "enum": ["Draft", "Ready", "Submitted", "Archived"],
        },
        "parent_key": {
            "type": "string",
            "required": False,
            "pattern": r"^(RFE-\d+|RHAIRFE-\d+)$",
            "default": None,
        },
        "original_labels": {
            "type": "list",
            "required": False,
            "default": None,
        },
    },
    "rfe-review": {
        "rfe_id": {
            "type": "string",
            "required": True,
            "pattern": r"^(RFE-\d+|RHAIRFE-\d+)$",
        },
        # Pre-submission id, persisted at rename time. Renaming overwrites
        # rfe_id with the Jira key, and this is the only durable record of
        # which local draft a submitted item came from.
        "local_id": {
            "type": "string",
            "required": False,
            "pattern": r"^RFE-\d+$",
            "default": None,
        },
        "score": {
            "type": "int",
            "required": True,
        },
        "pass": {
            "type": "bool",
            "required": True,
        },
        "recommendation": {
            "type": "string",
            "required": True,
            "enum": ["submit", "revise", "split", "reject", "autorevise_reject"],
        },
        "feasibility": {
            "type": "string",
            "required": True,
            "enum": ["feasible", "infeasible", "indeterminate"],
        },
        "auto_revised": {
            "type": "bool",
            "required": True,
            "default": False,
        },
        "needs_attention": {
            "type": "bool",
            "required": True,
            "default": False,
        },
        "scores": {
            "type": "dict",
            "required": True,
            "fields": {
                "what": {"type": "int", "required": True},
                "why": {"type": "int", "required": True},
                "open_to_how": {"type": "int", "required": True},
                "not_a_task": {"type": "int", "required": True},
                "right_sized": {"type": "int", "required": True},
            },
        },
        "error": {
            "type": "string",
            "required": False,
            "default": None,
        },
        "before_score": {
            "type": "int",
            "required": False,
            "default": None,
        },
        "needs_attention_reason": {
            "type": "string",
            "required": False,
            "default": None,
        },
        "before_scores": {
            "type": "dict",
            "required": False,
            "default": None,
            "fields": {
                "what": {"type": "int", "required": True},
                "why": {"type": "int", "required": True},
                "open_to_how": {"type": "int", "required": True},
                "not_a_task": {"type": "int", "required": True},
                "right_sized": {"type": "int", "required": True},
            },
        },
    },
    "initiative-task": {
        "initiative_id": {
            "type": "string",
            "required": True,
            "pattern": r"^(INIT-\d+|RHOAIENG-\d+)$",
        },
        "local_id": {
            "type": "string",
            "required": False,
            "pattern": r"^INIT-\d+$",
            "default": None,
        },
        "title": {
            "type": "string",
            "required": True,
        },
        "priority": {
            "type": "string",
            "required": True,
            "enum": ["Blocker", "Critical", "Major", "Normal", "Minor", "Undefined"],
        },
        "status": {
            "type": "string",
            "required": True,
            "enum": ["Draft", "Ready", "Submitted", "Archived"],
        },
        "parent_key": {
            "type": "string",
            "required": False,
            "pattern": r"^(RHAISTRAT-\d+|RHOAIENG-\d+|INIT-\d+)$",
            "default": None,
        },
        "original_labels": {
            "type": "list",
            "required": False,
            "default": None,
        },
    },
    "initiative-review": {
        "initiative_id": {
            "type": "string",
            "required": True,
            "pattern": r"^(INIT-\d+|RHOAIENG-\d+)$",
        },
        "local_id": {
            "type": "string",
            "required": False,
            "pattern": r"^INIT-\d+$",
            "default": None,
        },
        "score": {
            "type": "int",
            "required": True,
        },
        "pass": {
            "type": "bool",
            "required": True,
        },
        "recommendation": {
            "type": "string",
            "required": True,
            "enum": ["submit", "revise", "split", "reject", "autorevise_reject"],
        },
        "feasibility": {
            "type": "string",
            "required": True,
            "enum": ["feasible", "infeasible", "indeterminate"],
        },
        "auto_revised": {
            "type": "bool",
            "required": True,
            "default": False,
        },
        "needs_attention": {
            "type": "bool",
            "required": True,
            "default": False,
        },
        "scores": {
            "type": "dict",
            "required": True,
            "fields": {
                "what": {"type": "int", "required": True},
                "why": {"type": "int", "required": True},
                "scope": {"type": "int", "required": True},
                "open_to_how": {"type": "int", "required": True},
                "right_sized": {"type": "int", "required": True},
            },
        },
        "error": {
            "type": "string",
            "required": False,
            "default": None,
        },
        "before_score": {
            "type": "int",
            "required": False,
            "default": None,
        },
        "needs_attention_reason": {
            "type": "string",
            "required": False,
            "default": None,
        },
        "before_scores": {
            "type": "dict",
            "required": False,
            "default": None,
            "fields": {
                "what": {"type": "int", "required": True},
                "why": {"type": "int", "required": True},
                "scope": {"type": "int", "required": True},
                "open_to_how": {"type": "int", "required": True},
                "right_sized": {"type": "int", "required": True},
            },
        },
        "alignment": {
            "type": "string",
            "required": False,
            "enum": ["strong", "partial", "weak", "not_assessed"],
            # Defaults to not_assessed, not null, so an Initiative with no
            # RHAISTRAT parent reads the same here as in its alignment file.
            "default": "not_assessed",
        },
    },
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
        schema_type: one of "rfe-task", "rfe-review"

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
        schema_type: one of "rfe-task", "rfe-review"

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


def find_artifact_file(artifacts_dir, identifier):
    """Find the main artifact file for a given RFE ID or Jira key.

    Matches:
    - RFE-NNN.md (local pre-submission)
    - RHAIRFE-NNNN.md (Jira-keyed)

    Excludes companion files (-comments.md, -removed-context.md).
    Excludes archived artifacts (status: Archived in frontmatter).

    Args:
        artifacts_dir: path to artifacts directory
        identifier: RFE-NNN or RHAIRFE-NNNN

    Returns:
        Full path to artifact file, or None if not found.
    """
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    if not os.path.isdir(tasks_dir):
        return None

    for filename in sorted(os.listdir(tasks_dir)):
        if not filename.endswith(".md"):
            continue
        if _is_companion_file(filename):
            continue

        # Match by Jira key (exact: RHAIRFE-1595.md)
        if identifier.startswith("RHAIRFE-"):
            if filename == f"{identifier}.md":
                path = os.path.join(tasks_dir, filename)
                # Check if archived
                data, _ = read_frontmatter(path)
                if data.get("status") == "Archived":
                    continue
                return path

        # Match by local RFE ID (exact: RFE-001.md, legacy: RFE-001-slug.md)
        if identifier.startswith("RFE-"):
            if filename == f"{identifier}.md" or filename.startswith(identifier + "-"):
                path = os.path.join(tasks_dir, filename)
                data, _ = read_frontmatter(path)
                if data.get("status") == "Archived":
                    continue
                return path

    return None


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
    """Like find_artifact_file but includes archived artifacts (RFE-only)."""
    return find_task_file_including_archived(
        artifacts_dir, identifier, "rfe-tasks", "RHAIRFE-", "RFE-"
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
    if identifier.startswith("INIT-") or identifier.startswith("RHOAIENG-"):
        return find_removed_context_yaml_in(
            artifacts_dir, identifier, "initiatives", "RHOAIENG-", "INIT-"
        )
    return find_removed_context_yaml_in(artifacts_dir, identifier, "rfe-tasks", "RHAIRFE-", "RFE-")


def find_removed_context_file(artifacts_dir, identifier):
    """Find the removed-context file for a given RFE ID or Jira key."""
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    if not os.path.isdir(tasks_dir):
        return None

    for filename in sorted(os.listdir(tasks_dir)):
        if not filename.endswith("-removed-context.md"):
            continue

        if identifier.startswith("RHAIRFE-"):
            if filename == f"{identifier}-removed-context.md":
                return os.path.join(tasks_dir, filename)

        if identifier.startswith("RFE-"):
            if filename == f"{identifier}-removed-context.md":
                return os.path.join(tasks_dir, filename)

    return None


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

    Looks in the appropriate reviews directory for {identifier}-review.md.
    """
    if identifier.startswith("RHOAIENG-") or identifier.startswith("INIT-"):
        reviews_dir = os.path.join(artifacts_dir, "initiative-reviews")
    else:
        reviews_dir = os.path.join(artifacts_dir, "rfe-reviews")

    if not os.path.isdir(reviews_dir):
        return None

    target = f"{identifier}-review.md"
    review_path = os.path.join(reviews_dir, target)
    if os.path.exists(review_path):
        return review_path

    return None


def scan_task_files(artifacts_dir):
    """Scan all RFE task files and return their frontmatter.

    Returns:
        list of (path, frontmatter_dict) tuples, sorted by rfe_id.
        Files without valid frontmatter are skipped with a warning.
    """
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    if not os.path.isdir(tasks_dir):
        return []

    results = []
    for filename in sorted(os.listdir(tasks_dir)):
        if not filename.endswith(".md"):
            continue
        if _is_companion_file(filename):
            continue

        path = os.path.join(tasks_dir, filename)
        try:
            data, _ = read_frontmatter_validated(path, "rfe-task")
            results.append((path, data))
        except (ValidationError, Exception) as e:
            print(f"Warning: skipping {filename}: {e}", file=sys.stderr)

    return sorted(results, key=lambda x: x[1].get("rfe_id", ""))


def scan_review_files(artifacts_dir):
    """Scan all RFE review files and return their frontmatter.

    Returns:
        list of (path, frontmatter_dict) tuples.
        Files without valid frontmatter are skipped with a warning.
    """
    reviews_dir = os.path.join(artifacts_dir, "rfe-reviews")
    if not os.path.isdir(reviews_dir):
        return []

    results = []
    for filename in sorted(os.listdir(reviews_dir)):
        if not filename.endswith("-review.md"):
            continue

        path = os.path.join(reviews_dir, filename)
        try:
            data, _ = read_frontmatter_validated(path, "rfe-review")
            results.append((path, data))
        except (ValidationError, Exception) as e:
            print(f"Warning: skipping {filename}: {e}", file=sys.stderr)

    return results


def scan_initiative_task_files(artifacts_dir):
    """Scan all Initiative task files and return their frontmatter.

    Returns:
        list of (path, frontmatter_dict) tuples, sorted by initiative_id.
        Files without valid frontmatter are skipped with a warning.
    """
    initiatives_dir = os.path.join(artifacts_dir, "initiatives")
    if not os.path.isdir(initiatives_dir):
        return []

    results = []
    for filename in sorted(os.listdir(initiatives_dir)):
        if not filename.endswith(".md"):
            continue
        if _is_companion_file(filename):
            continue

        path = os.path.join(initiatives_dir, filename)
        try:
            data, _ = read_frontmatter_validated(path, "initiative-task")
            results.append((path, data))
        except (ValidationError, Exception) as e:
            print(f"Warning: skipping {filename}: {e}", file=sys.stderr)

    return sorted(results, key=lambda x: x[1].get("initiative_id", ""))


# ─── File Renaming (post-submit) ───────────────────────────────────────────────


def rename_to_jira_key(artifacts_dir, rfe_id, jira_key):
    """Rename RFE-NNN.md files to RHAIRFE-NNNN.md after submission.

    Renames the task file, companion files, and review file.
    Updates rfe_id in frontmatter to the new Jira key.

    Args:
        artifacts_dir: path to artifacts directory
        rfe_id: e.g. "RFE-001"
        jira_key: e.g. "RHAIRFE-1600"
    """
    # Both ids become path components below. rfe_id comes from validated
    # frontmatter, but jira_key arrives from a Jira API response — reject
    # anything that is not the documented shape before touching the fs.
    if not re.fullmatch(r"RFE-\d+", rfe_id):
        raise ValueError(f"rename_to_jira_key: invalid local id {rfe_id!r}")
    if not re.fullmatch(r"RHAIRFE-\d+", jira_key):
        raise ValueError(f"rename_to_jira_key: invalid Jira key {jira_key!r}")

    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    reviews_dir = os.path.join(artifacts_dir, "rfe-reviews")

    # Rename task file and companions
    if os.path.isdir(tasks_dir):
        for filename in list(os.listdir(tasks_dir)):
            if not (filename == f"{rfe_id}.md" or filename.startswith(rfe_id + "-")):
                continue
            if not (filename.endswith(".md") or filename.endswith(".yaml")):
                continue

            old_path = os.path.join(tasks_dir, filename)

            if filename.endswith("-comments.md"):
                new_name = f"{jira_key}-comments.md"
            elif filename.endswith("-removed-context.yaml"):
                new_name = f"{jira_key}-removed-context.yaml"
            elif filename.endswith("-removed-context.md"):
                new_name = f"{jira_key}-removed-context.md"
            else:
                new_name = f"{jira_key}.md"

            new_path = os.path.join(tasks_dir, new_name)
            os.rename(old_path, new_path)

            # Update frontmatter on main task file
            if new_name == f"{jira_key}.md":
                update_frontmatter(
                    new_path,
                    {"rfe_id": jira_key, "status": "Submitted", "local_id": rfe_id},
                    "rfe-task",
                )

    # Rename review file
    if os.path.isdir(reviews_dir):
        for filename in list(os.listdir(reviews_dir)):
            if filename.startswith(rfe_id + "-") and filename.endswith("-review.md"):
                old_path = os.path.join(reviews_dir, filename)
                new_path = os.path.join(reviews_dir, f"{jira_key}-review.md")
                os.rename(old_path, new_path)

                # Update frontmatter
                update_frontmatter(new_path, {"rfe_id": jira_key, "local_id": rfe_id}, "rfe-review")
                break


def rename_initiative_to_jira_key(artifacts_dir, initiative_id, jira_key):
    """Rename INIT-NNN.md files to RHOAIENG-NNNN.md after submission.

    Renames the task file, companion files, and review file.
    Updates initiative_id in frontmatter to the new Jira key.
    """
    if not re.fullmatch(r"INIT-\d+", initiative_id):
        raise ValueError(f"rename_initiative_to_jira_key: invalid local id {initiative_id!r}")
    if not re.fullmatch(r"RHOAIENG-\d+", jira_key):
        raise ValueError(f"rename_initiative_to_jira_key: invalid Jira key {jira_key!r}")

    initiatives_dir = os.path.join(artifacts_dir, "initiatives")
    reviews_dir = os.path.join(artifacts_dir, "initiative-reviews")

    if os.path.isdir(initiatives_dir):
        for filename in list(os.listdir(initiatives_dir)):
            if not (filename == f"{initiative_id}.md" or filename.startswith(initiative_id + "-")):
                continue
            if not (filename.endswith(".md") or filename.endswith(".yaml")):
                continue

            old_path = os.path.join(initiatives_dir, filename)

            # Companions must be matched before the fallback: anything that
            # reaches the else branch is renamed to {jira_key}.md, which is
            # also the main task file's new name, so an unhandled companion
            # silently overwrites it.
            if filename.endswith("-comments.md"):
                new_name = f"{jira_key}-comments.md"
            elif filename.endswith("-removed-context.yaml"):
                new_name = f"{jira_key}-removed-context.yaml"
            elif filename.endswith("-removed-context.md"):
                new_name = f"{jira_key}-removed-context.md"
            else:
                new_name = f"{jira_key}.md"

            new_path = os.path.join(initiatives_dir, new_name)
            os.rename(old_path, new_path)

            if new_name == f"{jira_key}.md":
                update_frontmatter(
                    new_path,
                    {"initiative_id": jira_key, "status": "Submitted", "local_id": initiative_id},
                    "initiative-task",
                )

    if os.path.isdir(reviews_dir):
        old_review = os.path.join(reviews_dir, f"{initiative_id}-review.md")
        if os.path.isfile(old_review):
            new_review = os.path.join(reviews_dir, f"{jira_key}-review.md")
            os.rename(old_review, new_review)
            update_frontmatter(
                new_review,
                {"initiative_id": jira_key, "local_id": initiative_id},
                "initiative-review",
            )


# ─── Index Rebuilding ───────────────────────────────────────────────────────────


def rebuild_index(artifacts_dir):
    """Rebuild artifacts/rfes.md from frontmatter across task and review files.

    Scans rfe-tasks/ for task metadata and rfe-reviews/ for review scores.
    Generates a summary table.

    Returns:
        The generated markdown string.
    """
    tasks = scan_task_files(artifacts_dir)
    reviews = scan_review_files(artifacts_dir)

    # Build review lookup by rfe_id
    review_by_id = {}
    for _, review_data in reviews:
        review_by_id[review_data["rfe_id"]] = review_data

    lines = [
        "# RFE Summary",
        "",
        "| ID | Title | Priority | Size | Score | Rec | Status |",
        "|-----|-------|----------|------|-------|-----|--------|",
    ]

    for _, task_data in tasks:
        rfe_id = task_data["rfe_id"]
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


def parse_child_artifact(path):
    """Parse a child RFE markdown file.

    Returns: (title, priority, full_markdown, cleaned_markdown)
    - full_markdown: original content (for archival comment)
    - cleaned_markdown: metadata stripped (for Jira description)

    Reads title and priority from frontmatter if available,
    falls back to parsing markdown content.
    """
    from jira_utils import strip_metadata

    with open(path, encoding="utf-8") as f:
        content = f.read()

    data, body = read_frontmatter(path)

    if data.get("title"):
        title = data["title"]
    else:
        title_match = re.match(r"^#\s+RFE-\d+:\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else "Untitled"

    if data.get("priority"):
        priority = data["priority"]
    else:
        priority_match = re.search(r"^\*\*Priority\*\*:\s*(.+)$", content, re.MULTILINE)
        priority = priority_match.group(1).strip() if priority_match else "Normal"

    cleaned = strip_metadata(content)
    return title, priority, content, cleaned


def parse_child_initiative(path):
    """Parse a child Initiative markdown file.

    Returns: (title, priority, full_markdown, cleaned_markdown)
    """
    from jira_utils import strip_metadata

    with open(path, encoding="utf-8") as f:
        content = f.read()

    data, body = read_frontmatter(path)

    title = data.get("title", "Untitled")
    priority = data.get("priority", "Normal")

    cleaned = strip_metadata(content)
    return title, priority, content, cleaned
