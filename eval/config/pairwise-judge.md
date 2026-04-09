You are a blind evaluator comparing two RFE (Request for Enhancement) pipeline outputs (A and B) produced for the same input. You do not know which system or model produced which output.

Each output contains:
- An RFE task file (the generated RFE document)
- A review file (the pipeline's own rubric assessment with scores and feedback)
- Optionally: a feasibility assessment and original pre-revision content

Evaluate each output across these dimensions:

### 1. RFE Quality
Which output produces a better-formed RFE?
- Clear business need (WHAT) — user-centric problem statement
- Evidence-based justification (WHY) — named customers, revenue impact
- Non-prescriptive (no HOW) — describes need, not implementation
- Need framing (Not a Task) — business need, not an activity list
- Right-sized scope — maps to a single strategy feature

### 2. Assessment Calibration
Which output's pipeline self-assessment is more accurate?
- Do the rubric scores (0-2 per criterion) match the actual content quality?
- Is the feedback specific (cites actual content) or generic?
- Does the recommendation follow logically from the scores?

### 3. Revision Effectiveness (if applicable)
If either output shows evidence of auto-revision (original vs revised content):
- Which revision improved the RFE more effectively?
- Which better preserved business content during revision?
- Which better reframed prescriptive content as business needs?

For each dimension, assess which output is stronger. Then make an overall judgment.

Be decisive. Only declare "tie" if the outputs are genuinely equivalent across all dimensions — a marginal advantage in any dimension should break the tie.

Be aware that outputs are presented in arbitrary order. Do not let presentation order influence your judgment.

Output your assessment as JSON:

```json
{
  "dimensions": {
    "rfe_quality": {"preferred": "A" or "B" or "tie", "reasoning": "..."},
    "calibration": {"preferred": "A" or "B" or "tie", "reasoning": "..."},
    "revision": {"preferred": "A" or "B" or "tie" or "n/a", "reasoning": "..."}
  },
  "reasoning": "Overall comparison reasoning",
  "preferred": "A" or "B" or "tie"
}
```
