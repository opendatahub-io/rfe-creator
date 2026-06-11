# JTBD Integration in RFE Creator

## Overview

The RFE Creator integrates with the Red Hat AI Jobs-to-be-Done (JTBD) knowledge registry to ground RFEs in validated user research. The integration uses progressive disclosure to minimize context window usage while providing full access to the research corpus.

## Architecture

```text
                    ┌─────────────────────────────────────┐
                    │          rfe.create skill            │
                    │                                     │
                    │  1. Bootstrap JTBD registry          │
                    │  2. Spawn JTBD agent                 │
                    │  3. Ask clarifying questions          │
                    │     (enriched with JTBD data)        │
                    │  4. Generate RFE                     │
                    │     (with JTBD evidence in WHY)      │
                    │  5. Write artifact + frontmatter     │
                    │     (jtbd_mapping field)             │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │        JTBD Agent (sub-agent)        │
                    │                                     │
                    │  Reads: governance.yaml (rules)      │
                    │  Reads: index.yaml (18 jobs, ~500t)  │
                    │  Matches: problem → up to 4 jobs   │
                    │  (ranked by alignment strength)    │
                    │  Reads: jobs/<id>.yaml (detail)      │
                    │  Returns: structured match data      │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │    .context/jtbd-registry/ (cloned)  │
                    │                                     │
                    │  index.yaml         (~500 tokens)    │
                    │  governance.yaml    (~200 tokens)    │
                    │  personas/*.yaml   (~300 tokens ea)  │
                    │  jobs/*.yaml       (~800-1500t ea)   │
                    └─────────────────────────────────────┘
```

## Progressive Disclosure

### What it means

Progressive disclosure is a pattern for managing context window cost when an agent needs access to a large knowledge base. Instead of loading the entire corpus (~15,000–25,000 tokens), the agent reads a small entry-point file first, determines what's relevant, and loads only the needed detail.

### How it works in this integration

| Layer | File(s) | Tokens | When read |
|-------|---------|--------|-----------|
| L0 | `index.yaml` | ~500 | Always — every RFE creation/review |
| L1 | `governance.yaml` | ~200 | Always — loaded before any data files |
| L2 | `personas/*.yaml` | ~300 each | Only when persona context helps disambiguate |
| L3 | `jobs/<id>.yaml` | ~800–1500 each | Only for matched jobs (up to 4 max) |

**Typical cost:** ~2,000–4,000 tokens per RFE (index + governance + 2–3 job files).
**Worst case:** ~7,500 tokens (index + governance + all 3 personas + 4 full job files).

### Why the agent reads index.yaml first

The index contains all 18 jobs with their opportunity scores, names, lifecycle phases, and file pointers. This gives the agent enough context to determine which jobs are relevant to the current RFE without reading any job detail. The matching happens against ~500 tokens of summary data; the agent then selects **up to 4 jobs ranked by alignment strength** (JTBD SMEs typically identify 4–5 per RFE) and drills into those job files for full detail.

This is enforced by the JTBD agent prompts, which explicitly order the navigation steps and prohibit reading all job files.

## Data Flow

### During `rfe.create`

1. `bootstrap-jtbd-registry.sh` clones/updates the registry to `.context/jtbd-registry/`
2. JTBD agent navigates the registry and returns structured match data
3. Clarifying questions reference JTBD pain points and personas
4. RFE WHY section cites opportunity scores, pain points, and user quotes
5. Frontmatter `jtbd_mapping` field records the mapping for downstream use

### During `rfe.review`

1. JTBD review agent validates existing mapping and scores alignment (0–2)
2. Scoring uses four dimensions: job mapping, evidence utilization, persona-task coherence, opportunity justification
3. If score < 2 and revision is triggered, revise agent consults registry to strengthen grounding
4. Composite score is recorded in review frontmatter as `scores.jtbd_alignment`

## JTBD Alignment Rubric

### Composite Score (0–2)

| Score | Label | Rule |
|-------|-------|------|
| **0** | No meaningful alignment | ANY dimension scores 0 |
| **1** | Partial alignment | All dimensions ≥1 but at least one is not 2 |
| **2** | Strong alignment | ALL dimensions score 2 |
| **null** | Not applicable | RFE is non-user-facing (infra, internal tooling) |

### Dimension Scoring

| Dimension | 0 | 1 | 2 |
|-----------|---|---|---|
| **Job Mapping** | No mapping or wrong mapping | Plausible but loose fit | Correct job, capability addresses job statement |
| **Evidence Utilization** | No evidence; WHY is assertion-only | Some evidence but incomplete or generic | Evidence well-integrated, directly supports the need |
| **Persona-Task Coherence** | No persona or contradicts mapping | Persona plausible but underdeveloped | Persona validated, maps to workflow, fits naturally |
| **Opportunity Justification** | No data or contradicts priority | Data referenced but implicit connection | Priority earned by data, proportional to signal |

### Interaction with existing review

The `jtbd_alignment` score is a **parallel signal** — it does NOT affect the existing pass/fail threshold (total ≥7, no zeros on what/why/open_to_how/not_a_task/right_sized). It is informational in the initial deployment.

## Bootstrap Script

`scripts/bootstrap-jtbd-registry.sh` follows the same pattern as `bootstrap-assess-rfe.sh`:

- Clones from the GitLab URL (configurable via `$JTBD_REGISTRY_URL` env var)
- Updates via `git pull --ff-only` if already cloned
- Validates required files exist (`index.yaml`, `governance.yaml`)
- Exits gracefully (exit 0) on failure — JTBD enrichment is never a hard blocker

## Schema

### `rfe-task` frontmatter — `jtbd_mapping` field

```yaml
jtbd_mapping:
  jobs:                                     # up to 4, ordered by alignment_rank
    - id: "prod.continuously_monitor"       # lifecycle.job_name format
      name: "Continuously Monitor Model Health"
      opportunity_score: 12.3
      lifecycle_phase: "production"
      alignment_rank: 1                     # 1 = strongest fit
      alignment_strength: "strong"          # strong | moderate | weak
    - id: "deploy.configure_serving"
      name: "Configure Model Serving"
      opportunity_score: 10.1
      lifecycle_phase: "deploy"
      alignment_rank: 2
      alignment_strength: "moderate"
  personas:
    - "maldi"
    - "alex"
  confidence: "high"                        # high | medium | low | none
```

### `rfe-review` frontmatter — `jtbd_alignment` score

```yaml
scores:
  what: 2
  why: 2
  open_to_how: 1
  not_a_task: 2
  right_sized: 2
  jtbd_alignment: 2                       # 0 | 1 | 2 | null
```

## Graceful Degradation

| Failure | Behavior |
|---------|----------|
| Bootstrap fails (network, auth) | RFE created normally without JTBD enrichment; `confidence: none` |
| No job match found | RFE created normally; `confidence: none` with explanatory note |
| Registry missing expected files | Agent reports absence; proceeds with available data |
| Job file missing user quotes | Cited as absent in output; no fabrication |

The RFE pipeline never blocks on JTBD availability.
