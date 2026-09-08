#!/usr/bin/env python3
"""Work-item type registry — the single import point for ``types/<name>/type.yaml``.

Usage:
    python3 scripts/type_registry.py list [--json]
    python3 scripts/type_registry.py show <type> [--json]
    python3 scripts/type_registry.py get <type> <dotted.path> [--json]
    python3 scripts/type_registry.py binding <type> [--json]

    Options: --root DIR (default: <repo>/types), --extra-roots A:B (default: env
    RFE_CREATOR_EXTRA_TYPES). Exit 0 on success, 1 on an unknown type / unreadable
    registry / missing key, 2 on a usage error.

Import-clean invariant (design work-item-types-unified.md §10 item 1, PR-1; Q5)
--------------------------------------------------------------------------------
This module imports ONLY the standard library and ``yaml``. It never imports another
rfe-creator module, never reads the filesystem at import time, takes the type root as
an explicit argument, and discovers the default root relative to ``__file__`` (never
the cwd). ``types/_schema/type.schema.json`` follows the same rule. Both are meant to
be lifted verbatim into the ``creator-core`` follow-up (design §10 item 10), so keep
them free of repo-specific helpers: JSON-Schema validation lives in
``scripts/validate_types.py`` (the only place ``jsonschema`` is imported), not here.

Adoption status (PR-2a): 18 scripts load the registry at import (module-level
``_TYPES = type_registry.load()``; the table in ``types/README.md`` lists them) and use
DESCRIPTOR values only; ``detect()``/``owns()`` are the design §5 rung-3 seed. The values a
pending script still carries are pinned equal to the descriptors by test, and the remaining
scripts adopt the registry one PR at a time (design §10 items 2-5); ``binding()`` overrides
and ``resolve`` land in PR-3.

Deployment binding override (design §3.2.1)
--------------------------------------------
``identity.<tracker>`` in the descriptor is the DEFAULT binding. ``Descriptor.binding``
computes the EFFECTIVE binding by overlaying the explicit, type-scoped environment
variables ``RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,LOCAL_PREFIX}`` (``<TYPE>`` is
the type name upper-cased, non-alphanumerics mapped to ``_``). When ``PROJECT`` is
overridden the write prefix ``<PROJECT>-`` is derived and placed first in
``key_prefixes``; the descriptor's own prefixes are kept after it as read prefixes.
Only binding fields are overridable — never judgement content, dirs, schema, rubric or
eval. Zero-config default: with no variable set the effective binding IS the descriptor
binding (``source: descriptor``); nothing here is required.

NOT implemented in PR-1 (lands with ``resolve`` in PR-3): the bare ``JIRA_PROJECT`` /
``JIRA_ISSUE_TYPE`` shorthand, which is only valid for the *resolved* type, and the
workspace ``rfe-creator.yaml`` ``bindings:`` file. Trust-boundary rule for whoever adds
them (§3.2.1 g): in headless and CI runs an override is honoured ONLY from the
environment (protected CI variables), never from a workspace file the checkout could
carry; the effective ``(project, issue_type)`` pair is printed at resolve time and
checked against the registered bindings before the first tracker write, and an
untrusted source or an unregistered pair is a hard failure, not a warning.

Drop-in roots
-------------
``RFE_CREATOR_EXTRA_TYPES`` (``os.pathsep``-separated directories, each holding
``<name>/type.yaml`` entries) adds development/test roots after the primary root. A type
name present in two roots is an error, and a ``type.yaml`` that resolves outside its root
(symlink escape) is rejected. Drop-ins pass exactly the same ``validate_types.py`` gates as
shipped types before any binding is used.

The seam is development and test only (design §3.5, PR1-05): in a headless or CI run —
any of ``RFE_CREATOR_HEADLESS``, ``CI``, ``GITHUB_ACTIONS`` set to a truthy value in the
registry's environment — an ``RFE_CREATOR_EXTRA_TYPES`` entry is honoured only when its
canonical path is allowlisted, via ``RFE_CREATOR_EXTRA_TYPES_ALLOWLIST`` (a protected CI
variable, the same trust boundary as §3.2.1 g) or the ``allowlisted_extra_roots`` argument.
Every other entry is dropped with one stderr line and recorded in
``TypeRegistry.ignored_extra_roots``. Explicit ``extra_roots=`` / ``--extra-roots`` values
are a deliberate caller action and are never gated. The marker is environment-only: this
module never probes the cwd (``tmp/pipeline-state.yaml`` is the pipeline's business — a
headless launcher exports ``RFE_CREATOR_HEADLESS=1`` for its subprocesses when a consumer
lands, PR-2/PR-3).
"""

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path

