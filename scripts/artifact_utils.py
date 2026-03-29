"""Artifact schema definitions, frontmatter read/write/validate, and index rebuilding.

Owns all structured metadata for RFE and strategy artifacts. Scripts and skills
use this module instead of regex-parsing markdown prose.

Frontmatter is stored as YAML between --- delimiters at the top of markdown files.
"""

import os
import re
import sys

import yaml


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
        "title": {
            "type": "string",
            "required": True,
        },
        "priority": {
            "type": "string",
            "required": True,
            "enum": ["Blocker", "Critical", "Major", "Normal", "Minor",
                     "Undefined"],
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
    },
    "rfe-review": {
        "rfe_id": {
            "type": "string",
            "required": True,
            "pattern": r"^(RFE-\d+|RHAIRFE-\d+)$",
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
            "enum": ["submit", "revise", "split", "reject"],
        },
        "feasibility": {
            "type": "string",
            "required": True,
            "enum": ["feasible", "infeasible"],
        },
        "revised": {
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
    },
    "strat-task": {
        "strat_id": {
            "type": "string",
            "required": True,
            "pattern": r"^(STRAT-\d+|RHAISTRAT-\d+)$",
        },
        "title": {
            "type": "string",
            "required": True,
        },
        "source_rfe": {
            "type": "string",
            "required": True,
            "pattern": r"^(RFE-\d+|RHAIRFE-\d+)$",
        },
        "jira_key": {
            "type": "string",
            "required": False,
            "pattern": r"^RHAISTRAT-\d+$",
            "default": None,
        },
        "priority": {
            "type": "string",
            "required": True,
            "enum": ["Blocker", "Critical", "Major", "Normal", "Minor",
                     "Undefined"],
        },
        "status": {
            "type": "string",
            "required": True,
            "enum": ["Draft", "Ready", "Refined", "Reviewed"],
        },
    },
    "strat-review": {
        "strat_id": {
            "type": "string",
            "required": True,
            "pattern": r"^(STRAT-\d+|RHAISTRAT-\d+)$",
        },
        "recommendation": {
            "type": "string",
            "required": True,
            "enum": ["approve", "revise", "split", "reject"],
        },
        "reviewers": {
            "type": "dict",
            "required": True,
            "fields": {
                "feasibility": {
                    "type": "string",
                    "required": True,
                    "enum": ["approve", "revise", "reject"],
                },
                "testability": {
                    "type": "string",
                    "required": True,
                    "enum": ["approve", "revise", "reject"],
                },
                "scope": {
                    "type": "string",
                    "required": True,
                    "enum": ["approve", "revise", "reject"],
                },
                "architecture": {
                    "type": "string",
                    "required": True,
                    "enum": ["approve", "revise", "reject"],
                },
            },
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
            errors.append(
                f"{full_name}: expected string, got {type(value).__name__}")
            return errors
        if "enum" in spec and value not in spec["enum"]:
            errors.append(
                f"{full_name}: '{value}' not in {spec['enum']}")
        if "pattern" in spec and not re.match(spec["pattern"], value):
            errors.append(
                f"{full_name}: '{value}' does not match {spec['pattern']}")

    elif expected_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(
                f"{full_name}: expected int, got {type(value).__name__}")

    elif expected_type == "bool":
        if not isinstance(value, bool):
            errors.append(
                f"{full_name}: expected bool, got {type(value).__name__}")

    elif expected_type == "dict":
        if not isinstance(value, dict):
            errors.append(
                f"{full_name}: expected dict, got {type(value).__name__}")
            return errors
        nested_schema = spec.get("fields", {})
        # Check for unknown fields in nested dict
        for key in value:
            if key not in nested_schema:
                errors.append(f"{full_name}: unknown field '{key}'")
        # Validate nested fields
        for field_name, field_spec in nested_schema.items():
            errors.extend(_validate_field(
                field_name, value.get(field_name), field_spec, full_name))

    return errors


def validate(data, schema_type):
    """Validate frontmatter data against a schema.

    Args:
        data: dict of frontmatter fields
        schema_type: one of "rfe-task", "rfe-review", "strat-review"

    Returns:
        list of error strings (empty if valid)

    Raises:
        ValueError: if schema_type is unknown
    """
    if schema_type not in SCHEMAS:
        raise ValueError(
            f"Unknown schema type: {schema_type}. "
            f"Valid types: {list(SCHEMAS.keys())}")

    schema = SCHEMAS[schema_type]
    errors = []

    # Check for unknown top-level fields
    for key in data:
        if key not in schema:
            errors.append(f"Unknown field: {key}")

    # Validate each defined field
    for field_name, field_spec in schema.items():
        errors.extend(_validate_field(
            field_name, data.get(field_name), field_spec))

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
                for nested_name, nested_spec in \
                        field_spec.get("fields", {}).items():
                    if nested_name not in nested and \
                            "default" in nested_spec:
                        nested[nested_name] = nested_spec["default"]
    return data


def get_schema_yaml(schema_type):
    """Return the schema definition as a YAML string for display."""
    if schema_type not in SCHEMAS:
        raise ValueError(
            f"Unknown schema type: {schema_type}. "
            f"Valid types: {list(SCHEMAS.keys())}")

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

_FRONTMATTER_RE = re.compile(
    r'^---\s*\n(.*?\n)---\s*\n', re.DOTALL)


def read_frontmatter(path):
    """Read and parse YAML frontmatter from a markdown file.

    Returns:
        (data_dict, body_string) — frontmatter as dict, remainder as string.
        Returns ({}, full_content) if no frontmatter found.
    """
    with open(path, encoding="utf-8") as f:
        content = f.read()

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_str = match.group(1)
    body = content[match.end():]

    data = yaml.safe_load(yaml_str)
    if not isinstance(data, dict):
        return {}, content

    return data, body


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

    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed in {path}:\n"
            + "\n".join(f"  - {e}" for e in errors))

    return data, body


def write_frontmatter(path, data, schema_type):
    """Write/update YAML frontmatter on a markdown file.

    Validates data against the schema before writing. Preserves the
    markdown body below the frontmatter. Creates the file if it doesn't
    exist (with empty body).

    Args:
        path: file path
        data: dict of frontmatter fields
        schema_type: one of "rfe-task", "rfe-review", "strat-review"

    Raises:
        ValidationError: if data fails schema validation
    """
    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed:\n"
            + "\n".join(f"  - {e}" for e in errors))

    # Read existing body if file exists
    body = ""
    if os.path.exists(path):
        _, body = read_frontmatter(path)

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False,
                         allow_unicode=True)
    content = f"---\n{yaml_str}---\n{body}"

    os.makedirs(os.path.dirname(path), exist_ok=True)
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

    Raises:
        ValidationError: if merged data fails validation
        FileNotFoundError: if file doesn't exist
    """
    data, body = read_frontmatter(path)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value

    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed after update in {path}:\n"
            + "\n".join(f"  - {e}" for e in errors))

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False,
                         allow_unicode=True)
    content = f"---\n{yaml_str}---\n{body}"

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ─── Artifact File Discovery ───────────────────────────────────────────────────

def _is_companion_file(filename):
    """Check if a filename is a companion file (comments, removed-context)."""
    return (filename.endswith(("-comments.md", "-removed-context.md"))
            or filename.endswith("-removed-context.yaml"))


def find_artifact_file(artifacts_dir, identifier):
    """Find the main artifact file for a given RFE ID or Jira key.

    Matches:
    - RFE-NNN-*.md (local pre-submission)
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

        # Match by local RFE ID (prefix: RFE-001-*.md)
        if identifier.startswith("RFE-"):
            if filename.startswith(identifier + "-"):
                path = os.path.join(tasks_dir, filename)
                data, _ = read_frontmatter(path)
                if data.get("status") == "Archived":
                    continue
                return path

    return None


