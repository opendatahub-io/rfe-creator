#!/usr/bin/env python3
"""Work-item type registry — the single import point for ``types/<name>/type.yaml``.

Usage:
    python3 scripts/type_registry.py list [--json]
    python3 scripts/type_registry.py show <type> [--json]
    python3 scripts/type_registry.py get <type> <dotted.path> [--json]
    python3 scripts/type_registry.py binding <type> [--json]
    python3 scripts/type_registry.py candidates <id> [--json]
    python3 scripts/type_registry.py resolve [--type T] [--batch FILE] [--artifact PATH]
                                             [--headless] [--workspace-root DIR] [--json] [ID ...]

    Options: --root DIR (default: <repo>/types), --extra-roots A:B (default: env
    RFE_CREATOR_EXTRA_TYPES). Exit 0 on success, 1 on an unknown type / unreadable
    registry / missing key / conflicting or invalid resolve input, 2 on a usage error,
    3 when ``resolve`` is ambiguous (``TYPE AMBIGUOUS: a, b - pass --type``).

Import-clean invariant (design work-item-types-unified.md §10 item 1, PR-1; Q5)
--------------------------------------------------------------------------------
This module imports ONLY the standard library and ``yaml``. It never imports another
rfe-creator module, never reads the filesystem at import time, takes the type root as
an explicit argument, and discovers the default root relative to ``__file__`` (never
the cwd). ``types/_schema/type.schema.json`` follows the same rule. Both are meant to
be lifted verbatim into the ``creator-core`` follow-up (design §10 item 10), so keep
them free of repo-specific helpers: JSON-Schema validation lives in
``scripts/validate_types.py`` (the only place ``jsonschema`` is imported), not here.

Adoption status (PR-3a): 28 scripts load the registry at import (module-level
``_TYPES = type_registry.load()``; the table in ``types/README.md`` lists them) and use
DESCRIPTOR values only; ``detect()``/``owns()`` are the design §5 rung-3 routers. The values a
pending script still carries are pinned equal to the descriptors by test, and the remaining
scripts adopt the registry one PR at a time (design §10 items 2-5). The resolution ladder
(``candidates``, ``resolve``, ``assert_registered_binding``) ships here. PR-3b wires the two
batch-root consumers — ``validate_batch_input.py`` and ``next_rfe_id.py --from-batch`` — through
``read_batch`` (the one parser of a batch file, read ONCE — the parsed pair is handed to
``resolve`` as ``batch_items``, so a pipe or ``/dev/stdin`` works) and ``resolve`` with
``items_are_ids=False`` (a bare string item is the caller's malformed entry, never an id signal)
and ``binding=False`` (the type verdict only: the environment's binding overrides are neither
read nor validated). They print the resolve line on STDERR only when a non-default rung decided
(D3): the legacy default — a bare list with no ``--type`` — is byte-identical to main on stdout
and stderr; an explicit ``--type`` or a mapping ``type:`` adds exactly one stderr line. The
remaining entry scripts follow in PR-3c.
``parse_type_arg`` is the one hand-parser behind the pipeline gates that take ``--type``
without argparse (``check_revised``, ``check_right_sized``, ``check_autofix_complete``), so
their error text cannot drift.

Deployment binding override (design §3.2.1)
--------------------------------------------
``identity.<tracker>`` in the descriptor is the DEFAULT binding. ``Descriptor.binding``
computes the EFFECTIVE binding by overlaying, in precedence order:

1. ``env`` — the explicit, type-scoped variables
   ``RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,LOCAL_PREFIX}`` (``<TYPE>`` is the type
   name upper-cased, non-alphanumerics mapped to ``_``);
2. ``shorthand`` — the bare ``JIRA_PROJECT`` / ``JIRA_ISSUE_TYPE`` variables, honoured ONLY
   when ``binding(shorthand=True)`` is asked for, which ``resolve`` does for the RESOLVED
   type alone (a shorthand can name one project, so it can bind one type per run);
3. ``workspace`` — the ``bindings:`` block of a workspace ``rfe-creator.yaml``
   (``bindings: {rfe: {jira: {project: KONFLUX, issue_type: Feature Request}}}``), read by
   ``load_workspace_bindings(root)`` and passed in as the ``workspace`` mapping;
4. the descriptor.

Every value passes the same grammar checks whatever its source. When ``project`` is
overridden the write prefix ``<PROJECT>-`` is derived and placed first in
``key_prefixes``; the descriptor's own prefixes are kept after it as read prefixes. When
``local_prefix`` is overridden the effective ``local_id_pattern`` is re-rendered by
substituting the new prefix for the descriptor prefix at the anchored start of the pattern
(PR-3 D13); a pattern that does not start with ``^`` + the descriptor prefix makes the
override a hard error. The result carries ``source`` (``descriptor``, ``env``, ``shorthand``,
``workspace`` or a ``+``-joined combination of the sources that contributed, in precedence
order) and ``overrides`` (the overridden field names). Only binding fields are overridable —
never judgement content, dirs, schema, rubric or eval. Zero-config default: with nothing set
the effective binding IS the descriptor binding (``source: descriptor``).

Trust boundary (§3.2.1 g): in a headless or CI run an override is honoured ONLY from the
environment (protected CI variables), never from a workspace file the checkout could carry.
``TypeRegistry.workspace_bindings()`` returns the file's mapping in an interactive run and
``{}`` (with one stderr line when the file would have overridden something) in a headless
one; ``assert_registered_binding`` — the runtime twin of the gate-1 uniqueness rule, called
before the first tracker write — rejects a workspace-sourced override in a headless run and
an effective ``(tracker, project, issue_type)`` that another registered type owns (ownership,
not membership: an rfe override that selects the initiative pair is refused even though the
pair is registered).

Type resolution (design §5, PR-3a)
-----------------------------------
``TypeRegistry.candidates(item_id)`` is the multi-candidate form of ``detect()`` over
EFFECTIVE bindings: rungs ``local_id_pattern`` full-match, ``key_prefix`` (effective and
descriptor prefixes), ``local_prefix``, then the Jira adapter's generic key grammar
``^[A-Z][A-Z0-9]+-[0-9]+$`` as a PROVISIONAL last rung (every Jira-bound type is a candidate,
discriminated post-fetch). The first rung with a match wins and returns every type matching
there. ``detect()`` keeps its Descriptor-or-None, descriptor-values-only contract for the
per-id routers.

``resolve(registry, ...)`` runs the ladder: ``--type`` (rung 1) > batch mapping ``type:``
(rung 2; a per-item ``type`` key and a ``--type`` that disagrees with the mapping are hard
errors, D1/D2; ``read_batch(path)`` is the shared parser of the two batch root forms — the
legacy bare list and ``{type: <t>, items: [...]}`` with exactly those two keys) >
deterministic signals (rung 3: artifact frontmatter ``type:``, else the artifact's parent
directory against every type's ``dirs``, else ids through ``candidates``)
> the grandfathered ``rfe`` default (rung 5). Conflicting deterministic signals are a hard
error, never a question. Provisional-only signals resolve when they single out one type and
are otherwise ambiguous: headless -> ``ResolveError`` (exit 3), interactive -> a
``Resolution`` with ``type_name None`` and ``candidates`` filled — the hook for the
interactive rung 4 (classification / picker), which is not implemented here. The resolved
line is ``TYPE RESOLVED: <type> (<rung>[; binding override project=...])``; the ``resolve``
CLI always prints it (D3), entry scripts stay silent for the legacy default.

Drop-in roots
-------------
``RFE_CREATOR_EXTRA_TYPES`` (``os.pathsep``-separated directories, each holding
``<name>/type.yaml`` entries) adds development/test roots after the primary root. A type
name present in two roots is an error, and a ``type.yaml`` that resolves outside its root
(symlink escape) is rejected. Drop-ins pass exactly the same ``validate_types.py`` gates as
shipped types before any binding is used.

The seam is development and test only (design §3.5, PR1-05): in a headless or CI run —
``is_headless(env, flag)``: any of ``RFE_CREATOR_HEADLESS``, ``CI``, ``GITHUB_ACTIONS`` set to
a truthy value in the registry's environment, or an explicit ``--headless`` flag, which
reaches the seam as ``load(headless=True)`` / ``TypeRegistry.headless`` (the ``resolve`` CLI
passes its flag; one predicate, PR-3 D4) — an ``RFE_CREATOR_EXTRA_TYPES`` entry is honoured
only when its
canonical path is allowlisted, via ``RFE_CREATOR_EXTRA_TYPES_ALLOWLIST`` (a protected CI
variable, the same trust boundary as §3.2.1 g) or the ``allowlisted_extra_roots`` argument.
Every other entry is dropped with one stderr line and recorded in
``TypeRegistry.ignored_extra_roots``. Explicit ``extra_roots=`` / ``--extra-roots`` values
are a deliberate caller action and are never gated. The marker is environment-only: this
module never probes the cwd (``tmp/pipeline-state.yaml`` is the pipeline's business — a
headless pipeline exports ``RFE_CREATOR_HEADLESS=1`` for its subprocesses), and the
workspace file is read only from an explicit ``workspace_root`` / ``--workspace-root``.
"""

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Default discovery root: <repo>/types, located relative to this file — never the cwd
# (precedent: pipeline_state.py os.path.dirname(__file__), snapshot_fetch.py SCRIPT_DIR).
# The plugin root: scripts/ and types/ ship together (a checkout or a marketplace install), so
# the directory above scripts/ is what every repo_path in a shipped descriptor is relative to.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = PLUGIN_ROOT / "types"


def _same_file_at(rel, absolute):
    """True when ``rel`` under the working directory IS the file at ``absolute`` — the same
    resolved path: the checkout is the cwd, or a ``types`` link into the plugin (the one the
    bootstrap makes) sits in it. A copy is never it, byte-identical or not: a writer who can
    place a copy in the working directory could swap it after this check and before the
    subagent reads it (CWE-367), and the plugin's own file is what the prompt must be. A
    missing path, a directory or any OS error is False — the caller then falls back to the
    absolute path."""
    candidate, target = Path(rel), Path(absolute)
    try:
        return candidate.is_file() and candidate.resolve() == target
    except OSError:
        return False


DESCRIPTOR_FILENAME = "type.yaml"
EXTRA_ROOTS_ENV = "RFE_CREATOR_EXTRA_TYPES"
# Design §3.5 / PR1-05: roots the env seam may add in a headless/CI run (os.pathsep-separated).
EXTRA_ROOTS_ALLOWLIST_ENV = "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST"
# Headless/CI markers, checked in the registry's env only (never the cwd). CI / GITHUB_ACTIONS
# are the conventional CI variables; RFE_CREATOR_HEADLESS is the explicit pipeline marker
# (exported by pipeline_state.py for a headless pipeline so every subprocess shares the
# predicate, PR-3 D4).
HEADLESS_MARKER_VARS = ("RFE_CREATOR_HEADLESS", "CI", "GITHUB_ACTIONS")
_FALSE_VALUES = {"", "0", "false", "no", "off"}
BINDING_ENV_PREFIX = "RFE_CREATOR_BINDING_"
# env suffix -> effective binding key (design §3.2.1: the overridable binding-only fields).
BINDING_OVERRIDE_FIELDS = {
    "PROJECT": "project",
    "ISSUE_TYPE": "issue_type",
    "LOCAL_PREFIX": "local_prefix",
}
# The overridable fields in the order the resolve line and ``overrides`` list them.
BINDING_FIELD_ORDER = ("project", "issue_type", "local_prefix")
# Bare shorthand variables per tracker (design §3.2.1): honoured for the RESOLVED type only,
# below the typed RFE_CREATOR_BINDING_* variables and above the workspace file.
SHORTHAND_ENV_VARS = {"jira": {"JIRA_PROJECT": "project", "JIRA_ISSUE_TYPE": "issue_type"}}
# Override sources in precedence order; the descriptor is the implicit last layer.
BINDING_SOURCES = ("env", "shorthand", "workspace")
# Workspace override file (design §3.2.1), read only from an explicit root — never the cwd.
WORKSPACE_FILENAME = "rfe-creator.yaml"
WORKSPACE_BINDINGS_KEY = "bindings"
# Q13: descriptors store dirs as "artifacts/<name>"; the bare form drops this component.
ARTIFACTS_DIR = "artifacts"
DIR_FORMS = ("artifacts", "bare")

