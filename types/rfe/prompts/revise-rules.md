# RFE revision rules

**Reframe, don't remove.** When the assessor flags HOW violations, the problem may not be the information — it's the framing. Prescriptive architecture and implementation directives can almost always be reframed into non-prescriptive context. For example, a section that assigns components to architectural roles can be reframed as a flat context list with a disclaimer that engineering should determine the design. Only remove content as a last resort when there is nothing reframeable.

**Critical distinction for HOW**: When the RFE is about integrating with or providing a specific vendor project, product, or API, naming that project/product is part of the WHAT (the business need), not the HOW. Do not generalize away named vendor solutions. Only reframe language that prescribes *internal implementation choices* (architecture patterns, specific K8s resources, build tooling, deployment ratios).

**Right-sizing is a recommendation, never auto-applied.** If right_sized scored 0 or 1, do NOT remove acceptance criteria or capabilities to force a different shape.

**Do not invent missing evidence.** If WHY is flagged for missing named customers, do not fabricate evidence — set `needs_attention=true` in Step 5 so the author is notified.

For each criterion the assessor flagged:
- **Open to HOW**: Reframe flagged sections to remove prescriptive framing while preserving useful context
- **WHY**: Strengthen with available evidence; if gaps remain, set `needs_attention=true` in Step 5 so the author is notified
- **Right-sized**: Report only — do not split or remove scope
- **WHAT / Not a task**: Follow assessor guidance if provided

## Content preservation classification

Classify each removed block's `type`:
- **`reworded`**: Same intent expressed differently. Exception: if original names specific vendor projects/APIs that were generalized away, classify as `genuine`.
- **`genuine`**: Implementation specifics useful as RHAISTRAT context (API names, architecture decisions, named vendor projects).
- **`non-substantive`**: Marketing filler or empty template placeholders.
