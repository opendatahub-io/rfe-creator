#!/usr/bin/env python3
"""Anti-regression lint: no new literal work-item key prefixes outside the type registry.

Design reference: work-item-types-unified.md 3.3 gate 1 ("a code lint rejecting new
literal prefix predicates ... ADR A.1's anti-regression guard") and 10 item 1 (PR-1).

What it checks
--------------
Every ``scripts/**/*.py`` file (except the registry itself, ``validate_types.py`` and this
lint) is parsed with :mod:`ast`. Each string constant that appears in *code* -- plain
literals, implicit concatenations, raw regex literals, the literal parts of f-strings,
call arguments such as ``startswith("RHAIRFE-")`` -- is checked for a literal occurrence of
any *lint prefix*. Docstrings and comments are not code and are never reported.

The lint prefix set is derived from the type registry (orchestrator decision Q6): the
union over all shipped descriptors of ``identity.<tracker>.key_prefixes``,
``identity.local_prefix``, the literal stems of ``conventions.parent_key_patterns``
(``'RHAISTRAT-\\d+'`` -> ``RHAISTRAT-``), ``snapshot.prefix`` and ``snapshot.report_prefix``.
Empty values are skipped. Matching is case-sensitive so ``rfe-tasks`` never matches
``RFE-``; a nested match (``RFE-`` inside ``RHAIRFE-``) is reported once, as the longest
prefix. Descriptor values are used as written -- deployment binding overrides
(``RFE_CREATOR_BINDING_*``) are deliberately ignored so the lint is deterministic.

Ratchet baseline
----------------
Existing occurrences are grandfathered by a per-file COUNT baseline
(``tests/data/prefix_predicate_baseline.json`` = ``{"<repo-relative file>": <count>}``,
one count per string constant). The baseline may only shrink: the lint exits 1 when a
file's count exceeds its baseline entry or when a file that is not in the baseline has
any occurrence, and prints every occurrence of each offending file as
``file:line:col: <prefix> in <literal> | <code>``. Files whose count dropped below the
baseline are reported as NOTEs so the baseline can be tightened. Every grandfathered
site is a PR-2 migration target, none is a permanent whitelist.

``--update-baseline`` rewrites the baseline from the current scan. Only ever run it
deliberately (after migrating a site to the registry), never to make a red lint green.

Usage
-----
    python3 scripts/lint_prefix_predicates.py
    python3 scripts/lint_prefix_predicates.py --baseline tests/data/prefix_predicate_baseline.json
    python3 scripts/lint_prefix_predicates.py --update-baseline
    python3 scripts/lint_prefix_predicates.py --root . --types-root types

Exit codes: 0 clean, 1 regression (or unparsable file), 2 usage error.
"""

import argparse
import ast
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent

SCAN_DIR = "scripts"
DEFAULT_BASELINE = Path("tests") / "data" / "prefix_predicate_baseline.json"
# The registry and its validators are the one place prefixes may legitimately live; this
# lint is excluded because it names the prefixes in its own docstring and messages.
EXCLUDED_FILES = frozenset({"type_registry.py", "validate_types.py", "lint_prefix_predicates.py"})
_LITERAL_PREVIEW = 60


@dataclass(frozen=True)
class Occurrence:
    """One string constant in code that contains at least one lint prefix."""

    path: str  # repo-relative POSIX path
    line: int
    col: int
    literal: str
    prefixes: tuple[str, ...]  # matched prefixes, longest-first at each position
    code: str  # stripped source line where the constant starts

    def format(self) -> str:
        literal = repr(self.literal)
        if len(literal) > _LITERAL_PREVIEW:
            literal = literal[: _LITERAL_PREVIEW - 3] + "..."
        where = f"{self.path}:{self.line}:{self.col}"
        return f"{where}: {','.join(self.prefixes)} in {literal} | {self.code}"


# --------------------------------------------------------------------------- prefix set


def pattern_stem(pattern: str) -> str:
    """Return the literal key-prefix stem of an anchored id regex, or "".

    ``'RHAISTRAT-\\d+'`` -> ``'RHAISTRAT-'``; ``'^(RFE-\\d+|RHAIRFE-\\d+)$'`` -> ``'RFE-'``
    (only the leading alternative -- descriptors list one pattern per alternative, see
    ``conventions.parent_key_patterns``). A pattern without a literal ``-`` stem (one that
    starts with a metacharacter, or a bare word like ``'(RFE|INIT)-\\d+'``) yields "" and
    contributes nothing.
    """
    body = pattern.lstrip("^(")
    run = []
    for ch in body:
        if ch.isalnum() or ch in "-_":
            run.append(ch)
        else:
            break
    stem = "".join(run)
    if "-" not in stem:
        return ""
    return stem[: stem.rfind("-") + 1]


