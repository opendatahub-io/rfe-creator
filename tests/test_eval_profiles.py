#!/usr/bin/env python3
"""Tests for the OpenRouter eval profiles (eval-profiles/) and the routing table the generated
configs carry.

The harness (agent-eval-harness >= 1.53.1) layers a profile over its base with the ``extends:``
policy: mappings merge key by key, scalars override, scalar lists extend with dedupe. The
tests mirror only that much of the policy, so they stay stdlib+pyyaml and import nothing from
the harness, and they pin the properties the migration off the LiteLLM proxy relies on:

* every profile chains back to the generated ``eval.yaml`` and a model profile merges to an
  ``openrouter:/`` skill AND subagent (a mixed pair fails the harness's provider-kind check);
* the merged allow list is exactly the base's (``Skill``, ``Agent``, the tmp Edit rule): no
  profile adds a blanket interpreter rule — the harness's absolute-workspace twins of the
  project's script rules cover the absolute-path calls weaker models make;
* no config authors the transport env the harness owns while a plan is active (the base URL,
  the auth token, the Vertex switches, the model aliases — rejected on presence) and nothing
  names an OpenRouter key;
* the routing table lives once, in the skeleton-generated configs: bare slugs as keys,
  provider SLUGS in ``order``, agent-path knobs only (``require_parameters`` and ``sort``
  cannot be sent from Claude Code); every model profile has an entry;
* the proxy-era hooks are gone.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PROFILES_DIR = REPO / "eval-profiles"
GENERATED = ("eval.yaml", "eval-initiative.yaml")
PROFILES = sorted(PROFILES_DIR.glob("*.yaml"))

# The env the direct transport writes itself (agent_eval.providers.env.MANAGED_ENV_KEYS, the
# subset an eval config could plausibly author) plus the two key variables that may never be
# authored anywhere.
MANAGED_ENV = frozenset(
    {
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLOUD_ML_REGION",
        "GOOGLE_CLOUD_PROJECT",
        "CLAUDE_CODE_USE_BEDROCK",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MANAGEMENT_KEY",
    }
)
# What Claude Code can act on (checked by preflight, audited after the run); the other
# OpenRouter routing knobs are judge-only and raise a load-time warning on an agent key.
AGENT_PATH_KNOBS = frozenset({"order", "only", "ignore", "allow_fallbacks", "quantizations"})
SLUG = re.compile(r"^[a-z0-9.-]+/[a-z0-9.-]+$")
PROVIDER_SLUG = re.compile(r"^[a-z0-9-]+$")
KEY_SHAPED = re.compile(r"sk-or-v1-[0-9a-f]{8,}")


class _Replace(list):
    """A list tagged ``!replace``: replaces the base list instead of extending it."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts the harness's ``!replace`` list tag."""


_Loader.add_constructor("!replace", lambda loader, node: _Replace(loader.construct_sequence(node)))


def _load(path):
    return yaml.load(path.read_text(), Loader=_Loader) or {}


def _merge(base, overlay):
    if isinstance(overlay, _Replace):
        return list(overlay)
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = dict(base)
        for key, value in overlay.items():
            out[key] = _merge(base[key], value) if key in base else value
        return out
    if isinstance(base, list) and isinstance(overlay, list):
        if all(not isinstance(x, (dict, list)) for x in base + overlay):
            return base + [x for x in overlay if x not in base]
        return overlay  # keyed/mapping lists are not what these tests assert on
    return overlay


def merged(path):
    """The profile at ``path`` merged over its chain; returns ``(config, chain)`` root first."""
    raw = _load(path)
    parent = raw.pop("extends", None)
    if parent is None:
        return raw, [path]
    assert not str(parent).startswith("/"), f"{path}: extends must be a relative path"
    base, chain = merged((path.parent / parent).resolve())
    return _merge(base, raw), chain + [path]


def _env_surfaces(raw):
    yield "execution.env", (raw.get("execution") or {}).get("env") or {}
    runner = raw.get("runner") or {}
    yield "runner.env", runner.get("env") or {}
    yield "runner.settings.env", (runner.get("settings") or {}).get("env") or {}
    for step in (raw.get("execution") or {}).get("steps") or []:
        yield f"steps[{step.get('id')}].env", step.get("env") or {}


def _bare_slug(uri):
    assert uri.startswith("openrouter:/"), uri
    return uri[len("openrouter:/") :].split(":", 1)[0]


LAYERS = [PROFILES_DIR / "openrouter.yaml", PROFILES_DIR / "openrouter-sandboxed.yaml"]
MODEL_PROFILES = [p for p in PROFILES if merged(p)[0]["models"]["skill"].startswith("openrouter:/")]
BASE_ALLOW = ["Skill", "Agent", "Edit(tmp/rfe-assess/**)"]


