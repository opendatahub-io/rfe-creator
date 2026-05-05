# JTBD-Informed Revision Extension

This document extends the existing `revise-agent.md` behavior. When the JTBD alignment score is 0 or 1, the revise agent uses this extension to strengthen the RFE's research grounding.

## When This Extension Activates

- `jtbd_alignment` score is 0 or 1
- `.context/jtbd-registry/index.yaml` exists (registry is available)
- The RFE is user-facing (score is not `null`)

## Registry Navigation (Progressive Disclosure)

Follow this sequence — same protocol as the JTBD review agent:

1. Read `.context/jtbd-registry/governance.yaml` — internalize constraints
2. Read `.context/jtbd-registry/index.yaml` — scan 18 jobs
3. Identify the job(s) flagged in the JTBD review findings
4. Read the full job file(s) for those jobs (max 3)

## Revision Actions by Dimension

### If Job Mapping scored 0:

- Scan the index for the correct job match based on the RFE's WHAT section
- If a match exists, update the `jtbd_mapping` frontmatter with the correct job ID
- If no match exists, note this explicitly — do NOT force a mapping

### If Job Mapping scored 1:

- If the mapping is tangential, check whether a more central job exists
- If the RFE spans multiple jobs, add acknowledgment of the overlap in the WHY section

### If Evidence Utilization scored 0:

- From the matched job file, extract the most relevant pain points
- Insert them into the WHY / Problem Statement section with proper citation format
- If user quotes are available, add 1–2 verbatim quotes as supporting evidence
- Add the opportunity score with importance/satisfaction breakdown

### If Evidence Utilization scored 1:

- Strengthen the connection between cited evidence and the specific capability
- If opportunity score is present but pain points are missing, add them
- If quotes are present but disconnected, reframe them in context of the argument

### If Persona-Task Coherence scored 0:

- From the matched job file, identify the primary persona
- Add persona context: which workflow step this capability affects
- If persona was contradictory to the job, correct the persona reference

### If Persona-Task Coherence scored 1:

- Add workflow context — which specific job step does this capability address?
- If targeting a secondary persona, acknowledge the primary persona as well

### If Opportunity Justification scored 0:

- Insert opportunity score from the matched job file
- If priority is Critical/Major but score is <7, flag the mismatch (do NOT auto-change priority)
- If score contradicts priority, add a note recommending priority re-evaluation

### If Opportunity Justification scored 1:

- Make the connection between opportunity score and priority explicit
- Add a sentence explaining why this investment level is justified given the data

## Constraints

- NEVER change the WHAT section based on JTBD data — the capability is the PM's decision
- NEVER fabricate evidence — only use data from files you actually read
- NEVER paraphrase user quotes — cite verbatim or don't cite at all
- NEVER auto-change priority level — recommend changes but leave the decision to the author
- ALWAYS maintain the existing tone and structure of the RFE — enhance, don't rewrite
- ALWAYS update `jtbd_mapping` frontmatter if you correct the job mapping