def find_artifact_file_including_archived(artifacts_dir, identifier):
    """Like find_artifact_file but includes archived artifacts."""
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    if not os.path.isdir(tasks_dir):
        return None

    for filename in sorted(os.listdir(tasks_dir)):
        if not filename.endswith(".md"):
            continue
        if _is_companion_file(filename):
            continue

        if identifier.startswith("RHAIRFE-"):
            if filename == f"{identifier}.md":
                return os.path.join(tasks_dir, filename)

        if identifier.startswith("RFE-"):
            if filename.startswith(identifier + "-"):
                return os.path.join(tasks_dir, filename)

    return None


def find_removed_context_yaml(artifacts_dir, identifier):
    """Find the removed-context YAML file for a given RFE ID or Jira key."""
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    if not os.path.isdir(tasks_dir):
        return None

    for filename in sorted(os.listdir(tasks_dir)):
        if not filename.endswith("-removed-context.yaml"):
            continue

        if identifier.startswith("RHAIRFE-"):
            if filename == f"{identifier}-removed-context.yaml":
                return os.path.join(tasks_dir, filename)

        if identifier.startswith("RFE-"):
            if filename.startswith(identifier + "-") and \
                    filename.endswith("-removed-context.yaml"):
                return os.path.join(tasks_dir, filename)

    return None


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
            if filename.startswith(identifier + "-") and \
                    filename.endswith("-removed-context.md"):
                return os.path.join(tasks_dir, filename)

    return None


