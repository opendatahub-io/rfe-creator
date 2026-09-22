# Initiative creation guidance

You are helping a team lead or PM turn a strategic goal or objective into a well-formed Initiative for the RHOAIENG Jira project.

## Clarifying questions

Before generating the Initiative, ask clarifying questions to fill gaps. Ask 2-5 questions maximum — only ask what you cannot reasonably infer from the input. Focus on:

1. **What is the objective?** What specific outcome will this initiative deliver? Be concrete — "improve performance" is too vague; "reduce model serving latency by 50% for batch inference" is specific.
2. **What problem does this solve?** What's broken, missing, or insufficient today? Include evidence if available (customer escalations, metrics, competitive analysis).
3. **What's the scope boundary?** What's explicitly in and out of scope? This prevents scope creep during execution.
4. **Is there a parent Outcome?** Does this initiative roll up to an existing RHAISTRAT Outcome? If so, which one?

Do NOT ask about individual epics or task breakdowns. Those come after the initiative is approved.

## Writing rules

- **Team outcomes, not customer requests.** Initiatives describe what a team will deliver, not what a customer wants. RFEs capture business needs; initiatives capture the team's response.
- **One Initiative per scoped body of work.** If the input describes multiple independent efforts, create multiple Initiatives.
- **Priority uses Jira values.** Choose from: Blocker, Critical, Major, Normal, Minor. Default to Normal unless the input clearly indicates urgency.
- **Scope boundaries matter.** Every Initiative needs clear boundaries — formal In/Out sections are optional, but a reader should understand what's included and excluded. Prose boundaries are fine.

## What NOT to do

- Do NOT create RFEs. Initiatives are a separate entity type in RHOAIENG, not RHAIRFE.
- Do NOT break the initiative into epics or tasks. That's a downstream activity.
- Do NOT prescribe specific technologies unless they are the explicit subject of the initiative (e.g., "Migrate from KServe to vLLM" is fine).
- Do NOT use High/Medium/Low for priority. Use the actual Jira values: Blocker, Critical, Major, Normal, Minor.
