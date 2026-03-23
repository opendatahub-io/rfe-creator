# RFE Creator

Claude Code skills for creating, reviewing, and submitting RFEs to the RHAIRFE Jira project.

Inspired by the [PRD/RFE workflow](https://github.com/ambient-code/workflows/tree/main/workflows/prd-rfe-workflow) in ambient, which established the pipeline pattern and multi-perspective review concept.

## Quick Start

```
# RFE Pipeline
/rfe.create     # Create RFEs from a problem statement
/rfe.review     # Validate RFEs (rubric + technical feasibility)
/rfe.submit     # Submit to Jira as RHAIRFEs
/rfe.speedrun   # Run the full pipeline with minimal interaction

# Strategy Pipeline (after RFE approval)
/strat.create      # Clone approved RFEs to RHAISTRAT in Jira
/strat.refine      # Feature refinement — the HOW
/strat.review      # Adversarial review (4 independent reviewers)
/strat.prioritize  # Place in existing backlog
```

## Pipeline

```
/rfe.create → /rfe.review → /rfe.submit
```

After technical leadership approves RFEs:

```
/strat.create → /strat.refine → /strat.review → /strat.prioritize
```

1. **Create**: Describe your need. The skill asks clarifying questions and produces RFEs.
2. **Review**: Validates RFEs against the assess-rfe rubric (if plugin installed) and checks technical feasibility.
3. **Submit**: Creates RHAIRFE tickets in Jira with correct field values.
4. **Strat Create**: Clone approved RFEs to RHAISTRAT in Jira.
5. **Strat Refine**: Add the HOW — technical approach, dependencies, components, non-functionals.
6. **Strat Review**: Four independent forked reviewers (feasibility, testability, scope, architecture).
7. **Strat Prioritize**: Place new strategies in the existing backlog ordering.

`/rfe.speedrun` runs the RFE pipeline (1-3) with reasonable defaults and auto-revision.

## Editing Between Steps

All artifacts are written to `artifacts/`. You can edit any file between steps:

- Edit an RFE in `artifacts/rfe-tasks/RFE-001-*.md`, then re-run `/rfe.review`
- Re-run `/rfe.create` to start over from scratch

## assess-rfe Plugin Integration

This skill optionally integrates with the [assess-rfe plugin](https://github.com/n1hility/assess-rfe):

- **During creation**: If `artifacts/rfe-rubric.md` exists (written by the plugin), the skill uses it to guide clarifying questions.
- **During review**: If the plugin is installed, `/rfe.review` invokes it for rubric scoring.
- **Without the plugin**: The skill still works — creation uses built-in questions, review runs only the technical feasibility check.

## Architecture Context

For RHOAI work, the technical feasibility review can use architecture context from [opendatahub-io/architecture-context](https://github.com/opendatahub-io/architecture-context). See `CLAUDE.md` for configuration.

## Jira MCP

If the Atlassian MCP server is configured, `/rfe.submit` creates tickets directly. Without it, the skill produces a formatted submission guide for manual entry.