# Value grammars for overrides. A lower-case or dash-less typo must fail loudly instead of
# silently minting a bogus write prefix (the §3.2.1 (b)/(g) lint runs on these values).
_PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_LOCAL_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,31}-$")

# Design §3.2.1 (f): each tracker adapter contributes a generic key grammar as the provisional
# last detection rung, so keys from an overridden project still resolve to a candidate set.
TRACKER_KEY_GRAMMARS = {"jira": re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")}
# ``candidates()`` rungs, most specific first; only the last one is provisional.
CANDIDATE_RUNGS = ("local_id_pattern", "key_prefix", "local_prefix", "tracker_grammar")
PROVISIONAL_RUNG = "tracker_grammar"
# ``resolve()`` rungs, strongest first (design §5; rung 4, the interactive picker, is not here).
# The last rung is the grandfathered default: entry scripts print no resolve line for it (D3).
LEGACY_DEFAULT_RUNG = "legacy default"
# D3, one line per run: a writer that spawns another writer for the type it already resolved
# (submit.py -> split_submit.py per split parent) sets this in the child's environment and the
# child skips its own ``TYPE RESOLVED`` line — the parent printed it.
RESOLVED_BY_PARENT_ENV = "RFE_CREATOR_RESOLVED_BY_PARENT"
RESOLVE_RUNGS = (
    "--type",
    "batch type",
    "frontmatter type",
    "artifact dir",
    "id grammar",
    LEGACY_DEFAULT_RUNG,
)
# Design §5 rung 5: the grandfathered default when nothing else decides.
LEGACY_DEFAULT_TYPE = "rfe"
# The batch mapping form (design §5 rung 2) has exactly these two root keys.
BATCH_MAPPING_KEYS = ("type", "items")
EXIT_AMBIGUOUS = 3

# Sentinel for Descriptor.get(): "no default supplied" must be distinguishable from None,
# because null is a legitimate descriptor value (e.g. pipeline.rubric.export for initiative).
MISSING = object()


# ASCII integers only: str.isdigit() also accepts "²" and Arabic-Indic digits that int() rejects.
_INDEX_RE = re.compile(r"-?[0-9]+")


class RegistryError(ValueError):
    """The registry could not be loaded (bad root, unreadable/invalid descriptor, duplicate),
    or an override / workspace file is invalid."""


class ResolveError(RegistryError):
    """``resolve`` could not decide: unknown type, conflicting signals, invalid batch or
    artifact input (``exit_code`` 1) or an ambiguous headless run (``exit_code`` 3)."""

    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


def binding_env_var(type_name, field):
    """Return the env var name that overrides binding ``field`` for ``type_name``.

    >>> binding_env_var("rfe", "PROJECT")
    'RFE_CREATOR_BINDING_RFE_PROJECT'
    >>> binding_env_var("docs-request", "issue_type")
    'RFE_CREATOR_BINDING_DOCS_REQUEST_ISSUE_TYPE'
    """
    token = re.sub(r"[^A-Z0-9]", "_", type_name.upper())
    return f"{BINDING_ENV_PREFIX}{token}_{field.upper()}"


def parse_extra_roots(value):
    """Split an ``RFE_CREATOR_EXTRA_TYPES`` value into a list of Paths (empty entries dropped)."""
    if not value:
        return []
    return [Path(p).expanduser() for p in value.split(os.pathsep) if p.strip()]


def is_headless(env, flag=False):
    """The one headless predicate (design §3.5, PR-3 D4).

    True when ``flag`` is set (an explicit ``--headless``) or when ``env`` carries a
    headless/CI marker: ``RFE_CREATOR_HEADLESS``, ``CI`` or ``GITHUB_ACTIONS`` set to anything
    but an empty/false value. The flag can only add: an explicit ``False`` does not switch a
    marked environment back to interactive.
    """
    if flag:
        return True
    for var in HEADLESS_MARKER_VARS:
        value = env.get(var, "")
        if str(value).strip().lower() not in _FALSE_VALUES:
            return True
    return False


def _canonical(path):
    """Canonical form used to compare a drop-in root against the allowlist."""
    return Path(path).expanduser().resolve()


def _flatten(mapping, prefix=""):
    """Flatten nested maps into dotted keys; non-map values (strings, lists) are kept as-is."""
    out = {}
    for key, value in mapping.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{dotted}."))
        else:
            out[dotted] = value
    return out


def _env_value(env, var):
    """The stripped string value of ``var`` in ``env``; blank or non-string counts as unset."""
    value = env.get(var, "")
    return value.strip() if isinstance(value, str) else ""


class Descriptor:
    """Thin wrapper over one parsed ``type.yaml`` dict.

    The raw mapping is available as ``data``; the accessors below are the projections the
    pin tests and the adopted scripts consume. Accessors return DESCRIPTOR values; only
    ``binding()`` applies the §3.2.1 override overlay, and ``owns_effective()`` is the one
    ownership test over it.
    """

    def __init__(self, name, data, path=None, env=None):
        self.name = name
        self.data = data
        self.path = Path(path) if path is not None else None
        # Default environment for binding(); the registry hands down the one it was built with
        # so reg.get(t).binding() and reg.bindings()[t] always agree.
        self._env = env

    def __repr__(self):
        return f"Descriptor({self.name!r}, path={str(self.path) if self.path else None!r})"

    def typed_path(self, rel):
        """The absolute path of a typed file a repo_path field names (``pipeline.prompts.*``,
        ``pipeline.dimensions[].prompt``). A path under this type's own directory
        (``types/<name>/...``) resolves against the descriptor's directory — a drop-in root
        carries its typed files wherever it lives — and any other plugin-relative path against
        the plugin root. This is the existence check's view (gate 1); what a launch block hands
        a subagent is ``launch_path``. An empty value (an absent optional file) stays empty."""
        if not rel:
            return ""
        own = f"types/{self.name}/"
        if self.path is not None and rel.startswith(own):
            return str((self.path.parent / rel[len(own) :]).resolve())
        return str((PLUGIN_ROOT / rel).resolve())

    def launch_path(self, rel):
        """The path a launch block hands a subagent for the typed file ``rel`` names.

        ``rel`` as written when the working directory carries that very file at that path —
        the checkout is the cwd (production), or ``types/`` is linked into the cwd (the
        bootstrap does that for the eval's run directory and a marketplace project) — and the
        absolute ``typed_path`` when it does not: a drop-in root outside the checkout, a
        working directory the bootstrap has not prepared, or a copy of the file in the working
        directory (never trusted, however identical: it could be swapped before the subagent
        reads it). The decision is per file: a partially vendored ``types/`` tree renders a
        mixed block.

        Relative is the default on purpose: a subagent whose first instruction names a file
        under one absolute root infers that root for every relative path that follows
        (``artifacts/...``, ``.context/...``) and reads them from the wrong tree — in the
        2026-09-23 evals 27 of 31 rfe feasibility agents did, and one recovered through
        ``cat`` so the transcript check lost its evidence. Keeping every path the subagent
        sees in one frame, the cwd, removes the inference; the absolute form is the fallback
        for a layout where no relative path resolves. Empty stays empty."""
        if not rel:
            return ""
        absolute = self.typed_path(rel)
        return rel if _same_file_at(rel, absolute) else absolute

    # -- generic access -------------------------------------------------------------------

    def get(self, dotted, default=MISSING):
        """Return the value at a dotted path (``"conventions.labels.split_quarantine"``).

        Integer segments index into lists (``"pipeline.dimensions.0.name"``). Raises
        ``KeyError`` when the path is absent and no ``default`` was given.
        """
        node = self.data
        for segment in dotted.split("."):
            if isinstance(node, dict) and segment in node:
                node = node[segment]
            elif isinstance(node, list) and _INDEX_RE.fullmatch(segment):
                try:
                    node = node[int(segment)]
                except IndexError:
                    node = MISSING
            else:
                node = MISSING
            if node is MISSING:
                if default is MISSING:
                    raise KeyError(f"{self.name}: no such descriptor field {dotted!r}")
                return default
        return node

    # -- identity -------------------------------------------------------------------------

    @property
    def tracker(self):
        """The tracker discriminator (``identity.tracker``): ``jira`` today."""
        return self.get("identity.tracker")

    def _tracker_block(self):
        tracker = self.tracker
        block = self.get(f"identity.{tracker}", None)
        if not isinstance(block, dict):
            raise KeyError(
                f"{self.name}: identity.tracker is {tracker!r} but identity.{tracker} is missing"
            )
        return block

    @property
    def key_prefixes(self):
        """Descriptor tracker key prefixes; the head is the write prefix, all are read prefixes.

        A github-style binding (design §8.6) declares ``alias_prefix`` instead; it is exposed as
        the single key prefix so every prefix-union consumer works unchanged.
        """
        block = self._tracker_block()
        if "key_prefixes" in block:
            return list(block["key_prefixes"])
        if "alias_prefix" in block:
            return [block["alias_prefix"]]
        return []

    @property
    def write_prefix(self):
        """The descriptor write prefix (``key_prefixes[0]``), or ``None`` when none is declared."""
        prefixes = self.key_prefixes
        return prefixes[0] if prefixes else None

    @property
    def local_prefix(self):
        return self.get("identity.local_prefix")

    @property
    def local_id_pattern(self):
        return self.get("identity.local_id_pattern")

    @property
    def id_field(self):
        return self.get("identity.id_field")

    # -- identity detection (design §5 rung 3, single-type routers) -----------------------

    def _matches_local_id(self, item_id):
        pattern = self.get("identity.local_id_pattern", None)
        return bool(pattern) and re.fullmatch(pattern, item_id) is not None

    def _has_key_prefix(self, item_id):
        return any(item_id.startswith(p) for p in self.key_prefixes if p)

    def _has_local_prefix(self, item_id):
        local = self.get("identity.local_prefix", None)
        return bool(local) and item_id.startswith(local)

    def owns(self, item_id):
        """True when ``item_id`` is one of this type's ids (design §5 rung 3, single type).

        The same ladder as ``TypeRegistry.detect`` applied to one descriptor: a full match of
        ``identity.local_id_pattern`` (most specific), else a tracker ``key_prefixes`` prefix
        match (Jira grammar), else an ``identity.local_prefix`` prefix match. Case-sensitive;
        ``None`` and the empty string never match. DESCRIPTOR values only — the §3.2.1 binding
        overlay is consulted by ``owns_effective``, ``TypeRegistry.candidates`` and ``resolve``,
        not here.
        """
        if not isinstance(item_id, str) or not item_id:
            return False
        return (
            self._matches_local_id(item_id)
            or self._has_key_prefix(item_id)
            or self._has_local_prefix(item_id)
        )

    def owns_effective(self, item_id, env=None, workspace=None):
        """``owns`` over the EFFECTIVE binding (design §3.2.1; PR-3c, the writers).

        The same three definite rungs as ``owns`` — ``local_id_pattern`` full match, a
        ``key_prefixes`` prefix match, a ``local_prefix`` prefix match — applied to
        ``binding(env, workspace=workspace)`` with the read parity ``candidates()`` applies:
        the overridden write prefix (``<PROJECT>-``) and the D13 re-rendered local pattern /
        prefix count, and so do the descriptor's own (an item minted before the override is
        still this type's). Never the provisional tracker-grammar rung: ownership is definite
        or absent. ``env`` defaults as for ``binding()`` (the registry's environment, else
        ``os.environ``), so with no override set this equals ``owns``; ``owns`` and
        ``TypeRegistry.detect`` stay descriptor-only for the per-id routers. Case-sensitive;
        ``None`` and the empty string never match.
        """
        if not isinstance(item_id, str) or not item_id:
            return False
        binding = self.binding(env, workspace=workspace)
        return any(
            _rung_matches(rung, self, binding, item_id)
            for rung in CANDIDATE_RUNGS
            if rung != PROVISIONAL_RUNG
        )

    # -- effective binding (design §3.2.1) --------------------------------------------------

    def binding(self, env=None, workspace=None, shorthand=False):
        """Return the EFFECTIVE tracker binding (design §3.2.1).

        ``identity.<tracker>`` overlaid, in precedence order, with the type-scoped
        ``RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,LOCAL_PREFIX}`` variables from ``env``
        (default: the registry's environment, else ``os.environ``); with the bare
        ``JIRA_PROJECT`` / ``JIRA_ISSUE_TYPE`` shorthand when ``shorthand`` is true (``resolve``
        passes it for the resolved type only); and with ``workspace[<type>][<tracker>]`` — the
        mapping ``load_workspace_bindings`` returns (obtain it through
        ``TypeRegistry.workspace_bindings`` so the headless trust boundary applies).

        Keys: ``tracker``, ``project``, ``issue_type``, ``key_prefixes`` (write prefix first —
        derived ``<PROJECT>-`` when the project is overridden, descriptor prefixes kept as read
        prefixes), ``local_prefix`` (effective), ``local_id_pattern`` (effective — re-rendered
        for an overridden local prefix, PR-3 D13), every other key of the binding block
        verbatim, ``source`` (``descriptor``, or the contributing sources ``+``-joined in
        precedence order: ``env``, ``shorthand``, ``workspace``) and ``overrides`` (the
        overridden field names in ``project, issue_type, local_prefix`` order). Blank
        variables count as unset; every value passes the same grammar checks.
        """
        if env is None:
            env = self._env if self._env is not None else os.environ
        block = self._tracker_block()
        effective = {"tracker": self.tracker}
        # A deep copy: the returned dict must never alias descriptor data (state_map and
        # friends are nested mappings a caller may edit without corrupting the registry).
        effective.update(copy.deepcopy(block))
        effective.setdefault("project", None)
        effective.setdefault("issue_type", None)
        effective["key_prefixes"] = self.key_prefixes
        descriptor_prefix = self.get("identity.local_prefix", None)
        effective["local_prefix"] = descriptor_prefix
        effective["local_id_pattern"] = self.get("identity.local_id_pattern", None)

        layers = [("env", self._env_overrides(env))]
        if shorthand:
            layers.append(("shorthand", self._shorthand_overrides(env)))
        if workspace:
            layers.append(("workspace", self._workspace_overrides(workspace)))
        overrides, origin = {}, {}
        for source, values in layers:
            for name, value in values.items():
                if name not in overrides:
                    overrides[name] = value
                    origin[name] = source

        if "project" in overrides:
            derived = f"{overrides['project']}-"
            read_prefixes = [p for p in effective["key_prefixes"] if p != derived]
            effective["key_prefixes"] = [derived] + read_prefixes
        if "local_prefix" in overrides:
            effective["local_id_pattern"] = _rerender_local_id_pattern(
                self.name,
                effective["local_id_pattern"],
                descriptor_prefix,
                overrides["local_prefix"],
            )
        effective.update(overrides)
        contributing = [s for s in BINDING_SOURCES if s in origin.values()]
        effective["source"] = "+".join(contributing) if contributing else "descriptor"
        effective["overrides"] = [f for f in BINDING_FIELD_ORDER if f in overrides]
        return effective

    def _env_overrides(self, env):
        out = {}
        for suffix, name in BINDING_OVERRIDE_FIELDS.items():
            var = binding_env_var(self.name, suffix)
            value = _env_value(env, var)
            if value:
                out[name] = _validate_override(var, name, value)
        return out

    def _shorthand_overrides(self, env):
        out = {}
        for var, name in SHORTHAND_ENV_VARS.get(self.tracker, {}).items():
            value = _env_value(env, var)
            if value:
                out[name] = _validate_override(var, name, value)
        return out

    def _workspace_overrides(self, workspace):
        per_type = workspace.get(self.name) if isinstance(workspace, dict) else None
        block = per_type.get(self.tracker) if isinstance(per_type, dict) else None
        if not isinstance(block, dict):
            return {}
        out = {}
        for name in BINDING_FIELD_ORDER:
            if name in block:
                label = f"{WORKSPACE_FILENAME} bindings.{self.name}.{self.tracker}.{name}"
                out[name] = _validate_override(label, name, block[name])
        return out

    # -- layout / conventions / schema ------------------------------------------------------

    def dirs(self, form="artifacts"):
        """Return the ``dirs`` map: ``form="artifacts"`` -> ``artifacts/rfe-tasks``,
        ``form="bare"`` -> ``rfe-tasks`` (Q13: both spellings exist among today's consumers).
        """
        if form not in DIR_FORMS:
            raise ValueError(f"unknown dirs form {form!r}; expected one of {DIR_FORMS}")
        result = {}
        for key, value in self.get("dirs").items():
            if form == "bare" and isinstance(value, str):
                value = _bare_dir(value)
            result[key] = value
        return result

    @property
    def labels(self):
        """``conventions.labels`` flattened: nested maps become ``feasibility.feasible`` keys.

        The nested form stays reachable through ``get("conventions.labels.feasibility")``.
        """
        return _flatten(self.get("conventions.labels"))

    @property
    def score_fields(self):
        return list(self.get("schema.review.score_fields"))

    @property
    def parent_key_pattern(self):
        """``^(a|b|c)$`` over ``conventions.parent_key_patterns``, or ``None`` when the
        descriptor declares no patterns.

        The ONE join behind every ``parent_key`` check: ``artifact_utils`` puts it on the
        ``<type>-task`` schema and ``validate_batch_input`` applies it to batch entries, so the
        task schema and the batch validator agree by construction (PR-1 checklist Q14,
        reconciled in PR-3b). Alternatives are used verbatim (unanchored regex fragments such
        as ``RHAISTRAT-\\d+``), exactly as the hand-written literals were.
        """
        patterns = self.get("conventions.parent_key_patterns", None)
        if not patterns:
            return None
        return "^(" + "|".join(patterns) + ")$"

    def parent_key_pattern_effective(self, env=None):
        """``parent_key_pattern`` over the EFFECTIVE binding (design §3.2.1; PR-3c, the writers).

        Under a project override the effective write prefix's ``<PROJECT>-\\d+`` is prepended
        as an alternative when it is not already one of the descriptor's
        ``conventions.parent_key_patterns``, so a child of a parent fetched under the override
        (``parent_key: KONFLUX-1``) passes the task schema and the batch validator. With no
        project override — or when the derived alternative is already declared — the string is
        ``parent_key_pattern`` byte for byte; ``None`` when the descriptor declares no patterns.
        ``env`` defaults as for ``binding()`` and a malformed override raises ``RegistryError``
        the same way (``artifact_utils`` and ``validate_batch_input`` fall back to
        ``parent_key_pattern`` then; the writers report the value).
        """
        patterns = self.get("conventions.parent_key_patterns", None)
        if not patterns:
            return None
        alternatives = list(patterns)
        binding = self.binding(env)
        if "project" in binding["overrides"]:
            derived = binding["key_prefixes"][0] + r"\d+"
            if derived not in alternatives:
                alternatives.insert(0, derived)
        return "^(" + "|".join(alternatives) + ")$"

    def accepted_pairs(self, binding, key):
        """The ``(project, issue_type)`` pairs a fetched issue behind ``key`` may carry to be this
        type's own under ``binding`` (design §3.2.1; PR-3c, the writers).

        The effective pair — what the run writes under — always; and, when ``key`` carries one
        of the DESCRIPTOR key prefixes, the descriptor pair as well: such a key names an item of
        the type created before the override (an ``RHAIRFE-`` issue while the rfe project is
        overridden to ``KONFLUX``), which the writers still update in place and the fetch still
        admits. With no override the two pairs are one. The key stem is never a witness (D9):
        it only widens what the fetched ``project.key`` / ``issuetype.name`` may show.
        """
        pairs = [(binding.get("project"), binding.get("issue_type"))]
        if isinstance(key, str) and any(key.startswith(p) for p in self.key_prefixes if p):
            block = self._tracker_block()
            descriptor_pair = (block.get("project"), block.get("issue_type"))
            if descriptor_pair not in pairs:
                pairs.append(descriptor_pair)
        return pairs


def render_pairs(pairs):
    """The text a writer's refusal puts after ``binds``: ``(A, B)`` for one accepted pair,
    ``(A, B) or, for a pre-override key, (C, D)`` for the two ``Descriptor.accepted_pairs``
    returns for a key that carries a descriptor prefix under an override."""
    rendered = [f"({project}, {issue_type})" for project, issue_type in pairs]
    if len(rendered) == 1:
        return rendered[0]
    return f"{rendered[0]} or, for a pre-override key, {rendered[1]}"


def _bare_dir(value):
    prefix = ARTIFACTS_DIR + "/"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _validate_override(var, field, value):
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{var}={value!r}: expected a non-empty string")
    if field == "project" and not _PROJECT_KEY_RE.match(value):
        raise RegistryError(f"{var}={value!r}: expected an upper-case tracker project key")
    if field == "local_prefix" and not _LOCAL_PREFIX_RE.match(value):
        raise RegistryError(f"{var}={value!r}: expected an upper-case prefix ending in '-'")
    return value


def _rerender_local_id_pattern(type_name, pattern, descriptor_prefix, new_prefix):
    """Re-render ``identity.local_id_pattern`` for an overridden local prefix (PR-3 D13).

    The descriptor pattern must start with ``^`` followed by ``descriptor_prefix`` — either
    literally (``^RFE-\\d+$``) or as ``re.escape(descriptor_prefix)`` (``^RFE\\-\\d+$``); that
    head is replaced by ``^`` + ``re.escape(new_prefix)`` and the result must compile and
    match against ``f"{new_prefix}1"`` without a regex error. Anything else is a hard error
    naming the type and the pattern. A descriptor without a pattern has nothing to pair and
    keeps ``None``.
    """
    if not pattern:
        return pattern
    heads = []
    if isinstance(descriptor_prefix, str) and descriptor_prefix:
        heads = sorted({descriptor_prefix, re.escape(descriptor_prefix)}, key=len, reverse=True)
    for head in heads:
        anchored = f"^{head}"
        if pattern.startswith(anchored):
            rendered = f"^{re.escape(new_prefix)}{pattern[len(anchored) :]}"
            try:
                re.compile(rendered).fullmatch(f"{new_prefix}1")
            except re.error as exc:
                raise RegistryError(
                    f"{type_name}: identity.local_id_pattern {pattern!r} re-rendered for local "
                    f"prefix {new_prefix!r} is not a valid regex ({rendered!r}): {exc}"
                ) from exc
            return rendered
    raise RegistryError(
        f"{type_name}: cannot override local_prefix to {new_prefix!r}: identity.local_id_pattern "
        f"{pattern!r} does not start with '^' followed by the descriptor local_prefix "
        f"{descriptor_prefix!r} (PR-3 D13: prefix and pattern are paired)"
    )


def load_workspace_bindings(root):
    """Return the ``bindings`` mapping of ``<root>/rfe-creator.yaml``, or ``{}``.

    Shape: ``{<type name>: {<tracker>: {project | issue_type | local_prefix: <str>}}}``. A
    missing file, an empty file or a file without a ``bindings`` block yields ``{}``; any
    other shape (a non-mapping level, an unknown field, a blank or non-string value) raises
    ``RegistryError`` naming the path and the offending key. ``root`` ``None`` reads nothing.
    Whether the mapping may be USED is decided by ``TypeRegistry.workspace_bindings`` (the
    §3.2.1 g trust boundary); this function only reads.
    """
    if root is None:
        return {}
    path = Path(root).expanduser() / WORKSPACE_FILENAME
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: invalid YAML: {exc}") from exc
    except OSError as exc:
        raise RegistryError(f"{path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RegistryError(f"{path}: expected a mapping at the top level, got {_shape(data)}")
    bindings = data.get(WORKSPACE_BINDINGS_KEY)
    if bindings is None:
        return {}
    if not isinstance(bindings, dict):
        raise RegistryError(
            f"{path}: '{WORKSPACE_BINDINGS_KEY}' must be a mapping of type name -> tracker -> "
            f"fields, got {_shape(bindings)}"
        )
    result = {}
    for type_name, trackers in bindings.items():
        where = f"{path}: {WORKSPACE_BINDINGS_KEY}.{type_name}"
        if not isinstance(type_name, str) or not isinstance(trackers, dict):
            raise RegistryError(f"{where}: expected a mapping of tracker -> fields")
        result[type_name] = {}
        for tracker, fields in trackers.items():
            where = f"{path}: {WORKSPACE_BINDINGS_KEY}.{type_name}.{tracker}"
            if not isinstance(tracker, str) or not isinstance(fields, dict):
                raise RegistryError(f"{where}: expected a mapping of binding fields")
            unknown = sorted(str(k) for k in fields if k not in BINDING_FIELD_ORDER)
            if unknown:
                raise RegistryError(
                    f"{where}: unknown field(s) {', '.join(unknown)}; overridable fields: "
                    f"{', '.join(BINDING_FIELD_ORDER)}"
                )
            for name, value in fields.items():
                if not isinstance(value, str) or not value.strip():
                    raise RegistryError(
                        f"{where}.{name}: expected a non-empty string, got {value!r}"
                    )
            result[type_name][tracker] = dict(fields)
    return result


def _shape(value):
    """Human-readable YAML shape for error messages."""
    if value is None:
        return "nothing"
    if isinstance(value, dict):
        keys = ", ".join(str(k) for k in value) or "(no keys)"
        return f"a mapping with keys {keys}"
    return type(value).__name__


def _binding_identity(binding):
    """The per-tracker identity key the uniqueness rule compares (design §3.3 rule 1):
    jira -> ``(tracker, project, issue_type)``; other trackers -> their own pair."""
    tracker = binding.get("tracker")
    if tracker == "jira":
        return (tracker, binding.get("project"), binding.get("issue_type"))
    return (tracker, binding.get("repo", binding.get("project")), str(binding.get("kind")))


@dataclass(frozen=True)
class Candidates:
    """Result of ``TypeRegistry.candidates``: the descriptors matching at the winning rung
    (``names()`` order), the rung name (``None`` when nothing matched) and whether the rung
    is provisional (only ``tracker_grammar`` is)."""

    matches: list
    rung: "str | None"
    provisional: bool

    @property
    def names(self):
        return [desc.name for desc in self.matches]


def _rung_matches(rung, desc, binding, item_id):
    # Read parity under a LOCAL_PREFIX override: the effective (re-rendered, D13) pattern and
    # prefix govern minting, but the descriptor's own stay READ forms, exactly as an overridden
    # project keeps the descriptor key prefixes as read prefixes — the local ids a deployment
    # minted before the override are still its own.
    if rung == "local_id_pattern":
        patterns = (binding.get("local_id_pattern"), desc.get("identity.local_id_pattern", None))
        return any(p and re.fullmatch(p, item_id) is not None for p in patterns)
    if rung == "key_prefix":
        # binding() already lists every descriptor prefix as a read prefix after the write one.
        return any(item_id.startswith(p) for p in binding.get("key_prefixes") or [] if p)
    if rung == "local_prefix":
        prefixes = (binding.get("local_prefix"), desc.get("identity.local_prefix", None))
        return any(p and item_id.startswith(p) for p in prefixes)
    grammar = TRACKER_KEY_GRAMMARS.get(binding.get("tracker"))
    return grammar is not None and grammar.fullmatch(item_id) is not None


class TypeRegistry:
    """Static enumeration of ``<root>/<name>/type.yaml`` descriptors across one or more roots."""

    def __init__(
        self,
        root=None,
        extra_roots=None,
        env=None,
        allowlisted_extra_roots=None,
        workspace_root=None,
        headless=False,
    ):
        self.env = os.environ if env is None else env
        # The explicit --headless flag (PR-3 D4): together with the env markers it is the one
        # predicate for the extra-roots seam, the workspace file, resolve and the ownership
        # check; a per-call ``headless`` argument can add to it, never switch it back.
        self.headless = bool(headless)
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        # Directory whose rfe-creator.yaml may carry a bindings: block; None (the default)
        # reads no file at all — the cwd is never probed implicitly.
        self.workspace_root = Path(workspace_root).expanduser() if workspace_root else None
        # RFE_CREATOR_EXTRA_TYPES entries dropped by the headless/CI gate (design §3.5).
        self.ignored_extra_roots = []
        if extra_roots is None:
            extra_roots = self._extra_roots_from_env(allowlisted_extra_roots)
        self.extra_roots = [Path(r) for r in extra_roots]
        self.roots = [self.root] + self.extra_roots
        self._types = {}
        for candidate in self.roots:
            self._scan(candidate)

    def _extra_roots_from_env(self, allowlisted):
        """The env seam (PR1-05): every RFE_CREATOR_EXTRA_TYPES entry in a normal run; in a
        headless/CI run — ``is_headless(self.env, self.headless)``, so an explicit
        ``--headless`` gates the seam exactly like a marker — only the entries whose canonical
        path is allowlisted, by the ``allowlisted_extra_roots`` argument or
        RFE_CREATOR_EXTRA_TYPES_ALLOWLIST."""
        roots = parse_extra_roots(self.env.get(EXTRA_ROOTS_ENV, ""))
        if not roots or not is_headless(self.env, self.headless):
            return roots
        allowed = {_canonical(p) for p in (allowlisted or [])}
        allowed.update(
            _canonical(p) for p in parse_extra_roots(self.env.get(EXTRA_ROOTS_ALLOWLIST_ENV, ""))
        )
        kept = []
        for candidate in roots:
            if _canonical(candidate) in allowed:
                kept.append(candidate)
            else:
                self.ignored_extra_roots.append(candidate)
        if self.ignored_extra_roots:
            dropped = ", ".join(str(p) for p in self.ignored_extra_roots)
            print(
                f"type_registry: headless/CI run — ignoring {EXTRA_ROOTS_ENV} root(s) not in "
                f"{EXTRA_ROOTS_ALLOWLIST_ENV}: {dropped}",
                file=sys.stderr,
            )
        return kept

    def _scan(self, root):
        if not root.is_dir():
            raise RegistryError(f"type root not found: {root}")
        resolved_root = root.resolve()
        for entry in sorted(root.iterdir()):
            # "_schema" and similar support dirs are not types; dot-dirs are never types.
            if not entry.is_dir() or entry.name.startswith(("_", ".")):
                continue
            descriptor_path = entry / DESCRIPTOR_FILENAME
            if not descriptor_path.is_file():
                continue
            real = descriptor_path.resolve()
            if not real.is_relative_to(resolved_root):
                raise RegistryError(f"{descriptor_path} resolves outside its root {root}: {real}")
            name = entry.name
            if name in self._types:
                raise RegistryError(
                    f"duplicate type {name!r}: {self._types[name].path} and {descriptor_path}"
                )
            self._types[name] = Descriptor(
                name, _read_descriptor(descriptor_path, name), path=real, env=self.env
            )

    # -- enumeration ----------------------------------------------------------------------

    def names(self):
        """Type names: ``rfe`` first when present, then the rest sorted (today's argparse order)."""
        rest = sorted(n for n in self._types if n != "rfe")
        return (["rfe"] if "rfe" in self._types else []) + rest

    def choices(self):
        """Alias of ``names()`` — the value for argparse ``choices=``."""
        return self.names()

    def get(self, name):
        try:
            return self._types[name]
        except KeyError:
            raise KeyError(
                f"unknown type {name!r}; available: {', '.join(self.names()) or '(none)'}"
            ) from None

    def __iter__(self):
        return iter([self._types[n] for n in self.names()])

    def __len__(self):
        return len(self._types)

    def __contains__(self, name):
        return name in self._types

    # -- effective bindings (design §3.2.1) -------------------------------------------------

    def bindings(self, env=None, workspace=None):
        """Effective binding per type (design §3.2.1), keyed by type name in ``names()`` order.
        ``workspace`` is the mapping ``workspace_bindings()`` returns (or ``None``)."""
        return {name: self._types[name].binding(env, workspace=workspace) for name in self.names()}

    def workspace_bindings(self, headless=None):
        """The workspace ``bindings:`` mapping this run may honour (design §3.2.1 g).

        Reads ``<workspace_root>/rfe-creator.yaml`` (nothing when the registry was built
        without ``workspace_root``). In an interactive run the mapping is returned as read. In
        a headless/CI run — ``is_headless(self.env, headless)``, or a registry built with
        ``headless=True`` — the file is never honoured:
        the result is ``{}`` and, when the file exists and carries a non-empty ``bindings``
        block, exactly one stderr line says it was ignored. A malformed file raises in both
        modes.
        """
        if self.workspace_root is None:
            return {}
        mapping = load_workspace_bindings(self.workspace_root)
        # A block keyed by an unregistered name is a typo, not a no-op: it fails like a typo in
        # a field name does, in both modes (the file is malformed for this registry).
        unknown = [name for name in mapping if name not in self._types]
        if unknown:
            raise RegistryError(
                f"{self.workspace_root / WORKSPACE_FILENAME}: {WORKSPACE_BINDINGS_KEY}."
                f"{unknown[0]}: unknown type; registered types: "
                f"{', '.join(self.names()) or '(none)'}"
            )
        if not is_headless(self.env, self.headless or bool(headless)):
            return mapping
        if mapping:
            print(
                f"type_registry: headless/CI run — ignoring the workspace bindings file "
                f"{self.workspace_root / WORKSPACE_FILENAME} ({WORKSPACE_BINDINGS_KEY}: is "
                f"honoured only in an interactive run; use {BINDING_ENV_PREFIX}* variables)",
                file=sys.stderr,
            )
        return {}

    # -- detection --------------------------------------------------------------------------

    def detect(self, item_id):
        """Return the ``Descriptor`` that owns ``item_id``, or ``None`` when no type does.

        The design §5 rung-3 deterministic id signal for the per-id routers, single candidate
        by construction. Three prefix rungs, each tried across every type in ``names()`` order
        before the next (so the most specific signal always wins, whatever the type order):

        1. ``identity.local_id_pattern`` full-matches ``item_id`` (``RFE-001``, ``INIT-001``);
        2. ``item_id`` starts with one of the tracker's ``identity.<tracker>.key_prefixes``
           (``RHAIRFE-1``, ``RHOAIENG-1`` — the Jira key grammar, design §3.6);
        3. ``item_id`` starts with ``identity.local_prefix`` — the parity rung: the sniffs this
           replaced tested ``startswith(local_prefix) or startswith(key_prefix)``, so a
           malformed local id (``INIT-x``) stays with the type it went to before PR-2. Ranked
           last so a tracker key always beats a local prefix, and kept separate so a later PR
           can drop it for types whose ``local_prefix`` is only a placeholder (the epic
           fixture; D6 deferred).

        Anything else — a peer pipeline's key (``RHAISTRAT-1``), lower-case, empty, ``None`` —
        returns ``None``; callers keep their own default (today: ``detect(x) or get("rfe")``).
        Uses DESCRIPTOR values only. The multi-candidate form over EFFECTIVE bindings, with the
        provisional tracker-grammar rung, is ``candidates()``; ``resolve`` builds on that.
        """
        if not isinstance(item_id, str) or not item_id:
            return None
        for rung in (
            Descriptor._matches_local_id,
            Descriptor._has_key_prefix,
            Descriptor._has_local_prefix,
        ):
            for desc in self:
                if rung(desc, item_id):
                    return desc
        return None

    def candidates(self, item_id, env=None, workspace=None):
        """Return the ``Candidates`` for ``item_id`` over EFFECTIVE bindings (design §5, D5).

        Each type's binding is computed once (``env`` / ``workspace`` as for ``bindings()``),
        then the rungs run in order and the first with at least one match wins, returning
        EVERY type matching at that rung in ``names()`` order:

        1. ``local_id_pattern`` — the effective pattern (re-rendered for an overridden local
           prefix, D13) or the descriptor's own pattern, kept as a read form, full-matches;
        2. ``key_prefix`` — a prefix match on any effective ``key_prefixes`` entry (the
           overridden write prefix first, the descriptor prefixes kept as read prefixes);
        3. ``local_prefix`` — a prefix match on the effective or the descriptor local prefix
           (parity rung);
        4. ``tracker_grammar`` — the Jira adapter's generic key grammar
           ``^[A-Z][A-Z0-9]+-[0-9]+$``: every Jira-bound type is a PROVISIONAL candidate
           (``provisional=True``), to be discriminated post-fetch on ``(project, issue_type)``.

        ``None``, the empty string and non-strings yield no matches (``rung None``).
        """
        if not isinstance(item_id, str) or not item_id:
            return Candidates([], None, False)
        bindings = self.bindings(env, workspace)
        for rung in CANDIDATE_RUNGS:
            matches = [d for d in self if _rung_matches(rung, d, bindings[d.name], item_id)]
            if matches:
                return Candidates(matches, rung, rung == PROVISIONAL_RUNG)
        return Candidates([], None, False)


def _read_descriptor(path, expected_name):
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path}: invalid YAML: {exc}") from exc
    except OSError as exc:
        raise RegistryError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError(f"{path}: descriptor must be a mapping, got {type(data).__name__}")
    declared = data.get("type")
    if declared != expected_name:
        raise RegistryError(
            f"{path}: 'type: {declared}' does not match its directory name {expected_name!r}"
        )
    return data


def load(
    root=None,
    extra_roots=None,
    env=None,
    allowlisted_extra_roots=None,
    workspace_root=None,
    headless=False,
):
    """Load a fresh registry (never cached — callers that want one instance keep it).

    ``headless=True`` is the explicit ``--headless`` flag (PR-3 D4): the registry then gates
    ``RFE_CREATOR_EXTRA_TYPES``, ignores the workspace file and treats ``resolve`` as headless
    even when no ``RFE_CREATOR_HEADLESS`` / ``CI`` / ``GITHUB_ACTIONS`` marker is set.
    """
    return TypeRegistry(
        root=root,
        extra_roots=extra_roots,
        env=env,
        allowlisted_extra_roots=allowlisted_extra_roots,
        workspace_root=workspace_root,
        headless=headless,
    )


# -- ownership check (design §3.2.1 g, §3.3 rule 1 at runtime) ------------------------------


def assert_registered_binding(
    desc, env=None, registry=None, workspace=None, headless=None, shorthand=False
):
    """Prove that ``desc``'s effective binding may be written to (design §3.2.1 g).

    Computes the effective binding (``env`` defaults to the descriptor's registry environment,
    ``shorthand`` as for ``binding()`` — pass it for the resolved type) and raises
    ``RegistryError`` when (i) the run is headless (``is_headless(env, headless)``, or the
    ``registry`` was built with ``headless=True``) and any
    override came from the workspace file, or (ii) the effective identity — for Jira the
    ``(tracker, project, issue_type)`` triple — equals the effective identity of ANY OTHER
    registered type (ownership, not membership: an rfe override that selects the initiative
    pair is rejected even though the pair is registered). ``registry`` defaults to a fresh
    ``load()`` with the same ``env``. Returns the effective binding on success. The write
    scripts call this after ``resolve`` and before the first tracker write (PR-3c).
    """
    if env is None:
        env = desc._env if desc._env is not None else os.environ
    if registry is None:
        registry = load(env=env)
    binding = desc.binding(env, workspace=workspace, shorthand=shorthand)
    headless_run = is_headless(env, bool(headless) or registry.headless)
    if headless_run and "workspace" in binding["source"].split("+"):
        raise RegistryError(
            f"{desc.name}: binding override(s) {', '.join(binding['overrides'])} come from the "
            f"workspace file ({WORKSPACE_FILENAME}), which is not trusted in a headless/CI run; "
            f"set {BINDING_ENV_PREFIX}* variables instead (design §3.2.1 g)"
        )
    own = _binding_identity(binding)
    for other in registry:
        if other.name == desc.name:
            continue
        if _binding_identity(other.binding(env, workspace=workspace)) == own:
            raise RegistryError(
                f"{desc.name}: effective binding {own} (source: {binding['source']}) is the "
                f"binding registered for type {other.name!r}; a tracker binding must be owned by "
                f"exactly one type (design §3.3 rule 1) — fix the override or pass --type "
                f"{other.name}"
            )
    return binding


def assert_not_shorthand(type_name, binding):
    """Refuse a binding the bare ``JIRA_PROJECT`` / ``JIRA_ISSUE_TYPE`` shorthand contributed
    to (PR-3c, the writers).

    The artifact layer the writers share (``artifact_utils``: the id grammar in ``SCHEMAS``, the
    rename guard, the lookups) reads ``binding()`` WITHOUT the shorthand, so a shorthand-sourced
    project would create the issue in the tracker and then fail the rename, orphaning it on
    every retry. The shorthand therefore stays a ``resolve`` CLI verdict only; a writer calls
    this right after ``assert_registered_binding`` and gets one ``RegistryError`` naming the
    typed variables to set instead (``binding_env_var``), which it prints as its one ``Error:``
    line before any file or tracker access. Returns ``binding`` when no shorthand contributed.
    """
    if "shorthand" in (binding.get("source") or "").split("+"):
        raise RegistryError(
            "JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is not honoured by the artifact layer; set "
            f"{binding_env_var(type_name, 'PROJECT')} / _ISSUE_TYPE instead"
        )
    return binding


# -- resolve (design §5) --------------------------------------------------------------------


@dataclass
class Resolution:
    """Outcome of ``resolve``: the type (``type_name`` ``None`` when ambiguous), its
    descriptor, the deciding rung, whether the decision is provisional (tracker-grammar
    candidates only), the effective binding (shorthand included) and, when ambiguous, the
    candidate type names."""

    type_name: "str | None"
    desc: "Descriptor | None"
    rung: "str | None"
    provisional: bool = False
    binding: "dict | None" = None
    candidates: list = field(default_factory=list)

    @property
    def ambiguous(self):
        return self.type_name is None

    def line(self):
        """``TYPE RESOLVED: <type> (<rung>[; binding override <field>=<value> ...])`` — the
        override fields in ``project, issue_type, local_prefix`` order (design §3.2.1 c) — or
        ``TYPE AMBIGUOUS: <a>, <b> - pass --type`` when no type was decided."""
        if self.type_name is None:
            return f"TYPE AMBIGUOUS: {', '.join(self.candidates)} - pass --type"
        detail = self.rung
        overrides = (self.binding or {}).get("overrides") or []
        if overrides:
            rendered = " ".join(
                f"{name}={self.binding[name]}" for name in BINDING_FIELD_ORDER if name in overrides
            )
            detail = f"{detail}; binding override {rendered}"
        return f"TYPE RESOLVED: {self.type_name} ({detail})"

    def as_dict(self):
        return {
            "type": self.type_name,
            "rung": self.rung,
            "provisional": self.provisional,
            "binding": self.binding,
            "candidates": list(self.candidates),
            "line": self.line(),
        }


def resolve(
    registry,
    *,
    explicit_type=None,
    batch=None,
    batch_items=None,
    items_are_ids=True,
    artifact=None,
    ids=(),
    env=None,
    headless=None,
    workspace=None,
    binding=True,
):
    """Run the design §5 resolution ladder and return a ``Resolution``.

    Rungs, strongest first:

    1. ``--type`` — ``explicit_type``; an unregistered name is a ``ResolveError`` listing the
       registered types.
    2. ``batch type`` — ``batch`` is a YAML path, read through ``read_batch`` unless the
       caller already did: ``batch_items`` is that ``(type, items)`` pair (a batch-root
       consumer reads a pipe or ``/dev/stdin`` exactly once) and ``batch`` then only names
       the file in messages. A mapping root with ``type`` and ``items`` is the mapping form
       and its ``type`` is the signal (a disagreeing ``explicit_type`` is an error, D1). A
       bare list root is the legacy form: no rung-2 signal. Any item — in either form — that
       is a mapping carrying a ``type`` key is an error (D2: per-item types are rejected).
       Bare string items join ``ids`` when ``items_are_ids`` (the default — a batch of ids,
       as the ``resolve`` CLI takes); an entry-grammar caller (the speedrun batch, where
       every item is a mapping) passes ``items_are_ids=False`` so a bare string is its own
       malformed-entry error — never an id signal, never a D5 error. Any other root shape is
       an error.
    3. deterministic signals — ``artifact`` (a markdown path): a string frontmatter ``type``
       is ``frontmatter type``; else its parent directory name against every type's ``dirs``
       is ``artifact dir`` AND its stem joins ``ids`` (so a stem owned by another type is a
       conflict, not a silent directory win). Every id goes through ``candidates()``; a
       non-provisional match is ``id grammar``. One type across the deterministic signals
       resolves it at the strongest rung that produced it; two or more is a ``conflicting
       type signals`` error (never a question). With an explicit or batch type, rung-3
       signals are evaluated only to detect a deterministic signal for a DIFFERENT type
       (error). Provisional-only candidates resolve with ``provisional=True`` when they
       single out one type. An id no type owns is no signal in an interactive run; in a
       headless run with neither an explicit nor a batch type it is an error (D5: headless
       never guesses) — an artifact stem is never held to that rule.
    4. (interactive classification / picker — not implemented; the ambiguous result is its
       hook): several candidates and nothing deterministic -> headless
       (``is_headless(env, headless)``, ``env`` defaulting to the registry's and a registry
       built with ``headless=True`` counting as headless) raises
       ``ResolveError`` with ``exit_code`` 3 naming the candidates; interactive returns a
       ``Resolution`` with ``type_name None`` and ``candidates`` filled.
    5. ``legacy default`` — no signal at all -> ``rfe`` when registered, else an error.

    The binding on the result is ``desc.binding(env, workspace, shorthand=True)`` — the bare
    ``JIRA_PROJECT`` / ``JIRA_ISSUE_TYPE`` shorthand applies to the resolved type only.
    ``workspace`` is the mapping ``TypeRegistry.workspace_bindings`` returned; a headless run
    whose resolved binding was overridden from the workspace is refused (§3.2.1 g). With
    ``binding=False`` the caller wants the type verdict only (``type_name``, ``rung``,
    ``desc``): the RESOLVED type's binding is not computed — its overrides are neither read
    nor validated, so a caller that would never apply them cannot fail on them —
    ``Resolution.binding`` is ``None``, the line carries no override clause and the §3.2.1 g
    refusal, which belongs to the binding, is skipped with it. The id rungs are deliberately
    NOT affected: ``candidates()`` always matches over effective bindings (an overridden
    write prefix decides ownership), so when ids are resolved a malformed override in the
    environment still raises ``RegistryError`` whatever ``binding`` says. The batch-root
    consumers pass ``binding=False`` together with ``items_are_ids=False`` and so never reach
    that path (PR-3b); the writers take the binding (PR-3c).
    """
    if env is None:
        env = registry.env
    headless_run = is_headless(env, bool(headless) or registry.headless)
    registered = registry.names()
    registered_text = ", ".join(registered) or "(none)"

    def known(name, where):
        if name not in registry:
            raise ResolveError(
                f"unknown type {name!r} ({where}); registered types: {registered_text}"
            )

    def resolved(name, rung, provisional):
        desc = registry.get(name)
        if not binding:
            return Resolution(name, desc, rung, provisional, None, [])
        effective = desc.binding(env, workspace=workspace, shorthand=True)
        if headless_run and "workspace" in effective["source"].split("+"):
            raise ResolveError(
                f"{name}: binding override(s) {', '.join(effective['overrides'])} come from the "
                f"workspace file ({WORKSPACE_FILENAME}), which is not trusted in a headless/CI "
                f"run; set {BINDING_ENV_PREFIX}* variables instead (design §3.2.1 g)"
            )
        return Resolution(name, desc, rung, provisional, effective, [])

    # rung 1
    if explicit_type is not None:
        known(explicit_type, "--type")

    # rung 2 — (item_id, strict): strict ids are the caller's and the batch's; a derived
    # artifact stem is not (D5 applies to strict ids only).
    id_list = [(i, True) for i in ids if isinstance(i, str) and i]
    batch_type, batch_ids = None, []
    if batch_items is not None:
        if batch is None:
            raise ValueError("resolve(batch_items=...) needs batch: the file name for messages")
        batch_type, items = batch_items
        batch_ids = _batch_item_ids(batch, items)
    elif batch is not None:
        batch_type, batch_ids = _batch_signal(batch)
    if batch_type is not None:
        known(batch_type, f"{batch} type:")
        if explicit_type is not None and explicit_type != batch_type:
            raise ResolveError(
                f"--type {explicit_type} disagrees with {batch} type: {batch_type}; both are "
                f"explicit, so neither is guessed — pass one or make them agree (PR-3 D1)"
            )
    if items_are_ids:
        id_list.extend((i, True) for i in batch_ids)
    chosen = explicit_type if explicit_type is not None else batch_type

    # rung 3 signals: (rung, label, candidate type names)
    deterministic = []
    provisional = []
    if artifact is not None:
        fm_type, dir_name, stem = _artifact_signal(artifact)
        if fm_type is not None:
            known(fm_type, f"{artifact} frontmatter type:")
            deterministic.append(("frontmatter type", f"{artifact} (type: {fm_type})", [fm_type]))
        else:
            owners = [d.name for d in registry if dir_name and dir_name in _artifact_dirs(d)]
            if owners:
                deterministic.append(("artifact dir", f"{artifact} (dir {dir_name})", owners))
            id_list.append((stem, False))
    for item_id, strict in id_list:
        found = registry.candidates(item_id, env, workspace)
        if not found.matches:
            if strict and headless_run and chosen is None:
                raise ResolveError(
                    f"no registered type owns id {item_id!r} (registered types: "
                    f"{registered_text}); a headless run never guesses — fix the id or pass "
                    f"--type (PR-3 D5)"
                )
            continue
        if found.provisional:
            provisional.append((item_id, found.names))
        else:
            deterministic.append(("id grammar", item_id, found.names))

    if chosen is not None:
        conflicts = [(label, names) for _, label, names in deterministic if chosen not in names]
        if conflicts:
            origin = "--type" if explicit_type is not None else "batch type"
            raise ResolveError(
                f"conflicting type signals: {origin} {chosen} vs {_render_pairs(conflicts)}; a "
                f"run is single-typed — split the input by type"
            )
        return resolved(chosen, "--type" if explicit_type is not None else "batch type", False)

    if deterministic:
        common = set(deterministic[0][2])
        for _, _, names in deterministic[1:]:
            common &= set(names)
        if not common:
            pairs = [(label, names) for _, label, names in deterministic]
            raise ResolveError(
                f"conflicting type signals: {_render_pairs(pairs)}; a run is single-typed — "
                f"split the input by type or pass --type"
            )
        if len(common) == 1:
            (name,) = common
            rung = min(
                (r for r, _, names in deterministic if name in names), key=RESOLVE_RUNGS.index
            )
            return resolved(name, rung, False)
        ambiguous = [n for n in registered if n in common]
        labels = [label for _, label, _ in deterministic]
        only_provisional = False
    elif provisional:
        union = {n for _, names in provisional for n in names}
        if len(union) == 1:
            (name,) = union
            return resolved(name, "id grammar", True)
        ambiguous = [n for n in registered if n in union]
        labels = [label for label, _ in provisional]
        only_provisional = True
    else:
        if LEGACY_DEFAULT_TYPE in registry:
            return resolved(LEGACY_DEFAULT_TYPE, LEGACY_DEFAULT_RUNG, False)
        raise ResolveError(
            f"no type signal and the legacy default type {LEGACY_DEFAULT_TYPE!r} is not "
            f"registered; pass --type (registered types: {registered_text})"
        )

    if headless_run:
        raise ResolveError(
            f"ambiguous type for {', '.join(labels)}: candidates {', '.join(ambiguous)} — pass "
            f"--type",
            exit_code=EXIT_AMBIGUOUS,
        )
    return Resolution(None, None, None, only_provisional, None, ambiguous)


def _render_pairs(pairs):
    return ", ".join(f"{label} -> {'/'.join(names)}" for label, names in pairs)


def _load_yaml(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ResolveError(f"{path}: invalid YAML in {what}: {exc}") from exc
    except OSError as exc:
        raise ResolveError(f"{path}: cannot read {what}: {exc}") from exc


def read_batch(path):
    """``(type or None, items)`` for a batch file — the ONE parser of the batch root, shared by
    ``resolve`` (rung 2), ``validate_batch_input.py`` and ``next_rfe_id.py --from-batch``.

    Two root forms (design §5 rung 2): the legacy bare list (``type`` is ``None``; the list is
    returned as is) and the mapping form ``{type: <t>, items: [...]}`` whose keys are EXACTLY
    ``type`` (a non-empty string, returned stripped) and ``items`` (a list). Every other root —
    a missing or extra key, a non-list ``items``, another ``type`` shape, an unreadable file or
    invalid YAML — raises ``ResolveError`` (``exit_code`` 1). The items are not inspected here:
    the per-item ``type`` rule (D2) belongs to the ladder (``resolve``), so a caller can map the
    shape errors of the file to its own usage-error code and leave the content errors to
    ``resolve``.
    """
    data = _load_yaml(path, "batch file")
    if isinstance(data, dict) and all(key in data for key in BATCH_MAPPING_KEYS):
        batch_type = data["type"]
        if not isinstance(batch_type, str) or not batch_type.strip():
            raise ResolveError(f"{path}: 'type' must be a non-empty string, got {batch_type!r}")
        items = data["items"]
        if not isinstance(items, list):
            raise ResolveError(f"{path}: 'items' must be a list, got {_shape(items)}")
        extra = [str(key) for key in data if key not in BATCH_MAPPING_KEYS]
        if extra:
            raise ResolveError(
                f"{path}: the mapping form takes exactly the keys 'type' and 'items'; unexpected "
                f"key(s): {', '.join(extra)}"
            )
        return batch_type.strip(), items
    if isinstance(data, list):
        return None, data
    raise ResolveError(
        f"{path}: expected a list of items (legacy form) or a mapping with 'type' and 'items' "
        f"(mapping form), got {_shape(data)}"
    )


def _batch_signal(path):
    """``(type or None, string item ids)`` for a batch file (design §5 rung 2, D1/D2)."""
    batch_type, items = read_batch(path)
    return batch_type, _batch_item_ids(path, items)


def _batch_item_ids(path, items):
    ids = []
    for index, item in enumerate(items):
        if isinstance(item, dict) and "type" in item:
            raise ResolveError(
                f"{path}: item {index} carries a per-item 'type' key ({item['type']!r}); a batch "
                f"is single-typed (design §5, D2) — split the batch by type and declare the type "
                f"once (--type or the mapping form's type:)"
            )
        if isinstance(item, str) and item.strip():
            ids.append(item.strip())
    return ids


def _artifact_signal(path):
    """``(frontmatter type or None, parent dir name, file stem)`` for an artifact path."""
    file = Path(path)
    if not file.is_file():
        raise ResolveError(f"{path}: artifact file not found")
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResolveError(f"{path}: cannot read artifact: {exc}") from exc
    front = _parse_frontmatter(text, path)
    fm_type = front.get("type")
    if fm_type is not None:
        if not isinstance(fm_type, str) or not fm_type.strip():
            raise ResolveError(
                f"{path}: frontmatter 'type' must be a non-empty string, got {fm_type!r}"
            )
        fm_type = fm_type.strip()
    return fm_type, file.parent.name, file.stem


def _parse_frontmatter(text, path):
    """The YAML mapping between the first two ``---`` lines (the first must be line 1), or
    ``{}`` when there is none. Deliberately local: this module imports no repo helper."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return {}
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            try:
                data = yaml.safe_load("\n".join(lines[1:index]))
            except yaml.YAMLError as exc:
                raise ResolveError(f"{path}: invalid frontmatter YAML: {exc}") from exc
            return data if isinstance(data, dict) else {}
    return {}


def _artifact_dirs(desc):
    try:
        dirs = desc.dirs("bare")
    except KeyError:
        return []
    return [value for value in dirs.values() if isinstance(value, str)]


# -- launch vars (design §4.1, §8.3; PR-5b) -------------------------------------------------

# The vendored assess-rfe checkout (bootstrap-assess-rfe.sh CONTEXT_DIR); pipeline.rubric.path is
# relative to it.
CONTEXT_DIR = ".context/assess-rfe"
# The generic create skill's pre-assigned id flag (PR-5 plan D15).
ID_FLAG = "--id"
DEFAULT_STAGES = ("create", "review", "submit", "split", "auto-fix", "speedrun")
# The fixed keys of every launch block, in emission order (the per-dimension DIMENSION_<NAME>_*
# keys follow the dimension list). launch_vars checks its output against this tuple so the two
# cannot drift; gate 1 and the dispatcher refuse a dimension whose derived <NAME>_PATH key
# (pipeline_state._review_vars) would shadow one of these.
LAUNCH_KEYS = (
    "STAGE",
    "TYPE",
    "TYPE_FLAG",
    "ENTITY",
    "ENTITY_PLURAL",
    "ID_FIELD",
    "ID_FLAG",
    "LOCAL_PREFIX",
    "KEY_PREFIX",
    "ID_GRAMMAR",
    "LOCAL_ID_EXAMPLE",
    "KEY_EXAMPLE",
    "TASK_SCHEMA",
    "REVIEW_SCHEMA",
    "TASKS_DIR",
    "ORIGINALS_DIR",
    "REVIEWS_DIR",
    "INDEX_ENABLED",
    "COMMENTS_COMPANION",
    "COMMENTS_FIELD",
    "STATE_PREFIX",
    "POLL_PREFIX",
    "POLL_FILE_PREFIX",
    "ASSESS_STAGING",
    "SCORER_AGENT",
    "PROMPT_PATH",
    "RUBRIC_EXPORT",
    "BOOTSTRAP",
    "CREATE_GUIDANCE_PATH",
    "TEMPLATE_PATH",
    "RULES_PATH",
    "SECTIONS_PATH",
    "REVISE_RULES_PATH",
    "SPLIT_RULES_PATH",
    "DIMENSIONS",
    "DIMENSION_FILES",
    "SCORE_FIELDS",
    "SCORE_SET",
    "BEFORE_SCORE_SET",
    "SCORE_ZERO_SET",
    "REVIEW_EXTRA_FIELDS",
    "REVIEW_EXTRA_SET",
    "EXTRA_RULES",
    "SIZE_FIELD",
    "SIZE_SET",
    "SIZE_ENUM",
    "BATCH_EXTRA_FIELDS",
    "PARENT_FLAG",
    "NEXT_ID_FLAGS",
    "REPORT_PREFIX",
    "RUN_REPORT",
    "HTML_REPORT",
    "RESPLIT_FIELD",
    "RESPLIT_BELOW",
    "LABEL_PREFIX",
    "NEEDS_ATTENTION_LABEL",
    "VERDICT_LABELS",
    "QUERY_DEFAULT",
    "CONTEXT_DIR",
)
# The phase-var keys the dispatcher adds beside the launch block (pipeline_state._review_vars,
# _assess_vars): a dimension may not shadow these either.
PHASE_VAR_KEYS = ("FIRST_PASS", "ID", "KEY", "ASSESS_PATH", "DATA_FILE", "RUN_DIR")


def dimension_key(name):
    """The launch-var stem of a dimension name: ``a-b`` and ``a_b`` both give ``A_B``."""
    return str(name).upper().replace("-", "_")


def dimension_key_collisions(names):
    """Why a dimension list cannot render: ``(name, reason)`` for a name whose ``<KEY>_PATH``
    is a fixed launch-block or phase-var key, and for a pair of names that normalise to the
    same stem (their DIMENSION_<KEY>_* and <KEY>_PATH lines would shadow each other)."""
    problems, seen = [], {}
    for name in names:
        if not isinstance(name, str):
            continue
        key = dimension_key(name)
        if f"{key}_PATH" in LAUNCH_KEYS or f"{key}_PATH" in PHASE_VAR_KEYS:
            problems.append((name, f"renders {key}_PATH, a launch-block key"))
        if key in seen and seen[key] != name:
            problems.append((name, f"normalises to {key} like {seen[key]!r}"))
        seen.setdefault(key, name)
    return problems


# Interactive skills poll through a second prefix the descriptor does not carry (tmp/<type>-poll-).
_POLL_FILE_PREFIX = "tmp/{type}-poll-"
_ASSESS_STAGING = "tmp/rfe-assess/single"


def _flag(value):
    return "true" if value else "false"


def _dimension_condition(dim):
    cond = dim.get("condition")
    if not cond:
        return "always"
    if "frontmatter_field" in cond:
        return f"{cond['frontmatter_field']} startswith {cond['prefix']}"
    if "context_exists" in cond:
        return f"context_exists {cond['context_exists']}"
    return "always"


def launch_vars(desc, stage):
    """The ``KEY=value`` block a launch directive carries for ``stage`` (design §8.3).

    Every typed literal a generic ``rfe-*`` body or a prompt skeleton needs — ids, dirs,
    schemas, state and poll prefixes, the scorer agent, the composed rubric path, the typed
    prompt files, the dimensions and their verdict-label families, the score-field stubs, the
    declarative review rules, the re-split threshold, the report prefix — rendered from the
    descriptor, so no body hand-writes a typed path (PR-5 plan D13). Deterministic: same
    descriptor, same lines, same order.
    Values are single-line; runtime placeholders (``{ID}``, ``{KEY}``) are left for the agent.
    ``stage`` must be one of the type's ``pipeline.stages``.

    Typed-file paths (the template, the guidance, the rules, the sections, the split prompt,
    every dimension prompt) render through ``Descriptor.launch_path``: the descriptor's own
    relative value whenever the working directory carries that file (the checkout is the cwd,
    or the bootstrap linked ``types/`` in), absolute only when it does not (a drop-in root
    outside the checkout, an unprepared working directory) — a subagent must see every path
    in one frame, or it infers the absolute root for the relative ones. Workspace paths
    (``artifacts/...``, ``tmp/...``, the
    rubric under ``.context/``) and every command (``BOOTSTRAP``, ``python3 scripts/...``) are
    always relative — the headless allowlist matches command text literally, and the workspace
    is the cwd.
    """
    stages = list(desc.get("pipeline.stages", None) or DEFAULT_STAGES)
    if stage not in stages:
        raise ResolveError(
            f"{desc.name}: stage {stage!r} is not one of the type's pipeline.stages "
            f"({', '.join(stages)})",
            exit_code=2,
        )
    dirs = desc.dirs()
    pipe = desc.get("pipeline")
    prompts = pipe.get("prompts") or {}
    dims = list(pipe.get("dimensions") or [])
    rubric = pipe.get("rubric") or {}
    resplit = pipe.get("resplit") or {}
    display = desc.get("display")
    score_fields = desc.score_fields
    review_schema = desc.get("schema.review") or {}
    extra_fields = review_schema.get("extra_fields") or {}
    task_extra = desc.get("schema.task.extra_fields", None) or {}
    batch_extra = list(desc.get("batch.extra_fields", None) or [])
    labels = desc.get("conventions.labels") or {}
    write_prefix = desc.write_prefix or ""
    local_prefix = desc.local_prefix
    comments = bool(desc.get("companions.comments", False))
    report_prefix = desc.get("snapshot.report_prefix", "") or ""
    size = task_extra.get("size") if isinstance(task_extra, dict) else None

    rules = []
    for rule in review_schema.get("extra_rules") or []:
        when, then = rule.get("when") or {}, rule.get("then")
        rules.append(f"If `{when.get('field')}` is `{when.get('equals')}`, set `{then}=true`.")
    extra_set = "".join(
        f" {name}=<{'/'.join(str(v) for v in spec.get('enum', []))}>"
        if isinstance(spec, dict) and spec.get("enum")
        else f" {name}=<value>"
        for name, spec in extra_fields.items()
    )

    out = [
        ("STAGE", stage),
        ("TYPE", desc.name),
        ("TYPE_FLAG", f"--type {desc.name}"),
        ("ENTITY", display["entity"]),
        ("ENTITY_PLURAL", display["entity_plural"]),
        ("ID_FIELD", desc.id_field),
        ("ID_FLAG", ID_FLAG),
        ("LOCAL_PREFIX", local_prefix),
        ("KEY_PREFIX", write_prefix),
        ("ID_GRAMMAR", f"{write_prefix}NNNN or {local_prefix}NNN"),
        ("LOCAL_ID_EXAMPLE", f"{local_prefix}001"),
        ("KEY_EXAMPLE", f"{write_prefix}1234"),
        ("TASK_SCHEMA", f"{desc.name}-task"),
        ("REVIEW_SCHEMA", f"{desc.name}-review"),
        ("TASKS_DIR", dirs["tasks"]),
        ("ORIGINALS_DIR", dirs["originals"]),
        ("REVIEWS_DIR", dirs["reviews"]),
        ("INDEX_ENABLED", _flag(desc.get("index.enabled", False))),
        ("COMMENTS_COMPANION", _flag(comments)),
        ("COMMENTS_FIELD", ',"comment"' if comments else ""),
        ("STATE_PREFIX", pipe.get("state_prefix", "") or ""),
        ("POLL_PREFIX", pipe.get("poll_prefix", "") or ""),
        ("POLL_FILE_PREFIX", _POLL_FILE_PREFIX.format(type=desc.name)),
        ("ASSESS_STAGING", _ASSESS_STAGING),
        ("SCORER_AGENT", pipe["scorer_agent"]),
        ("PROMPT_PATH", f"{CONTEXT_DIR}/{rubric['path']}"),
        ("RUBRIC_EXPORT", rubric.get("export") or "none"),
        ("BOOTSTRAP", f"bash scripts/bootstrap-assess-rfe.sh --type {desc.name}"),
        ("CREATE_GUIDANCE_PATH", desc.launch_path(prompts.get("create_guidance", ""))),
        ("TEMPLATE_PATH", desc.launch_path(prompts.get("template", ""))),
        ("RULES_PATH", desc.launch_path(prompts.get("review_rules", ""))),
        ("SECTIONS_PATH", desc.launch_path(prompts.get("review_sections", ""))),
        ("REVISE_RULES_PATH", desc.launch_path(prompts.get("revise_rules", ""))),
        ("SPLIT_RULES_PATH", desc.launch_path(prompts.get("split_rules", ""))),
        ("DIMENSIONS", ",".join(d["name"] for d in dims)),
    ]
    for dim in dims:
        key = dim["name"].upper().replace("-", "_")
        out.extend(
            [
                (f"DIMENSION_{key}_PROMPT", desc.launch_path(dim["prompt"])),
                (f"DIMENSION_{key}_FILE", f"{dirs['reviews']}/{{ID}}-{dim['name']}.md"),
                (f"DIMENSION_{key}_BLOCKING", _flag(dim.get("blocking", True))),
                (f"DIMENSION_{key}_CONDITION", _dimension_condition(dim)),
            ]
        )
    out.extend(
        [
            (
                "DIMENSION_FILES",
                "; ".join(
                    f"{d['name']}: {dirs['reviews']}/{{ID}}-{d['name']}.md"
                    + ("" if d.get("blocking", True) else " (if it exists)")
                    for d in dims
                ),
            ),
            ("SCORE_FIELDS", ",".join(score_fields)),
            ("SCORE_SET", " ".join(f"scores.{f}=<n>" for f in score_fields)),
            ("BEFORE_SCORE_SET", " ".join(f"before_scores.{f}=<n>" for f in score_fields)),
            ("SCORE_ZERO_SET", " ".join(f"scores.{f}=0" for f in score_fields)),
            ("REVIEW_EXTRA_FIELDS", ",".join(str(k) for k in extra_fields)),
            ("REVIEW_EXTRA_SET", extra_set),
            ("EXTRA_RULES", " ".join(rules) if rules else "none"),
            ("SIZE_FIELD", _flag(size)),
            ("SIZE_SET", " size=<size>" if size else ""),
            (
                "SIZE_ENUM",
                ",".join(str(v) for v in size.get("enum", [])) if isinstance(size, dict) else "",
            ),
            ("BATCH_EXTRA_FIELDS", ",".join(str(f) for f in batch_extra)),
            ("PARENT_FLAG", "--parent" if "parent_key" in batch_extra else ""),
            (
                "NEXT_ID_FLAGS",
                f"--prefix {local_prefix.rstrip('-')} --dir {dirs['tasks']}",
            ),
            ("REPORT_PREFIX", report_prefix),
            ("RUN_REPORT", f"artifacts/auto-fix-runs/{report_prefix}<timestamp>.yaml"),
            ("HTML_REPORT", f"artifacts/auto-fix-runs/{report_prefix}<timestamp>-report.html"),
            ("RESPLIT_FIELD", str(resplit.get("score_field", ""))),
            ("RESPLIT_BELOW", str(resplit.get("below", ""))),
            ("LABEL_PREFIX", desc.get("conventions.label_prefix")),
            ("NEEDS_ATTENTION_LABEL", labels.get("needs_attention", "")),
            (
                # The verdict-label families of the declared dimensions (conventions.labels
                # keyed by a dimension name): what the submit body documents and the scripts
                # keep mutually exclusive per family.
                "VERDICT_LABELS",
                ", ".join(
                    f"{d['name']}.{verdict}"
                    for d in dims
                    if isinstance(labels.get(d["name"]), dict)
                    for verdict in labels[d["name"]]
                )
                or "none",
            ),
            ("QUERY_DEFAULT", desc.get("conventions.query_default", "") or ""),
            ("CONTEXT_DIR", CONTEXT_DIR),
        ]
    )
    fixed = [key for key, _ in out if not key.startswith("DIMENSION_") or key in LAUNCH_KEYS]
    if fixed != list(LAUNCH_KEYS):
        raise RegistryError("launch_vars drifted from LAUNCH_KEYS; update the tuple with the block")
    for key, value in out:
        if not isinstance(value, str) or "\n" in value:
            raise RegistryError(f"{desc.name}: launch var {key} is not a single line: {value!r}")
    return out


def render_launch_vars(pairs):
    """``KEY=value`` lines, one per pair, trailing newline."""
    return "".join(f"{key}={value}\n" for key, value in pairs)


# -- the shared --type hand-parser (design §5 rung 1) ---------------------------------------


def parse_type_arg(registry, argv, default=LEGACY_DEFAULT_TYPE, flag="--type"):
    """Pop ``<flag> <name>`` out of ``argv`` and validate the name against ``registry``.

    The one hand-parser behind the pipeline gates that take ``--type`` without argparse
    (``check_revised.py``, ``check_right_sized.py``, ``check_autofix_complete.py``), so their
    error text cannot drift. Returns ``(type_name, remaining_argv)``: only the first ``flag``
    occurrence is consumed, wherever it sits in ``argv`` (``argv`` itself is not mutated), and
    ``default`` (``rfe``, design §5 rung 5) is the type when the flag is absent. Raises
    ``ResolveError`` with ``exit_code`` 2 — the usage-error code; the message is ready for an
    ``ERROR:`` prefix and ends with the registered type list — when the flag is the last
    token, when the name is not registered, or when the flag is absent and ``default`` is
    not registered.
    """
    argv = list(argv)
    registered = ", ".join(registry.names()) or "(none)"
    if flag in argv:
        idx = argv.index(flag)
        if idx + 1 >= len(argv):
            raise ResolveError(f"{flag} requires a value; registered types: {registered}", 2)
        name = argv[idx + 1]
        del argv[idx : idx + 2]
        if name not in registry:
            raise ResolveError(f"unknown {flag} {name!r}; registered types: {registered}", 2)
        return name, argv
    if default not in registry:
        raise ResolveError(
            f"no {flag} given and the default type {default!r} is not registered; registered "
            f"types: {registered}",
            2,
        )
    return default, argv


# -- CLI -----------------------------------------------------------------------------------


def _emit(value, as_json):
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=False))
    elif isinstance(value, (dict, list)):
        print(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=100).rstrip("\n"))
    elif value is None:
        print("null")
    else:
        print(value)


def _cmd_list(reg, args):
    if args.json:
        _emit(reg.names(), True)
    else:
        for name in reg.names():
            print(name)
    return 0


def _cmd_show(reg, args):
    desc = reg.get(args.type)
    if not args.json:
        print(f"# {desc.name} ({desc.path})")
    _emit(desc.data, args.json)
    return 0


def _cmd_get(reg, args):
    desc = reg.get(args.type)
    _emit(desc.get(args.dotted), args.json)
    return 0


def _cmd_launch_vars(reg, args):
    desc = reg.get(args.type)
    pairs = launch_vars(desc, args.stage)
    if args.json:
        _emit(dict(pairs), True)
    else:
        sys.stdout.write(render_launch_vars(pairs))
    return 0


def _cmd_binding(reg, args):
    desc = reg.get(args.type)
    _emit(_binding_view(desc, desc.binding()), args.json)
    return 0


def _binding_view(desc, binding):
    """The ``binding`` CLI view of an effective binding.

    ``overrides`` is dropped when empty and ``local_id_pattern`` when it is the descriptor's
    own, so with nothing overridden the printed binding is the PR-1 output byte for byte —
    the diagnostic CLI is held to the same no-visible-change bar as the entry scripts. Both
    keys appear as soon as they carry information (an override is listed; a re-rendered D13
    pattern is shown). ``binding()`` itself always carries both; ``resolve --json`` prints the
    full dict.
    """
    view = dict(binding)
    if not view.get("overrides"):
        view.pop("overrides", None)
    if view.get("local_id_pattern") == desc.get("identity.local_id_pattern", None):
        view.pop("local_id_pattern", None)
    return view


def _cmd_candidates(reg, args):
    found = reg.candidates(args.id)
    if args.json:
        _emit(
            {
                "id": args.id,
                "rung": found.rung,
                "provisional": found.provisional,
                "types": found.names,
            },
            True,
        )
        return 0
    rung = found.rung or "none"
    if found.provisional:
        rung += " (provisional)"
    print(f"{rung}: {' '.join(found.names) or '-'}")
    return 0


def _cmd_resolve(reg, args):
    workspace = reg.workspace_bindings(headless=args.headless)
    result = resolve(
        reg,
        explicit_type=args.type,
        batch=args.batch,
        artifact=args.artifact,
        ids=args.ids,
        headless=args.headless,
        workspace=workspace,
    )
    if args.json:
        _emit(result.as_dict(), True)
    else:
        print(result.line())
    return EXIT_AMBIGUOUS if result.ambiguous else 0


def _add_options(parser, suppress):
    """Add the shared options. ``--root``/``--extra-roots``/``--json`` are accepted both before
    and after the subcommand; the subparser copies use SUPPRESS defaults so they never clobber
    a value that was given before the subcommand (argparse applies subparser defaults last).
    """
    kw = {"default": argparse.SUPPRESS} if suppress else {}
    parser.add_argument("--root", help=f"type root directory (default: {DEFAULT_ROOT})", **kw)
    parser.add_argument(
        "--extra-roots",
        help=f"additional roots, {os.pathsep!r}-separated (default: env {EXTRA_ROOTS_ENV})",
        **kw,
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text", **kw)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="type_registry.py",
        description="Inspect the work-item type registry (types/<name>/type.yaml).",
    )
    _add_options(parser, suppress=False)
    common = argparse.ArgumentParser(add_help=False)
    _add_options(common, suppress=True)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p_list = sub.add_parser("list", parents=[common], help="list registered type names")
    p_list.set_defaults(func=_cmd_list)

    p_show = sub.add_parser("show", parents=[common], help="print a descriptor")
    p_show.add_argument("type")
    p_show.set_defaults(func=_cmd_show)

    p_get = sub.add_parser(
        "get", parents=[common], help="print one descriptor field by dotted path"
    )
    p_get.add_argument("type")
    p_get.add_argument("dotted", metavar="dotted.path")
    p_get.set_defaults(func=_cmd_get)

    p_vars = sub.add_parser(
        "launch-vars",
        parents=[common],
        help="print the KEY=value launch block of one stage (design §8.3; PR-5)",
    )
    p_vars.add_argument("type")
    p_vars.add_argument("stage", help="one of the type's pipeline.stages")
    p_vars.set_defaults(func=_cmd_launch_vars)

    p_binding = sub.add_parser(
        "binding", parents=[common], help="print the effective tracker binding"
    )
    p_binding.add_argument("type")
    p_binding.set_defaults(func=_cmd_binding)

    p_candidates = sub.add_parser(
        "candidates",
        parents=[common],
        help="print the detection rung and the candidate types of one id",
    )
    p_candidates.add_argument("id", metavar="ID")
    p_candidates.set_defaults(func=_cmd_candidates)

    p_resolve = sub.add_parser(
        "resolve",
        parents=[common],
        help="resolve the work-item type of a run (design §5) and print the TYPE RESOLVED line",
    )
    p_resolve.add_argument("--type", default=None, help="explicit type (rung 1)")
    p_resolve.add_argument(
        "--batch",
        default=None,
        metavar="FILE",
        help="batch YAML file (rung 2: the mapping form's type:; list items join the ids)",
    )
    p_resolve.add_argument(
        "--artifact",
        default=None,
        metavar="PATH",
        help="artifact markdown file (rung 3: frontmatter type:, else its directory, else stem)",
    )
    p_resolve.add_argument(
        "--headless",
        action="store_true",
        help=f"treat the run as headless (also implied by {'/'.join(HEADLESS_MARKER_VARS)})",
    )
    p_resolve.add_argument(
        "--workspace-root",
        default=None,
        metavar="DIR",
        help=f"directory holding {WORKSPACE_FILENAME} (default: none; the cwd is never probed)",
    )
    p_resolve.add_argument("ids", nargs="*", metavar="ID", help="item ids (rung 3: id grammar)")
    p_resolve.set_defaults(func=_cmd_resolve)
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    extra_roots = parse_extra_roots(args.extra_roots) if args.extra_roots is not None else None
    try:
        reg = load(
            root=args.root,
            extra_roots=extra_roots,
            workspace_root=getattr(args, "workspace_root", None),
            # resolve's --headless gates the env seam too (PR-3 D4); the other subcommands
            # have no such flag and rely on the env markers alone.
            headless=getattr(args, "headless", False),
        )
        return args.func(reg, args)
    except ResolveError as exc:
        print(f"ERROR: {exc.args[0]}", file=sys.stderr)
        return exc.exit_code
    except (RegistryError, KeyError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
