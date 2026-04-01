# RFE Creator

Claude Code skills for creating, reviewing, and submitting RFEs to the RHAIRFE Jira project.

Inspired by the [PRD/RFE workflow](https://github.com/ambient-code/workflows/tree/main/workflows/prd-rfe-workflow) in ambient, which established the pipeline pattern and multi-perspective review concept.

## Quick Start

```
# RFE Pipeline
/rfe.create     # Write a new RFE from a problem statement
/rfe.review     # Review, improve, and auto-revise RFEs
/rfe.split      # Split an oversized RFE into right-sized pieces
/rfe.submit     # Submit new or update existing RFEs in Jira
/rfe.speedrun   # Full pipeline end-to-end with minimal interaction
/rfe.auto-fix   # Batch review+revise+split pipeline (non-interactive)

# Improve an existing Jira RFE
/rfe.review RHAIRFE-1234      # Fetch, review, and auto-revise
/rfe.split RHAIRFE-1234       # Fetch and split an oversized RFE
/rfe.speedrun RHAIRFE-1234    # Fetch, review, revise, and update in one step

# Batch operations
/rfe.speedrun --input batch.yaml --headless --dry-run              # Batch create + review from YAML
/rfe.speedrun --input batch.yaml --headless --announce-complete    # Print completion marker for CI
/rfe.auto-fix --jql "project = RHAIRFE AND ..."         # Batch review from JQL query
/rfe.auto-fix RHAIRFE-1234 RHAIRFE-5678                 # Batch review explicit IDs

# Strategy Pipeline (after RFE approval)
/strat.create      # Clone approved RFEs to RHAISTRAT in Jira
/strat.refine      # Feature refinement — the HOW
/strat.review      # Adversarial review (4 independent reviewers)
/strat.prioritize  # Place in existing backlog

# Maintenance
/rfe-creator.update-deps   # Force update vendored dependencies
```

## Pipeline

### New RFEs

```
/rfe.create → /rfe.review → /rfe.submit
```

`/rfe.review` auto-revises issues it finds (up to 2 cycles). You can also edit artifacts manually between steps.

`/rfe.speedrun` runs the full pipeline with reasonable defaults and minimal interaction.

### Existing Jira RFEs

```
/rfe.review RHAIRFE-1234 → /rfe.submit
```

Or in one step: `/rfe.speedrun RHAIRFE-1234`

### Batch Operations

Create and review multiple RFEs from a YAML file:

```
/rfe.speedrun --headless --dry-run --input batch.yaml
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
/rfe.auto-fix --jql "project = RHAIRFE AND status = New" --limit 20
/rfe.auto-fix RHAIRFE-1234 RHAIRFE-5678 RHAIRFE-9012
```

Auto-fix processes in batches (default 5), handles review, revision, splitting, retry, and report generation.

### Strategy (after RFE approval)

```
/strat.create → /strat.refine → /strat.review → /strat.prioritize
```

## Pipeline Steps

1. **Create**: Describe your need. The skill asks clarifying questions and produces RFEs. Supports `--headless` to skip questions (for batch/CI use).
2. **Review**: Scores RFEs against the assess-rfe rubric, checks technical feasibility, and auto-revises issues. Accepts Jira keys to review existing RFEs. Supports `--headless` for non-interactive use.
3. **Split**: Decompose an oversized RFE into right-sized pieces. Runs review on new RFEs, self-corrects right-sizing (up to 3 cycles), and checks scope coverage. Supports `--headless`.
4. **Auto-fix**: Batch pipeline that orchestrates review + revision + split + retry across many RFEs. Accepts explicit IDs or a `--jql` query. Processes in configurable batches (`--batch-size N`, default 5). Generates run reports and HTML review reports.
5. **Submit**: Creates new RHAIRFE tickets or updates existing ones in Jira. Supports `--dry-run` to validate without writing to Jira.
6. **Speedrun**: End-to-end pipeline (create → auto-fix → submit). Supports `--input <yaml>` for batch creation, `--headless` for CI, `--announce-complete` for completion signaling, `--dry-run` to skip Jira writes, and `--batch-size N`.
7. **Strat Create**: Clone approved RFEs to RHAISTRAT in Jira.
8. **Strat Refine**: Add the HOW — technical approach, dependencies, components, non-functionals.
9. **Strat Review**: Four independent forked reviewers (feasibility, testability, scope, architecture).
10. **Strat Prioritize**: Place new strategies in the existing backlog ordering.

## Editing Between Steps

All artifacts are written to `artifacts/`. You can edit any file between steps:

- Edit an RFE in `artifacts/rfe-tasks/RFE-001-*.md`, then re-run `/rfe.review`
- Re-run `/rfe.create` to start over from scratch

## assess-rfe Integration

Skills automatically bootstrap the [assess-rfe](https://github.com/n1hility/assess-rfe) plugin from GitHub on first use:

- **During creation**: The rubric is exported to `artifacts/rfe-rubric.md` and used to guide clarifying questions.
- **During review**: `/rfe.review` invokes assess-rfe for rubric scoring.
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

## CI / Headless Mode

All orchestrator skills support `--headless` for non-interactive use in CI pipelines. Combined with `--dry-run`, you can validate the full pipeline without Jira writes:

```bash
claude -p "/rfe.speedrun --headless --dry-run --input batch.yaml"
```

Add `--announce-complete` to print a `FULL RUN COMPLETE` marker when the pipeline finishes — useful for CI harnesses that need a reliable completion signal:

```bash
claude -p "/rfe.speedrun --headless --announce-complete --input batch.yaml"
```

Flag persistence: parsed arguments are written to `tmp/*.yaml` config files so they survive context compression during long batch runs.
