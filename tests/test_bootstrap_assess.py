#!/usr/bin/env python3
"""Tests for scripts/bootstrap-assess-rfe.sh --type validation.

Bootstrap is the only gate between "the plugin checkout is complete" and an
agent phase that can never finish. A checkout missing the initiative rubric or
the initiative-scorer agent used to exit 0 here, and the failure surfaced much
later as wait-for-wave returning exit 3 forever with nothing to diagnose.
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import type_registry  # noqa: E402
from pipeline_state import PIPELINE_TYPES  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "bootstrap-assess-rfe.sh")
SKILLS_DIR = os.path.join(REPO_ROOT, ".claude", "skills")

RFE_RUBRIC = "skills/assess-rfe/scripts/agent_prompt.md"
INITIATIVE_RUBRIC = "skills/assess-initiative/scripts/agent_prompt.md"


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("# stub\n")


@pytest.fixture
def fake_checkout(tmp_path):
    """A working dir with a pre-existing .context/assess-rfe.

    The directory being present sends the script down the `git pull` branch,
    which fails on a non-repo and is swallowed by its `|| echo WARN` — so these
    tests never touch the network.
    """
    ctx = tmp_path / ".context" / "assess-rfe"
    os.makedirs(ctx)
    orig = os.getcwd()
    os.chdir(tmp_path)
    yield ctx
    os.chdir(orig)


def _run(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("ASSESS_RFE_REF", "RFE_SKIP_BOOTSTRAP")}
    result = subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, env=env)
    return result.stdout, result.stderr, result.returncode


def _add_rfe_assets(ctx):
    _touch(str(ctx / RFE_RUBRIC))


def _add_initiative_assets(ctx, rubric=True, agent=True):
    if rubric:
        _touch(str(ctx / INITIATIVE_RUBRIC))
    if agent:
        _touch(str(ctx / "agents" / "initiative-scorer.md"))


class TestTypeValidation:
    def test_rfe_default_passes_without_initiative_assets(self, fake_checkout):
        """The RFE path must not start failing over an asset it never uses."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run()
        assert rc == 0, stderr

    def test_initiative_fails_when_rubric_missing(self, fake_checkout):
        """The exact upstream state today: RFE rubric present, initiative absent."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 1
        assert "assess-initiative" in stderr
        assert "ASSESS_RFE_REPO" in stderr

    def test_initiative_fails_when_scorer_agent_missing(self, fake_checkout):
        """Rubric alone is not enough — the assess agent needs the subagent type."""
        _add_rfe_assets(fake_checkout)
        _add_initiative_assets(fake_checkout, agent=False)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 1
        assert "initiative-scorer" in stderr

    def test_initiative_passes_with_full_checkout(self, fake_checkout):
        _add_rfe_assets(fake_checkout)
        _add_initiative_assets(fake_checkout)

        _, stderr, rc = _run("--type", "initiative")
        assert rc == 0, stderr

    def test_equals_form_accepted(self, fake_checkout):
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type=initiative")
        assert rc == 1, stderr
        assert "assess-initiative" in stderr

    def test_unknown_type_rejected_with_the_registered_list(self, fake_checkout):
        """The names come from `type_registry.py list` (design §5 rung 1): the paper epic
        descriptor is not registered, so it is refused with the list that is."""
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--type", "epic")
        assert rc == 2
        assert stderr == "ERROR: unknown --type 'epic' (registered types: rfe, initiative)\n"

    def test_unknown_type_rejected_before_skip_bootstrap(self, fake_checkout):
        """Validation stays ahead of the RFE_SKIP_BOOTSTRAP short-circuit."""
        env = {**os.environ, "RFE_SKIP_BOOTSTRAP": "1"}
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "bogus"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert "registered types: rfe, initiative" in result.stderr
        assert result.stdout == ""

    def test_drop_in_type_is_accepted(self, fake_checkout, drop_in_root):
        """A type the registry enumerates (here through the RFE_CREATOR_EXTRA_TYPES seam,
        allowlisted so a CI run honours it too) passes without editing the script."""
        root = drop_in_root.memo()
        env = {
            **os.environ,
            "RFE_CREATOR_EXTRA_TYPES": root,
            "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": root,
            "RFE_SKIP_BOOTSTRAP": "1",
        }
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "memo"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step\n"
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "bogus"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert "(registered types: rfe, initiative, memo)" in result.stderr

    @staticmethod
    def _env_with_python3(shim_dir):
        env = {**os.environ, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"}
        env["RFE_SKIP_BOOTSTRAP"] = "1"
        env.pop("PYTHONPATH", None)
        return env

    def test_unreadable_registry_falls_back_to_the_shipped_root(self, fake_checkout, tmp_path):
        """This script is the dependency bootstrap, so it cannot require a working registry:
        when `type_registry.py list` fails the names come from types/<name>/type.yaml (rfe
        first, as `list` prints them), the registry's stderr is not shown, and a registered
        --type behaves exactly as before PR-3a."""
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_python = fakebin / "python3"
        fake_python.write_text("#!/bin/sh\necho 'ERROR: fake registry failure' >&2\nexit 1\n")
        fake_python.chmod(0o755)
        env = self._env_with_python3(fakebin)
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "initiative"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step\n"
        assert result.stderr == ""
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "bogus"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert result.stderr == (
            "ERROR: unknown --type 'bogus' (registered types: rfe, initiative)\n"
        )

    def test_python_without_yaml_still_validates_the_type(self, fake_checkout, tmp_path):
        """The interactive first run: PyYAML is not installed yet (site-packages disabled
        stands in for that), and `--type rfe` must still exit 0 under RFE_SKIP_BOOTSTRAP."""
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        shim = fakebin / "python3"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n')
        shim.chmod(0o755)
        env = self._env_with_python3(fakebin)
        probe = subprocess.run(
            ["python3", "-c", "import yaml"], capture_output=True, text=True, env=env
        )
        if probe.returncode == 0:
            pytest.skip("this interpreter imports yaml even without site-packages")
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "rfe"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step\n"
        assert result.stderr == ""
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "epic"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert result.stderr == "ERROR: unknown --type 'epic' (registered types: rfe, initiative)\n"

    def test_unreadable_registry_without_a_types_root_exits_2(self, fake_checkout, tmp_path):
        """Both sources gone — the script copied away from the repo, so neither
        <scripts>/type_registry.py nor <scripts>/../types exists — is the one fatal case."""
        stray = tmp_path / "stray" / "scripts"
        stray.mkdir(parents=True)
        copied = stray / "bootstrap-assess-rfe.sh"
        shutil.copy(SCRIPT, copied)
        env = {**os.environ, "RFE_SKIP_BOOTSTRAP": "1"}
        result = subprocess.run(
            ["bash", str(copied), "--type", "rfe"], capture_output=True, text=True, env=env
        )
        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr.startswith("ERROR: could not read the type registry")
        assert "holds no <name>/type.yaml" in result.stderr

    def test_unknown_argument_rejected(self, fake_checkout):
        _add_rfe_assets(fake_checkout)

        _, stderr, rc = _run("--initiative")
        assert rc == 2
        assert "unknown argument" in stderr

    def test_skip_bootstrap_still_short_circuits(self, fake_checkout):
        """RFE_SKIP_BOOTSTRAP wins over validation — offline runs stay possible."""
        env = {**os.environ, "RFE_SKIP_BOOTSTRAP": "1"}
        result = subprocess.run(
            ["bash", SCRIPT, "--type", "initiative"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0


class TestPathsMatchPipelineRegistry:
    """The shell script restates paths PIPELINE_TYPES already owns.

    Duplication is fine, silent divergence is not: if the rubric moves in the
    registry but not here, bootstrap validates a path nothing reads.
    """

    def _script_text(self):
        with open(SCRIPT) as f:
            return f.read()

    def test_rfe_rubric_matches_registry(self):
        assert PIPELINE_TYPES["rfe"]["rubric_path"].endswith(RFE_RUBRIC)
        assert RFE_RUBRIC in self._script_text()

    def test_initiative_rubric_matches_registry(self):
        assert PIPELINE_TYPES["initiative"]["rubric_path"].endswith(INITIATIVE_RUBRIC)
        assert INITIATIVE_RUBRIC in self._script_text()

    def test_scorer_agent_filename_matches_registry(self):
        expected = PIPELINE_TYPES["initiative"]["scorer_type"] + ".md"
        assert f'INITIATIVE_AGENT="{expected}"' in self._script_text()


class TestCallersDeclareType:
    """A skill that forgets the flag silently loses the gate. Since PR-5b the callers are the
    generic bodies and the typed split prompt, rendered per type with the launch block: every
    bootstrap call they render must carry --type <t>. The legacy initiative bodies are checked
    too until PR-5c deletes them."""

    GENERIC_CALLERS = ("rfe-create", "rfe-review", "rfe-auto-fix", "rfe-speedrun")

    def _desc(self, t):
        return type_registry.load(extra_roots=[], env={}).get(t)

    def _surfaces(self, t):
        """(repo-relative file, stage) of every generic surface that bootstraps for type t."""
        for name in self.GENERIC_CALLERS:
            yield f".claude/skills/{name}/SKILL.md", name.split("-", 1)[1]
        yield self._desc(t).get("pipeline.prompts.split_rules"), "split"

    def _rendered(self, t, rel, stage):
        with open(os.path.join(REPO_ROOT, rel)) as f:
            text = f.read()
        for key, value in type_registry.launch_vars(self._desc(t), stage):
            text = text.replace("{" + key + "}", value)
        return text

    @staticmethod
    def _bootstrap_lines_in(text):
        return [ln for ln in text.splitlines() if re.search(r"bootstrap-assess-rfe\.sh", ln)]

    @pytest.mark.parametrize("t", sorted(PIPELINE_TYPES))
    def test_every_generic_caller_passes_the_resolved_type(self, t):
        seen = []
        for rel, stage in self._surfaces(t):
            for line in self._bootstrap_lines_in(self._rendered(t, rel, stage)):
                seen.append((rel, line.strip()))
                assert f"--type {t}" in line, f"{rel} rendered for {t}: {line.strip()}"
        assert len(seen) >= len(self.GENERIC_CALLERS) + 1, seen

    @pytest.mark.parametrize("t", sorted(PIPELINE_TYPES))
    def test_the_launch_block_is_the_only_bootstrap_site(self, t):
        """No generic body or typed prompt hand-writes the command: every bootstrap call is
        the BOOTSTRAP launch var, which carries the type — a body cannot drop the flag."""
        block = dict(type_registry.launch_vars(self._desc(t), "review"))
        assert block["BOOTSTRAP"] == f"bash scripts/bootstrap-assess-rfe.sh --type {t}"
        for rel, _ in self._surfaces(t):
            with open(os.path.join(REPO_ROOT, rel)) as f:
                raw = f.read()
            assert "bootstrap-assess-rfe.sh" not in raw, rel
            assert "{BOOTSTRAP}" in raw, rel

    def _bootstrap_lines(self, path):
        with open(path) as f:
            return [ln for ln in f if re.search(r"bootstrap-assess-rfe\.sh", ln)]

    def _skill_files(self):
        for dirpath, _, filenames in os.walk(SKILLS_DIR):
            for name in filenames:
                if name.endswith(".md"):
                    yield os.path.join(dirpath, name)

    def test_initiative_skills_pass_type_initiative(self):
        missing = []
        for path in self._skill_files():
            if "initiative-" not in path:
                continue
            for line in self._bootstrap_lines(path):
                if "--type initiative" not in line:
                    missing.append(f"{os.path.relpath(path, REPO_ROOT)}: {line.strip()}")
        detail = "\n".join(missing)
        assert not missing, f"initiative skills invoking bootstrap without --type:\n{detail}"

    def test_at_least_one_initiative_caller_exists(self):
        """Guards the filter above from passing vacuously."""
        found = [
            path
            for path in self._skill_files()
            if "initiative-" in path and self._bootstrap_lines(path)
        ]
        assert len(found) >= 3

    def test_pipeline_setup_phase_is_type_aware(self):
        """The pipeline is where a missed gate becomes an unbounded wait-for-wave spin."""
        from pipeline_state import _build_phase_config

        for ptype in PIPELINE_TYPES:
            setup = _build_phase_config(ptype)["SETUP"]
            # SETUP runs its bootstrap steps as a concurrent "commands" list.
            commands = setup.get("commands") or [setup["command"]]
            assert any(f"bootstrap-assess-rfe.sh --type {ptype}" in c for c in commands)


class TestRubricPin:
    """The checkout sits at the descriptor's pipeline.rubric.ref (design §7.3 / Q9) unless
    ASSESS_RFE_REF overrides it, and the script verifies where it landed."""

    @staticmethod
    def _git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    @pytest.fixture
    def assess_repo(self, tmp_path):
        """A local assess-rfe stand-in with two commits; returns (url, first_sha, second_sha)."""
        repo = tmp_path / "assess-rfe-origin"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        env_args = ["-c", "user.name=t", "-c", "user.email=t@example.com"]
        _touch(str(repo / RFE_RUBRIC))
        _touch(str(repo / "skills" / "export-rubric" / "scripts" / "export_rubric.py"))
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), *env_args, "commit", "-q", "-m", "one"], check=True)
        first = self._git(repo, "rev-parse", "HEAD")
        with open(repo / RFE_RUBRIC, "a") as f:
            f.write("second\n")
        subprocess.run(
            ["git", "-C", str(repo), *env_args, "commit", "-q", "-am", "two"], check=True
        )
        second = self._git(repo, "rev-parse", "HEAD")
        return f"file://{repo}", first, second

    @pytest.fixture
    def workdir(self, tmp_path):
        wd = tmp_path / "wd"
        wd.mkdir()
        orig = os.getcwd()
        os.chdir(wd)
        yield wd
        os.chdir(orig)

    def _env(self, drop_in_root, repo_url, pin, descriptor_repo=None, **extra):
        """The bootstrap's environment for a drop-in `memo` type pinned at ``pin``. By default
        the clone URL comes from ASSESS_RFE_REPO=``repo_url``; with ``descriptor_repo`` the
        descriptor names the repository instead and the env override is left unset."""
        from conftest import MEMO_OVERRIDES

        overrides = {**MEMO_OVERRIDES, "pipeline.rubric.ref": pin}
        if descriptor_repo is not None:
            overrides["pipeline.rubric.repo"] = descriptor_repo
        root = drop_in_root.add("memo", overrides=overrides)
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("ASSESS_RFE_REF", "ASSESS_RFE_REPO", "RFE_SKIP_BOOTSTRAP")
        }
        env.update({"RFE_CREATOR_EXTRA_TYPES": root, "RFE_CREATOR_EXTRA_TYPES_ALLOWLIST": root})
        if descriptor_repo is None:
            env["ASSESS_RFE_REPO"] = repo_url
        env.update(extra)
        return env

    def test_descriptor_repo_is_cloned_without_an_env_override(
        self, workdir, drop_in_root, assess_repo
    ):
        """Rule 6 / CWE-345 (CodeRabbit on #198): with ASSESS_RFE_REPO unset the clone URL is
        the descriptor's pipeline.rubric.repo, not a built-in default."""
        url, first, second = assess_repo
        result = self._run(self._env(drop_in_root, url, first, descriptor_repo=url))
        assert result.returncode == 0, result.stderr
        assert (
            f"cloning assess-rfe from {url} (types/memo/type.yaml pipeline.rubric.repo)"
            in result.stdout
        )
        assert self._git(workdir / ".context" / "assess-rfe", "remote", "get-url", "origin") == url
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first

    def test_env_repo_override_wins_over_the_descriptor(
        self, workdir, tmp_path, drop_in_root, assess_repo
    ):
        url, first, second = assess_repo
        mirror = tmp_path / "assess-rfe-mirror"
        subprocess.run(["git", "clone", "-q", "--bare", url, str(mirror)], check=True)
        mirror_url = f"file://{mirror}"
        result = self._run(
            self._env(drop_in_root, url, first, descriptor_repo=url, ASSESS_RFE_REPO=mirror_url)
        )
        assert result.returncode == 0, result.stderr
        assert f"cloning assess-rfe from {mirror_url} (ASSESS_RFE_REPO)" in result.stdout
        assert (
            self._git(workdir / ".context" / "assess-rfe", "remote", "get-url", "origin")
            == mirror_url
        )
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first

    def _run(self, env):
        return subprocess.run(
            ["bash", SCRIPT, "--type", "memo"], capture_output=True, text=True, env=env
        )

    def test_fresh_clone_lands_on_the_descriptor_pin(self, workdir, drop_in_root, assess_repo):
        url, first, second = assess_repo
        result = self._run(self._env(drop_in_root, url, first))
        assert result.returncode == 0, result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first
        assert (
            f"assess-rfe at {first[:12]} (types/memo/type.yaml pipeline.rubric.ref)"
            in result.stdout
        )

    def test_cached_checkout_is_moved_to_the_pin(self, workdir, drop_in_root, assess_repo):
        """The second run of the day: the clone exists at the other commit."""
        url, first, second = assess_repo
        subprocess.run(["git", "clone", "-q", url, ".context/assess-rfe"], check=True)
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == second
        result = self._run(self._env(drop_in_root, url, first))
        assert result.returncode == 0, result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first

    def test_env_override_wins_over_the_pin(self, workdir, drop_in_root, assess_repo):
        url, first, second = assess_repo
        result = self._run(self._env(drop_in_root, url, first, ASSESS_RFE_REF=second))
        assert result.returncode == 0, result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == second
        # The fresh clone already sits on the default branch head, which is the override.
        assert f"assess-rfe at {second[:12]} (ASSESS_RFE_REF, already checked out)" in result.stdout

    def test_branch_override_is_accepted_without_a_sha_check(
        self, workdir, drop_in_root, assess_repo
    ):
        url, first, second = assess_repo
        result = self._run(self._env(drop_in_root, url, first, ASSESS_RFE_REF="main"))
        assert result.returncode == 0, result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == second

    def test_unresolvable_pin_fails_loudly(self, workdir, drop_in_root, assess_repo):
        url, first, second = assess_repo
        result = self._run(self._env(drop_in_root, url, "0" * 40))
        assert result.returncode == 1
        assert f"could not check out assess-rfe at {'0' * 40}" in result.stderr
        assert "types/memo/type.yaml pipeline.rubric.ref" in result.stderr

    def _clone_at(self, url, sha):
        subprocess.run(["git", "clone", "-q", url, ".context/assess-rfe"], check=True)
        subprocess.run(
            ["git", "-C", ".context/assess-rfe", "checkout", "-q", "--detach", sha], check=True
        )
        # Offline from here on: every fetch fails.
        subprocess.run(
            [
                "git",
                "-C",
                ".context/assess-rfe",
                "remote",
                "set-url",
                "origin",
                "file:///nonexistent",
            ],
            check=True,
        )

    def test_checkout_already_at_the_pin_needs_no_network(self, workdir, drop_in_root, assess_repo):
        """Review finding 1(a): HEAD already equals the pin — fine, continue, no fetch."""
        url, first, second = assess_repo
        self._clone_at(url, first)
        result = self._run(self._env(drop_in_root, url, first))
        assert result.returncode == 0, result.stderr
        assert "already checked out" in result.stdout
        assert "fetch failed" not in result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first

    def test_offline_cached_objects_reach_the_pin(self, workdir, drop_in_root, assess_repo):
        """The fetch fails but the cached objects hold the pin: a warning, then the move."""
        url, first, second = assess_repo
        self._clone_at(url, second)
        result = self._run(self._env(drop_in_root, url, first))
        assert result.returncode == 0, result.stderr
        assert "WARN: assess-rfe fetch failed, using cached objects" in result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first

    def test_positive_mismatch_fails_with_git_error_text(self, workdir, drop_in_root, assess_repo):
        """Review finding 1(c): HEAD readable, not the pin, pin unreachable — exit 1, and git's
        own message is printed so the operator can tell a missing commit from a stale lock."""
        url, first, second = assess_repo
        self._clone_at(url, second)
        result = self._run(self._env(drop_in_root, url, "0" * 40))
        assert result.returncode == 1
        assert f"could not check out assess-rfe at {'0' * 40}" in result.stderr
        assert f"HEAD is {second[:12]}" in result.stderr
        assert re.search(r"fatal|error", result.stderr), result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 directory")
    def test_unoperable_checkout_warns_and_continues(self, workdir, drop_in_root, assess_repo):
        """Review finding 1(b): git cannot open the checkout (here an unreadable .git; in
        production a checkout owned by another UID, which git refuses as "dubious ownership"
        and this script never overrides) — warn, keep the vendored files as found, no git
        write, exit 0."""
        url, first, second = assess_repo
        self._clone_at(url, second)
        git_dir = workdir / ".context" / "assess-rfe" / ".git"
        os.chmod(git_dir, 0)
        try:
            result = self._run(self._env(drop_in_root, url, first))
        finally:
            os.chmod(git_dir, 0o755)
        assert result.returncode == 0, result.stderr
        assert "WARN: git cannot operate on .context/assess-rfe" in result.stderr
        assert "is not enforced, using the checkout as is" in result.stderr
        # Nothing was checked out: HEAD is where the (untrusted) checkout left it.
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == second
        # The skills were still vendored from the checkout as found.
        assert (
            workdir / ".claude" / "skills" / "assess-rfe" / "scripts" / "agent_prompt.md"
        ).is_file()

    def test_dubious_ownership_is_respected_not_overridden(
        self, workdir, drop_in_root, assess_repo
    ):
        """CodeRabbit on #198: git's own ownership refusal, not a filesystem permission. With
        GIT_TEST_ASSUME_DIFFERENT_OWNER git reports "dubious ownership" for every command on
        the checkout; the script must take the WARN-and-continue branch — vendored files
        copied, HEAD untouched, no fetch. A `safe.directory` override in the script would
        make this test fail: git would then accept the checkout and move HEAD to the pin.

        Not every git build or config honours the hook (the GitHub ubuntu runner does not:
        lint.yml run 35604448686 saw rev-parse and checkout succeed and only the fetch fail),
        so the test probes git first and skips itself, with the reason, where the premise
        does not hold. The mode-000 test above is the unconditional unopenable-checkout guard.
        """
        url, first, second = assess_repo
        self._clone_at(url, second)
        env = self._env(drop_in_root, url, first, GIT_TEST_ASSUME_DIFFERENT_OWNER="1")
        probe = subprocess.run(
            ["git", "-C", ".context/assess-rfe", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            env=env,
        )
        if probe.returncode == 0 or "dubious ownership" not in probe.stderr:
            version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
            safe_dirs = {
                scope: subprocess.run(
                    ["git", "config", scope, "--get-all", "safe.directory"],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                for scope in ("--system", "--global")
            }
            pytest.skip(
                "this git does not refuse the checkout under GIT_TEST_ASSUME_DIFFERENT_OWNER=1: "
                f"{version.strip()}; probe rc={probe.returncode} stderr={probe.stderr.strip()!r}; "
                f"safe.directory system={safe_dirs['--system']!r} global={safe_dirs['--global']!r}"
            )
        result = self._run(env)
        assert result.returncode == 0, result.stderr
        assert "WARN: git cannot operate on .context/assess-rfe" in result.stderr
        assert "dubious ownership" in result.stderr
        assert "fetch failed" not in result.stderr
        assert (
            workdir / ".claude" / "skills" / "assess-rfe" / "scripts" / "agent_prompt.md"
        ).is_file()
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == second

    def test_long_hex_tag_that_is_not_its_targets_prefix_is_a_ref(
        self, workdir, drop_in_root, assess_repo
    ):
        """pin_is_commit boundary (CodeRabbit on #198): `deadbeef0` passes the all-hex and
        length checks, so only the resolves-to-itself check makes it a ref name; drop that
        check and the HEAD verification fails here."""
        url, first, second = assess_repo
        repo = url[len("file://") :]
        assert not first.startswith("deadbeef0")
        subprocess.run(["git", "-C", repo, "tag", "deadbeef0", first], check=True)
        result = self._run(self._env(drop_in_root, url, second, ASSESS_RFE_REF="deadbeef0"))
        assert result.returncode == 0, result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first
        assert f"assess-rfe at {first[:12]} (ASSESS_RFE_REF)" in result.stdout

    def test_short_hex_tag_that_is_its_targets_prefix_is_still_a_ref(
        self, workdir, drop_in_root, assess_repo
    ):
        """The length check alone: a 4-hex tag named after its target's first four characters
        resolves to a commit that starts with it, so only `>= 7` keeps it a ref name. With the
        checkout already at the target, dropping the length check would report the
        "already checked out" shortcut instead of checking the ref out normally."""
        url, first, second = assess_repo
        repo = url[len("file://") :]
        tag = first[:4]
        subprocess.run(["git", "-C", repo, "tag", tag, first], check=True)
        subprocess.run(["git", "clone", "-q", url, ".context/assess-rfe"], check=True)
        subprocess.run(
            ["git", "-C", ".context/assess-rfe", "checkout", "-q", "--detach", first], check=True
        )
        result = self._run(self._env(drop_in_root, url, second, ASSESS_RFE_REF=tag))
        assert result.returncode == 0, result.stderr
        assert "already checked out" not in result.stdout
        assert f"assess-rfe at {first[:12]} (ASSESS_RFE_REF)" in result.stdout
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first

    def test_hex_named_tag_override_is_a_ref_not_a_sha(self, workdir, drop_in_root, assess_repo):
        """Review finding 2: `cafe` is all-hex but resolves to a commit that does not start
        with it, so it is a ref name and HEAD is not checked against it."""
        url, first, second = assess_repo
        repo = url[len("file://") :]
        subprocess.run(["git", "-C", repo, "tag", "cafe", first], check=True)
        result = self._run(self._env(drop_in_root, url, second, ASSESS_RFE_REF="cafe"))
        assert result.returncode == 0, result.stderr
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first
        assert f"assess-rfe at {first[:12]} (ASSESS_RFE_REF)" in result.stdout

    def test_worktree_with_a_git_file_is_pinned(self, workdir, drop_in_root, assess_repo):
        """Review finding 3: a worktree's .git is a file; it is a git checkout all the same."""
        url, first, second = assess_repo
        repo = url[len("file://") :]
        os.makedirs(".context")
        subprocess.run(
            [
                "git",
                "-C",
                repo,
                "worktree",
                "add",
                "-q",
                "--detach",
                str(workdir / ".context" / "assess-rfe"),
                second,
            ],
            check=True,
        )
        assert (workdir / ".context" / "assess-rfe" / ".git").is_file()
        try:
            result = self._run(self._env(drop_in_root, url, first))
            assert result.returncode == 0, result.stderr
            assert "not a git checkout" not in result.stderr
            assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first
        finally:
            subprocess.run(
                [
                    "git",
                    "-C",
                    repo,
                    "worktree",
                    "remove",
                    "--force",
                    str(workdir / ".context" / "assess-rfe"),
                ]
            )

    def test_awk_fallback_pins_without_a_working_registry(self, workdir, tmp_path, assess_repo):
        """Review item 6: no working python3 (the first interactive run) — the type list and the
        pin both come from the descriptor next to the script, and the clone lands on it."""
        url, first, second = assess_repo
        stray = tmp_path / "stray"
        (stray / "scripts").mkdir(parents=True)
        (stray / "types" / "rfe").mkdir(parents=True)
        shutil.copy(SCRIPT, stray / "scripts" / "bootstrap-assess-rfe.sh")
        with open(os.path.join(REPO_ROOT, "types", "rfe", "type.yaml")) as f:
            descriptor = f.read()
        shipped_ref = re.search(r'^    ref: "([0-9a-f]{40})"', descriptor, re.M).group(1)
        (stray / "types" / "rfe" / "type.yaml").write_text(descriptor.replace(shipped_ref, first))
        fakebin = tmp_path / "fakebin"
        fakebin.mkdir()
        fake_python = fakebin / "python3"
        fake_python.write_text("#!/bin/sh\necho 'ERROR: fake registry failure' >&2\nexit 1\n")
        fake_python.chmod(0o755)
        env = {
            k: v for k, v in os.environ.items() if k not in ("ASSESS_RFE_REF", "RFE_SKIP_BOOTSTRAP")
        }
        env["PATH"] = f"{fakebin}{os.pathsep}{env['PATH']}"
        env["ASSESS_RFE_REPO"] = url
        result = subprocess.run(
            ["bash", str(stray / "scripts" / "bootstrap-assess-rfe.sh"), "--type", "rfe"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
        assert (
            f"assess-rfe at {first[:12]} (types/rfe/type.yaml pipeline.rubric.ref)" in result.stdout
        )
        assert self._git(workdir / ".context" / "assess-rfe", "rev-parse", "HEAD") == first

    def test_non_git_checkout_warns_and_continues(self, fake_checkout):
        """A vendored copy (no .git) cannot be pinned; the script says so and keeps the
        previous behaviour rather than failing an offline run."""
        _add_rfe_assets(fake_checkout)
        _, stderr, rc = _run()
        assert rc == 0, stderr
        assert "is not a git checkout; the rubric pin" in stderr
        assert "types/rfe/type.yaml pipeline.rubric.ref" in stderr

    def test_shipped_pins_match_the_fallback_parser(self):
        """The no-Python fallback (the awk programme in the script) must read the same repo and
        ref the registry serves; both shipped descriptors pin one full SHA."""
        with open(SCRIPT) as f:
            script = f.read()
        m = re.search(r"awk -v key=\"\$1\" '((?:[^']|\n)*?)' \"\$TYPES_ROOT", script)
        assert m, "the rubric_field_from_root awk programme moved"
        programme = m.group(1)
        registry_py = os.path.join(REPO_ROOT, "scripts", "type_registry.py")
        for name in ("rfe", "initiative"):
            values = {}
            for field in ("ref", "repo"):
                registry = subprocess.run(
                    [sys.executable, registry_py, "get", name, f"pipeline.rubric.{field}"],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                descriptor = os.path.join(REPO_ROOT, "types", name, "type.yaml")
                awk = subprocess.run(
                    ["awk", "-v", f"key={field}", programme, descriptor],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
                assert registry == awk, (name, field, registry, awk)
                values[field] = registry
            assert re.fullmatch(r"[0-9a-f]{40}", values["ref"]), (name, values)
            assert values["repo"] == "https://github.com/opendatahub-io/assess-rfe", (name, values)
