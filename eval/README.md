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

The evaluation runs the `rfe.speedrun` skill headlessly against 25 test cases: 20 derived from real RHAIRFE Jira issues plus 5 deliberately weak drafts (see [Weak-draft cases](#weak-draft-cases)). Each test case provides a problem statement (prompt + clarifying context), and the pipeline creates, reviews, auto-fixes, and (dry-run) submits RFEs.

### How it was generated

`/eval-analyze --skill rfe.speedrun` recursively read the skill chain (rfe.speedrun -> rfe.create, rfe.auto-fix, rfe.review, rfe.split, rfe.submit, assess-rfe) and generated `eval.yaml` with dataset schema, output descriptions, and suggested judges. The configuration and judges were then iteratively refined through multiple eval runs across Opus and Sonnet.

### Configuration

- **`eval.yaml`** — defines the skill, dataset, outputs, judges, and thresholds.
- **`eval.md`** — cached skill analysis (auto-generated, tracks SKILL.md hash for freshness).
- **`eval/config/pairwise-judge.md`** — prompt for blind A/B comparison across runs.

### Dataset

`eval/dataset/cases/` contains 25 test cases, each with:

| File | Purpose |
|------|---------|
| `input.yaml` | Skill input: `prompt`, `priority`, `clarifying_context` |
| `annotations.yaml` | Expected scores, feasibility/recommendation expectations, test tags |

Input files contain only the fields the skill needs in batch Mode A — no Jira keys or existing RFE content.

#### Weak-draft cases

Cases `case-021` through `case-025` are deliberately weak drafts (`difficulty: hard`) that the pipeline is expected to auto-revise. Their `annotations.yaml` carries `tags: [weak-draft, revision-expected, <mode>]`, where `<mode>` names the flaw the draft carries. Only `revision-expected` has judge semantics: the `revision_coverage` check fails a case tagged with it unless its RFE shows revision evidence. Any one of eight signals counts:

1. `auto_revised: true` in the review frontmatter (or the legacy `revised: true`)
2. `before_score != score`
3. `before_scores != scores` (per-criterion breakdown moved)
4. an `rfe-originals/` body that differs from the current task body (whitespace-normalised)
5. a removed-context companion (`{id}-removed-context.yaml` or `.md`)
6. a leftover `{id}-review-state.json` (a re-review cycle ran)
7. the shared run report recording a revision for the item (`revision_cycles > 0`, `auto_revised: true`, or `before_score != after_score`)
8. a non-empty Revision History section (anything other than the template's `none` placeholder)

Without these cases every draft passed first review at 8-10, so the revise/reassess path was never exercised: `revision_quality` scored the decision not to revise 4-5 and `revision_flag_consistency` passed vacuously.

The `revision_coverage` threshold (`min_pass_rate: 0.92`) tolerates 2 failing cases out of 25: 3 of 5 weak drafts revised leaves 2 failing cases, 23/25 = 0.92 (pass); 2 of 5 leaves 3, 22/25 = 0.88 (fail). That 2-failure slack is shared by every way a case can fail this check:

- a weak draft the create step repairs on its own (passes first review, no revision);
- a weak draft the first review **rejects or splits** instead of revising. `scripts/filter_for_revision.py` never routes `reject`, `autorevise_reject` or `split` recommendations into the revise path, so a correct reject or split leaves no revision evidence; split children are not routed to any case directory in batch mode and count only towards run-level coverage via the report;
- any case without a review file (`No review files to check`).

The 0.92 value was calibrated offline against runs that predate the weak drafts; record the per-tagged-case outcome (revised / repaired at create / rejected / split) of the first 25-case run and re-tune the threshold against it. A multi-item run in which nothing was revised fails every case (0.0), on either pipeline. Single-item runs (Harbor tasks, `execution.mode: case`), whose run report describes exactly one input item (split children do not add inputs), skip the run-level check and are decided by the tag alone.

### Judges

| Judge | Type | What it checks |
|-------|------|----------------|
| `files_exist` | check | Task + review files produced |
| `frontmatter_valid` | check | YAML schema, score ranges, pass logic consistency |
| `run_report_exists` | check | Auto-fix YAML run report with required fields |
| `recommendation_consistency` | check | pass/fail aligns with recommendation, infeasible != submit |
| `revision_flag_consistency` | check | `auto_revised` agrees with revision evidence (state file, history, moved score, removed-context) |
| `revision_coverage` | check | Revise path exercised: `revision-expected` cases were revised; every case fails when a multi-item run revised nothing (single-item runs: tag alone decides) |
| `pipeline_flow` | check | Phases ran, no tracebacks, no Phase 1 deletions |
| `architecture_context_used` | check | Feasibility files must not indicate missing architecture context |
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

The evaluation runs the `initiative-speedrun` skill headlessly against 16 test cases derived from real RHOAIENG Jira initiatives. Each test case provides an objective (prompt + clarifying context), and the pipeline creates, reviews (with assessment, feasibility, and strategic alignment), auto-fixes, and (dry-run) submits Initiatives.

### Configuration

- **`eval-initiative.yaml`** — defines the skill, dataset, outputs, judges, and thresholds.
- **`eval/config/initiative-pairwise-judge.md`** — prompt for blind A/B comparison across runs.

### Dataset

`eval/initiative-dataset/cases/` contains 16 test cases, each with:

| File | Purpose |
|------|---------|
| `input.yaml` | Skill input: `prompt`, `priority`, `clarifying_context` |
| `annotations.yaml` | Expected scores, feasibility/recommendation/alignment expectations, test tags |

One case (`case-012`, tagged `sparse-input`) provides minimal context to test sparse-input handling.

### Judges

| Judge | Type | What it checks |
|-------|------|----------------|
| `files_exist` | check | Task + review files produced |
| `frontmatter_valid` | check | YAML schema, score ranges, alignment enum, pass logic consistency |
| `run_report_exists` | check | Initiative run report YAML with required fields |
| `recommendation_consistency` | check | pass/fail aligns with recommendation, infeasible != submit, weak alignment sets needs_attention |
| `revision_flag_consistency` | check | `auto_revised` agrees with revision evidence (state file, history, moved score, removed-context) |
| `revision_coverage` | check | Revise path exercised: untagged-but-revised cases count; every case fails when a multi-item run revised nothing (single-item runs: tag alone decides) |
| `pipeline_flow` | check | Phases ran, no fatal tracebacks |
| `architecture_context_used` | check | Feasibility files used architecture context |
| `initiative_quality` | LLM | Initiative quality (WHAT/WHY/Scope/HOW/Right-sized) + calibration accuracy |
| `revision_quality` | LLM | Revision improvement + content preservation |
| `pairwise` | LLM | Blind A/B comparison (only with `--baseline`) |
