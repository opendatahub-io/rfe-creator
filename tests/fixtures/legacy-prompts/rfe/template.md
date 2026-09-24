# RFE Template

Scale the output to match the size of the RFE. Use the appropriate section set below.

## Size Guide

- **S** (Small): A focused, well-understood need. 1-2 acceptance criteria. Use the Concise format.
- **M** (Medium): A clear need with some nuance. 3-5 acceptance criteria. Use the Standard format.
- **L** (Large): A significant need spanning multiple user scenarios. 5-8 acceptance criteria. Use the Full format.
- **XL** (Extra Large): A major initiative with broad impact. 8+ acceptance criteria. Use the Full format with all optional sections.

---

## Concise Format (S)

```markdown
## Summary
<2-3 sentences: what the user needs and why>

## Affected Customers
<Named customers, segments, or partners>

## Business Justification
<Why this matters: revenue, customer commitment, strategic investment, competitive need>

## Acceptance Criteria
- [ ] <User-perspective criterion>
- [ ] <User-perspective criterion>
```

---

## Standard Format (M)

```markdown
## Summary
<What the user needs and why, in enough detail to be unambiguous>

## Problem Statement
<What users cannot do today, or what is painful. Describe from the user's perspective.>

## Affected Customers
<Named customers, segments, or partners. Include scale/impact where known.>

## Business Justification
<Evidence-based: revenue impact, customer commitments, strategic investments, competitive positioning. Not assertions — data or named sources.>

## Acceptance Criteria
- [ ] <User-perspective criterion>
- [ ] <User-perspective criterion>
- [ ] <User-perspective criterion>

## Success Criteria
<How do we know the problem is solved? Measurable outcomes.>
```

---

## Full Format (L/XL)

```markdown
## Summary
<Comprehensive description of the business need>

## Problem Statement
<What users cannot do today, or what is painful. Include current workarounds if any.>

## Affected Customers
<Named customers, segments, or partners. Include:>
- Scale of impact (number of customers, revenue at risk)
- Specific customer requests or commitments if applicable

## Business Justification
<Evidence-based justification:>
- Revenue impact or opportunity
- Customer commitments or escalations
- Strategic investment alignment
- Competitive positioning
- Market data or analyst input

## User Scenarios
<Describe 2-3 concrete scenarios from the user's perspective:>
1. <Scenario: As a [role], I need to [action] because [reason]>
2. <Scenario>

## Acceptance Criteria
- [ ] <User-perspective criterion>
- [ ] <User-perspective criterion>
- [ ] <User-perspective criterion>
- [ ] <User-perspective criterion>
- [ ] <User-perspective criterion>

## Success Criteria
<Measurable outcomes that indicate the problem is solved>

## Scope
### In Scope
<What this RFE covers>

### Out of Scope
<What this RFE explicitly does NOT cover — to prevent scope creep>

## Open Questions
<Questions that need answers before this can proceed>
```