import yaml

# Default discovery root: <repo>/types, located relative to this file — never the cwd
# (precedent: pipeline_state.py os.path.dirname(__file__), snapshot_fetch.py SCRIPT_DIR).
DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "types"

DESCRIPTOR_FILENAME = "type.yaml"
EXTRA_ROOTS_ENV = "RFE_CREATOR_EXTRA_TYPES"
# Design §3.5 / PR1-05: roots the env seam may add in a headless/CI run (os.pathsep-separated).
EXTRA_ROOTS_ALLOWLIST_ENV = "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST"
# Headless/CI markers, checked in the registry's env only (never the cwd). CI / GITHUB_ACTIONS
# are the conventional CI variables; RFE_CREATOR_HEADLESS is the explicit pipeline marker
# (exported by the headless launcher once a consumer needs the gate, PR-3; the PR-2a adopters
# read descriptor values only).
HEADLESS_MARKER_VARS = ("RFE_CREATOR_HEADLESS", "CI", "GITHUB_ACTIONS")
_FALSE_VALUES = {"", "0", "false", "no", "off"}
BINDING_ENV_PREFIX = "RFE_CREATOR_BINDING_"
# env suffix -> effective binding key (design §3.2.1: the overridable binding-only fields).
BINDING_OVERRIDE_FIELDS = {
    "PROJECT": "project",
    "ISSUE_TYPE": "issue_type",
    "LOCAL_PREFIX": "local_prefix",
}
# Q13: descriptors store dirs as "artifacts/<name>"; the bare form drops this component.
ARTIFACTS_DIR = "artifacts"
DIR_FORMS = ("artifacts", "bare")

# Value grammars for env overrides. A lower-case or dash-less typo must fail loudly instead
# of silently minting a bogus write prefix (the §3.2.1 (b)/(g) lint runs on these values).
_PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_LOCAL_PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9]{0,31}-$")

# Sentinel for Descriptor.get(): "no default supplied" must be distinguishable from None,
# because null is a legitimate descriptor value (e.g. pipeline.rubric.export for initiative).
MISSING = object()


# ASCII integers only: str.isdigit() also accepts "²" and Arabic-Indic digits that int() rejects.
_INDEX_RE = re.compile(r"-?[0-9]+")


class RegistryError(ValueError):
    """The registry could not be loaded (bad root, unreadable/invalid descriptor, duplicate)."""


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


def is_headless(env):
    """True when ``env`` carries a headless/CI marker (design §3.5): ``RFE_CREATOR_HEADLESS``,
    ``CI`` or ``GITHUB_ACTIONS`` set to anything but an empty/false value."""
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