def find_review_file(artifacts_dir, identifier):
    """Find the review file for a given RFE ID or Jira key.

    Looks in rfe-reviews/ for {identifier}-review.md or
    {identifier}-*-review.md (for RFE-NNN slugged names).
    """
    reviews_dir = os.path.join(artifacts_dir, "rfe-reviews")
    if not os.path.isdir(reviews_dir):
        return None

    for filename in sorted(os.listdir(reviews_dir)):
        if not filename.endswith("-review.md"):
            continue

        if identifier.startswith("RHAIRFE-"):
            if filename == f"{identifier}-review.md":
                return os.path.join(reviews_dir, filename)

        if identifier.startswith("RFE-"):
            if filename.startswith(identifier + "-"):
                return os.path.join(reviews_dir, filename)

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


# ─── File Renaming (post-submit) ───────────────────────────────────────────────

def rename_to_jira_key(artifacts_dir, rfe_id, jira_key):
    """Rename RFE-NNN-*.md files to RHAIRFE-NNNN.md after submission.

    Renames the task file, companion files, and review file.
    Updates rfe_id in frontmatter to the new Jira key.

    Args:
        artifacts_dir: path to artifacts directory
        rfe_id: e.g. "RFE-001"
        jira_key: e.g. "RHAIRFE-1600"
    """
    tasks_dir = os.path.join(artifacts_dir, "rfe-tasks")
    reviews_dir = os.path.join(artifacts_dir, "rfe-reviews")

    # Rename task file and companions
    if os.path.isdir(tasks_dir):
        for filename in list(os.listdir(tasks_dir)):
            if not filename.startswith(rfe_id + "-"):
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
                update_frontmatter(new_path,
                                   {"rfe_id": jira_key,
                                    "status": "Submitted"},
                                   "rfe-task")

    # Rename review file
    if os.path.isdir(reviews_dir):
        for filename in list(os.listdir(reviews_dir)):
            if filename.startswith(rfe_id + "-") and \
                    filename.endswith("-review.md"):
                old_path = os.path.join(reviews_dir, filename)
                new_path = os.path.join(reviews_dir,
                                        f"{jira_key}-review.md")
                os.rename(old_path, new_path)

                # Update frontmatter
                update_frontmatter(new_path,
                                   {"rfe_id": jira_key},
                                   "rfe-review")
                break


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
                f"| {rfe_id} | {title} "
                f"| {priority} | {size} | {score} "
                f"| {rec} | {status} |"
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
        title_match = re.match(r'^#\s+RFE-\d+:\s+(.+)$', content,
                               re.MULTILINE)
        title = title_match.group(1).strip() if title_match else "Untitled"

    if data.get("priority"):
        priority = data["priority"]
    else:
        priority_match = re.search(r'^\*\*Priority\*\*:\s*(.+)$', content,
                                   re.MULTILINE)
        priority = priority_match.group(1).strip() \
            if priority_match else "Normal"

    cleaned = strip_metadata(content)
    return title, priority, content, cleaned