def test_profiles_exist_next_to_the_shared_layer():
    assert (PROFILES_DIR / "openrouter.yaml").exists()
    assert (PROFILES_DIR / "openrouter-glm-5.2.yaml").exists(), "the spec's acceptance profile"
    assert MODEL_PROFILES


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.name)
def test_every_profile_chains_to_the_generated_rfe_config(profile):
    _, chain = merged(profile)
    assert chain[0] == REPO / "eval.yaml", [p.name for p in chain]
    assert chain[-1] == profile


@pytest.mark.parametrize("profile", MODEL_PROFILES, ids=lambda p: p.name)
def test_model_profile_puts_skill_and_subagent_on_openrouter(profile):
    cfg, _ = merged(profile)
    models = cfg["models"]
    assert models["skill"].startswith("openrouter:/"), models["skill"]
    assert models["subagent"].startswith("openrouter:/"), models["subagent"]
    assert not models["judge"].startswith("openrouter:/"), (
        "the judge stays on the ambient Anthropic"
    )
    assert _bare_slug(models["skill"]) == _bare_slug(models["subagent"])


@pytest.mark.parametrize("layer", LAYERS, ids=lambda p: p.name)
def test_shared_layer_alone_keeps_the_anthropic_roles(layer):
    cfg, _ = merged(layer)
    base = _load(REPO / "eval.yaml")
    assert {k: cfg["models"][k] for k in ("skill", "subagent", "judge")} == {
        k: base["models"][k] for k in ("skill", "subagent", "judge")
    }


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.name)
def test_merged_allow_list_is_the_base_list_and_nothing_more(profile):
    cfg, _ = merged(profile)
    allow = cfg["permissions"]["allow"]
    assert _load(REPO / "eval.yaml")["permissions"]["allow"] == BASE_ALLOW
    # No profile widens Bash: the harness's absolute twins of the project's relative
    # script rules cover the absolute-path calls; a blanket interpreter rule would be an
    # exfiltration transport for subagents holding the operator key at `audit`.
    assert allow == BASE_ALLOW, allow
    assert not [r for r in allow if r.startswith("Bash(")]
    assert cfg["permissions"].get("deny") == []
    # Claude Code consults Edit()/Read() path rules only; a Write(path) rule is accepted,
    # never matched, and warned about at startup.
    assert not [r for r in allow if r.startswith("Write(")], allow


@pytest.mark.parametrize(
    "path", [REPO / g for g in GENERATED] + PROFILES, ids=lambda p: str(p.relative_to(REPO))
)
def test_no_config_authors_the_managed_transport_env_or_a_key(path):
    raw = _load(path)
    for surface, env in _env_surfaces(raw):
        assert isinstance(env, dict), surface
        assert not (set(env) & MANAGED_ENV), (
            f"{path.name} {surface}: {sorted(set(env) & MANAGED_ENV)}"
        )
    assert not KEY_SHAPED.search(path.read_text()), f"{path.name} contains a key-shaped value"


@pytest.mark.parametrize("profile", PROFILES, ids=lambda p: p.name)
def test_profile_blanks_the_jira_write_credentials_and_little_else(profile):
    cfg, _ = merged(profile)
    env = cfg["execution"]["env"]
    assert env["JIRA_USER"] == "" and env["JIRA_TOKEN"] == ""
    assert set(env) <= {"JIRA_USER", "JIRA_TOKEN", "RFE_SKIP_BOOTSTRAP"}, env


def test_sandboxed_layer_locks_bash_egress_and_hides_the_key():
    cfg, chain = merged(PROFILES_DIR / "openrouter-sandboxed.yaml")
    assert [p.name for p in chain] == ["eval.yaml", "openrouter.yaml", "openrouter-sandboxed.yaml"]
    sandbox = cfg["runner"]["settings"]["sandbox"]
    assert sandbox["enabled"] is True and sandbox["failIfUnavailable"] is True
    assert sandbox["autoAllowBashIfSandboxed"] is True, "no rule needed for sandboxed Bash"
    assert sandbox["allowUnsandboxedCommands"] is False and sandbox["excludedCommands"] == []
    assert sandbox["network"] == {"allowedDomains": [], "strictAllowlist": True}
    assert "/tmp/rfe-assess" in sandbox["filesystem"]["allowWrite"]
    assert {"name": "ANTHROPIC_AUTH_TOKEN", "mode": "deny"} in sandbox["credentials"]["envVars"]
    assert cfg["execution"]["env"]["RFE_SKIP_BOOTSTRAP"] == "1", (
        "bootstrap clones + writes .claude/skills"
    )
    # Everything else is the shared layer's.
    shared, _ = merged(PROFILES_DIR / "openrouter.yaml")
    for key in ("models", "mlflow", "judges", "dataset", "thresholds"):
        assert cfg[key] == shared[key], key
    assert cfg["execution"]["timeout"] == shared["execution"]["timeout"]
    assert cfg["execution"]["max_budget_usd"] == shared["execution"]["max_budget_usd"]
    assert cfg["runner"]["type"] == shared["runner"]["type"]


