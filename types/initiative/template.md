# Initiative Template

Initiatives describe outcomes — what a team will deliver and why it matters. They sit between strategic Outcomes and tactical Epics in the Jira hierarchy.

Unlike RFEs (which describe business needs from the user's perspective), initiatives describe **team commitments** — scoped bodies of work with clear objectives and evidence for why they matter.

Real initiative descriptions are typically prose. Formal sections are optional — what matters is that the content is clear, not that it follows a specific format.

---

```markdown
## Objective
<What this initiative delivers and why it matters. Be specific about what changes for the team, product, or platform. A reader should understand exactly what they would be delivering.>

## Problem Statement
<What is broken, missing, or insufficient today? Include concrete evidence: metrics, incidents, cost data, competitive gaps. Generic assertions without data are weaker than specific evidence.>

## Scope
<What is included in this initiative. Optionally, what is explicitly NOT included — this prevents scope creep. Formal In Scope / Out of Scope sections are helpful but not required; prose that makes boundaries clear works too.>
```

### Guidance

- **Describe outcomes, not architecture.** Name technologies when they are integration context (the deliverable IS wiring in that technology) or when suggesting candidates for the team to evaluate. Avoid mandating specific technology choices when alternatives exist.
- **Keep it focused.** An initiative should be a single coherent effort. If workstreams could be planned and executed by different teams on different timelines, they should be separate initiatives.
- **Prose is fine.** Teams naturally write initiatives as narrative — that is expected. Score quality comes from content clarity, not formatting.
