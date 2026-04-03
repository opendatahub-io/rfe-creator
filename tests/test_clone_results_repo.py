#!/usr/bin/env python3
"""Tests for scripts/clone_results_repo.py — URL building logic."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from clone_results_repo import build_clone_url


class TestBuildCloneUrl:
    def test_bare_path_with_token(self):
        """Bare project path + token → authenticated GitLab URL."""
        url = build_clone_url("org/group/repo", "glpat-xxx")
        assert url == "https://bot:glpat-xxx@gitlab.com/org/group/repo.git"

    def test_bare_path_no_token_raises(self):
        """Bare project path without token → ValueError."""
        with pytest.raises(ValueError, match="DATA_REPO_TOKEN required"):
            build_clone_url("org/group/repo", "")

    def test_https_url_with_token(self):
        """Full HTTPS URL + token → token injected."""
        url = build_clone_url(
            "https://gitlab.com/org/repo.git", "glpat-xxx")
        assert url == "https://bot:glpat-xxx@gitlab.com/org/repo.git"

    def test_https_url_no_token_passthrough(self):
        """Full HTTPS URL without token → unchanged."""
        orig = "https://gitlab.com/org/repo.git"
        assert build_clone_url(orig, "") == orig

    def test_ssh_url_passthrough(self):
        """SSH URL → unchanged regardless of token."""
        orig = "git@gitlab.com:org/repo.git"
        assert build_clone_url(orig, "glpat-xxx") == orig

    def test_https_url_with_port(self):
        """HTTPS URL with port → port preserved after token injection."""
        url = build_clone_url(
            "https://gitlab.example.com:8443/org/repo.git", "tok")
        assert "bot:tok@gitlab.example.com:8443" in url
