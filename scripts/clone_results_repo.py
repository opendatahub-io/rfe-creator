#!/usr/bin/env python3
"""Sparse-clone a data repo, fetching only snapshot files.

Only materializes the 'latest' symlink and issue-snapshot-*.yaml files.
Skips reviews, tasks, originals, run reports, and HTML reports.

Usage:
    DATA_REPO_TOKEN=<token> python3 scripts/clone_results_repo.py <repo-path-or-url> [dest]

Examples:
    DATA_REPO_TOKEN=glpat-xxx python3 scripts/clone_results_repo.py redhat/rhel-ai/agentic-ci/rfe-autofixer-results
    DATA_REPO_TOKEN=glpat-xxx python3 scripts/clone_results_repo.py https://gitlab.com/my/repo.git /tmp/data-repo

If repo arg is a bare path (no ://), builds a GitLab HTTPS URL with
the token embedded. Prints the clone destination to stdout.
"""

import os
import shutil
import subprocess
import sys
import urllib.parse


def build_clone_url(repo, token):
    """Build an authenticated clone URL from repo spec and token.

    Raises ValueError if a bare project path is given without a token.
    Local absolute paths (starting with /) are passed through as-is.
    """
    if os.path.isabs(repo):
        return repo
    if "://" not in repo and "@" not in repo:
        if not token:
            raise ValueError(
                "DATA_REPO_TOKEN required for private repos")
        return f"https://bot:{token}@gitlab.com/{repo}.git"
    if token and repo.startswith("https://"):
        parsed = urllib.parse.urlparse(repo)
        return parsed._replace(
            netloc=f"bot:{token}@{parsed.hostname}"
            + (f":{parsed.port}" if parsed.port else "")
        ).geturl()
    return repo


def main():
    if len(sys.argv) < 2:
        print("Usage: clone_results_repo.py <repo-path-or-url> [dest]",
              file=sys.stderr)
        sys.exit(1)

    repo = sys.argv[1]
    dest = sys.argv[2] if len(sys.argv) > 2 else "/tmp/data-repo"
    token = os.environ.get("DATA_REPO_TOKEN", "")
    try:
        clone_url = build_clone_url(repo, token)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if os.path.exists(dest):
        shutil.rmtree(dest)

    # Sparse clone — only download blobs we check out
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none",
         "--sparse", clone_url, dest],
        check=True, capture_output=True, text=True,
    )

    # Materialize only the symlink and snapshots
    subprocess.run(
        ["git", "sparse-checkout", "set", "--no-cone",
         "/latest",
         "/*/auto-fix-runs/issue-snapshot-*.yaml"],
        cwd=dest, check=True, capture_output=True, text=True,
    )

    print(dest)


if __name__ == "__main__":
    main()