@pytest.mark.parametrize("profile", MODEL_PROFILES, ids=lambda p: p.name)
def test_profile_flips_only_the_open_model_glue(profile):
    cfg, _ = merged(profile)
    base = _load(REPO / "eval.yaml")
    assert cfg["execution"]["timeout"] > base["execution"]["timeout"]
    assert cfg["mlflow"]["experiment"] == "rfe-speedrun-openrouter"
    # Real dollars: the per-run pool the audit checks is the same number the CLI cap derives
    # from (x cli_budget_inflation), not the Anthropic-priced 100 of the base.
    pool = cfg["models"]["providers"]["openrouter"]["budget"]["run_usd"]
    assert cfg["execution"]["max_budget_usd"] == pool < base["execution"]["max_budget_usd"]
    # Everything the baseline is measured with comes from the base untouched.
    for key in ("dataset", "inputs", "outputs", "traces", "judges", "thresholds", "runner"):
        assert cfg[key] == base[key], key


def test_routing_table_is_generated_once_and_shared_by_both_configs():
    tables = [_load(REPO / g)["models"]["providers"] for g in GENERATED]
    assert tables[0] == tables[1]
    assert (
        "openrouter" in tables[0] and "z-ai/glm-5.2" in tables[0]["openrouter"]["routing"]["models"]
    )
    skeleton = (REPO / "eval/config/skeleton.yaml").read_text()
    assert "  providers:\n    openrouter:\n" in skeleton, "the table is authored in the skeleton"


def test_routing_table_is_agent_path_only_with_bare_slugs_and_provider_slugs():
    block = _load(REPO / "eval.yaml")["models"]["providers"]["openrouter"]
    assert block["api_key_env"] == "OPENROUTER_API_KEY"
    assert block["attribution"]["referer"] == "https://github.com/opendatahub-io/rfe-creator"
    routing = block["routing"]
    # Enforcement is chosen in the shared profile, next to budget.run_usd: a base that said
    # key-guardrail without a run_usd would fail every consumer of eval.yaml at load.
    assert not ({"enforcement", "policy", "guardrail"} & set(routing)), sorted(routing)
    assert set(routing["defaults"]) <= AGENT_PATH_KNOBS
    for slug, spec in routing["models"].items():
        assert SLUG.match(slug), f"{slug}: key by the bare slug (no openrouter:/, no :variant)"
        assert set(spec) <= AGENT_PATH_KNOBS, f"{slug}: {sorted(set(spec) - AGENT_PATH_KNOBS)}"
        assert spec.get("order"), f"{slug}: an entry without a pin is noise"
        assert all(PROVIDER_SLUG.match(p) for p in spec["order"]), f"{slug}: slugs, not names"
        assert isinstance(spec["allow_fallbacks"], bool), slug
        for q in spec.get("quantizations") or []:
            assert q == q.lower(), f"{slug}: {q}"


@pytest.mark.parametrize("profile", MODEL_PROFILES, ids=lambda p: p.name)
def test_profile_chooses_a_valid_enforcement_next_to_the_budget(profile):
    cfg, _ = merged(profile)
    block = cfg["models"]["providers"]["openrouter"]
    enforcement = block["routing"]["enforcement"]
    assert enforcement in ("audit", "key-guardrail")
    assert block["routing"]["policy"] in ("strict", "warn")
    assert block["budget"]["run_usd"] > 0, "key-guardrail needs it and audit flags against it"


def test_merge_helper_honours_replace_so_a_dropped_allow_list_cannot_pass(tmp_path):
    profile = tmp_path / "replace.yaml"
    base = Path(__import__("os").path.relpath(REPO / "eval.yaml", tmp_path))
    profile.write_text(
        f"extends: {base.as_posix()}\npermissions:\n  allow: !replace ['Bash(python3 *)']\n"
    )
    cfg, chain = merged(profile)
    assert chain[0] == REPO / "eval.yaml"
    assert cfg["permissions"]["allow"] == ["Bash(python3 *)"], "replace must not extend"
    assert "Skill" not in cfg["permissions"]["allow"]


@pytest.mark.parametrize("profile", MODEL_PROFILES, ids=lambda p: p.name)
def test_every_model_profile_has_a_routing_entry(profile):
    cfg, _ = merged(profile)
    table = cfg["models"]["providers"]["openrouter"]["routing"]["models"]
    assert _bare_slug(cfg["models"]["skill"]) in table


@pytest.mark.parametrize(
    "path",
    [REPO / g for g in GENERATED] + [REPO / "eval/config/skeleton.yaml"] + PROFILES,
    ids=lambda p: str(p.relative_to(REPO)),
)
def test_no_proxy_era_hooks_remain(path):
    text = path.read_text()
    assert "hooks:" not in text and "reconcile_cost" not in text and "litellm" not in text.lower()


def test_dotenv_is_ignored():
    assert ".env" in (REPO / ".gitignore").read_text().splitlines()
