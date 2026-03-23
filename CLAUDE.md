# RFE Creator

Skills for creating, reviewing, and submitting RFEs to the RHAIRFE Jira project.

## Artifact Conventions

All skills read from and write to the `artifacts/` directory in the working directory.

```
artifacts/
  rfe-rubric.md             # Written by assess-rfe plugin (if installed) — rubric reference
  rfes.md                   # RFE master list — summary of all RFEs
  rfe-tasks/                # Individual RFE files
    RFE-001-*.md
    RFE-002-*.md
  rfe-review-report.md      # Review results (rubric scores + feasibility)
  jira-tickets.md           # Jira ticket mapping after submission
  strat-tickets.md          # RHAISTRAT ticket mapping after cloning
  strat-tasks/              # Individual strategy files, linked to source RFEs
    STRAT-001-*.md
    STRAT-002-*.md
  strat-review-report.md    # Strategy review results (4 forked reviewers)
  strat-prioritization.md   # Prioritization decisions and rationale
```

## Jira Field Mappings

### RHAIRFE Project
- **Project**: `RHAIRFE`
- **Issue Type**: `Feature Request`
- **Priority values** (use these exactly): Blocker, Critical, Major, Normal, Minor, Undefined
- **Status on creation**: `New`

### RHAISTRAT Project (for reference — used by strat skills)
- **Project**: `RHAISTRAT`
- **Issue Type**: `Feature`
- **Clone link type**: `Cloners` (outward: "clones", inward: "is cloned by")
- **Related link type**: `Related`
- **Informs link type**: `Informs`

## Architecture Context

`/rfe.review` automatically fetches architecture context from [opendatahub-io/architecture-context](https://github.com/opendatahub-io/architecture-context) into `.context/architecture-context/` and detects the latest RHOAI version. No manual setup needed.

Architecture context is used during:
- `/rfe.review` (technical feasibility fork)
- Strategy skills (Phase 2)

Architecture context is NOT used during `/rfe.create` — RFEs describe business needs, not implementation.
