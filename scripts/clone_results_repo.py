#!/usr/bin/env python3
"""Sparse-clone the results repository, fetching only what the incremental fetch reads.

Materializes, per work-item type, the type's 'latest' symlink and its snapshot files
(<snapshot.prefix>*.yaml) and nothing else: no reviews, tasks, originals, run reports
or HTML reports.

Usage:
    DATA_REPO_TOKEN=<token> [DATA_REPO_TYPES=<type>[,<type>...]] \\
        python3 scripts/clone_results_repo.py <repo-path-or-url> [dest]

Examples:
    DATA_REPO_TOKEN=glpat-xxx python3 scripts/clone_results_repo.py \\
        redhat/rhel-ai/agentic-ci/rfe-autofixer-results
    DATA_REPO_TOKEN=glpat-xxx python3 scripts/clone_results_repo.py \\
        https://gitlab.com/my/repo.git /tmp/data-repo
    DATA_REPO_TOKEN=glpat-xxx DATA_REPO_TYPES=initiative python3 scripts/clone_results_repo.py \\
        redhat/rhel-ai/agentic-ci/rfe-autofixer-results /tmp/data-repo

Results repository layout (AISDLC-202): one repository, one subtree per type. The rfe
type's runs live at the root (`<run>/` and the root `latest` -- grandfathered), every
other type's under its `snapshot.results_subdir` (`initiative/<run>/`,
`initiative/latest`). DATA_REPO_TYPES (comma-separated registry type names) selects
the subtrees to materialize: each type contributes `/<subdir>/latest` and
`/<subdir>/*/auto-fix-runs/<snapshot.prefix>*.yaml` (root forms when the subdir is
empty), both read from types/<type>/type.yaml through the type registry, and the
requested subtree directories are created after the checkout so a first run finds an
existing, empty subtree ("no 'latest' symlink") instead of a missing path. Unset, the
sparse set is the literal rfe pair (DEFAULT_SPARSE_PATTERNS) and the registry is not
imported: the production RFE job's clone is unchanged. `test-data/` is always excluded.
An unknown type name is an error before anything is cloned.

If repo arg is a bare path (no ://), builds a GitLab HTTPS URL with
the token embedded. Prints the clone destination to stdout.
"""

import os
import shutil
import subprocess
import sys
import urllib.parse

TYPES_ENV = "DATA_REPO_TYPES"
TEST_DATA_EXCLUDE = "!/test-data/**"
# The rfe layout exactly as the production job has always cloned it: the root `latest`
# and the root run directories' rfe snapshots. tests/test_clone_results_repo.py pins this
# tuple to the rfe descriptor (snapshot.results_subdir "" and snapshot.prefix) and pins
# that an unset DATA_REPO_TYPES selects it without loading the registry. The one
# grandfathered prefix literal of this file (tests/data/prefix_predicate_baseline.json).
DEFAULT_SPARSE_PATTERNS = (
    "/latest",
    "/*/auto-fix-runs/issue-snapshot-*.yaml",
    TEST_DATA_EXCLUDE,
)


def build_clone_url(repo, token):
    """Build an authenticated clone URL from repo spec and token.

    Raises ValueError if a bare project path is given without a token.
    Local absolute paths (starting with /) are passed through as-is.
    """
    if os.path.isabs(repo):
        return repo
    if "://" not in repo and "@" not in repo:
        if not token:
            raise ValueError("DATA_REPO_TOKEN required for private repos")
        return f"https://bot:{token}@gitlab.com/{repo}.git"
    if token and repo.startswith("https://"):
        parsed = urllib.parse.urlparse(repo)
        return parsed._replace(
            netloc=f"bot:{token}@{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")
        ).geturl()
    return repo


def parse_types(value):
    """DATA_REPO_TYPES value -> ordered, de-duplicated type names ('' or None -> [])."""
    names = []
    for part in (value or "").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    return names


def type_layouts(type_names):
    """[(results_subdir, snapshot_prefix)] for the named registry types, in order.

    An empty list loads nothing. Raises ValueError naming an unknown type (and the
    registered names) or a descriptor that lacks one of the two snapshot fields.
    """
    if not type_names:
        return []
    import type_registry  # lazy: the default path never touches the registry

    registry = type_registry.load()
    layouts = []
    for name in type_names:
        if name not in registry.names():
            raise ValueError(
                f"{TYPES_ENV}: unknown type {name!r} (registered: {', '.join(registry.names())})"
            )
        desc = registry.get(name)
        try:
            layouts.append((desc.get("snapshot.results_subdir"), desc.get("snapshot.prefix")))
        except KeyError as exc:
            raise ValueError(f"{TYPES_ENV}: {exc}") from None
    return layouts


def patterns_for(layouts):
    """The sparse-checkout set for ``layouts`` (see type_layouts).

    No layouts -> DEFAULT_SPARSE_PATTERNS verbatim. Otherwise each type contributes its
    `latest` and its snapshot glob under its results subtree (root forms for an empty
    subtree), de-duplicated in order, followed by the test-data exclusion.
    """
    if not layouts:
        return list(DEFAULT_SPARSE_PATTERNS)
    patterns = []
    for subdir, prefix in layouts:
        root = f"/{subdir}" if subdir else ""
        for pattern in (f"{root}/latest", f"{root}/*/auto-fix-runs/{prefix}*.yaml"):
            if pattern not in patterns:
                patterns.append(pattern)
    patterns.append(TEST_DATA_EXCLUDE)
    return patterns


def sparse_patterns(type_names):
    """The sparse-checkout set for the DATA_REPO_TYPES names (parsed); [] = the default."""
    return patterns_for(type_layouts(type_names))


def main():
    if len(sys.argv) < 2:
        print("Usage: clone_results_repo.py <repo-path-or-url> [dest]", file=sys.stderr)
        sys.exit(1)

    repo = sys.argv[1]
    dest = sys.argv[2] if len(sys.argv) > 2 else "/tmp/data-repo"
    token = os.environ.get("DATA_REPO_TOKEN", "")
    try:
        clone_url = build_clone_url(repo, token)
        layouts = type_layouts(parse_types(os.environ.get(TYPES_ENV)))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    patterns = patterns_for(layouts)

    if os.path.exists(dest):
        shutil.rmtree(dest)

    # Sparse clone — only download blobs we check out
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", clone_url, dest],
        check=True,
        capture_output=True,
        text=True,
    )

    # Materialize only the symlinks and snapshots of the selected subtrees
    subprocess.run(
        ["git", "sparse-checkout", "set", "--no-cone", *patterns],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )

    # A subtree with no run yet matches nothing: create it so the reader sees an
    # existing, empty data dir ("no 'latest' symlink") rather than a missing path.
    for subdir, _prefix in layouts:
        if subdir:
            os.makedirs(os.path.join(dest, subdir), exist_ok=True)

    print(dest)


if __name__ == "__main__":
    main()
