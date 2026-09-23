#!/usr/bin/env python3
"""Render the committed eval configs from the shared skeleton and per-type fragments.

Design work-item-types-unified.md section 4.5: eval CONFIGS are the one piece of generated
data. ``eval/config/skeleton.yaml`` holds the structure, every deterministic check and the
shared judge prose once; ``types/<t>/type.yaml`` supplies identity, dirs, score fields and
the AUTHORITATIVE ``eval.thresholds``; ``types/<t>/eval/fragment.yaml`` supplies the typed
prose (schema: ``types/_schema/eval-fragment.schema.json``). The output is written to the
descriptor's ``eval.config`` path and committed, so the harness and the tests read plain
YAML; ``--check`` is the regenerate-and-diff gate (``make lint`` / lint.yml, and
``validate_types.py`` gate 1).

Usage:
    python3 scripts/generate_eval_config.py                  # rewrite every type's config
    python3 scripts/generate_eval_config.py --type rfe       # one type
    python3 scripts/generate_eval_config.py --check          # exit 1 if any committed
                                                             # config differs from a fresh render
    python3 scripts/generate_eval_config.py --type rfe --stdout

Template rules (the whole language — there is deliberately no conditional or loop):

* ``${type.<dotted>}`` reads the descriptor (``Descriptor.get``), ``${fragment.<dotted>}``
  the fragment, ``${gen.<name>}`` a value derived below (``DERIVED`` lists them).
* A placeholder alone on its line is a BLOCK slot: the value's lines are inserted at the
  slot's indentation (blank lines stay blank). An empty value removes the slot line, and
  when that leaves two blank lines touching, one of them too.
* Any other placeholder is INLINE and its value must be a single line.
* Lines starting with ``#@`` are skeleton-only commentary and never rendered.
* Every placeholder must resolve, every fragment key must be consumed, the rendered text
  must parse as YAML, every ``check:`` body must compile, and every threshold must name a
  judge — otherwise generation fails and nothing is written.

stdlib + pyyaml only (design Q5); the fragment JSON-Schema check lives in validate_types.py.
"""

from __future__ import annotations

import argparse
import difflib
import keyword
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_registry  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SKELETON_RELPATH = "eval/config/skeleton.yaml"
FRAGMENT_NAME = "fragment.yaml"
PAIRWISE_NAME = "pairwise-judge.md"
FRAGMENT_SCHEMA_VERSION = 1
SKELETON_COMMENT = "#@"

PLACEHOLDER = re.compile(r"\$\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}")
# Descriptor values are spliced verbatim into the skeleton, some of them inside the Python of
# a check: nothing that could close a string literal or an f-string field is allowed there.
_CODE_UNSAFE = re.compile(r"""['"\\{}\n\r]""")
# Text that may sit inside a generated f-string / message verbatim.
_PLAIN_TEXT = re.compile(r"^[A-Za-z0-9_./= -]+$")
BLOCK_LINE = re.compile(r"^(?P<indent>[ \t]*)\$\{(?P<name>[a-z][a-z0-9_.]*)\}[ \t]*$")

# Fragment keys the generator consumes outside the placeholder walk.
_FRAGMENT_META_KEYS = ("schema_version",)
_FRAGMENT_DERIVED_KEYS = ("architecture_context.not_relevant_pattern",)

# ${gen.<name>} values, for the skeleton author. Each is documented at its builder below.
DERIVED = (
    "name",
    "skill",
    "arguments",
    "create_skill",
    "batch_pattern",
    "write_prefix",
    "score_fields_slash",
    "score_fields_py",
    "criteria_count",
    "quality_judge",
    "annotations_extra",
    "review_extra_fields",
    "run_report_entry_fields",
    "checks.extra_enum_fields",
    "checks.extra_rules",
    "checks.extra_rules_desc",
    "arch.not_relevant_pattern_def",
    "pairwise_prompt_file",
    "thresholds",
)


