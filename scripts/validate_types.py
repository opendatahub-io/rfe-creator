#!/usr/bin/env python3
"""Validate work-item type descriptors (types/<type>/type.yaml) — design §3.3 gates.

The descriptors are the (future) single source of truth for every per-type
registry in scripts/. This validator is the contract enforcer described in
design-proposals/work-item-types-unified.md §3.3:

  Gate 1 — lint-time, always on:
    * JSON Schema (types/_schema/type.schema.json, Draft 2020-12)
    * every repo-relative file reference exists: pipeline.prompts.*,
      pipeline.dimensions[].prompt, eval.config, eval.dataset
      (pipeline.rubric.export is an OUTPUT the bootstrap writes — not required;
      pipeline.rubric.path lives in the assess checkout — gate 2, Q24)
    * at least one descriptor is discovered (an empty root is a finding, never a
      vacuous pass — make lint / lint.yml depend on this script)
    * kind is work-item for every REGISTERED descriptor: the v1 engine runs that
      kind only; strategy / decomposition are recorded for the sibling pipelines
      and validate schema-only as fixtures (design §3.2)
    * schema.review.score_fields is non-empty
    * identity.local_id_pattern is an anchored, compilable regex
    * conventions.labels.alignment only when an alignment dimension is declared
    * pipeline.rubric.ref is a 7-40 char lowercase hex commit SHA (documentary
      until the bootstrap pins it in PR-2, Q9); for the D3 embedded rubric
      (pipeline.rubric.repo: self) pipeline.rubric.rubric_version is a 7-64 char
      lowercase hex content hash instead
    * no executable code under any descriptor root: *.py / *.sh / *.bash / *.zsh
      or a shebang file (types/ is data only — design §3.5.1, PR1-21)
    * the review error stub verify_phase.py:105-127 writes after a failed
      agent wave validates against artifact_utils.SCHEMAS["<type>-review"]
      (the wait-for-wave deadlock guard: a stub the schema rejects is a
      barrier that never clears)
    * cross-type invariants, evaluated on EFFECTIVE bindings (§3.2.1(b)):
      unique (tracker, project, issue_type); unique local_prefix,
      local_id_pattern, id_field; unique non-empty poll/state/report prefixes
      (empty grandfathered for type rfe only); snapshot.prefix non-empty and
      pairwise prefix-collision-free (snapshot_fetch.py:142-149 globs
      f"{prefix}*.yaml"); no local_prefix stem equal to any effective project key
  Gate 2 — --with-deps, post-bootstrap, opt-in (Q8):
    * pipeline.rubric.path exists under --assess-dir (under the repo root when
      pipeline.rubric.repo is self — the rubric is embedded, not vendored)
    * <assess-dir>/agents/<pipeline.scorer_agent>.md exists
  Gate 3 — --verify --type T, pipeline SETUP fail-fast:
    * gate 1 for that one type, one-line diagnosis, non-zero exit.
      NOT wired into any pipeline phase in PR-1 (design §10, PR-8 wires it).

Usage:
    python3 scripts/validate_types.py [--root DIR] [--extra-roots A:B]
                                      [--with-deps [--assess-dir .context/assess-rfe]]
                                      [--verify --type T] [--json]

Exit codes:
    0  every descriptor passed — prints "OK: <n> type(s) valid: rfe, initiative"
    1  findings — one "ERROR <type-or-*>: <message>" line per finding
       ("*" marks a cross-type or registry-level finding)
    2  usage error, or jsonschema not installed

--json emits {"ok": bool, "types": [...], "errors": [{"type": ..., "message": ...}]}.

PR-1 status: inert. Nothing in the pipeline or the skills invokes this script;
`make lint` / lint.yml are the intended callers. The registry it reads
(scripts/type_registry.py) is stdlib+pyyaml and import-clean; the jsonschema
dependency is confined to this script and its tests (Q5) and imported lazily
so the registry itself never needs it.
"""

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# Repo-relative descriptor references resolve against the checkout that
# contains this script, never against cwd (design §3.5.1).
REPO_ROOT = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))
import type_registry  # noqa: E402

