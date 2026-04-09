# Evaluation — rfe.speedrun

Automated evaluation of the `rfe.speedrun` pipeline using the [agent-eval-harness](https://github.com/opendatahub-io/agent-eval-harness).

## Quick Start

### Setup

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
```

### Review results

Each run produces:
- `eval/runs/<run-id>/report.html` — HTML report with scoring summary, per-case details, and baseline diff
- `eval/runs/<run-id>/summary.yaml` — machine-readable scores
- `eval/runs/<run-id>/stdout.log` — full execution trace

## How it works

The evaluation runs the `rfe.speedrun` skill headlessly against 20 test cases derived from real RHAIRFE Jira issues. Each test case provides a problem statement (prompt + clarifying context), and the pipeline creates, reviews, auto-fixes, and (dry-run) submits RFEs.

### How it was generated

`/eval-analyze --skill rfe.speedrun` recursively read the skill chain (rfe.speedrun -> rfe.create, rfe.auto-fix, rfe.review, rfe.split, rfe.submit, assess-rfe) and generated `eval.yaml` with dataset schema, output descriptions, and suggested judges. The configuration and judges were then iteratively refined through multiple eval runs across Opus and Sonnet.

### Configuration

- **`eval.yaml`** — defines the skill, dataset, outputs, judges, and thresholds.
- **`eval.md`** — cached skill analysis (auto-generated, tracks SKILL.md hash for freshness).
- **`eval/config/pairwise-judge.md`** — prompt for blind A/B comparison across runs.

### Dataset

`eval/dataset/cases/` contains 20 test cases, each with:

| File | Purpose |
|------|---------|
| `input.yaml` | Skill input: `prompt`, `priority`, `clarifying_context` |
| `reference-rfe.md` | Gold standard RFE (with YAML frontmatter) |
| `reference-review.md` | Gold standard review (with rubric scores) |
| `annotations.yaml` | Expected scores, source metadata, test tags |

Input files contain only the fields the skill needs in batch Mode A — no Jira keys or existing RFE content.

### Judges

| Judge | Type | What it checks |
|-------|------|----------------|
| `files_exist` | check | Task + review files produced |
| `frontmatter_valid` | check | YAML schema, score ranges, pass logic consistency |
| `run_report_exists` | check | Auto-fix YAML run report with required fields |
| `recommendation_consistency` | check | pass/fail aligns with recommendation, infeasible != submit |
| `pipeline_flow` | check | Phases ran, no tracebacks, no Phase 1 deletions |
| `rfe_quality` | LLM | RFE quality (WHAT/WHY/HOW/task/scope) + calibration accuracy |
| `revision_quality` | LLM | Revision improvement + content preservation |
| `pairwise` | LLM | Blind A/B comparison (only with `--baseline`) |

### Tool interception

During evaluation, PreToolUse hooks:
- **Auto-answer** `AskUserQuestion` prompts from test case context
- **Block Jira** interactions (the skill runs with `--dry-run`)
