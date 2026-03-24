# RFE Creator

Claude Code skills for creating, reviewing, and submitting RFEs to the RHAIRFE Jira project.

Inspired by the [PRD/RFE workflow](https://github.com/ambient-code/workflows/tree/main/workflows/prd-rfe-workflow) in ambient, which established the pipeline pattern and multi-perspective review concept.

## Quick Start

```
# RFE Pipeline
/rfe.create     # Write a new RFE from a problem statement
/rfe.review     # Review, improve, and auto-revise RFEs
/rfe.submit     # Submit new or update existing RFEs in Jira
/rfe.speedrun   # Full pipeline end-to-end with minimal interaction

# Improve an existing Jira RFE
/rfe.review RHAIRFE-1234      # Fetch, review, and auto-revise
/rfe.speedrun RHAIRFE-1234    # Fetch, review, revise, and update in one step

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

### Strategy (after RFE approval)

```
/strat.create → /strat.refine → /strat.review → /strat.prioritize
```

## Pipeline Steps

1. **Create**: Describe your need. The skill asks clarifying questions and produces RFEs.
2. **Review**: Scores RFEs against the assess-rfe rubric, checks technical feasibility, and auto-revises issues. Accepts a Jira key to review existing RFEs.
3. **Submit**: Creates new RHAIRFE tickets or updates existing ones in Jira.
4. **Strat Create**: Clone approved RFEs to RHAISTRAT in Jira.
5. **Strat Refine**: Add the HOW — technical approach, dependencies, components, non-functionals.
6. **Strat Review**: Four independent forked reviewers (feasibility, testability, scope, architecture).
7. **Strat Prioritize**: Place new strategies in the existing backlog ordering.

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

## Jira MCP

If the Atlassian MCP server is configured, `/rfe.submit` creates or updates tickets directly. Without it, the skill produces a formatted submission guide for manual entry.