SCHEMA_RELPATH = Path("_schema") / "type.schema.json"
DEFAULT_ASSESS_DIR = Path(".context") / "assess-rfe"

# Q9: lint only that the ref is a plausible commit SHA; the value is documentary
# until scripts/bootstrap-assess-rfe.sh pins it (PR-2). Matched with fullmatch so a
# trailing newline (which `$` alone tolerates) is rejected.
RUBRIC_REF_RE = re.compile(r"^[0-9a-f]{7,40}$")
# D3 (design §11): an embedded rubric (`repo: self`) is pinned by a content hash
# instead of a commit — sha256 hex is 64 chars, a truncated prefix is accepted.
RUBRIC_VERSION_RE = re.compile(r"^[0-9a-f]{7,64}$")

# Design §3.2: the only pipeline kind the v1 engine has a phase table for. The
# schema enum also admits the two sibling kinds so their descriptors (the epic
# fixture) validate, but a REGISTERED descriptor must be runnable.
V1_ENGINE_KIND = "work-item"
RESERVED_SIBLING_KINDS = ("strategy", "decomposition")

# Design §3.5.1 / PR1-21: descriptor roots hold data only. Anything with one of
# these suffixes, or starting with a shebang, is executable code and a finding.
CODE_SUFFIXES = (".py", ".sh", ".bash", ".zsh")

# The only type allowed to keep empty poll/state/report prefixes: its files
# pre-date the per-type prefixing (tmp/speedrun-all-ids.txt, run-report.*),
# see pipeline_state.py:107, check_autofix_complete.py:18, generate_run_report.py:30.
GRANDFATHERED_EMPTY_PREFIX_TYPE = "rfe"

# Mirror of verify_phase.py:105-127 — the constant part of the error stub the
# post-barrier verifier writes with `frontmatter.py set` when an agent produced
# no output. Only the id field, the phase name and the score keys vary.
ERROR_STUB_PHASE = "assess"
ERROR_STUB_CONSTANTS = {
    "score": 0,
    "pass": False,
    "recommendation": "revise",
    "feasibility": "feasible",
    "auto_revised": False,
    "needs_attention": True,
}


class MissingDependencyError(RuntimeError):
    """jsonschema is not importable (dev dependency, see requirements-dev.txt)."""


@dataclass
class Finding:
    """One validation failure.

    type    descriptor name the finding is reported under, or "*" for
            cross-type / registry-level findings
    types   every descriptor involved (used by --verify to keep only the
            findings that concern the verified type; empty = concerns all)
    """

    type: str
    message: str
    gate: int = 1
    types: frozenset = field(default_factory=frozenset)


@dataclass
class Report:
    types: list
    findings: list

    @property
    def ok(self):
        return not self.findings

    def lines(self):
        return [f"ERROR {f.type}: {f.message}" for f in self.findings]

    def to_dict(self):
        return {
            "ok": self.ok,
            "types": list(self.types),
            "errors": [{"type": f.type, "message": f.message} for f in self.findings],
        }


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _opt(desc, dotted, default=None):
    """Descriptor.get() that never raises: absent keys read as `default`."""
    try:
        return desc.get(dotted, default)
    except (KeyError, TypeError, AttributeError):
        return default


def _fmt_types(names):
    return ", ".join(sorted(names))


def _load_jsonschema():
    try:
        import jsonschema
    except ImportError as exc:
        raise MissingDependencyError(
            "jsonschema is required by scripts/validate_types.py (JSON-Schema gate) but is "
            "not installed — run: pip install -r requirements-dev.txt"
        ) from exc
    return jsonschema


