# ADR-000x: Make RFE Creator pluggable across work item types

**Status:** Proposed — decision record (§§1–3, 5, 8 remain authoritative). The mechanism sections (§4) are superseded by `work-item-types-unified.md`; Appendix A is the review trail that led there
**Date:** 2026-08-10
**Author:** Eder Ignatowicz, Antonin Stefanutti
**Deciders:** TBD

## 1. Context

RFE Creator is a deliverable request pipeline. Today it handles one kind of request: feature requests in the RFE project.

The stages generalize — fetch, score against a rubric, revise, re-score, split if oversized, write back to Jira, report the delta. The implementation does not, yet. What varies per type is a short list of content decisions, and those are hardcoded in several places at once: skill prose, 17 python dicts, shell scripts, an external rubric repo, CI variables. The work is to lift them out into something reusable and open for extension.

Initiative support is the evidence that the stages generalize. It delivered a working second pipeline on the only path the architecture offers — copy the skill layer, restate the type facts per script — and in doing so it mapped exactly where the extension points belong. This ADR turns that map into a contract.

### 1.1 One intake, many destinations

Today one intake reaches one destination: RFE → RHAIRFE / Feature Request.

```
                                    ┌─→  Feature      (RHAISTRAT)
Feature Request  ──[ classify ]──→  │
   (intake)                         └─→  Initiative   (RHAISTRAT)
```

Everything enters as a feature request. Product-driven — customer need, external ask — resolves to a Feature. Engineering-driven — architectural work, platform improvement, debt with a clear start and stop — resolves to an Initiative. Same hierarchy level, prioritized together. The same contract serves types beyond these two: features owned by another org, requests from a different workspace with their own template and rubric, each plugging in without touching the pipeline.

Two consequences. "RFE" is a binding, not a concept — the tool creates feature requests, they live in RHAIRFE today and become Features in RHAISTRAT. That change has to be confined to one type definition rather than spread across the codebase. And work type becomes an output of the pipeline, not only an input — an author does not always know whether they are writing a Feature or an Initiative, and the tool can tell them. That makes classification an extension point in its own right, and it is the one with nowhere to live today.

## 2. What needs to generalize

Standing Initiatives up located every place a type asserts itself. Four findings.

**Dispatch generalizes; judgement does not.** Workflow skills are dispatch surfaces — `initiative-auto-fix/SKILL.md` is 144 lines carrying 17 script invocations. Of the 1,476 lines written for the second type:

| | Lines | Files |
|---|---|---|
| Dispatch — one body can serve every type | 933 (63%) | auto-fix, review, split, submit, speedrun |
| Judgement — stays per type | 543 (37%) | template, create questionnaire, feasibility, review/revise/fetch/split prompts |

The dispatch half is the same invocation sequence, phase order, polling and wave synchronization, with `--type initiative` on the command line. Nothing in it expresses what an Initiative *is*, which is why one body can serve every type. The 543 lines are the extension surface §3 enumerates, and where the agilists' Definition of Ready has to land.

**Type facts need one home.** The scripts layer is already parameterized rather than forked — the right shape, and most of the work. It lacks a single place to state a type, so 17 scripts each carry a per-type dict. Alignment is by hand and unverifiable at runtime: criterion names are read from review frontmatter with `.get()`, so a drifted name renders as 0 rather than raising, which is why `test_score_field_registry.py` pins four of them together. One home makes that class of check unnecessary.

**A type spans repos.** Rubrics live in the external assess repo by design — the assessor and the quality dashboard consume them without running this pipeline. For Initiatives the rubric is the piece still to write, and the piece that matters most: it is where the Definition of Ready becomes executable. Whatever expresses a type has to be a manifest, able to name things it does not contain.

**Classification has nowhere to live.** Type is chosen by which slash command someone typed, which works exactly as long as the human already knows the answer.

## 3. The extension points

Ten. Everything else — the 32-phase state machine, snapshotting, conflict detection, content preservation, label swapping, ADF conversion, run reports, resume, wave synchronization — is machinery that does not care what it processes.

