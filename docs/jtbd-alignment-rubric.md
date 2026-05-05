# JTBD Alignment Scoring Rubric

This rubric is used by the JTBD review agent to score how well an RFE is grounded in validated JTBD research data.

## Scoring Table

| Score | Label | Job Mapping | Evidence Utilization | Persona-Task Coherence | Opportunity Justification |
|-------|-------|-------------|---------------------|----------------------|--------------------------|
| **0** | No meaningful alignment | No mapping exists, OR mapping is clearly wrong (cited job is unrelated to the RFE's purpose) | No research evidence cited — WHY relies on assertion, anecdote, or assumed need | No persona identified, OR persona contradicts the JTBD mapping (e.g., AI engineer job but ops workflow) | No opportunity data cited, OR data actively contradicts stated priority (e.g., Critical on a job scoring <7) |
| **1** | Partial alignment | Maps to a plausible job but fit is loose — scope is tangential to the job's core definition, OR spans multiple jobs without acknowledging overlap | Some evidence cited but incomplete — opportunity score without pain points, OR quotes disconnected from argument, OR evidence used generically without tying to the specific capability | Persona identified and plausible but underdeveloped — named without explaining which workflow step is affected, OR targets a secondary persona rather than the job's primary user | Opportunity data referenced but connection to priority is implicit — reader must infer why the score justifies investment, OR moderate-opportunity job (8–10) cited without acknowledging it's not highest-need tier |
| **2** | Strong alignment | Maps to the correct job(s) — stated capability directly addresses the job statement; if multiple jobs cited, relationship is coherent | Evidence well-integrated — pain points, scores, and/or quotes cited in direct support of the need; makes a clear case for why *this specific capability* matters | Persona identified, job maps to a validated part of their workflow, capability fits naturally into how they work or explicitly addresses a documented workflow gap | Priority earned by data — high-opportunity (≥11) backs Critical/Major; moderate backs Normal with rationale; investment ask is proportional to research signal |

## Composite Scoring Rule

- Award a **0** if ANY of the 0-level criteria are true (a single fundamental failure in any dimension)
- Award a **2** only if ALL of the 2-level criteria are true (every dimension must be strong)
- Award a **1** in all other cases (directionally correct but gaps in one or more dimensions)
- Award **null** if the RFE is legitimately non-user-facing (pure infra, internal tooling, no external user)

## Interaction with Existing Review Scores

The JTBD alignment score is a **parallel signal** that does not affect the existing pass/fail determination:

```yaml
scores:
  what: 2          # existing rubric
  why: 2           # existing rubric
  open_to_how: 1   # existing rubric
  not_a_task: 2    # existing rubric
  right_sized: 2   # existing rubric
  total: 9         # pass/fail threshold: ≥7 with no zeros
  jtbd_alignment: 1  # parallel signal, non-blocking
```

## Review Output Format

The review file includes a JTBD Alignment section:

```markdown
## JTBD Alignment

**Score:** <0 | 1 | 2 | N/A>

| Dimension | Score | Finding |
|-----------|-------|---------|
| Job Mapping | <0-2> | <1 sentence> |
| Evidence Utilization | <0-2> | <1 sentence> |
| Persona-Task Coherence | <0-2> | <1 sentence> |
| Opportunity Justification | <0-2> | <1 sentence> |

**Matched JTBD:** <job name> (<job-id>, opportunity: <score>)
**Target persona:** <persona name(s)>

### Recommendations (if score < 2)
- <specific action to improve JTBD grounding>
```

## When JTBD Alignment Cannot Be Scored

Some RFEs legitimately have no JTBD mapping:
- Pure backend architecture or infrastructure work
- Internal tooling with no external user
- Capabilities outside the scope of the 18 validated jobs

In these cases, set `jtbd_alignment: null` with a note explaining why. This is distinct from a score of 0 (which indicates a user-facing RFE that failed to ground itself in research).