def find_schema_file(root=None):
    """Locate type.schema.json: <root>/_schema first, then the shipped types/_schema."""
    candidates = []
    if root is not None:
        candidates.append(Path(root) / SCHEMA_RELPATH)
    candidates.append(Path(type_registry.DEFAULT_ROOT) / SCHEMA_RELPATH)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_schema(root=None):
    """Return (schema_dict, path) or (None, None) when no schema file exists."""
    path = find_schema_file(root)
    if path is None:
        return None, None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh), path


def _load_artifact_utils():
    """artifact_utils is an rfe-creator module; the validator still runs without it
    (e.g. once lifted into creator-core), skipping only the error-stub check."""
    try:
        import artifact_utils
    except ImportError:
        return None
    return artifact_utils


# ─── Gate 1: per-descriptor checks ─────────────────────────────────────────────


def schema_messages(desc, schema):
    """JSON-Schema findings for one descriptor (Draft 2020-12)."""
    jsonschema = _load_jsonschema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(desc.data), key=lambda e: list(e.absolute_path))
    messages = []
    for err in errors:
        location = getattr(err, "json_path", None) or "$." + ".".join(
            str(p) for p in err.absolute_path
        )
        text = err.message
        if err.validator in ("oneOf", "anyOf") and err.context:
            # The stock message dumps the whole instance; the closest sub-error
            # (e.g. "'github' should not be valid under {...}") names the cause.
            best = jsonschema.exceptions.best_match(err.context)
            cause = best.message
            if len(cause) > 120:  # e.g. a `not` message, which embeds the instance
                rule = json.dumps(best.validator_value, default=str)[:80]
                cause = f"{best.validator} constraint at {best.json_path} ({rule})"
            text = f"violates {err.validator} (closest sub-error: {cause})"
        if len(text) > 300:
            text = text[:297] + "..."
        messages.append(f"schema: {location}: {text}")
    return messages


def path_messages(desc, repo_root):
    """Every repo-relative reference gate 1 requires to exist (Q24)."""
    repo_root = Path(repo_root)
    messages = []

    def missing(dotted, rel, want_file):
        if not isinstance(rel, str) or not rel:
            return  # shape problems belong to the JSON-Schema finding
        target = repo_root / rel
        ok = target.is_file() if want_file else target.exists()
        if not ok:
            kind = "file" if want_file else "path"
            messages.append(f"{dotted}: {kind} not found: {rel} (relative to {repo_root})")

    prompts = _opt(desc, "pipeline.prompts") or {}
    if isinstance(prompts, dict):
        for key, rel in prompts.items():
            missing(f"pipeline.prompts.{key}", rel, want_file=True)

    dimensions = _opt(desc, "pipeline.dimensions") or []
    if isinstance(dimensions, list):
        engine_phases = {"fetch", "create", "assess", "review", "revise", "split"}
        seen_names = set()
        for dim in dimensions:
            dname = dim.get("name") if isinstance(dim, dict) else None
            # Only strings take part: a malformed name (list, mapping, number) is the
            # JSON-Schema check's finding, and an unhashable one must not raise here.
            if not isinstance(dname, str):
                continue
            if dname in engine_phases:
                messages.append(f"pipeline.dimensions name {dname!r} collides with an engine phase")
            elif dname in seen_names:
                messages.append(f"pipeline.dimensions name {dname!r} is declared twice")
            seen_names.add(dname)
        for i, dim in enumerate(dimensions):
            if isinstance(dim, dict):
                missing(f"pipeline.dimensions[{i}].prompt", dim.get("prompt"), want_file=True)

    missing("eval.config", _opt(desc, "eval.config"), want_file=True)
    missing("eval.dataset", _opt(desc, "eval.dataset"), want_file=False)
    return messages


def score_fields_messages(desc):
    fields = _opt(desc, "schema.review.score_fields")
    if not isinstance(fields, list) or not fields:
        return ["schema.review.score_fields must be a non-empty list"]
    return []


