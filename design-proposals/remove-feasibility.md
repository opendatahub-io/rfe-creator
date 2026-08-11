# Proposal: Remove Feasibility from rfe-creator

## Argument

The feasibility step in `rfe-creator` has a 0% `feasibility-fail` rate in production. The opus call is the most expensive stage of the pipeline, 50,000–80,000 token context read fires on every single RFE, and almost always returns "feasible".

RFEs describe WHAT and WHY — they purposefully leave the HOW to engineering. Since nobody has proposed a solution, the abstraction makes the bar at this level too high. The check is set up to pass.

The intent behind the check is right: catching infeasible ideas early prevents backtracking later. But the check's own definition makes that nearly impossible at the RFE stage. According to the prompts, something is only "infeasible" if the platform's architecture fundamentally conflicts with the need — not if it's ambitious, vague, or hard to build. Since RFEs don't propose an implementation, there's almost nothing concrete enough to rule out. The gate is important later stage where there's an actual plan to evaluate.

Feasibility is really important at the strategy stage, where there's a HOW to judge. `strat-creator`'s `strategy-feasibility-review` asks whether a specific implementation works, whether the effort estimate is credible, whether component choices hold up, and whether cross-team dependencies are accounted for. Those are questions an engineering plan can really fail on. That check is the same as the RFE one. It already exists, runs on opus, and reads the same architecture context.

## Cost

The feasibility step is the single most expensive operation in the rfe pipeline:

- `model: opus` on every RFE (most expensive model)
- 50,000–80,000 tokens of architecture context reads per call
- Fires in parallel with assess on every RFE — including rough drafts that are about to fail rubric scoring and get revised anyway
- For a batch of 20 RFEs: 1–1.6 million tokens to produce a `feasibility-pass` label ~100% of the time

At current opus pricing ($5.00/MTok input, $25.00/MTok output), the cost per RFE is roughly:

```
cost_per_rfe ≈ (80,000 × $5 + 750 × $25) / 1,000,000 ≈ $0.42
total_feasibility_cost = $0.42 × N
```

For 100 RFEs — tests, drafts, or real ones — that's ~$42.00 to produce "feasible" every time, including on rough drafts that haven't passed rubric scoring yet.

## Speed

Assess and feasibility run in parallel, but the review agents in Step 3 are blocked until both complete. Removing feasibility means review agents start as soon as assess finishes, cutting the extra wait.

## Recommendation

Remove feasibility from `rfe-creator`. It's the most expensive step in the pipeline and the least useful. The signal it produces is redundant with `strat-creator`'s feasibility check, which runs later, on a concrete plan, where the question matters more.