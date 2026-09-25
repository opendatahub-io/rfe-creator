# RFE Creator

Claude Code skills for creating, reviewing, and submitting RFEs to the RHAIRFE Jira project and Initiatives to the RHOAIENG Jira project.

Inspired by the [PRD/RFE workflow](https://github.com/ambient-code/workflows/tree/main/workflows/prd-rfe-workflow) in ambient, which established the pipeline pattern and multi-perspective review concept.

## Quick Start

```
# RFE Pipeline
/rfe-create     # Write a new RFE from a problem statement
/rfe-review     # Review, improve, and auto-revise RFEs
/rfe-split      # Split an oversized RFE into right-sized pieces
/rfe-submit     # Submit new or update existing RFEs in Jira
/rfe-speedrun   # Full pipeline end-to-end with minimal interaction
/rfe-auto-fix   # Batch review+revise+split pipeline (non-interactive)

# Improve an existing Jira RFE
/rfe-review RHAIRFE-1234      # Fetch, review, and auto-revise
/rfe-split RHAIRFE-1234       # Fetch and split an oversized RFE
/rfe-speedrun RHAIRFE-1234    # Fetch, review, revise, and update in one step

# Batch operations
/rfe-speedrun --input batch.yaml --headless --dry-run              # Batch create + review from YAML
/rfe-speedrun --input batch.yaml --headless --announce-complete    # Print completion marker for CI
/rfe-auto-fix --jql "project = RHAIRFE AND ..."         # Batch review from JQL query
/rfe-auto-fix RHAIRFE-1234 RHAIRFE-5678                 # Batch review explicit IDs

# Initiatives (RHOAIENG): the same skills with --type initiative
/rfe-create --type initiative                                       # Write a new Initiative from an engineering objective
/rfe-speedrun --type initiative RHOAIENG-12345                      # Fetch, review, revise, and update an Initiative
/rfe-auto-fix --type initiative --jql "project = RHOAIENG AND ..."  # Batch review Initiatives from JQL query

# Maintenance
/rfe-creator.update-deps   # Force update vendored dependencies
```

Every skill takes `--type <type>` (`rfe` or `initiative`); without it the type is resolved from the ids you pass (`RHAIRFE-*` / `RFE-*` are RFEs, `RHOAIENG-*` / `INIT-*` are Initiatives) and defaults to `rfe`. The dotted names of the original RFE skills (`/rfe.create`, `/rfe.review`, `/rfe.split`, `/rfe.submit`, `/rfe.speedrun`, `/rfe.auto-fix`) remain as compatibility aliases that run the same bodies.

## Install

**From a checkout** (development, CI, the evals): clone the repository and run the skills with the checkout as the working directory. Nothing else is needed; the first skill run bootstraps the vendored dependencies.

**From the Claude Code marketplace:** `/plugin marketplace add opendatahub-io/skills-registry`, then `/plugin install rfe-creator@opendatahub-skills`, and run the skills from any project directory. The plugin lives under `~/.claude/plugins/cache/`; the first thing each skill does in a directory without `scripts/` is run the bootstrap's `--layout` step through its own plugin path, which links the plugin's `scripts/` and `types/` into the working directory. From then on every command runs exactly as in a checkout. The bootstrap refuses a directory that already has its own `scripts/` or `types/` rather than mixing them. What the bootstrap writes into your project — the two links, `.context/`, the pipeline state under `tmp/`, the vendored `.claude/agents/*-scorer.md` and `.claude/skills/{assess-rfe,assess-initiative,export-rubric}/` — is appended to the repository's `.git/info/exclude` when the directory is inside a git repository (entries are anchored to the subdirectory when it is not the top level; it prints the list instead when it cannot write there). Your work items under `artifacts/`, including the exported `artifacts/rfe-rubric.md`, are yours to commit or ignore. Permission rules are per project: to run the pipelines headless without prompts, copy the `permissions` block (the `allow` list and `additionalDirectories`) and the `hooks` block from this repository's `.claude/settings.json` into your project's `.claude/settings.local.json`.

**From the Codex marketplace** (`codex plugin marketplace add opendatahub-io/skills-registry`): Codex installs the repository under `~/.codex/plugins/cache/<marketplace>/rfe-creator/<version>/` and runs the skills from your project (Codex's documentation does not say whether that fetch is complete; the plugin's `scripts/` and `types/` must be present in it). Codex substitutes no variables in a skill body, so the layout check's path is resolved from the skill file's location, which Codex shows in its skill list; after that the layout is the same. Only the agent-free skills, `$rfe-create`, `$rfe-submit` and `$rfe-creator.update-deps`, run end to end under Codex: the review, split, auto-fix and speedrun pipelines launch parallel subagents through Claude Code's Agent tool, for which Codex has no equivalent.

## Pipeline

### New RFEs

```
/rfe-create → /rfe-review → /rfe-submit
```

`/rfe-review` auto-revises issues it finds (up to 2 cycles). You can also edit artifacts manually between steps.

`/rfe-speedrun` runs the full pipeline with reasonable defaults and minimal interaction.

### Existing Jira RFEs

```
/rfe-review RHAIRFE-1234 → /rfe-submit
```

Or in one step: `/rfe-speedrun RHAIRFE-1234`

### Batch Operations

Create and review multiple RFEs from a YAML file:

```
/rfe-speedrun --headless --dry-run --input batch.yaml
```

YAML format:

```yaml
- prompt: "Users need to verify model signatures at serving time"
  priority: Critical
  labels: [candidate-3.5]
- prompt: "TrustyAI operator crashes on large clusters"
  priority: Major
```

Review a batch of existing Jira RFEs:

```
/rfe-auto-fix --jql "project = RHAIRFE AND status = New" --limit 20
/rfe-auto-fix RHAIRFE-1234 RHAIRFE-5678 RHAIRFE-9012
```

Auto-fix processes in batches (default 5), handles review, revision, splitting, retry, and report generation.

### Initiatives

The same skills run the pipeline for Initiatives targeting the RHOAIENG Jira project: pass `--type initiative` — e.g., `/rfe-speedrun --type initiative`, `/rfe-auto-fix --type initiative RHOAIENG-12345` (an `RHOAIENG-*` or `INIT-*` id resolves the type on its own). Use `RHOAIENG-*` keys for existing Jira initiatives and `INIT-*` IDs for new ones. Initiative review includes strategic alignment assessment (against RHAISTRAT Outcomes) in addition to rubric scoring and technical feasibility. Each type's judgement — template, clarifying questions, review rules, split rules and review dimensions — lives under `types/<type>/`; the skill bodies are shared.

### Strategy Pipeline

The strategy skills live in a dedicated repo: [opendatahub-io/strat-creator](https://github.com/opendatahub-io/strat-creator). This plugin no longer ships copies of its reviewer skills.

## Pipeline Steps

1. **Create**: Describe your need. The skill asks clarifying questions and produces RFEs. Supports `--headless` to skip questions (for batch/CI use).
2. **Review**: Scores RFEs against the assess-rfe rubric, checks technical feasibility, and auto-revises issues. Accepts Jira keys to review existing RFEs. Supports `--headless` for non-interactive use.
3. **Split**: Decompose an oversized RFE into right-sized pieces. Runs review on new RFEs, self-corrects right-sizing (up to 3 cycles), and checks scope coverage. Supports `--headless`.
4. **Auto-fix**: Batch pipeline that orchestrates review + revision + split + retry across many RFEs. Accepts explicit IDs or a `--jql` query. Processes in configurable batches (`--batch-size N`, default 5). Generates run reports and HTML review reports.
5. **Submit**: Creates new RHAIRFE tickets or updates existing ones in Jira. Supports `--dry-run` to validate without writing to Jira.
6. **Speedrun**: End-to-end pipeline (create → auto-fix → submit). Supports `--input <yaml>` for batch creation, `--headless` for CI, `--announce-complete` for completion signaling, `--dry-run` to skip Jira writes, and `--batch-size N`.

All pipeline steps apply identically to Initiatives (`--type initiative`). Initiative review additionally runs a strategic alignment check against the parent RHAISTRAT Outcome when one is linked.

## Editing Between Steps

All artifacts are written to `artifacts/`. You can edit any file between steps:

- Edit an RFE in `artifacts/rfe-tasks/RFE-001.md`, then re-run `/rfe-review`
- Edit an Initiative in `artifacts/initiatives/INIT-001.md`, then re-run `/rfe-review --type initiative`
- Re-run `/rfe-create` (or `/rfe-create --type initiative`) to start over from scratch

## assess-rfe Integration

Skills automatically bootstrap the [assess-rfe](https://github.com/opendatahub-io/assess-rfe) plugin from GitHub on first use:

- **During creation**: The rubric is exported to `artifacts/rfe-rubric.md` and used to guide clarifying questions.
- **During review**: `/rfe-review` invokes assess-rfe for rubric scoring.
- **Without network access**: The skills still work — creation uses built-in questions, review runs only the technical feasibility check.

Run `/rfe-creator.update-deps` to force-refresh to the latest version.

## Architecture Context

For RHOAI work, the technical feasibility and strategy reviews use architecture context from [opendatahub-io/architecture-context](https://github.com/opendatahub-io/architecture-context). This is fetched automatically via sparse checkout on first use.

## Jira Integration

Submission uses the Jira REST API directly via Python scripts (not the MCP server). Set these environment variables:

```bash
export JIRA_SERVER=https://your-site.atlassian.net
export JIRA_USER=your-email@example.com
export JIRA_TOKEN=your-api-token
```

The Atlassian MCP server is used for read operations (fetching issues, comments) when available, with a REST API fallback.

## Development

### Prerequisites

- Python 3.10+ with [ruff](https://docs.astral.sh/ruff/)
- [shellcheck](https://www.shellcheck.net/)

### Validate Changes

```bash
make lint
```

This runs [skillsaw](https://github.com/stbenjam/skillsaw), ruff (check + format), shellcheck, the work item type lints, and pytest. The type lint needs the dev dependencies (`pip install -r requirements-dev.txt`).

You can auto-fix some skillsaw issues with `make skillsaw-fix`.

### Work Item Types

RFE and Initiative are the two registered work item types. Each is described by a data-only descriptor, `types/<type>/type.yaml` (field reference in [types/README.md](types/README.md), provider guide in [docs/type-provider-guide.md](docs/type-provider-guide.md)), which `python3 scripts/validate_types.py` lints and `python3 scripts/type_registry.py` reads. Tests pin the descriptors to the per-type tables the scripts still carry; the scripts move to reading the registry in follow-up PRs.

See [AGENTS.md](AGENTS.md) for architecture details and conventions.

## CI / Headless Mode

All orchestrator skills support `--headless` for non-interactive use in CI pipelines. Combined with `--dry-run`, you can validate the full pipeline without Jira writes:

```bash
claude -p "/rfe-speedrun --headless --dry-run --input batch.yaml"
```

Add `--announce-complete` to print a `FULL RUN COMPLETE` marker when the pipeline finishes — useful for CI harnesses that need a reliable completion signal:

```bash
claude -p "/rfe-speedrun --headless --announce-complete --input batch.yaml"
```

Flag persistence: parsed arguments are written to `tmp/*.yaml` config files so they survive context compression during long batch runs.