def lint_prefixes(registry) -> list[str]:
    """Q6 prefix set derived from every descriptor in ``registry``, longest first."""
    found = set()
    for name in registry.names():
        descriptor = registry.get(name)
        values = []
        tracker = descriptor.get("identity.tracker", None)
        if tracker:
            values.extend(descriptor.get(f"identity.{tracker}.key_prefixes", None) or [])
        values.append(descriptor.get("identity.local_prefix", None))
        for pattern in descriptor.get("conventions.parent_key_patterns", None) or []:
            values.append(pattern_stem(str(pattern)))
        values.append(descriptor.get("snapshot.prefix", None))
        values.append(descriptor.get("snapshot.report_prefix", None))
        for value in values:
            if isinstance(value, str) and value.strip():
                found.add(value.strip())
    return sort_prefixes(found)


def sort_prefixes(prefixes) -> list[str]:
    """Longest first, then alphabetical -- the order :func:`find_prefixes` relies on."""
    return sorted(set(prefixes), key=lambda p: (-len(p), p))


def find_prefixes(text: str, prefixes) -> tuple[str, ...]:
    """Return the lint prefixes literally present in ``text`` in order of position.

    Longer prefixes win at a given position and a shorter prefix nested inside a longer
    match is not reported separately (``"RHAIRFE-"`` -> ``("RHAIRFE-",)``, not also
    ``"RFE-"``).
    """
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str]] = []
    for prefix in sort_prefixes(prefixes):
        start = 0
        while True:
            index = text.find(prefix, start)
            if index < 0:
                break
            end = index + len(prefix)
            if not any(a < end and index < b for a, b in taken):
                taken.append((index, end))
                hits.append((index, prefix))
            start = index + 1
    return tuple(prefix for _, prefix in sorted(hits))


# ------------------------------------------------------------------------------- scanner


class _Collector(ast.NodeVisitor):
    """Collect code string constants containing a lint prefix; skip docstrings."""

    def __init__(self, prefixes, lines: list[str], path: str):
        self._prefixes = sort_prefixes(prefixes)
        self._lines = lines
        self._path = path
        self._docstrings: set[int] = set()
        self.occurrences: list[Occurrence] = []

    # -- docstrings are documentation, not code -------------------------------------
    def _skip_docstring(self, node) -> None:
        body = getattr(node, "body", None) or []
        first = body[0] if body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            self._docstrings.add(id(first.value))

    def visit_Module(self, node):  # noqa: N802 - ast.NodeVisitor naming
        self._skip_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):  # noqa: N802
        self._skip_docstring(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node):  # noqa: N802
        self._skip_docstring(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        self._skip_docstring(node)
        self.generic_visit(node)

    # -- string constants ------------------------------------------------------------
    def visit_JoinedStr(self, node):  # noqa: N802
        # The literal parts of an f-string are Constant nodes; Python < 3.12 gives them
        # the position of the enclosing JoinedStr, 3.12+ their own. Always report the
        # f-string's position so output is identical across interpreter versions.
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                self._check(part.value, node)
            else:
                self.visit(part)  # FormattedValue: nested expressions and format specs

    def visit_Constant(self, node):  # noqa: N802
        if isinstance(node.value, str) and id(node) not in self._docstrings:
            self._check(node.value, node)

    def _check(self, text: str, node) -> None:
        prefixes = find_prefixes(text, self._prefixes)
        if not prefixes:
            return
        line = getattr(node, "lineno", 0) or 0
        code = self._lines[line - 1].strip() if 0 < line <= len(self._lines) else ""
        self.occurrences.append(
            Occurrence(
                path=self._path,
                line=line,
                col=(getattr(node, "col_offset", 0) or 0) + 1,
                literal=text,
                prefixes=prefixes,
                code=code,
            )
        )


def scan_source(source: str, prefixes, path: str = "<string>") -> list[Occurrence]:
    """Scan Python ``source`` and return its prefix occurrences in code order."""
    tree = ast.parse(source, filename=path)
    collector = _Collector(prefixes, source.splitlines(), path)
    collector.visit(tree)
    return sorted(collector.occurrences, key=lambda o: (o.line, o.col, o.literal))


def scan_file(path: Path, prefixes, rel: str) -> list[Occurrence]:
    return scan_source(path.read_text(encoding="utf-8"), prefixes, rel)


def iter_scan_files(root: Path) -> list[Path]:
    """All ``scripts/**/*.py`` under ``root`` minus :data:`EXCLUDED_FILES`, sorted."""
    scan_root = root / SCAN_DIR
    if not scan_root.is_dir():
        return []
    return sorted(
        p
        for p in scan_root.rglob("*.py")
        if p.is_file() and p.name not in EXCLUDED_FILES and "__pycache__" not in p.parts
    )


def scan_tree(root: Path, prefixes) -> tuple[dict[str, list[Occurrence]], list[str]]:
    """Scan the tree; return ``{rel_path: occurrences}`` (non-empty only) and parse errors."""
    results: dict[str, list[Occurrence]] = {}
    errors: list[str] = []
    for path in iter_scan_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            occurrences = scan_file(path, prefixes, rel)
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"ERROR {rel}: cannot parse ({exc})")
            continue
        if occurrences:
            results[rel] = occurrences
    return results, errors


def counts_of(results: dict[str, list[Occurrence]]) -> dict[str, int]:
    return {rel: len(occ) for rel, occ in sorted(results.items())}