# The generic skills the harness drives since PR-5b (design §4.4).
GENERIC_SPEEDRUN_SKILL = "rfe-speedrun"
GENERIC_CREATE_SKILL = "rfe-create"


class GenerateError(ValueError):
    """Anything that stops a config from being rendered; the message names the type."""


# ─── inputs ──────────────────────────────────────────────────────────────────────


def eval_dir(desc):
    if desc.path is None:
        raise GenerateError(f"{desc.name}: descriptor has no path; cannot locate eval/")
    return Path(desc.path).parent / "eval"


def fragment_path(desc):
    return eval_dir(desc) / FRAGMENT_NAME


def pairwise_path(desc):
    return eval_dir(desc) / PAIRWISE_NAME


def config_path(desc, repo_root=None):
    """Where the rendered config is committed: eval.config, repo-relative."""
    root = REPO_ROOT if repo_root is None else Path(repo_root)
    rel = desc.get("eval.config")
    return root / rel


def load_fragment(desc):
    path = fragment_path(desc)
    if not path.is_file():
        raise GenerateError(f"{desc.name}: eval fragment missing: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise GenerateError(f"{desc.name}: {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise GenerateError(f"{desc.name}: {path} must be a mapping")
    if data.get("schema_version") != FRAGMENT_SCHEMA_VERSION:
        raise GenerateError(
            f"{desc.name}: {path} schema_version must be {FRAGMENT_SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )
    return data


def load_skeleton(repo_root=None):
    root = REPO_ROOT if repo_root is None else Path(repo_root)
    path = root / SKELETON_RELPATH
    if not path.is_file():
        raise GenerateError(f"skeleton missing: {path}")
    return path.read_text(encoding="utf-8")


def flatten(mapping, prefix=""):
    """Nested mapping -> {dotted: leaf}. Lists are not part of the fragment language."""
    flat = {}
    for key, value in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, dotted + "."))
        elif isinstance(value, list):
            raise GenerateError(f"fragment key {dotted!r} is a list; slots take strings")
        else:
            flat[dotted] = value
    return flat


# ─── derived values ──────────────────────────────────────────────────────────────


def _desc_get(desc, dotted):
    try:
        return desc.get(dotted)
    except Exception as exc:  # KeyError / RegistryError: the message is what matters
        raise GenerateError(f"{desc.name}: descriptor has no {dotted!r} ({exc})") from exc


def _review_extra_fields(desc):
    fields = _desc_get(desc, "schema.review").get("extra_fields") or {}
    if not isinstance(fields, dict):
        raise GenerateError(f"{desc.name}: schema.review.extra_fields must be a mapping")
    return fields


def _extra_rules(desc):
    rules = _desc_get(desc, "schema.review").get("extra_rules") or []
    out = []
    for i, rule in enumerate(rules):
        try:
            when, then = rule["when"], rule["then"]
            field, equals = when["field"], when["equals"]
        except (KeyError, TypeError) as exc:
            raise GenerateError(
                f"{desc.name}: schema.review.extra_rules[{i}] needs when.field, when.equals, then"
            ) from exc
        out.append((field, equals, then))
    return out


def _format_threshold_value(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenerateError(f"threshold value {value!r} is not a number")
    return repr(float(value)) if isinstance(value, float) else str(value)


def _render_thresholds(desc, notes):
    thresholds = _desc_get(desc, "eval.thresholds")
    if not isinstance(thresholds, dict) or not thresholds:
        raise GenerateError(f"{desc.name}: eval.thresholds must be a non-empty mapping")
    unknown = sorted(set(notes) - set(thresholds))
    if unknown:
        raise GenerateError(
            f"{desc.name}: threshold_notes for judges without a threshold: {', '.join(unknown)}"
        )
    lines = []
    for judge, metrics in thresholds.items():
        note = notes.get(judge)
        if note:
            note_lines = str(note).rstrip("\n").split("\n")
            lines.append(f"# {judge}: {note_lines[0]}")
            lines.extend(f"# {ln}" if ln else "#" for ln in note_lines[1:])
        if not isinstance(metrics, dict) or not metrics:
            raise GenerateError(f"{desc.name}: eval.thresholds.{judge} must be a mapping")
        lines.append(f"{judge}:")
        for metric, value in metrics.items():
            lines.append(f"  {metric}: {_format_threshold_value(value)}")
    return "\n".join(lines)


def derived_values(desc, fragment_flat, repo_root=None):
    """The ${gen.*} table. Everything here is a projection of the descriptor (or, for the
    architecture-context pattern and the threshold notes, of the fragment)."""
    root = REPO_ROOT if repo_root is None else Path(repo_root)
    score_fields = list(desc.score_fields)
    if not score_fields:
        raise GenerateError(f"{desc.name}: schema.review.score_fields is empty")
    write_prefix = desc.write_prefix
    if not write_prefix:
        raise GenerateError(f"{desc.name}: no tracker key prefix to derive the fetched naming")

    # Generated Python never embeds a descriptor value raw: field names and rule values go
    # through repr() (a proper literal, whatever they contain), a field is a local variable
    # only when it is a plain identifier, and message text that is not plain falls back to a
    # quoted literal outside the f-string.
    extra_fields = _review_extra_fields(desc)
    review_extra = []
    enum_checks = []
    for index, (name, spec) in enumerate(extra_fields.items()):
        name = str(name)
        spec = spec if isinstance(spec, dict) else {}
        enum = spec.get("enum")
        if enum:
            review_extra.append(f"{name} ({'/'.join(str(v) for v in enum)})")
            local = name if name.isidentifier() and not keyword.iskeyword(name) else None
            local = local or f"_extra_field_{index}"
            allowed = f"valid_{local}s" if local == name else f"_allowed_{index}"
            if _PLAIN_TEXT.match(name):
                message = f"f\"{{fname}}: invalid {name} '{{{local}}}'\""
            else:
                message = f'f"{{fname}}: invalid " + {name!r} + f" \'{{{local}}}\'"'
            enum_checks.extend(
                [
                    f"{local} = fm.get({name!r})",
                    f"{allowed} = {[str(v) for v in enum]!r}",
                    f"if {local} and {local} not in {allowed}:",
                    f"    errors.append({message})",
                ]
            )
        else:
            review_extra.append(f"{name} ({spec.get('type', 'any')})")

    rule_lines = []
    rule_desc = []
    for field, equals, then in _extra_rules(desc):
        field, then = str(field), str(then)
        rule_lines.append(f"if fm.get({field!r}, '') == {equals!r} and not fm.get({then!r}):")
        text = f"{field}={equals} but {then}=false"
        if _PLAIN_TEXT.match(text):
            rule_lines.append(f'    errors.append(f"{{fname}}: {text}")')
        else:
            rule_lines.append(f'    errors.append(f"{{fname}}: " + {text!r})')
        rule_desc.append(f"Type rule: {field}={equals} requires {then}=true.")

    annotations = []
    for name in _desc_get(desc, "eval").get("annotations_extra") or []:
        field = name[len("expected_") :] if str(name).startswith("expected_") else str(name)
        spec = extra_fields.get(field) if isinstance(extra_fields, dict) else None
        enum = spec.get("enum") if isinstance(spec, dict) else None
        if enum:
            annotations.append(f"- {name} (nullable string: {'/'.join(str(v) for v in enum)})")
        else:
            annotations.append(f"- {name} (nullable)")

    entry_fields = ["id", "recommendation", "revision_cycles"]
    entry_fields.extend(
        str(f) for f in (_desc_get(desc, "reporting.run_report").get("extra_entry_fields") or [])
    )

    pattern = fragment_flat.get("architecture_context.not_relevant_pattern")
    if pattern is None:
        pattern_def = (
            "not_relevant_pattern = None"
            "  # this type never declares architecture context irrelevant"
        )
    else:
        if not isinstance(pattern, str) or not pattern:
            raise GenerateError(
                f"{desc.name}: architecture_context.not_relevant_pattern must be a regex or null"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise GenerateError(
                f"{desc.name}: architecture_context.not_relevant_pattern is not a valid "
                f"regex: {exc}"
            ) from exc
        pattern_def = "\n".join(
            ["not_relevant_pattern = re.compile(", f"    {pattern!r},", "    re.IGNORECASE", ")"]
        )

    pairwise = pairwise_path(desc)
    if not pairwise.is_file():
        raise GenerateError(f"{desc.name}: pairwise judge prompt missing: {pairwise}")
    pairwise_rel = Path(os.path.relpath(pairwise, root)).as_posix()

    notes = {
        key[len("threshold_notes.") :]: value
        for key, value in fragment_flat.items()
        if key.startswith("threshold_notes.")
    }

    return {
        # <type>-speedrun: the harness's run name (eval/runs/<name>/<run-id>).
        "name": f"{desc.name}-speedrun",
        # The generic speedrun skill every type runs through (design §4.4; PR-5b) and the
        # explicit type it is given — `--type <t>` for every type, rfe included, so the CI
        # form never relies on the headless legacy default (plan D2).
        "skill": GENERIC_SPEEDRUN_SKILL,
        "arguments": f"--headless --dry-run --input batch.yaml --type {desc.name}",
        # The generic create skill: the pipeline_flow judge's Phase-1 marker (plan D3).
        "create_skill": GENERIC_CREATE_SKILL,
        # identity.local_prefix + the harness's zero-padded batch index.
        "batch_pattern": f"{desc.local_prefix}{{n:03d}}",
        # key_prefixes[0]: how fetched items are named.
        "write_prefix": write_prefix,
        # score_fields joined by "/" (prose) and as a Python list literal (checks).
        "score_fields_slash": "/".join(score_fields),
        "score_fields_py": repr(score_fields),
        "criteria_count": str(len(score_fields)),
        # <type>_quality: the per-type LLM judge (eval.thresholds names it the same way).
        "quality_judge": f"{desc.name}_quality",
        # eval.annotations_extra as dataset-schema lines; enums from the review schema.
        "annotations_extra": "\n".join(annotations),
        # schema.review.extra_fields as review-schema prose, trailing comma for the list.
        "review_extra_fields": ", ".join(review_extra) + "," if review_extra else "",
        # run-report entry fields: the shared trio + reporting.run_report.extra_entry_fields.
        "run_report_entry_fields": ", ".join(entry_fields),
        # frontmatter_valid: enum validation for every extra review field with an enum.
        "checks.extra_enum_fields": "\n".join(enum_checks),
        # recommendation_consistency: schema.review.extra_rules as code and as prose.
        "checks.extra_rules": "\n".join(rule_lines),
        "checks.extra_rules_desc": "\n".join(rule_desc),
        # architecture_context_used: the carve-out regex, or None when the type has none.
        "arch.not_relevant_pattern_def": pattern_def,
        # types/<t>/eval/pairwise-judge.md, repo-relative.
        "pairwise_prompt_file": pairwise_rel,
        # eval.thresholds verbatim, with the fragment's calibration notes as comments.
        "thresholds": _render_thresholds(desc, notes),
    }


# ─── rendering ───────────────────────────────────────────────────────────────────


def _header(desc, repo_root=None):
    root = REPO_ROOT if repo_root is None else Path(repo_root)
    frag = Path(os.path.relpath(fragment_path(desc), root)).as_posix()
    typ = Path(os.path.relpath(desc.path, root)).as_posix()
    return (
        "# GENERATED — do not edit by hand. Regenerate: python3 scripts/generate_eval_config.py\n"
        f"# Sources: {SKELETON_RELPATH} (shared structure, checks and judge prose), {frag}\n"
        f"# (typed prose) and {typ} (identity, dirs, score fields, thresholds).\n"
    )


class _Resolver:
    def __init__(self, desc, fragment_flat, gen):
        self.desc = desc
        self.fragment = fragment_flat
        self.gen = gen
        self.used = set()

    def __call__(self, name):
        namespace, _, rest = name.partition(".")
        if namespace == "type" and rest:
            value = _desc_get(self.desc, rest)
            if isinstance(value, str) and _CODE_UNSAFE.search(value):
                raise GenerateError(
                    f"{self.desc.name}: descriptor value {rest} ({value!r}) contains a quote, "
                    "backslash, brace or newline; the skeleton splices it into check code"
                )
            return value
        if namespace == "fragment" and rest:
            if rest not in self.fragment:
                raise GenerateError(f"{self.desc.name}: fragment has no key {rest!r}")
            self.used.add(rest)
            return self.fragment[rest]
        if namespace == "gen" and rest:
            if rest not in self.gen:
                raise GenerateError(f"{self.desc.name}: unknown derived value {rest!r}")
            return self.gen[rest]
        raise GenerateError(f"{self.desc.name}: unknown placeholder ${{{name}}}")


def _scalar(desc, name, value):
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise GenerateError(f"{desc.name}: ${{{name}}} is {type(value).__name__}, need a scalar")
    text = str(value)
    if "\n" in text.rstrip("\n"):
        raise GenerateError(
            f"{desc.name}: ${{{name}}} is multi-line; put the placeholder alone on its line"
        )
    return text.rstrip("\n")


def _block_lines(desc, name, value, indent):
    if not isinstance(value, str):
        raise GenerateError(
            f'{desc.name}: block slot ${{{name}}} needs a string ("" for empty), '
            f"got {type(value).__name__}"
        )
    text = value.rstrip("\n")
    if not text:
        return []
    return [f"{indent}{ln}" if ln else "" for ln in text.split("\n")]


def render(desc, fragment, skeleton_text, repo_root=None):
    """The rendered config text for one type (header included). Raises GenerateError."""
    fragment_flat = flatten(fragment)
    gen = derived_values(desc, fragment_flat, repo_root)
    resolve = _Resolver(desc, fragment_flat, gen)

    def inline(match):
        name = match.group(1)
        return _scalar(desc, name, resolve(name))

    source = [ln for ln in skeleton_text.split("\n") if not ln.startswith(SKELETON_COMMENT)]
    out = []
    i = 0
    while i < len(source):
        line = source[i]
        block = BLOCK_LINE.match(line)
        if block:
            name = block.group("name")
            lines = _block_lines(desc, name, resolve(name), block.group("indent"))
            if lines:
                out.extend(lines)
            elif out and out[-1] == "" and i + 1 < len(source) and source[i + 1] == "":
                i += 1  # the empty slot sat between two blank lines: keep one
        else:
            out.append(PLACEHOLDER.sub(inline, line))
        i += 1
    text = "\n".join(out)
    if "${" in text:
        bad = sorted({m for m in re.findall(r"\$\{[^}\n]*\}?", text)})
        raise GenerateError(f"{desc.name}: unresolved placeholder(s): {', '.join(bad)}")

    consumed = resolve.used | set(_FRAGMENT_DERIVED_KEYS)
    unused = sorted(
        k
        for k in fragment_flat
        if k not in consumed
        and k not in _FRAGMENT_META_KEYS
        and not k.startswith("threshold_notes.")
    )
    if unused:
        raise GenerateError(
            f"{desc.name}: fragment keys not used by the skeleton: {', '.join(unused)}"
        )

    text = _header(desc, repo_root) + text
    _verify(desc, text)
    return text


def _verify(desc, text):
    try:
        config = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GenerateError(f"{desc.name}: rendered config is not valid YAML: {exc}") from exc
    judges = config.get("judges") if isinstance(config, dict) else None
    if not isinstance(judges, list) or not judges:
        raise GenerateError(f"{desc.name}: rendered config has no judges")
    names = []
    for judge in judges:
        name = judge.get("name") if isinstance(judge, dict) else None
        if not name:
            raise GenerateError(f"{desc.name}: a judge has no name")
        if name in names:
            raise GenerateError(f"{desc.name}: judge {name!r} is defined twice")
        names.append(name)
        check = judge.get("check")
        if check is not None:
            body = "def _check():\n" + "".join(f"    {ln}\n" for ln in str(check).split("\n"))
            try:
                compile(body, f"<{desc.name}:{name}.check>", "exec")
            except SyntaxError as exc:
                raise GenerateError(
                    f"{desc.name}: judge {name!r} check does not compile: {exc.msg} "
                    f"(line {exc.lineno - 1 if exc.lineno else '?'})"
                ) from exc
    thresholds = config.get("thresholds") or {}
    unknown = sorted(set(thresholds) - set(names))
    if unknown:
        raise GenerateError(
            f"{desc.name}: eval.thresholds name judges the config does not define: "
            f"{', '.join(unknown)}"
        )


def render_type(desc, repo_root=None, skeleton_text=None):
    """Load the fragment and skeleton for ``desc`` and render."""
    if skeleton_text is None:
        skeleton_text = load_skeleton(repo_root)
    return render(desc, load_fragment(desc), skeleton_text, repo_root)


# ─── check / write ───────────────────────────────────────────────────────────────


def compare(desc, repo_root=None, skeleton_text=None):
    """(path, rendered, committed_or_None, unified_diff). Raises GenerateError."""
    rendered = render_type(desc, repo_root, skeleton_text)
    path = config_path(desc, repo_root)
    committed = path.read_text(encoding="utf-8") if path.is_file() else None
    diff = ""
    if committed != rendered:
        diff = "".join(
            difflib.unified_diff(
                (committed or "").splitlines(keepends=True),
                rendered.splitlines(keepends=True),
                fromfile=f"{path.name} (committed)",
                tofile=f"{path.name} (rendered)",
                n=1,
            )
        )
    return path, rendered, committed, diff


def _select(registry, only):
    if only is None:
        return list(registry)
    if only not in registry:
        raise GenerateError(f"unknown type {only!r} (available: {', '.join(registry.names())})")
    return [registry.get(only)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--type", dest="only", help="Render one type only")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if any committed config differs from a fresh render",
    )
    parser.add_argument("--stdout", action="store_true", help="Print the render instead of writing")
    parser.add_argument("--root", help="Type descriptor root (default: types/)")
    parser.add_argument(
        "--repo-root", help="Where eval.config paths and the skeleton resolve (default: the repo)"
    )
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT

    try:
        registry = type_registry.load(root=args.root)
        targets = _select(registry, args.only)
        skeleton_text = load_skeleton(repo_root)
        stale = []
        for desc in targets:
            path, rendered, committed, diff = compare(desc, repo_root, skeleton_text)
            rel = Path(os.path.relpath(path, repo_root)).as_posix()
            if args.stdout:
                sys.stdout.write(rendered)
                continue
            if committed == rendered:
                print(f"{desc.name}: {rel} up to date")
                continue
            if args.check:
                stale.append(rel)
                print(f"{desc.name}: {rel} is out of date", file=sys.stderr)
                sys.stderr.write(diff)
                continue
            path.write_text(rendered, encoding="utf-8")
            print(f"{desc.name}: wrote {rel}")
    except GenerateError as exc:
        print(f"generate_eval_config: {exc}", file=sys.stderr)
        return 2
    except type_registry.RegistryError as exc:
        print(f"generate_eval_config: {exc}", file=sys.stderr)
        return 2
    if stale:
        print(
            f"generate_eval_config: {len(stale)} config(s) out of date; run "
            "python3 scripts/generate_eval_config.py and commit the result",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
