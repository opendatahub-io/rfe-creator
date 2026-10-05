#!/usr/bin/env python3
"""List the active architecture-context overlays.

The feasibility review needs the overlay files under
``.context/architecture-context/overlays/`` that are ``status: active``. Discovering
them used to be a Glob call in the prompt; the dedicated Glob tool no longer exists on
native Claude Code builds (since 2.1.117 search goes through Bash), and shell globs and
loops are not on the headless Bash allowlist, so neither runner can rely on it. This
script is the one allow-listed way to get the list under every runner.

One line per active overlay, tab-separated, sorted by filename:

    <path>  <id>  <title>  release=<r1,r2>  affects=<c1,c2>

``README.md`` and superseded overlays are skipped. A missing directory is not an error:
the script prints nothing and exits 0, and the prompt proceeds without overlays.

Usage:
    python3 scripts/list_overlays.py
    python3 scripts/list_overlays.py --release 3.6 --affects notebooks,dashboard
    python3 scripts/list_overlays.py --paths
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from artifact_utils import ValidationError, read_frontmatter  # noqa: E402

DEFAULT_DIR = os.path.join(".context", "architecture-context", "overlays")


def _as_list(value):
    """Frontmatter lists are lists; a bare scalar is treated as a one-item list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def overlay_entries(directory):
    """(path, frontmatter) for every active overlay under ``directory``, by filename."""
    if not os.path.isdir(directory):
        return []
    entries = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md") or name == "README.md":
            continue
        path = os.path.join(directory, name)
        try:
            data, _body = read_frontmatter(path)
        except ValidationError as exc:
            print(f"WARNING: skipping {path}: {exc}", file=sys.stderr)
            continue
        if str(data.get("status", "")).strip().lower() != "active":
            continue
        entries.append((path, data))
    return entries


def matches(data, release=None, affects=None):
    """The README's matching rules: release in the list or ``all``; ``affects``
    intersects the requested components, ``platform`` matching everything."""
    if release is not None:
        releases = {r.strip() for r in _as_list(data.get("release"))}
        if release not in releases and "all" not in releases:
            return False
    if affects:
        components = {c.strip() for c in _as_list(data.get("affects"))}
        if "platform" not in components and not components & set(affects):
            return False
    return True


def format_line(path, data):
    return "\t".join(
        [
            path,
            str(data.get("id", "")),
            str(data.get("title", "")).replace("\t", " ").replace("\n", " "),
            "release=" + ",".join(_as_list(data.get("release"))),
            "affects=" + ",".join(_as_list(data.get("affects"))),
        ]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="List active architecture-context overlays")
    parser.add_argument(
        "--dir", default=DEFAULT_DIR, help=f"overlays directory (default {DEFAULT_DIR})"
    )
    parser.add_argument(
        "--release", help="keep overlays whose release list has this version or 'all'"
    )
    parser.add_argument(
        "--affects",
        help=(
            "comma-separated component names; keep overlays whose affects list intersects "
            "them (an overlay with affects: [platform] matches every component)"
        ),
    )
    parser.add_argument("--paths", action="store_true", help="print paths only")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.dir):
        print(f"no overlays directory at {args.dir}; proceeding without overlays", file=sys.stderr)
        return 0
    affects = [a.strip() for a in args.affects.split(",") if a.strip()] if args.affects else None
    for path, data in overlay_entries(args.dir):
        if not matches(data, release=args.release, affects=affects):
            continue
        print(path if args.paths else format_line(path, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
