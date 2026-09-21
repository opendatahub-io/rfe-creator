# Evaluation — rfe.speedrun

Automated evaluation of the `rfe.speedrun` pipeline using the [agent-eval-harness](https://github.com/opendatahub-io/agent-eval-harness).

## Quick Start

### Setup

Slash commands below (`/eval-setup`, `/eval-run`, etc.) require the [agent-eval-harness](https://github.com/opendatahub-io/agent-eval-harness) to be installed.

```bash
# Install dependencies and configure environment
/eval-setup
```

### Run an evaluation

```bash
# Run with Opus
/eval-run --model opus

# Run with Sonnet (subagents also on Sonnet)
/eval-run --model sonnet --subagent-model sonnet

# Compare against a previous run
/eval-run --model opus --baseline <previous-run-id>

# Run a subset of cases for faster iteration
/eval-run --model opus --case autoscaling
```

### Review results

Each run produces:
- `eval/runs/<run-id>/report.html` — HTML report with scoring summary, per-case details, and baseline diff
- `eval/runs/<run-id>/summary.yaml` — machine-readable scores
- `eval/runs/<run-id>/stdout.log` — full execution trace

## How it works

The evaluation runs the `rfe.speedrun` skill headlessly against 26 test cases: 20 derived from real RHAIRFE Jira issues plus 6 deliberately weak drafts (see [Weak-draft cases](#weak-draft-cases)). Each test case provides a problem statement (prompt + clarifying context), and the pipeline creates, reviews, auto-fixes, and (dry-run) submits RFEs.

### How it was generated

`/eval-analyze --skill rfe.speedrun` recursively read the skill chain (rfe.speedrun -> rfe.create, rfe.auto-fix, rfe.review, rfe.split, rfe.submit, assess-rfe) and generated `eval.yaml` with dataset schema, output descriptions, and suggested judges. The configuration and judges were then iteratively refined through multiple eval runs across Opus and Sonnet.

### Configuration

- **`eval.yaml`** — defines the skill, dataset, outputs, judges, and thresholds. **Generated** —
  do not edit it by hand: `python3 scripts/generate_eval_config.py` renders it from
  `eval/config/skeleton.yaml` (the shared structure, every check and the shared judge prose),
  `types/rfe/eval/fragment.yaml` (the RFE-specific prose) and `types/rfe/type.yaml` (identity,
  directories, score fields and the authoritative thresholds). Change those and regenerate;
  `--check` (part of `make lint` and CI) fails with the diff when the committed file is stale.
- **`eval.md`** — cached skill analysis (auto-generated, tracks SKILL.md hash for freshness).
- **`types/rfe/eval/pairwise-judge.md`** — prompt for blind A/B comparison across runs.

### Dataset

`eval/dataset/cases/` contains 25 test cases, each with:

| File | Purpose |
|------|---------|
| `input.yaml` | Skill input: `prompt`, `priority`, `clarifying_context` |
| `annotations.yaml` | Expected scores, feasibility/recommendation expectations, test tags |

Input files contain only the fields the skill needs in batch Mode A — no Jira keys or existing RFE content.

#### Weak-draft cases

Cases `case-021` through `case-026` are deliberately weak drafts (`difficulty: hard`) that the pipeline is expected to auto-revise. Their `annotations.yaml` carries `tags: [weak-draft, revision-expected, <mode>]`, where `<mode>` names the flaw the draft carries. Only `revision-expected` has judge semantics: the `revision_coverage` check fails a case tagged with it unless its RFE shows revision evidence. Any one of eight signals counts:

1. `auto_revised: true` in the review frontmatter (or the legacy `revised: true`)
2. `before_score != score`
3. `before_scores != scores` (per-criterion breakdown moved)
4. an `rfe-originals/` body that differs from the current task body (whitespace-normalised)
5. a removed-context companion (`{id}-removed-context.yaml` or `.md`)
6. a leftover `{id}-review-state.json` (a re-review cycle ran)
7. the shared run report recording a revision for the item (`revision_cycles > 0`, `auto_revised: true`, or `before_score != after_score`)
8. a non-empty Revision History section (anything other than the template's `none` placeholder or a decorated variant of it such as "None (first pass)." or "No revisions yet")

Without these cases every draft passed first review at 8-10, so the revise/reassess path was never exercised: `revision_quality` scored the decision not to revise 4-5 and `revision_flag_consistency` passed vacuously.

The `revision_coverage` threshold (`min_pass_rate: 0.92`) tolerates 2 failing cases out of 26: 4 of 6 weak drafts revised leaves 2 failing cases, 24/26 = 0.923 (pass); 3 of 6 leaves 3, 23/26 = 0.885 (fail). That 2-failure slack is shared by every way a case can fail this check:

- a weak draft the create step repairs on its own (passes first review, no revision);
- a weak draft the first review **rejects or splits** instead of revising. `scripts/filter_for_revision.py` never routes `reject`, `autorevise_reject` or `split` recommendations into the revise path, so a correct reject or split leaves no revision evidence; split children are not routed to any case directory in batch mode and count only towards run-level coverage via the report;
- any case without a review file (`No review files to check`).

The 0.92 value was calibrated on two live 5-case runs on 2026-09-07 (claude-opus-4-6, `--dry-run`). The first authoring of the five drafts, which relied on rewording weaknesses (a mandated design, a chore framing, a vague ask, an internal requester), was repaired by the create step in 4 of 5 cases (first-pass scores 8-9, only the missing-WHY draft was revised). The second authoring added an in-character evidence anchor to each draft and 5 of 5 were revised (first-pass 5-8, every one failing on `why: 0`, three also on `not_a_task`, `what` or `right_sized`), all five passed re-review, and `revision_coverage` scored 1.0. Re-tune only if a future run shows a different distribution.

**Authoring a weak draft that survives the create step.** The creator and the reviser share a model and a rubric, so any weakness that can be fixed by rewording is fixed at create time and the draft passes first review. The only weakness that survives is one that needs information the input does not contain, stated plainly in the requester's voice: no customer has asked, no support case exists, no internal team should be listed as an affected customer, and the customer and business sections should stay honest rather than padded with generic segments. The assessor awards `why: 1` for any generic or internal segment, so the anchor must close every such route; it awards `why: 0` only when the draft names no beneficiaries and no justification at all.

**Second-revision cases.** `case-026` (RFE) and initiative `case-021` also carry the tag `second-revision-expected`: each pairs an unfixable WHY (no business evidence anywhere, with an in-character instruction not to pad) with one fixable zero (a checklist framing, `not_a_task`; a dictated vendor stack, `open_to_how`). The first revision fixes the fixable zero and moves the score, the re-review still fails on WHY, and reassess cycle 2 launches the second revise agent, which changes nothing and leaves the item `pass: false`, `recommendation: revise`, `needs_attention: true` with a WHY reason. This is the path #194 (AISDLC-45) restored and #192 (AISDLC-33) flags; no other dataset case stays failing after a revision, so without these two it never runs in an eval. No judge keys on the tag: `revision_coverage` sees ordinary revision evidence, `revision_flag_consistency` checks the flag against it, and `recommendation_consistency` checks the failing end state. The second launch itself is visible only in the run's transcript (a second revise agent for the item) and in the Revision History, because `generate_run_report.py` records `revision_cycles: 1` for any revised item; a deterministic judge over the history's entry count was considered and rejected as fragile (agents write one bullet per criterion, not per cycle).

### Judges

| Judge | Type | What it checks |
|-------|------|----------------|
| `files_exist` | check | Task + review files produced |
| `frontmatter_valid` | check | YAML schema, score ranges, pass logic consistency |
| `run_report_exists` | check | Auto-fix YAML run report with required fields |
| `recommendation_consistency` | check | pass/fail aligns with recommendation, infeasible != submit |
| `revision_flag_consistency` | check | `auto_revised` agrees with revision evidence (state file, history, moved score, removed-context) |
| `revision_coverage` | check | Revise path exercised: `revision-expected` cases were revised; every case fails when a multi-item run revised nothing (single-item runs: tag alone decides) |
| `pipeline_flow` | check | All three phases (create, auto-fix, submit) detected in stdout, no fatal tracebacks, no Phase 1 deletions |
| `architecture_context_used` | check | Every feasibility file's writer transcript read `.context/architecture-context`; the prose fallback applies only to runs without transcript capture |
| `rfe_quality` | LLM | RFE quality (WHAT/WHY/HOW/task/scope) + calibration accuracy |
| `revision_quality` | LLM | Revision improvement + content preservation |
| `pairwise` | LLM | Blind A/B comparison (only with `--baseline`) |

### Tool interception

During evaluation, PreToolUse hooks:
- **Auto-answer** `AskUserQuestion` prompts from test case context
- **Block Jira** interactions (the skill runs with `--dry-run`)

---

# Evaluation — initiative-speedrun

Automated evaluation of the `initiative-speedrun` pipeline using the [agent-eval-harness](https://github.com/opendatahub-io/agent-eval-harness).

## Quick Start

```bash
# Run with Opus
/eval-run --config eval-initiative.yaml --model opus

# Compare against a previous run
/eval-run --config eval-initiative.yaml --model opus --baseline <previous-run-id>
```

## How it works

The evaluation runs the `initiative-speedrun` skill headlessly against 21 test cases: 16 derived from real RHOAIENG Jira initiatives (two of them, `case-007` and `case-013`, retargeted into weak drafts) and five deliberately weak drafts. Each test case provides an objective (prompt + clarifying context), and the pipeline creates, reviews (with assessment, feasibility, and strategic alignment), auto-fixes, and (dry-run) submits Initiatives.

### Configuration

- **`eval-initiative.yaml`** — defines the skill, dataset, outputs, judges, and thresholds.
  **Generated** from the same `eval/config/skeleton.yaml` as `eval.yaml`, with
  `types/initiative/eval/fragment.yaml` and `types/initiative/type.yaml`; the two configs can
  no longer drift apart silently (see the RFE section above for how to change and regenerate).
- **`types/initiative/eval/pairwise-judge.md`** — prompt for blind A/B comparison across runs.

### Dataset

`eval/initiative-dataset/cases/` contains 20 test cases, each with:

| File | Purpose |
|------|---------|
| `input.yaml` | Skill input: `prompt`, `priority`, `clarifying_context` |
| `annotations.yaml` | Expected scores, feasibility/recommendation/alignment expectations, test tags |

One case (`case-012`, tagged `sparse-input`) provides minimal context to test sparse-input handling.

#### Weak-draft cases

Cases `case-007`, `case-013` and `case-017` through `case-021` are deliberately weak drafts (`difficulty: hard`) that the pipeline is expected to auto-revise, the initiative counterpart of the RFE cases `case-021` to `case-026` above. Their `annotations.yaml` carries `tags: [weak-draft, revision-expected, <mode>...]`, where `<mode>` names the flaw: `why-missing` (the requester insists the only problem statement is "the platform does not have it" — the rubric's circular-justification 0), `prescriptive-how` (technology choices dictated as closed decisions, with an instruction not to soften them — the rubric's mandate 0) and `scope-missing` (no boundaries at all). Each flaw targets a criterion the rubric scores 0, protected by an in-character "do not add / do not soften" anchor, because the first calibration run (2026-09-17) showed that anything softer is repaired at create time: an honest "we have no data but suspect" earned WHY 1, an enumerated list of open scope questions earned Scope 1, and the drafts passed at 9; only the case with neither evidence nor boundaries failed first review. Target at most two zeros per case: the review contract routes three or more zeros to `reject`, which never enters the revise path (case-019 frames the work as implementation steps to keep WHAT thin, but its targeted zero is Open to HOW alone; case-020 was re-anchored on a firm deliverable after a run scored it WHAT, WHY and Scope at 0 and rejected it). `case-007` and `case-013` were recalibrated the same way (#155, prescriptive off-allowlist stack; negated WHY); the 2026-09-18 calibration run (#193) confirmed all six fail first review on the targeted criterion and get revised, so all six carry `revision-expected` (at 0.92 over 20 cases one may still go unrevised). The second calibration run confirmed all four: first reviews of 8, 8, 7 and 6 on the targeted criterion, all four revised, coverage 1.0. `expected_pass: false` and `expected_recommendation: revise` describe the first review. The revise agent then either adds evidence or, when there is none to add, flags the section with `[NEEDS: ...]` and sets `needs_attention`; the judge looks at neither marker, only at the revision itself — the same eight evidence signals as for RFEs (`auto_revised: true`, a moved score, a body that differs from the original, a companion artifact, a run-report revision, a non-empty Revision History). A flagged section is a body change, so an honest "no evidence" revision still counts.

Without these cases the initiative gate rested on the run-level rule alone, and passed only because `case-012` happened to be revised while the pipeline dropped the requester context; once #188 forwarded that context, a full run revised nothing and `revision_coverage` scored 0.0.

### Judges

| Judge | Type | What it checks |
|-------|------|----------------|
| `files_exist` | check | Task + review files produced |
| `frontmatter_valid` | check | YAML schema, score ranges, alignment enum, pass logic consistency |
| `run_report_exists` | check | Initiative run report YAML with required fields |
| `recommendation_consistency` | check | pass/fail aligns with recommendation, infeasible != submit, weak alignment sets needs_attention |
| `revision_flag_consistency` | check | `auto_revised` agrees with revision evidence (state file, history, moved score, removed-context) |
| `revision_coverage` | check | Revise path exercised (21 cases, 7 tagged weak drafts; 0.92 allows one failing case): untagged-but-revised cases count; every case fails when a multi-item run revised nothing (single-item runs: tag alone decides) |
| `pipeline_flow` | check | All three phases (create, auto-fix, submit) detected in stdout, no fatal tracebacks, no Phase 1 deletions |
| `architecture_context_used` | check | Every feasibility file's writer transcript read `.context/architecture-context` (or the review declares the context not relevant); the prose fallback applies only to runs without transcript capture |
| `initiative_quality` | LLM | Initiative quality (WHAT/WHY/Scope/HOW/Right-sized) + calibration accuracy |
| `revision_quality` | LLM | Revision improvement + content preservation |
| `pairwise` | LLM | Blind A/B comparison (only with `--baseline`) |