# ------------------------------------------------------------------------------ baseline


def load_baseline(path: Path) -> dict[str, int]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
        for k, v in data.items()
    ):
        raise ValueError(f'{path}: expected {{"<relative file>": <count>}}')
    return dict(sorted(data.items()))


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dict(sorted(counts.items())), fh, indent=2, sort_keys=True)
        fh.write("\n")


_TIGHTEN = "tighten with --update-baseline"


@dataclass(frozen=True)
class Regression:
    path: str
    count: int
    baseline: int | None  # None: file not in the baseline


def compare(counts: dict[str, int], baseline: dict[str, int]) -> tuple[list[Regression], list[str]]:
    """Return (regressions, notes). Notes flag entries the baseline could tighten."""
    regressions: list[Regression] = []
    notes: list[str] = []
    for rel, count in sorted(counts.items()):
        allowed = baseline.get(rel)
        if allowed is None:
            regressions.append(Regression(rel, count, None))
        elif count > allowed:
            regressions.append(Regression(rel, count, allowed))
        elif count < allowed:
            notes.append(f"NOTE {rel}: {count} < baseline {allowed}; {_TIGHTEN}")
    for rel, allowed in sorted(baseline.items()):
        if rel not in counts and allowed > 0:
            notes.append(f"NOTE {rel}: 0 < baseline {allowed}; {_TIGHTEN}")
    return regressions, notes


# ----------------------------------------------------------------------------------- CLI


def _load_registry(types_root):
    """Import the sibling registry lazily and load it without env influence."""
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    try:
        import type_registry
    except ImportError as exc:  # pragma: no cover - environment problem, not a lint result
        _usage_error(f"scripts/type_registry.py is required to derive the lint prefix set ({exc})")
    # extra_roots=[] / env={}: drop-in descriptor roots (RFE_CREATOR_EXTRA_TYPES) and
    # binding overrides are dev/deploy seams; the lint reflects the shipped registry only.
    try:
        return type_registry.load(root=types_root, extra_roots=[], env={})
    except Exception as exc:  # RegistryError, OSError, YAML errors: not a lint result
        _usage_error(f"cannot load the type registry: {exc}")


def _usage_error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_prefix_predicates.py",
        description=(
            "Fail when a scripts/**/*.py file gains a literal work-item key prefix "
            "(derived from the type registry) beyond its per-file baseline count."
        ),
    )
    parser.add_argument(
        "--root",
        default=str(_REPO_ROOT),
        help="repository root containing scripts/ to scan (default: this checkout)",
    )
    parser.add_argument(
        "--types-root",
        default=None,
        help="type registry root (default: the registry's own default, <root>/types)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help=f"baseline JSON path (default: <root>/{DEFAULT_BASELINE.as_posix()})",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="rewrite the baseline from the current scan (deliberate use only)",
    )
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / SCAN_DIR).is_dir():
        parser.error(f"{root} has no {SCAN_DIR}/ directory")
    baseline_path = Path(args.baseline) if args.baseline else root / DEFAULT_BASELINE
    types_root = Path(args.types_root).resolve() if args.types_root else None

    registry = _load_registry(types_root)
    prefixes = lint_prefixes(registry)
    if not prefixes:
        parser.error("the type registry yields no lint prefixes; nothing to check")

    results, errors = scan_tree(root, prefixes)
    counts = counts_of(results)
    total = sum(counts.values())
    for message in errors:
        print(message)

    if args.update_baseline:
        previous = load_baseline(baseline_path) if baseline_path.is_file() else {}
        grown = [
            (rel, previous.get(rel, 0), count)
            for rel, count in counts.items()
            if count > previous.get(rel, 0)
        ]
        write_baseline(baseline_path, counts)
        for rel, before, after in grown:
            print(
                f"WARNING {rel}: baseline grew {before} -> {after}; new prefix literals "
                "belong in the type registry (design 3.3 gate 1)"
            )
        print(
            f"Wrote {os.path.relpath(baseline_path)}: {len(counts)} file(s), "
            f"{total} occurrence(s) of {', '.join(prefixes)}"
        )
        return 1 if errors else 0

    if not baseline_path.is_file():
        parser.error(f"baseline {baseline_path} not found; create it with --update-baseline")
    try:
        baseline = load_baseline(baseline_path)
    except (ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    regressions, notes = compare(counts, baseline)
    for regression in regressions:
        if regression.baseline is None:
            allowed = "not in baseline"
        else:
            allowed = f"baseline {regression.baseline}"
        print(
            f"ERROR {regression.path}: {regression.count} literal prefix occurrence(s) in code, "
            f"{allowed} -- read the prefix from the type registry instead"
        )
        for occurrence in results[regression.path]:
            print(f"  {occurrence.format()}")
    for note in notes:
        print(note)
    if regressions or errors:
        return 1
    print(
        f"OK: {len(iter_scan_files(root))} file(s) scanned, {total} grandfathered occurrence(s) "
        f"in {len(counts)} file(s) within baseline (prefixes: {', '.join(prefixes)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