class Descriptor:
    """Thin wrapper over one parsed ``type.yaml`` dict.

    The raw mapping is available as ``data``; the accessors below are the projections the
    pin tests and (from PR-2) the scripts consume. Accessors return DESCRIPTOR values;
    only ``binding()`` applies the §3.2.1 environment overlay.
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

    # -- identity detection (PR-2 seed of the design §5 ladder) ---------------------------

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
        overlay is not consulted (that lands with ``resolve`` in PR-3).
        """
        if not isinstance(item_id, str) or not item_id:
            return False
        return (
            self._matches_local_id(item_id)
            or self._has_key_prefix(item_id)
            or self._has_local_prefix(item_id)
        )

    def binding(self, env=None):
        """Return the EFFECTIVE tracker binding (design §3.2.1).

        ``identity.<tracker>`` overlaid with ``RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,
        LOCAL_PREFIX}`` from ``env`` (default: the registry's environment, else ``os.environ``).
        Keys: ``tracker``, ``project``, ``issue_type``, ``key_prefixes`` (write prefix first —
        derived ``<PROJECT>-`` when the project is overridden, descriptor prefixes kept as read
        prefixes), ``local_prefix`` (effective), every other key of the binding block verbatim,
        and ``source`` (``"descriptor"`` or ``"env"``). Empty variables count as unset.
        ``local_id_pattern`` is NOT re-derived from an overridden ``local_prefix`` in PR-1.
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
        effective["local_prefix"] = self.get("identity.local_prefix", None)

        overrides = {}
        for suffix, field in BINDING_OVERRIDE_FIELDS.items():
            var = binding_env_var(self.name, suffix)
            value = env.get(var, "")
            value = value.strip() if isinstance(value, str) else ""
            if value:
                overrides[field] = _validate_override(var, field, value)

        if "project" in overrides:
            project = overrides["project"]
            derived = f"{project}-"
            read_prefixes = [p for p in effective["key_prefixes"] if p != derived]
            effective["key_prefixes"] = [derived] + read_prefixes
        effective.update(overrides)
        effective["source"] = "env" if overrides else "descriptor"
        return effective

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


def _bare_dir(value):
    prefix = ARTIFACTS_DIR + "/"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _validate_override(var, field, value):
    if field == "project" and not _PROJECT_KEY_RE.match(value):
        raise RegistryError(f"{var}={value!r}: expected an upper-case tracker project key")
    if field == "local_prefix" and not _LOCAL_PREFIX_RE.match(value):
        raise RegistryError(f"{var}={value!r}: expected an upper-case prefix ending in '-'")
    return value


class TypeRegistry:
    """Static enumeration of ``<root>/<name>/type.yaml`` descriptors across one or more roots."""

    def __init__(self, root=None, extra_roots=None, env=None, allowlisted_extra_roots=None):
        self.env = os.environ if env is None else env
        self.root = Path(root) if root is not None else DEFAULT_ROOT
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
        headless/CI run only the entries whose canonical path is allowlisted — by the
        ``allowlisted_extra_roots`` argument or RFE_CREATOR_EXTRA_TYPES_ALLOWLIST."""
        roots = parse_extra_roots(self.env.get(EXTRA_ROOTS_ENV, ""))
        if not roots or not is_headless(self.env):
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

    def bindings(self, env=None):
        """Effective binding per type (design §3.2.1), keyed by type name in ``names()`` order."""
        return {name: self._types[name].binding(env) for name in self.names()}

    # -- detection --------------------------------------------------------------------------

    def detect(self, item_id):
        """Return the ``Descriptor`` that owns ``item_id``, or ``None`` when no type does.

        PR-2 seed of the design §5 ladder — rung 3's deterministic id signal, single candidate
        by construction. Three prefix rungs, each tried across every type in ``names()`` order
        before the next (so the most specific signal always wins, whatever the type order):

        1. ``identity.local_id_pattern`` full-matches ``item_id`` (``RFE-001``, ``INIT-001``);
        2. ``item_id`` starts with one of the tracker's ``identity.<tracker>.key_prefixes``
           (``RHAIRFE-1``, ``RHOAIENG-1`` — the Jira key grammar, design §3.6);
        3. ``item_id`` starts with ``identity.local_prefix`` — the parity rung: the sniffs this
           replaces tested ``startswith(local_prefix) or startswith(key_prefix)``, so a
           malformed local id (``INIT-x``) stays with the type it went to before PR-2. Ranked
           last so a tracker key always beats a local prefix, and kept separate so PR-3 can
           drop it for types whose ``local_prefix`` is only a placeholder (the epic fixture).

        Anything else — a peer pipeline's key (``RHAISTRAT-1``), lower-case, empty, ``None`` —
        returns ``None``; callers keep their own default (today: ``detect(x) or get("rfe")``).
        Uses DESCRIPTOR values only; the §3.2.1 binding overlay, multi-candidate resolution
        (shared prefixes → candidate set, tie-broken on the binding's ``issue_type``) and the
        post-fetch ``(project, issue_type)`` check are PR-3 (``resolve``).
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


def load(root=None, extra_roots=None, env=None, allowlisted_extra_roots=None):
    """Load a fresh registry (never cached — callers that want one instance keep it)."""
    return TypeRegistry(
        root=root,
        extra_roots=extra_roots,
        env=env,
        allowlisted_extra_roots=allowlisted_extra_roots,
    )


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


def _cmd_binding(reg, args):
    desc = reg.get(args.type)
    _emit(desc.binding(), args.json)
    return 0


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

    p_binding = sub.add_parser(
        "binding", parents=[common], help="print the effective tracker binding"
    )
    p_binding.add_argument("type")
    p_binding.set_defaults(func=_cmd_binding)
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    extra_roots = parse_extra_roots(args.extra_roots) if args.extra_roots is not None else None
    try:
        reg = load(root=args.root, extra_roots=extra_roots)
        return args.func(reg, args)
    except (RegistryError, KeyError) as exc:
        message = exc.args[0] if exc.args else str(exc)
        print(f"ERROR: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