| # | Extension point | RFE | Initiative |
|---|---|---|---|
| 1 | Destination binding | RHAIRFE/Feature Request; target RHAISTRAT/Feature | RHOAIENG/Initiative; target RHAISTRAT/Initiative |
| 2 | Document template | Summary, Problem, Customers, Justification, Acceptance Criteria, Size | Objective, Problem, Scope — 26 lines |
| 3 | Authoring questionnaire | customers, justification, problem, size, success | objective, problem, scope, parent |
| 4 | Rubric | what/why/open_to_how/not_a_task/right_sized | what/why/scope/open_to_how/right_sized |
| 5 | Reviewer panel | feasibility | feasibility + strategic alignment |
| 6 | Grounding context | RHOAI architecture context | same |
| 7 | Artifact layout & schema | rfe-tasks/, rfe_id, size, index | initiatives/, initiative_id, no size, alignment |
| 8 | Jira write conventions | rfe-creator-*, [RFE Creator] | initiative-*, [Initiative Creator] |
| 9 | Eval config & dataset | eval.yaml, eval/dataset/ | eval-initiative.yaml, eval/initiative-dataset/ |
| 10 | Classification rule | — | — |

Point 4 is the only one outside this repo, and the one still missing. Point 6 is identical today and will not stay that way — architecture context is RHOAI-specific and cross-org Initiatives are the case Initiatives exist for. Point 10 has no home at all.

## 4. Decision

One declarative descriptor as the single source of truth for §3. A type-generic skill layer. Classification as a pipeline step. The assess repo generalized the same way.

### 4.1 One descriptor replaces seventeen registries

Declared once, read through one accessor (`scripts/work_type.py`). No script keeps its own dict.

Both types stated in full, at today's bindings. Phase 3 changes the `jira:` block and nothing else.

```yaml
# types/rfe.yaml   (shape, not final field names)
id: rfe
classify:
  driver: product              # customer need, external requester, user-visible outcome
jira:
  project: RHAIRFE             # → RHAISTRAT at phase 3
  issue_type: Feature Request  # → Feature at phase 3
  local_prefix: RFE-
  key_prefix: RHAIRFE-
template: template.md          # Summary, Problem, Customers, Justification, AC, Size
questionnaire: questions.md
rubric:
  path: rubrics/rfe/agent_prompt.md
  criteria: [what, why, open_to_how, not_a_task, right_sized]
reviewers:
  - {skill: rfe-feasibility-review, writes: feasibility}
context: [architecture-context]
artifacts:
  tasks_dir: rfe-tasks
  id_field: rfe_id
  index: true                  # artifacts/rfes.md
  extra_fields: [size]         # S/M/L/XL, drives the split phase
conventions: {label_prefix: rfe-creator, actor_name: RFE Creator}
eval: {dataset: eval/dataset}
```

```yaml
# types/initiative.yaml
id: initiative
classify:
  driver: engineering          # architectural/platform work, no external requester
jira:
  project: RHOAIENG            # → RHAISTRAT at phase 3
  issue_type: Initiative       # → Initiative in RHAISTRAT, id to confirm
  local_prefix: INIT-
  key_prefix: RHOAIENG-
  also_reads: [RHOAIENG-]      # legacy keys stay fetchable after the flip
template: template.md          # Definition of Ready, §5
questionnaire: questions.md
rubric:
  path: rubrics/initiative/agent_prompt.md
  criteria: [goal, motivation, impact, stakeholders,
             success_criteria, scope_control, right_sized]
reviewers:
  - {skill: initiative-feasibility-review, writes: feasibility}
  - {skill: strategic-alignment-review, writes: alignment, when: has_parent}
context: [architecture-context]
artifacts:
  tasks_dir: initiatives
  id_field: initiative_id
  index: false
  extra_fields: [alignment]
conventions: {label_prefix: initiative, actor_name: Initiative Creator}
eval: {dataset: eval/initiative-dataset}
```

The two definitions differ in exactly the ten places §3 enumerates and nowhere else. That diff is the contract.

