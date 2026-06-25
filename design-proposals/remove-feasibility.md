# Proposal: Remove Feasibility from rfe-creator

## The Core Argument

The feasibility step in `rfe-creator` has a 0% `feasibility-fail` rate in production. The opus call is the most expensive stage of the pipeline, 50,000–80,000 token context read fires on every single RFE, and almost always returns "feasible".

RFEs describe WHAT and WHY — they purposefully leave the HOW to engineering. The infeasibility bar is "the platform's architecture conflicts with this need." At that level of abstraction, before anyone has proposed an implementation, nothing qualifies. The check is structurally set up to pass.

Feasibility is really important at the strategy stage, where there's a HOW to judge. `strat-creator`'s `strategy-feasibility-review` asks whether a specific implementation approach works, whether the effort estimate is credible, whether component choices hold up, and whether cross-team dependencies are accounted for. Those are questions an engineering plan can really fail on. That check is the same as this one, already exists, runs on opus, and reads the same architecture context.

## Cost

The feasibility step is the single most expensive operation in the pipeline:

- `model: opus` on every RFE (most expensive model)
- 50,000–80,000 tokens of architecture context reads per call
- Fires in parallel with assess on every RFE — including rough drafts that are about to fail rubric scoring and get revised anyway
- For a batch of 20 RFEs: 1–1.6 million tokens to produce a `feasibility-pass` label ~100% of the time

## The Recommendation

Remove feasibility from `rfe-creator`. It's the most expensive step in the pipeline and the least useful. The signal it produces is redundant with `strat-creator`'s feasibility check, which runs later, on a concrete plan, where the question actually means something.
