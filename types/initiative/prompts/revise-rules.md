# Initiative revision rules

- **WHAT**: Make the objective specific and concrete. Replace vague language with clear outcomes. A reader should understand exactly what will be delivered.
- **WHY**: Add evidence — metrics, incidents, cost data, competitive gaps. If you don't have specifics, flag the section with `[NEEDS: specific metrics/evidence]` for human follow-up.
- **Scope**: Clarify boundaries. If scope is fuzzy, add explicit inclusions and exclusions. Prose is fine — formal In/Out sections are optional.
- **Open to HOW**: When implementation details cross from describing deliverables into mandating architecture, reframe prescriptive content into suggestive context (see Implementation Detail Boundaries below).
- **Right-sized**: If the initiative bundles independent workstreams, flag for split recommendation rather than attempting to narrow scope in-place.

## Implementation Detail Boundaries

**Reframe, don't remove.** When implementation details cross from What into How, the problem is usually the framing, not the information. Reframe prescriptive architecture into suggestive context rather than deleting it. Only remove content as a last resort.

**What's acceptable:**
- Technologies dictated by integration context (e.g. "must support vLLM" when vLLM is the existing runtime)
- Technologies listed as suggestions or illustrations (e.g. "could use Redis or similar caching layer")
- Technical detail that describes the deliverable itself (observable behavior, APIs, interfaces)

**What crosses the line:**
- Prescribing technology choices as decisions when alternatives exist (e.g. "implement using Redis" when the choice is open)
- Mandating internal architecture, repo structure, or algorithmic design
- Linking design docs as "the solution"

When reframing, add suggestive language ("such as", "could leverage", "one approach would be") rather than removing the technology reference entirely.

**Do not invent missing evidence.** If Problem Statement is flagged for missing data, do not fabricate evidence — set `needs_attention=true` in Step 5 so the author is notified.

## Content preservation classification

Classify each removed block's `type`:
- **`reworded`**: Same intent expressed differently. Exception: if original names specific technologies that were generalized away, classify as `genuine`.
- **`genuine`**: Implementation specifics useful as strategy context (architecture decisions, technology choices, design rationale).
- **`non-substantive`**: Marketing filler or empty template placeholders.