Schema composition is additive only — a base task and review schema plus the type's `extra_fields` and `rubric.criteria`. No type subtracts from the base, for the same reason no type replaces shared prose (§4.2).

The descriptor is declarative and inert. `artifact_utils.SCHEMAS` becomes generated from it rather than hand-written, `test_score_field_registry.py` becomes unnecessary, and the RHAISTRAT convergence becomes an edit to one type definition instead of a sweep across 17 files and 933 lines of prose.

### 4.2 Type-generic skills

One skill body per stage, with the type as a parameter. Per-type entry points stay as thin aliases — they are in muscle memory and in every CI job — and become a few lines that set `--type` and hand off.

A type owns `template.md`, `questions.md`, and *additive* overrides to the shared review, revise and split prompts. That constraint is what stops the fork reappearing under a new name: a type may add guidance to a shared prompt, never replace it. A type that needs to replace shared prose has found a bug in the shared prose, and it gets fixed for everyone.

### 4.3 Classification as a pipeline step

At create time the skill classifies from the descriptors' `classify` blocks, states its reasoning, and asks for confirmation interactively; under `--headless` it classifies and records the decision. At intake of an existing issue, auto-fix and review classify from the issue's own content, so a mis-filed request is identified rather than scored against the wrong rubric. Reclassifying re-runs from the template step, not from zero.

The signal already exists. The RFE rubric's `not_a_task` criterion scores 0 for task, chore or tech-debt framing — which under §1.1 is not a failing feature request, it is a well-formed Initiative arriving at the wrong door. Today the pipeline tells the author their RFE is bad when it should be telling them they wrote an Initiative.

Classification is not new machinery. It is a signal the system already computes and discards.

### 4.4 The assess side generalizes the same way

Rubrics stay external, but external must not mean one plugin per type. That repo is already close: its scoring skill takes the rubric as a substituted path at agent launch, and its run setup takes a project key as an argument. Type-specific there is the rubric text, not the machinery.

```
assess/                        (renamed from assess-rfe)
  skills/{assess,export-rubric}/
  rubrics/{rfe,initiative}/agent_prompt.md
  agents/scorer.md             # one Read/Write-only scorer, type-agnostic
```

One scorer agent, not one per type. The restriction that matters — Read and Write only, so a prompt-injected issue description cannot exfiltrate — is a property of the scoring role, not of the type being scored. Forking it duplicates a containment boundary and gives it a second place to drift.

`PIPELINE_TYPES.scorer_type` disappears, `rubric_path` moves to the descriptor, and `export_rubric.py` writes `artifacts/<type>-rubric.md`.

### 4.5 Sequencing

| Phase | Change | Risk |
|---|---|---|
| 1 | Descriptor + work_type.py; the 17 dicts read from it. Behaviour-preserving. | Low |
| 2 | Skills collapse to generic bodies + thin aliases | Medium, eval-gated |
| 3 | Destination flip to RHAISTRAT for both types | Medium, live data and JQL |
| 4 | Unified intake + classification | Medium, needs eval cases |
| 5 | *(follow-up ADR)* externally owned type packs | — |

Phase 1 pays for itself alone: it removes the drift class and turns phase 3 from a sweep into an edit. Whether a descriptor loads from *outside* this repo is the follow-up ADR; the shape here is built to allow it.

## 5. What Initiative supplies

**Destination — RHAISTRAT.** Initiatives belong at the same hierarchy level as Features so everything visible during prioritization sits in one workspace. The binding moves; `also_reads: [RHOAIENG-]` keeps the ~665 existing Initiatives workable in place. No bulk migration. Confirm the RHAISTRAT Initiative issue-type id before phase 3 — 10103 is RHOAIENG's.

**Template — the full Definition of Ready.** The 26-line template is replaced. An Initiative does not meet DoR without: Overview; Explicit Goal with supporting goals; Non-Goals; Motivation as problem plus hypothesis; Impact across architecture, resources, teams and components; named Stakeholders; Success Criteria across technical, operational and customer; Assumptions and Constraints; and a verifiable Definition of Done. Engineering-driven, time-boxed to 3–6 months, complete when its dependent Epics close.