def local_id_pattern_messages(desc):
    pattern = _opt(desc, "identity.local_id_pattern")
    if not isinstance(pattern, str):
        return []  # absence/type is a JSON-Schema finding
    messages = []
    if not (pattern.startswith("^") and pattern.endswith("$")):
        messages.append(
            f"identity.local_id_pattern {pattern!r} must be anchored with ^ and $ "
            "(it is the authoritative local-id grammar detect() full-matches against)"
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        messages.append(f"identity.local_id_pattern {pattern!r} is not a valid regex: {exc}")
    return messages


def alignment_labels_messages(desc):
    """conventions.labels.alignment is reserved for types that declare an alignment
    dimension (design §3.2) — a label set nothing can ever apply is a stale copy."""
    if _opt(desc, "conventions.labels.alignment") is None:
        return []
    dimensions = _opt(desc, "pipeline.dimensions") or []
    names = {d.get("name") for d in dimensions if isinstance(d, dict)}
    if "alignment" in names:
        return []
    return [
        "conventions.labels.alignment is declared but no pipeline.dimensions[] entry "
        "is named 'alignment'"
    ]


def rubric_ref_messages(desc):
    """The rubric pin: a commit SHA for an external assess repo (Q9), or — when
    pipeline.rubric.repo is `self` (D3, the schema's second oneOf branch) — a
    content hash in pipeline.rubric.rubric_version."""
    if _opt(desc, "pipeline.rubric.repo") == "self":
        version = _opt(desc, "pipeline.rubric.rubric_version")
        if isinstance(version, str) and RUBRIC_VERSION_RE.fullmatch(version):
            return []
        return [
            "pipeline.rubric.rubric_version must be a 7-64 character lowercase hex content "
            f"hash when pipeline.rubric.repo is 'self' (D3), got {version!r}"
        ]
    ref = _opt(desc, "pipeline.rubric.ref")
    if isinstance(ref, str) and RUBRIC_REF_RE.fullmatch(ref):
        return []
    return [f"pipeline.rubric.ref must be a 7-40 character lowercase hex commit SHA, got {ref!r}"]


def kind_messages(desc):
    """A registered descriptor must be of the kind the v1 engine runs (design §3.2).

    The schema enum admits strategy / decomposition so that sibling-pipeline
    descriptors (tests/fixtures/types/epic) validate schema-only; enumerating one
    through the registry would hand the work-item phase table a pipeline it cannot
    run, so gate 1 refuses it here rather than in the schema.
    """
    kind = _opt(desc, "kind", V1_ENGINE_KIND)
    if kind not in RESERVED_SIBLING_KINDS:
        return []  # work-item passes; a non-string / unknown value is a JSON-Schema finding
    return [
        f"kind {kind!r} cannot be registered: the v1 engine runs the {V1_ENGINE_KIND!r} kind "
        "only; strategy / decomposition descriptors are recorded for the sibling pipelines "
        "and validate schema-only (design §3.2)"
    ]


def build_error_stub(desc, phase=ERROR_STUB_PHASE):
    """The frontmatter verify_phase.py:105-127 writes for an id whose agent produced
    nothing — built from the descriptor instead of verify_phase._TYPE_CONFIG.

    Field for field: `<id_field>=<id>`, `error=<phase>_failed`, `score=0`,
    `pass=false`, `recommendation=revise`, `feasibility=feasible`,
    `auto_revised=false`, `needs_attention=true`,
    `needs_attention_reason=Agent failed: <phase>_failed`, then one
    `scores.<f>=0` per score field (verify_phase.py:46-52 / :59-65).
    """
    id_field = _opt(desc, "identity.id_field")
    local_prefix = _opt(desc, "identity.local_prefix") or ""
    score_fields = _opt(desc, "schema.review.score_fields") or []
    error_msg = f"{phase}_failed"
    stub = {id_field: f"{local_prefix}1", "error": error_msg}
    stub.update(ERROR_STUB_CONSTANTS)
    stub["needs_attention_reason"] = f"Agent failed: {error_msg}"
    stub["scores"] = {f: 0 for f in score_fields}
    return stub


def error_stub_messages(desc, artifact_utils):
    """Run the stub through the exact write path of `frontmatter.py set`
    (artifact_utils.update_frontmatter: apply_defaults, then validate)."""
    if artifact_utils is None:
        return []
    schema_name = f"{desc.name}-review"
    if schema_name not in artifact_utils.SCHEMAS:
        return []  # no base review schema for this type yet (PR-2 derives them)
    if not isinstance(_opt(desc, "identity.id_field"), str):
        return []  # a JSON-Schema finding already covers the missing id field
    stub = copy.deepcopy(build_error_stub(desc))
    artifact_utils.apply_defaults(stub, schema_name)
    errors = artifact_utils.validate(stub, schema_name)
    if not errors:
        return []
    return [
        f"review error stub (verify_phase.py:105-127) is rejected by "
        f"artifact_utils.SCHEMAS[{schema_name!r}]: " + "; ".join(errors)
    ]


def per_type_findings(desc, schema, repo_root, artifact_utils):
    messages = []
    declared = desc.data.get("type") if isinstance(desc.data, dict) else None
    if declared != desc.name:
        messages.append(f"type field {declared!r} does not match the directory name {desc.name!r}")
    if schema is not None:
        messages.extend(schema_messages(desc, schema))
    messages.extend(kind_messages(desc))
    messages.extend(path_messages(desc, repo_root))
    messages.extend(score_fields_messages(desc))
    messages.extend(local_id_pattern_messages(desc))
    messages.extend(alignment_labels_messages(desc))
    messages.extend(rubric_ref_messages(desc))
    messages.extend(error_stub_messages(desc, artifact_utils))
    return [Finding(desc.name, m, 1, frozenset({desc.name})) for m in messages]


# ─── Gate 1: data-only descriptor roots (design §3.5.1, PR1-21) ─────────────────


def _has_shebang(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def data_only_findings(registry):
    """No executable code under any descriptor root: scripts live in scripts/ and
    are invoked by their cwd-relative path; a `types/<t>` shipped via git-subdir
    receives descriptor data only, never code (design §3.5.1, §3.7)."""
    names = set(registry.names())
    findings = []
    for root in registry.roots:
        root = Path(root)
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix.lower() not in CODE_SUFFIXES and not _has_shebang(path):
                continue
            rel = path.relative_to(root)
            owner = rel.parts[0] if len(rel.parts) > 1 and rel.parts[0] in names else None
            findings.append(
                Finding(
                    "*",
                    f"executable code under the descriptor root: {path} (descriptor roots are "
                    "data only — design §3.5.1; scripts live in scripts/)",
                    1,
                    frozenset({owner}) if owner else frozenset(),
                )
            )
    return findings


# ─── Gate 1: cross-type invariants (effective bindings) ────────────────────────


def _effective_bindings(registry, env):
    """Return ({name: binding dict or None}, [Finding]).

    A binding the registry refuses to compute — a malformed identity block or
    an invalid RFE_CREATOR_BINDING_* override value — is reported as a finding
    for that type (design §3.2.1(g): a bad override is a hard failure, never a
    silently skipped lint) and read as None by the cross-type checks below.
    """
    bindings, findings = {}, []
    for desc in registry:
        try:
            bindings[desc.name] = desc.binding(env)
        except Exception as exc:
            bindings[desc.name] = None
            findings.append(
                Finding(
                    desc.name,
                    f"effective binding cannot be computed: {exc}",
                    1,
                    frozenset({desc.name}),
                )
            )
    return bindings, findings


def _identity_key(binding):
    tracker = binding.get("tracker")
    if tracker == "jira":
        return (tracker, binding.get("project"), binding.get("issue_type"))
    # Future trackers (design §3.6): the binding's own identity pair.
    return (tracker, binding.get("repo", binding.get("project")), str(binding.get("kind")))


def _duplicates(pairs):
    """[(key, name)] -> {key: [names]} for keys owned by more than one type."""
    owners = {}
    for key, name in pairs:
        owners.setdefault(key, []).append(name)
    return {k: v for k, v in owners.items() if len(v) > 1}


def cross_type_findings(registry, env):
    findings = []

    def cross(message, names):
        findings.append(Finding("*", message, 1, frozenset(names)))

    bindings, binding_findings = _effective_bindings(registry, env)
    findings.extend(binding_findings)
    descs = {d.name: d for d in registry}

    # (1) unique effective tracker binding per type — jira: (project, issue_type)
    ident_pairs = []
    for name, b in bindings.items():
        if isinstance(b, dict):
            ident_pairs.append((_identity_key(b), name))
    for key, names in _duplicates(ident_pairs).items():
        label = ", ".join(str(k) for k in key)
        cross(f"duplicate effective binding ({label}) shared by types: {_fmt_types(names)}", names)

    # (2) unique local_prefix (effective — LOCAL_PREFIX is overridable, §3.2.1),
    #     local_id_pattern and id_field
    def eff_local_prefix(name):
        b = bindings.get(name)
        if isinstance(b, dict) and isinstance(b.get("local_prefix"), str):
            return b["local_prefix"]
        return _opt(descs[name], "identity.local_prefix")

    for label, getter in (
        ("identity.local_prefix", eff_local_prefix),
        ("identity.local_id_pattern", lambda n: _opt(descs[n], "identity.local_id_pattern")),
        ("identity.id_field", lambda n: _opt(descs[n], "identity.id_field")),
    ):
        pairs = [(getter(n), n) for n in descs if getter(n) is not None]
        for value, names in _duplicates(pairs).items():
            cross(f"duplicate {label} {value!r} shared by types: {_fmt_types(names)}", names)

    # (3) poll/state/report prefixes: empty only for the grandfathered type,
    #     unique among the non-empty values
    for dotted in ("pipeline.poll_prefix", "pipeline.state_prefix", "snapshot.report_prefix"):
        non_empty = []
        for name, desc in descs.items():
            value = _opt(desc, dotted)
            if not isinstance(value, str):
                continue
            if value == "":
                if name != GRANDFATHERED_EMPTY_PREFIX_TYPE:
                    cross(
                        f"{dotted} is empty for type {name!r} (an empty prefix is "
                        f"grandfathered for type {GRANDFATHERED_EMPTY_PREFIX_TYPE!r} only)",
                        [name],
                    )
                continue
            non_empty.append((value, name))
        for value, names in _duplicates(non_empty).items():
            cross(f"duplicate {dotted} {value!r} shared by types: {_fmt_types(names)}", names)

    # (4) snapshot.prefix: never empty, pairwise not a prefix of one another —
    #     snapshot_fetch.py:142-149 selects a type's snapshots with glob(f"{prefix}*.yaml")
    snapshot_prefixes = []
    for name, desc in descs.items():
        value = _opt(desc, "snapshot.prefix")
        if not isinstance(value, str):
            continue
        if value == "":
            cross(
                f"snapshot.prefix is empty for type {name!r} (an empty prefix globs every "
                "other type's snapshots: snapshot_fetch.py:142-149)",
                [name],
            )
            continue
        snapshot_prefixes.append((value, name))
    for i, (a, name_a) in enumerate(snapshot_prefixes):
        for b, name_b in snapshot_prefixes[i + 1 :]:
            if a.startswith(b) or b.startswith(a):
                cross(
                    f"snapshot.prefix collision: {a!r} (type {name_a}) and {b!r} (type "
                    f"{name_b}) — one is a prefix of the other, so snapshot_fetch's "
                    "f'{prefix}*.yaml' glob would select both types' snapshots",
                    [name_a, name_b],
                )

    # (5) no local_prefix stem may equal any effective project key (§3.2.1(b) —
    #     the collision PR #122 feared, enforced as a lint rather than a DRAFT- rename)
    project_owners = {}
    for name, b in bindings.items():
        if isinstance(b, dict) and isinstance(b.get("project"), str):
            project_owners.setdefault(b["project"].upper(), []).append(name)
    for name in descs:
        prefix = eff_local_prefix(name)
        if not isinstance(prefix, str) or not prefix:
            continue
        stem = prefix.rstrip("-").upper()
        for owner in project_owners.get(stem, []):
            cross(
                f"identity.local_prefix {prefix!r} of type {name!r} has the same stem as the "
                f"effective project key {stem!r} of type {owner!r} — local ids would be "
                "indistinguishable from tracker keys (design §3.2.1(b))",
                [name, owner],
            )
    return findings


# ─── Gate 2: --with-deps ───────────────────────────────────────────────────────


def with_deps_findings(registry, assess_dir, repo_root=None):
    assess_dir = Path(assess_dir)
    repo_root = REPO_ROOT if repo_root is None else Path(repo_root)
    if not assess_dir.is_dir():
        return [
            Finding(
                "*",
                f"with-deps: assess checkout not found at {assess_dir} "
                "(run scripts/bootstrap-assess-rfe.sh or pass --assess-dir)",
                2,
            )
        ]
    findings = []
    for desc in registry:
        rubric_path = _opt(desc, "pipeline.rubric.path")
        # D3: an embedded rubric (repo: self) lives in this repo, not in the assess checkout.
        rubric_base = repo_root if _opt(desc, "pipeline.rubric.repo") == "self" else assess_dir
        if isinstance(rubric_path, str) and not (rubric_base / rubric_path).is_file():
            where = (
                f"{rubric_base} (rubric.repo is 'self')" if rubric_base is repo_root else assess_dir
            )
            findings.append(
                Finding(
                    desc.name,
                    f"with-deps: pipeline.rubric.path {rubric_path!r} not found under {where}",
                    2,
                    frozenset({desc.name}),
                )
            )
        agent = _opt(desc, "pipeline.scorer_agent")
        if isinstance(agent, str):
            agent_file = assess_dir / "agents" / f"{agent}.md"
            if not agent_file.is_file():
                findings.append(
                    Finding(
                        desc.name,
                        f"with-deps: scorer agent file {agent_file} not found "
                        f"(pipeline.scorer_agent={agent!r})",
                        2,
                        frozenset({desc.name}),
                    )
                )
    return findings


# ─── Driver ────────────────────────────────────────────────────────────────────


def validate_all(
    root=None,
    extra_roots=None,
    env=None,
    with_deps=False,
    assess_dir=None,
    only=None,
    repo_root=None,
):
    """Run the gates and return a Report.

    root / extra_roots / env are forwarded to type_registry.load(); env is also
    what the cross-type lint reads the RFE_CREATOR_BINDING_* overrides from.
    `only` restricts the report to one type (gate 3, --verify). `repo_root`
    overrides where repo-relative references are resolved (tests).

    Raises MissingDependencyError when jsonschema is not installed.
    """
    env = os.environ if env is None else env
    repo_root = REPO_ROOT if repo_root is None else Path(repo_root)
    try:
        registry = type_registry.load(root=root, extra_roots=extra_roots, env=env)
    except Exception as exc:  # a broken descriptor tree is a finding
        return Report([], [Finding("*", f"cannot load type registry: {exc}")])

    names = list(registry.names())
    if not names:
        # Never a vacuous pass: make lint / lint.yml depend on this exit code, so a
        # mis-set --root or a checkout without types/ must fail, not print "0 valid".
        roots = ", ".join(str(r) for r in registry.roots)
        return Report(
            [],
            [
                Finding(
                    "*",
                    f"no type descriptors found under {roots} (expected <root>/<name>/type.yaml)",
                )
            ],
        )
    if only is not None and only not in names:
        return Report(
            [],
            [Finding("*", f"unknown type {only!r} (available: {', '.join(names) or 'none'})")],
        )

    findings = []
    schema, schema_path = load_schema(root)
    if schema is None:
        looked = [str(p) for p in ([Path(root) / SCHEMA_RELPATH] if root else [])]
        looked.append(str(Path(type_registry.DEFAULT_ROOT) / SCHEMA_RELPATH))
        findings.append(
            Finding("*", "JSON Schema not found (looked at: " + ", ".join(looked) + ")")
        )

    if schema is not None:
        jsonschema = _load_jsonschema()
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.exceptions.SchemaError as exc:
            findings.append(
                Finding("*", f"{schema_path} is not a valid JSON Schema: {exc.message}")
            )
            schema = None

    artifact_utils = _load_artifact_utils()
    for desc in registry:
        if only is not None and desc.name != only:
            continue
        findings.extend(per_type_findings(desc, schema, repo_root, artifact_utils))

    for finding in data_only_findings(registry):
        if only is None or only in finding.types:
            findings.append(finding)

    for finding in cross_type_findings(registry, env):
        if only is None or only in finding.types:
            findings.append(finding)

    if with_deps:
        assess_dir = DEFAULT_ASSESS_DIR if assess_dir is None else assess_dir
        for finding in with_deps_findings(registry, assess_dir, repo_root):
            if only is None or not finding.types or only in finding.types:
                findings.append(finding)

    return Report([only] if only is not None else names, findings)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="validate_types.py",
        description="Validate types/<type>/type.yaml descriptors (design §3.3 gates).",
    )
    parser.add_argument("--root", help="descriptor root (default: <repo>/types)")
    parser.add_argument(
        "--extra-roots",
        help=f"additional descriptor roots, {os.pathsep!r}-separated "
        "(default: $RFE_CREATOR_EXTRA_TYPES)",
    )
    parser.add_argument(
        "--with-deps",
        action="store_true",
        help="gate 2: also check pipeline.rubric.path and the scorer agent in the assess checkout",
    )
    parser.add_argument(
        "--assess-dir",
        help=f"assess checkout for --with-deps (default: {DEFAULT_ASSESS_DIR}, relative to cwd)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="gate 3: validate one type (requires --type), one-line diagnosis",
    )
    parser.add_argument("--type", help="type name for --verify")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verify and not args.type:
        parser.error("--verify requires --type T")
    if args.type and not args.verify:
        parser.error("--type is only meaningful with --verify")
    if args.assess_dir and not args.with_deps:
        parser.error("--assess-dir is only meaningful with --with-deps")

    # Same grammar as `type_registry.py --extra-roots` / RFE_CREATOR_EXTRA_TYPES: empty
    # entries dropped ("A:" is one root, not root A plus the cwd); "" is an explicit
    # empty list that switches the env seam off.
    extra_roots = (
        type_registry.parse_extra_roots(args.extra_roots) if args.extra_roots is not None else None
    )
    try:
        report = validate_all(
            root=args.root,
            extra_roots=extra_roots,
            with_deps=args.with_deps,
            assess_dir=args.assess_dir,
            only=args.type if args.verify else None,
        )
    except MissingDependencyError as exc:
        print(f"ERROR *: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.verify:
        if report.ok:
            print(f"VERIFY OK: type {args.type!r} passes gate 1")
        else:
            diagnosis = " | ".join(f.message for f in report.findings)
            print(f"VERIFY FAILED: type {args.type!r} — {diagnosis}")
    elif report.ok:
        print(f"OK: {len(report.types)} type(s) valid: {', '.join(report.types)}")
    else:
        for line in report.lines():
            print(line)
        print(
            f"validate_types: {len(report.findings)} finding(s) across {len(report.types)} type(s)",
            file=sys.stderr,
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
