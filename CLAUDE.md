# RFE Creator

Skills for creating, reviewing, and submitting RFEs to the RHAIRFE Jira project.

## Artifact Conventions

All skills read from and write to the `artifacts/` directory in the working directory.

```
artifacts/
  rfe-rubric.md             # Written by assess-rfe plugin (if installed) — rubric reference
  rfes.md                   # Generated index — rebuilt from frontmatter, not a source of truth

  rfe-tasks/                # Individual RFE files with YAML frontmatter
    RHAIRFE-1595.md          # Existing Jira issue (keyed by Jira key)
    RHAIRFE-1595-comments.md # Companion: stakeholder comment history
    RHAIRFE-1595-removed-context.yaml  # Companion: structured removed content with type classification
    RHAIRFE-1595-removed-context.md  # Legacy companion (markdown, being phased out)
    RFE-001-slug.md          # New RFE (pre-submission, renamed on submit)

  rfe-originals/            # Raw Jira descriptions at time of fetch (not templated)
    RHAIRFE-1595.md          # Baseline for before/after analysis and submit-time conflict detection

  rfe-reviews/              # Per-issue review files with YAML frontmatter
    RHAIRFE-1595-review.md
    RFE-001-slug-review.md

  strat-tasks/              # Individual strategy files with YAML frontmatter
    RHAISTRAT-400.md
  strat-reviews/            # Per-strategy review files with YAML frontmatter
    RHAISTRAT-400-review.md

  strat-tickets.md          # RHAISTRAT ticket mapping after cloning
  strat-prioritization.md   # Prioritization decisions and rationale
```

### Frontmatter

All task and review files use YAML frontmatter for structured metadata. Skills must use `scripts/frontmatter.py` to read schemas, set fields, and read validated data — never write YAML by hand.

```bash
# Get schema for a file type
python3 scripts/frontmatter.py schema rfe-task
python3 scripts/frontmatter.py schema rfe-review
python3 scripts/frontmatter.py schema strat-task
python3 scripts/frontmatter.py schema strat-review

# Set/update frontmatter on a file
python3 scripts/frontmatter.py set <path> field=value field=value ...

# Read validated frontmatter as JSON
python3 scripts/frontmatter.py read <path>

# Rebuild rfes.md index from all frontmatter
python3 scripts/frontmatter.py rebuild-index
```

### File Naming

- **Existing Jira issues**: Use Jira key as filename and `rfe_id` (e.g., `RHAIRFE-1595.md` with `rfe_id: RHAIRFE-1595`)
- **New RFEs (pre-submission)**: Use `RFE-NNN-slug.md` naming with `rfe_id: RFE-NNN`
- **On submit**: `RFE-NNN-slug.md` files are renamed to `RHAIRFE-NNNN.md`, and `rfe_id` is updated to the Jira key
- **Companion files**: Same prefix as main file with `-comments.md` or `-removed-context.md` suffix
- **Archived RFEs**: Set `status: Archived` in frontmatter (no filename changes)

## Jira Integration

### Write Operations (submit, update, comment)

All write operations use the Jira REST API directly via Python scripts (`scripts/submit.py`, `scripts/split_submit.py`). This ensures the exact sequence of Jira API calls is deterministic and not dependent on LLM tool-calling decisions — critical for operations like split submissions that require multi-step transactional workflows (archive, create, link, close).

Required environment variables:

```
JIRA_SERVER=https://your-site.atlassian.net
JIRA_USER=your-email@example.com
JIRA_TOKEN=your-api-token
```

To create an API token: https://id.atlassian.com/manage-profile/security/api-tokens

### Read Operations (fetch issues, comments)

Read operations support two modes:

1. **Atlassian MCP server** (preferred when available) — used by `/rfe.review`, `/rfe.split`, and `/strat.create` when fetching issues from Jira
2. **REST API fallback** — if the MCP server is unavailable, skills fall back to `python3 scripts/fetch_issue.py` using the same `JIRA_SERVER`/`JIRA_USER`/`JIRA_TOKEN` env vars

Skills that only work with local artifacts (`/rfe.create`) do not require Jira access.

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