**Questionnaire.** Derived from the DoR sections, in order, asking only for what the input has not already answered.

**Rubric — re-derived from DoR.** The blocking work item. The current criteria were inherited from the RFE rubric and measure none of DoR. One criterion per cluster: goal, motivation, impact, stakeholders, success_criteria, scope_control, right_sized.

`open_to_how` is absent. An Initiative is engineering-driven and may state its approach; the criterion that keeps an RFE from prescribing implementation is the wrong instrument here. `right_sized` stays because the split phase needs a size signal. Scale and pass threshold belong to the rubric owner; the criteria set is what this ADR fixes.

**The rest.** Reviewer panel: feasibility plus strategic alignment when a parent exists. Context: RHOAI architecture context, recorded as a value rather than an assumption. Artifacts, conventions and eval dataset as implemented. Classification: engineering-driven, architectural or platform work, no direct external requester, clear start and stop within roughly two quarters — the complement resolves to Feature.

Feature requests keep their current values with one change: the destination binding becomes RHAISTRAT/Feature at phase 3.

## 6. Consequences

Adding a type becomes one descriptor, one template, one questionnaire, one rubric, one dataset. Pipeline fixes reach every type at once. The RHAISTRAT convergence becomes a configuration change. Authors stop needing the answer before they ask the question.

The costs are real. Generic skills are less directly editable — changing feature-request behaviour now means considering Initiatives. The destination flip is confined to the descriptor inside this repo, but it is not free outside it: the target issue type carries its own id, workflow, transitions and required fields, so what the pipeline writes to Jira — description shape, field mapping, priority, parent link — has to be validated against it, and reporting, dashboards, scheduled JQL, data-repo partitioning and saved filters all assume today's projects. The DoR template raises the bar mid-flight, so Initiatives written against the lean template will score lower — a measurement change, not a quality regression, and the run reports should say so. Nothing here makes Initiatives score until the rubric is authored. And classification will sometimes be wrong; confirmation, recorded reasoning and cheap reclassification mitigate it, but an unchallenged wrong call still files an item in the wrong project.

Not covered here: renaming the data repos, parameterizing the assessor and auto-fixer CI jobs, whether the dashboard unifies types, and settling one skill naming convention.

## 7. Alternatives

**Leave it forked.** Zero refactor cost. Rejected: drift is the observed state after one type, and it has nowhere to put classification.

**Collapse the 17 registries only.** Rejected as the decision, adopted as phase 1 — it leaves 933 lines of dispatch prose to drift.

**Self-contained `types/<name>/` packs, no shared registry.** Rejected for now: pack layout is a packaging decision, and choosing it before knowing who owns a pack designs for the wrong boundary.

**Move rubrics into this repo.** Rejected: the assessor and dashboard consume them without running this pipeline.

**Two intakes, never unified.** Rejected: it assumes the author already knows the answer. The most common failure this tool sees — a request scored down as a task rather than a business need — is usually an Initiative in the wrong queue, and a two-intake design can only report that, never resolve it.

## 8. Open items

1. RHAISTRAT issue-type ids and workflow for Feature and Initiative. Confirm before phase 3.
2. Initiative rubric authorship. The DoR-derived criteria need an owner in the assess repo. Blocks Initiative scoring entirely.
3. Kanban exit criteria. This ADR scopes the tool to guaranteeing Definition of Ready at creation and re-checking it at review. It does not model state transitions. Enforcing exit criteria per column is a per-type readiness model — a new extension point and a separate decision.

These pieces are not independent. The descriptor is what makes the skill layer collapsible; the collapsed skill layer gives classification somewhere to live; classification is what makes one intake with many destinations a design rather than a routing accident. Land them separately and you get three partial refactors that never compound into a pluggable system.

## 9. Links

- Initiative guidance DRAFT
- RHAI Initiative definition and template

---

## Appendix A — Review notes (2026-08-12)

Review of the proposal above, against merged `main` (`8888b18`, post-#143) and the in-flight
`assess-rfe` PR #10. Claims were checked by reading and executing the code, not by inspection alone.
**Nothing in §§1–9 has been edited in response** — this appendix is the review trail; corrections in
A.4 are recorded, not applied.

**Overall:** the direction is sound and the diagnosis is unusually well-evidenced. Two things should
change before this is approvable: the phase ordering in §4.5 (A.1) and the mechanism in §4.2 (A.2).

### A.1 Blocking — P3 removes the only type discriminator; P4 supplies its replacement

Type is currently inferred from the Jira key prefix, in ~23 predicates. §1.1 converges both types into
RHAISTRAT, deleting that signal, while §4.3 classification — the successor discriminator — is P4.

| Site | Behaviour after the flip |
|---|---|
| `submit.py:472` `is_existing = item_id.startswith(jira_prefix)` | `False` for every `RHAISTRAT-` key → existing items treated as new → **duplicate Jira issues** |
| `artifact_utils.py:732` `find_review_file` | falls through to `rfe-reviews/` → review silently not found |
| `artifact_utils.py:608/618/677` | bottom out on `RHAIRFE-`/`RFE-` tests → return `None` (removed context vanishes) |
| `pipeline_state.py:142` `_has_rhaistrat_parent` | over-triggers — every parent is `RHAISTRAT-*`, so alignment runs on non-Outcome parents |
| `assess-rfe agent_types.py` `{"RHOAIENG": "initiative-scorer"}` | returns `rfe-scorer` for both types |

It is also not a config edit: both id patterns hard-reject the new key space —
`rfe_id ^(RFE-\d+|RHAIRFE-\d+)$` and `initiative_id ^(INIT-\d+|RHOAIENG-\d+)$` both fail on
`RHAISTRAT-123`. **P3 is a data-model change, not a binding change.** (`also_reads` is new machinery:
zero occurrences in either repo today.)

*Recommendation:* decide "where does type live" **before** P3 — an ordered chain
(frontmatter `work_type` → Jira issue-type name → label → classify). Add `work_type` and `jira_key` to
the schemas in P1; redefine `is_existing` as `jira_key is not None`; make `key_prefix` a list; add a
lint rejecting literal `startswith("RHAIRFE-"|"RHOAIENG-"|"INIT-"|"RFE-")`. Note P1's stated scope
("the 17 dicts read from it") does **not** cover these prefix predicates or the schema regexes — widen it.

### A.2 Blocking — §4.2 is the right goal via the wrong mechanism

Measured overlap between each `rfe.X` and `initiative-X` SKILL.md, after normalising mechanical type
tokens (paths, prefixes, poll names, `--type`) — raw line-identity is only 19–41% and badly understates it:

| auto-fix | review | split | speedrun | submit | create | overall |
|---|---|---|---|---|---|---|
| 94% | 80% | 79% | 78% | 61% | 53% | **78%** |

This **supports** §2's dispatch/judgement thesis and the collapse — for dispatch stages. `create` (53%)
should be excluded.

But *additive-only overrides* cannot express the divergence it must. The two review prompts state
contradictory rules on the same signal:

- `rfe.review/prompts/review-agent.md:41` — split when `right_sized`=1/2 **AND** capabilities serve
  different customer segments.
- `initiative-review/prompts/review-agent.md:50` — "Breadth across domains or personas is **not**, by
  itself, grounds to split."

An addition cannot retract a rule already in the base; whichever polarity sits in the shared prompt is
wrong for the other type.

*Recommendation:* re-scope P2 to **one authored template per stage, N generated flat bodies**. Keep
`.claude/skills/<type>-<stage>/SKILL.md` on disk flat and unbranched (what the model actually reads),
generated from `skills/_templates/<stage>.md` + `types/<t>.yaml`, with CI asserting the checked-in file
matches regeneration. Conditionals move from LLM-evaluated prose to a deterministic generator — single
-source authoring without the runtime-indirection reliability tax. Drop additive-only for judgement
prompts: keep them per-type, with the descriptor pointing at them (as `PIPELINE_TYPES.review_prompts`
already does).

### A.3 Blocking — the rubric rewrite has no phase, and it invalidates its own gate

§5 replaces the initiative criteria, but P2 is "eval-gated" on `eval-initiative.yaml` — the instrument
being replaced. Give the rubric its own numbered phase before the skill collapse.

Blast radius is smaller than it appears: the 16-case dataset is **77/80 `expected_scores` null**, so the
cases are nearly criteria-agnostic. The real coupling is four hardcoded spots in `eval-initiative.yaml`
(`:48`, `:108`, `:188` — inside a validator — and `:386`) plus judge prose at `:370-383`.

Also unaddressed: `assess-rfe` PR #10 ships the exact criteria set §5 declares wrong, with fresh
calibration work. State the disposition (merge as interim / redirect / abandon).

### A.4 Corrections (recorded, not applied above)

- **"543 judgement / 63-37"** reconciles only by excluding `strategic-alignment-review` (105 lines) and
  `assess-agent.md` (14). Including them: **1,595 lines, 58.5 / 41.5**. Strategic-alignment-review *is*
  extension point #5. The argument survives; the numbers should be restated.
- **"32-phase state machine"** → `PHASES` has **31** entries (30 phase-config blocks).
- **auto-fix "17 script invocations"** → **16**.
- **§8 item 1 / §5 "issue-type id … 10103"** — the code sends issue type **by name**
  (`jira_utils.create_issue` → `{"issuetype": {"name": ...}}`), never by id. The underlying concern
  (workflow, transitions, required fields, name collisions) is real; the framing is not.
- Verified exact and load-bearing: **17 per-type dicts** (22 scripts accept `--type`), **933 dispatch
  lines**, `initiative-auto-fix` = 144 lines, `test_score_field_registry.py` pinning **4** registries,
  and §4.3's premise verbatim in the RFE rubric — `(0=task/chore/tech debt, 1=borderline, 2=clear
  business need)`.

### A.5 Gaps to close before approval

- **§7 omits the alternative already implemented.** PR #150 adds `check_skill_parity.py` + tests wired
  into CI, so "leaves 933 lines to drift" engages a strawman. Add it and reject it on a stated bar — the
  honest argument is that parity ≠ correctness (both split forks are stale in *different* ways, which a
  parity lint structurally cannot catch).
- **"Eval-gated" is undefined** — what score delta blocks a phase?
- **No rollback plan** for P2/P3, the two hard-to-reverse phases.
- **Classification has no accuracy bar and no labeled corpus**, so P4 cannot be gated the way P2 is.
  `not_a_task=0` covers task *and chore and tech debt* — it detects "not a business need", which is
  weaker than "is an Initiative". Headless auto-classification picks the destination **project** with no
  confirmation; that needs a guardrail.
- **Descriptor scope creep** — `when: has_parent` is a DSL entering YAML. State where the descriptor stops.
- **"Single source of truth" is false across the repo boundary** (the rubric lives in assess-rfe); needs a
  stated contract, not just a path.

### A.6 Suggested re-sequencing

P1 descriptor + `work_type.py` + **identifier predicates and schema patterns** + `work_type`/`jira_key` in
frontmatter → P2 rubric re-derivation (reset the eval baseline) → P3 generated flat bodies (excluding
`create`), gated on the now-stable eval → P4 destination flip, gated on a `submit.py --dry-run` plan
showing zero unexpected "Would create" → P5 classification.

This fixes the one structural flaw: the discriminator exists before the thing that removes it.

### A.7 Related

`design-proposals/request-type-extensibility.md` is a separate, longer proposal on the same theme
(manifest-registry "types as data in core", judged across three candidate designs). It overlaps this ADR
but differs on mechanism — notably it keeps `rfe.*` as the generic bodies with **permanent** per-type
alias families, where §4.2 here treats aliases as thin shims. The two should be reconciled or explicitly
scoped against each other.
