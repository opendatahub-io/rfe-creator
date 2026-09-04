# Design Proposal: Request-Type Extensibility — Types as Data in Core

**Status:** Superseded by `work-item-types-unified.md` (2026-08-12); kept as the design record for the manifest-registry candidate and its panel judgement

> **Positions in this document that the unified design rejects or changes — do not implement from here:** permanent `initiative-*` alias families (the skills are deleted instead); "zero required CI changes, ever" (scoped to production pipelines; repository CI work is listed); the `types/strategy/` prototype (strat-creator is a peer pipeline of a different kind); per-type scorer agents (one type-agnostic scorer, coordinated); the prefix-only alignment condition (a named `parent_is_outcome` hook); tag-based rubric pins (immutable commit SHAs); prefix-keyed identity (`(project, issue_type)` with prefix lists and a post-fetch candidate resolution); the `RFE_CREATOR_EXTRA_TYPES` seam as an unrestricted runtime input (development/test only, allowlisted roots in protected runs)
**Repo:** opendatahub-io/rfe-creator (analysis at merged main `8888b18`, post PR #143)
**Provenance:** Synthesis of three independent designs (manifest-registry, plugin-per-type, layered-shim) judged by a 3-lens panel (end-user UX, provider DX, ops/migration). Winning base: **manifest-registry** (types-as-data in core). Best ideas from the non-winners are grafted in; panel disagreements are resolved explicitly in §0.1; fatal flaws found in the winning design are fixed and marked **[panel fix]**.

---

## 0. Executive summary

Every request type (rfe, initiative, and future types like strategy) becomes a **declarative descriptor directory `types/<type>/`** inside rfe-creator: one `type.yaml` plus the genuinely type-specific markdown (template, prompt fragments, review-dimension bodies, eval fragment, Jira-emulator seed). A single loader, `scripts/type_registry.py`, becomes the only import point for type data; the ~20 private per-script `_TYPE_CONFIG` dicts, `PIPELINE_TYPES`, `TYPE_CONFIGS`, `SCHEMAS`, and `PHASE_CHECKS` all become derived views. The six `rfe.*` skills become the **type-generic entry points** (`--type <t>` on each), with calibrated classification in `create` and deterministic script-side auto-detection everywhere else. The `initiative-*` skill names survive as **permanent generated aliases**, and every future type gets its own generated alias family (`/strategy-create`, …) so no human is ever forced onto rfe-branded commands for a non-RFE type.

Why this shape: the per-type variance surface is ~19 items and ~95% plain strings; the only per-type *code* in the whole dispatch is one named condition (`_has_rhaistrat_parent`, `scripts/pipeline_state.py:125-149`). Meanwhile the mirrored-skill approach demonstrably rots — all four shipped drift bugs live in copied orchestration skeleton, zero in genuinely typed content. This is a data problem, not a plugin problem — but we publish the provider contract (guide, skeleton, validator action, registry metadata) **now**, so the day a second external team appears, extraction to out-of-repo packaging is a transport PR, not a redesign.

**CI impact: zero required changes, ever.** `/rfe.auto-fix` in the autofixer's `CLAUDE_PROMPT` *is* the generic skill; the assessor never touches rfe-creator; the eval pipeline reads the skill name from each PR's own config. There is no alias-indirection hop on any production or eval hot path.

### 0.1 Panel disagreements — resolved

| Disagreement | Positions | Decision & tradeoff |
|---|---|---|
| **Naming: keep `rfe.*` generic vs new `request.*` + aliases** | ops + provider judges: keep `rfe.*` (zero CI touch, no indirection hop on hot paths). UX judge: `rfe.*`-as-generic injects classification friction into `/rfe.create` and forces initiative users onto rfe-branded names. | **Keep `rfe.*` as the generic bodies.** The UX costs are mitigated, not accepted: (a) classification is *calibrated* — a clearly-RFE idea gets a one-line notice, never a question **[panel fix]**; (b) per-type alias families are **permanent** and generated for every type, so each type keeps/gets native-branded commands **[panel fix]**; (c) a cosmetic `request.*` alias layer can be added later at ~zero cost (pure generated aliases over the same bodies) — flagged as maintainer decision, §8-Q1. Hot paths never route through an alias. |
| **Alias lifecycle: delete after 2 cycles vs permanent** | Winning design deleted aliases; UX + provider judges: permanent-by-default. | **Permanent-by-default** (layered-shim policy). Aliases cost ~100 generated lines; deleting them is a cross-repo, non-atomic risk with no reward. Only production hot paths matter for the weak-model instruction-drop concern, and those hit generic bodies directly (no initiative job exists in any production pipeline today — grep-verified). |
| **Packaging now (plugin-per-type) vs in-repo** | Provider judge liked plugin autonomy; ops judge showed the distribution story was overstated (`AGENT_ENABLED_PLUGINS` hard-fails on unknown names, `agentic-ci/src/agentic_ci/plugins.py:61-77`; the autofixer disables baked plugins) and `depends_on` is display-only (skills-registry ARCHITECTURE.md:127-131). | **In-repo `types/` now; marketplace-ready contract published now; transport deferred until a second external provider.** The `RFE_CREATOR_EXTRA_TYPES` seam is exercised by a CI test from day 1 so it never bit-rots. Provider autonomy comes from CODEOWNERS routing + the companion-skills doctrine (§2.8), not from a federation built for a market of one. |
| **Migration mechanics: one-shot registry swap vs incremental** | Ops + provider judges both preferred layered-shim's mechanic. | **Source-of-truth-by-test-before-by-import**: PR-1 pins every existing dict equal to descriptor projections; scripts then migrate one-per-PR while the pin test shrinks. No PR touches 20 scripts at once. |
| **Classification UX: always-confirm vs calibrated** | layered-shim always confirmed; plugin-per-type calibrated. | **Calibrated** (clear match → notice + proceed; ambiguous → AskUserQuestion; headless → forbidden). |
| **Eval config flip vs keep alias** | Flipping `eval-initiative.yaml` to the generic skill merges run dirs under `eval/runs/rfe.speedrun/`; keeping the alias adds a hop on weak-model eval runs. | **Flip to the generic invocation** (`skill: rfe.speedrun`, args `--type initiative`). No hop where weak models run; MLflow experiments stay distinct per config; one-time re-baseline is budgeted in PR-6. |

---

## 1. Motivation & current state (brief)

PR #143 added initiatives by mirroring: 6 user-facing skill pairs + per-type feasibility skills + initiative-only `strategic-alignment-review`, a `PIPELINE_TYPES` table, and `--type` flags on every script. The measured cost:

- **Near-total skeleton duplication:** of ~1,860 lines across the 12 mirrored non-template skill pairs, 60-70% is verbatim after token substitution; `initiative-auto-fix` differs from `rfe.auto-fix` by **5 normalized lines of 144**; the assess-agent prompts by **0 of 14**. Genuine type content concentrates in templates (103/105 lines differ), revision policy (74/68), split heuristics (100/157), and rubrics (42% identity).
- **A shattered descriptor:** the ~19-item per-type variance surface is restated across ~20 private per-script dicts (`submit.py TYPE_CONFIGS:59-126`, `split_submit.py SPLIT_CONFIG:61-104`, `verify_phase.py _TYPE_CONFIG:40-67`, `check_review_progress.py PHASE_CHECKS:18-32`, `artifact_utils.py SCHEMAS:60-285`, …) with only two cross-imports in the codebase. `tests/test_score_field_registry.py:2-18` documents the failure mode verbatim: *"Four places in this repo independently hard-code that same set… Nothing enforces agreement… That is exactly how the initiative PDF shipped with the RFE key set."*
- **The copies already rot:** interactive `rfe.review:85` and `rfe.split/prompts/split-agent.md:25` use the pre-assess-rfe#5 rubric path while `pipeline_state.py:99` uses the new one; `rfe.review:310` returns to auto-fix steps that no longer exist; the initiative eval yaml silently dropped the rm-artifacts check and grew three other semantic drifts; `.claude/settings.json` allowlists a nonexistent script.

**Success metric for this design (grafted from layered-shim): drift surface eliminated, not lines deduplicated.** All four shipped drift bugs live in skeleton copies; the ~40% of prompt text that stays per-type (templates, rubrics, heuristics) is real authorship and is *supposed* to differ.

---

## 2. Extension contract specification

### 2.1 Layout: `types/<type>/`

```
types/
  _schema/type.schema.json        # JSON Schema for type.yaml (schema_version gate)
  rfe/
    type.yaml                     # THE descriptor — single source of truth
    template.md                   # moved from .claude/skills/rfe.create/rfe-template.md
    prompts/                      # type-specific prompt fragments consumed by the generic bodies
      create-questions.md         #   clarifying-question guidance
      revision-policy.md          #   today: revise-agent.md's 74 type-specific lines
      split-heuristics.md         #   decomposition unit (capability / customer segment)
      recommendation-rules.md     #   review-agent split/reject heuristics
      review-sections.md          #   body headings ("Strategy Considerations" …)
    dimensions/
      feasibility.md              # body of rfe-feasibility-review/SKILL.md
    eval/
      fragment.yaml               # criteria, thresholds, extra judge rules
      pairwise-judge.md           # moved from eval/config/pairwise-judge.md
    jira-emulator-seed.yaml       # project, workflow, transitions, link types (tests/conftest.py:106-121 pattern)
  initiative/
    …                             # same shape; dimensions/ adds alignment.md
                                  #   (body of strategic-alignment-review/SKILL.md)
```

Eval datasets stay at `eval/dataset/` and `eval/initiative-dataset/`, referenced by the descriptor. Dimension "skills" stop being skills: they are already consumed as prompt *files*, never via the Skill tool (`rfe.review/SKILL.md:91`; `feasibility_skill`/`alignment_skill` paths in `PIPELINE_TYPES`), so moving them under `types/<t>/dimensions/` is a path change, not a behavior change.

**Hard contract clause — data only [grafted from plugin-per-type]:** `types/<t>/` contains **no executable code, ever** — YAML and markdown only. Rationale, written down: the eval pipeline is the one place `.claude/settings.json`'s ~35 exact relative-path Bash allow rules are enforced (`gitlab-rfe-creator-eval` runner.py:51-69); per-type scripts would reproduce the absolute-path-denial → text-only-stop failure family, and a data-only payload keeps third-party type review tractable.

### 2.2 The type descriptor — `types/initiative/type.yaml` (fields verified against the code)

```yaml
schema_version: 1
type: initiative
display: { entity: Initiative, entity_plural: Initiatives }

classification:                    # consumed ONLY by interactive create (§3.3)
  summary: "A strategic multi-team objective in RHOAIENG, often parented to a RHAISTRAT strategy."
  signals: ["multi-team / multi-quarter objective", "workstreams", "RHAISTRAT parent"]
  counter_signals: ["single customer-facing capability gap (→ rfe)"]

ids:                               # submit.py TYPE_CONFIGS + next_rfe_id.py --prefix/--dir
  field: initiative_id
  local_prefix: INIT-
  jira_prefix: RHOAIENG-

dirs:                              # PIPELINE_TYPES + the 13 per-script _TYPE_CONFIG dirs
  tasks: artifacts/initiatives
  originals: artifacts/initiative-originals
  reviews: artifacts/initiative-reviews
index: { enabled: false }          # has_index (submit.py:124); rfe: artifacts/rfes.md
companions: { comments: false, removed_context: true }

jira:                              # verified against submit.py TYPE_CONFIGS
  project: RHOAIENG
  issue_type: Initiative           # type = (project, issuetype) — project alone under-determines
  type_label: Initiative
  label_prefix: initiative
  labels:
    rubric_pass: initiative-autofix-rubric-pass
    feasibility: { feasible: initiative-feasibility-pass,
                   infeasible: initiative-feasibility-fail,
                   indeterminate: initiative-feasibility-unknown }
    alignment:   { strong: initiative-alignment-strong,
                   partial: initiative-alignment-partial,
                   weak: initiative-alignment-weak }
  split_link_type: "Work item split"   # today hardcoded at split_submit.py:187
  comment_prefix: "[Initiative Creator]"
  removed_context_preamble: "*[Initiative Creator]* The following technical implementation details…"
  jql_default: 'project = RHOAIENG AND issuetype = Initiative'
  parent_key_patterns: ["RHAISTRAT-\\d+", "RHOAIENG-\\d+", "INIT-\\d+"]

schema:                            # collapses artifact_utils.SCHEMAS + the 7 criteria restatement sites
  task:
    extra_fields: {}               # rfe adds: size {enum: [S, M, L, XL]}
  review:
    score_fields: [what, why, scope, open_to_how, right_sized]
    extra_fields:
      alignment: { enum: [strong, partial, weak, not_assessed], default: not_assessed }
    extra_rules:
      - { when: { field: alignment, equals: weak }, then: needs_attention }

pipeline:
  stages: [create, review, submit, split, auto-fix, speedrun]   # a type may ship fewer (§2.8) [grafted]
  poll_prefix: "initiative-"       # new types MUST use "<type>-"; rfe grandfathers ""
  state_prefix: "initiative-"      # tmp/ file namespace; rfe grandfathers the bare family
  scorer_agent: initiative-scorer
  rubric:
    repo: opendatahub-io/assess-rfe
    ref: v1.2.0                    # PINNED — today's bootstrap floats at main (git pull --ff-only)
    path: skills/assess-initiative/scripts/agent_prompt.md
    export: artifacts/initiative-rubric.md   # fixes: initiative rubric never reaches create today
  dimensions:                      # replaces feasibility_skill / alignment_skill / None
    - { name: feasibility, prompt: types/initiative/dimensions/feasibility.md, blocking: true }
    - name: alignment
      prompt: types/initiative/dimensions/alignment.md
      blocking: false
      condition: { frontmatter_field: parent_key, prefix: RHAISTRAT- }   # data, not code hook
      skip_stub: { result: not_assessed }    # → _write_poll_stub (pipeline_state.py:152-161)
  split:
    trigger: { score_field: right_sized, max: 1 }   # parameterizes check_right_sized.py:52

batch: { extra_fields: [parent_key] }    # validate_batch_input.py:42-43

snapshot: { prefix: initiative-snapshot-, report_prefix: initiative-run- }

reporting:
  item_key: per_initiative
  criterion_labels: { what: What, why: Why, scope: Scope, open_to_how: "Open to How", right_sized: "Right-sized" }
  extra_entry_fields: [alignment, feasibility, needs_attention]

eval:
  config: eval-initiative.yaml     # GENERATED from skeleton + fragment.yaml, checked in, CI-verified
  dataset: eval/initiative-dataset
  mlflow_experiment: initiative-speedrun-eval
  thresholds: { architecture_context_used: 0.85, quality_min_mean: 3.5 }   # thresholds are type policy
  timeout: 8100
  annotations_extra: [expected_alignment]  # → jira_cases.py schema; expected_* MUST be populated
```

The rfe descriptor is the same shape with `score_fields: [what, why, open_to_how, not_a_task, right_sized]`, `labels.rubric_pass: rfe-creator-autofix-rubric-pass`, feasibility labels `rfe-creator-feasibility-{pass,fail,unknown}`, `index.enabled: true`, `companions.comments: true`, and grandfathered empty `poll_prefix`/`snapshot.prefix` (allowed for `type: rfe` only). Both incumbent types fit the schema with zero residue.

### 2.3 Loading: `scripts/type_registry.py` — the only import point

```python
all_types() -> [str]          # enumerates types/*/type.yaml (+ $RFE_CREATOR_EXTRA_TYPES dev dirs)
get(name) -> TypeDescriptor   # parsed, schema-validated, cached
detect(token) -> str | None   # "RHAIRFE-1595"→rfe; "INIT-004"→initiative; path→dirs map; frontmatter type:
resolve(args) -> str          # full ladder (§3.3); never interactive itself
choices() -> [str]            # replaces every hardcoded choices=["rfe","initiative"]
```

Every private dict becomes a derived view: `PIPELINE_TYPES`, `TYPE_CONFIGS`, `SPLIT_CONFIG` (its 4 function refs replaced by generics parameterized on descriptor fields — `find_task_file_including_archived` at `artifact_utils.py:629` and `_feasibility_labels(label_prefix)` at `split_submit.py:113` prove derivability), `SNAPSHOT_CONFIG`, `verify_phase._TYPE_CONFIG`, `PHASE_CHECKS` (generated from dirs + poll_prefix + dimension names), reporting configs, `SCHEMAS` (base + descriptor deltas), `frontmatter._detect_schema_type`, the five prefix-sniff sites (`prep_assess.py:24`, `filter_for_revision.py:28`, `preserve_review_state.py:21`, `artifact_utils.py:677/732` → `detect()`), `_save_originals`' string-replace (`pipeline_state.py:474` → `dirs.originals`), `jql_query.py`'s literal labels, and `_check_condition` (named-hook if-chain → `condition:` expression objects; a named-hook registry in core Python remains the closed escape hatch). Module aliases like `submit.FEASIBILITY_LABELS` (submit.py:129) are kept so tests stay green.

### 2.4 Discovery

Static enumeration of `types/*/type.yaml` at import. `RFE_CREATOR_EXTRA_TYPES` adds drop-in dirs for local/provider development — **exercised by a CI test from day 1 [grafted]** so the future out-of-repo transport seam never rots. Distribution to CI stays the existing channels: merge to main → daily sandbox image rebuild + `ci-stage`/`ci-prod` branch promotion. skills-registry gains an informational `provides: request-type` metadata field (like `depends_on`, display/validation only).

### 2.5 Validation: `scripts/validate_types.py` — three gates [grafted: SETUP fail-fast from plugin-per-type]

1. **Lint-time (make lint / CI):** JSON-Schema per descriptor; referenced files exist; `score_fields` non-empty (the review barrier requires numeric `score` — `check_review_progress.py:40-49`); review schema accepts the verify_phase error-stub shape (`score=0, pass=false, recommendation=revise, feasibility=feasible, needs_attention=true`, `verify_phase.py:106-127`); cross-type invariants — unique poll/state prefixes, unique local/jira ID prefixes, **snapshot prefixes mutually prefix-collision-free** (`update_snapshot_hashes` matches "latest file matching prefix", `snapshot_fetch.py:290-317`; a collision corrupts another type's snapshot); generated files in sync. Plus a **skill-body lint**: no AskUserQuestion or interactive affordance outside sections marked interactive-only **[grafted from layered-shim — targets the headless text-only-stop family]**.
2. **Cross-repo (`--with-deps`, post-bootstrap CI):** `pipeline.rubric.path` exists in the assess checkout at `rubric.ref`; scorer agent file exists. This *replaces* the hand-mirrored gates in `bootstrap-assess-rfe.sh` and retires the duplication pins in `tests/test_bootstrap_assess.py:134-155`.
3. **Runtime, pipeline SETUP:** `validate_types.py --verify --type <t>` runs where `bootstrap-assess-rfe.sh --type` runs today (`pipeline_state.py:226`), hard-failing with a diagnosis **before any agent wave** — preserving the exact failure-mode motivation in the bootstrap header ("wait-for-wave spins on exit 3 with nothing to diagnose") and generalizing it to all descriptor assets.

`tests/test_score_field_registry.py` inverts from a hand-pinned 4-way contract into a structural test (registries are derived), and its `TestRegistryCoverage` ("a third type must not silently skip a registry") becomes automatic.

### 2.6 Versioning & provenance [grafted: stamping from plugin-per-type]

- `schema_version` in every descriptor; core supports N and N−1 with additive-only evolution inside a major.
- `pipeline.rubric.ref` is **pinned** (tag or SHA), adopting the skills-registry `owner/repo@sha:path` rubric_ref convention (registry.yaml:63,787). The generalized bootstrap honors it (env overrides `ASSESS_RFE_REPO/ASSESS_RFE_REF` still win, as the eval pipeline already plumbs).
- At assess time, the **rubric checkout SHA and the descriptor's content hash are stamped into review frontmatter** (`rubric_version`, `type_version` — new optional schema fields). This closes the documented hole: today no artifact records which rubric scored a review, so eval baselines cannot separate skill regressions from rubric drift.

### 2.7 What the contract refuses to customize

- **The phase graph and state machine** (31 phases, transition graph, cycle caps, `tmp/pipeline-state.yaml` — `pipeline_state.py:58-90,532-731`). Types plug into fixed slots (dimensions, prompts, stages); they do not reshape the graph. Barrier correctness is the most incident-prone property in the system.
- **Numeric scoring**: every type's review must produce numeric `score`, `pass`, `recommendation` and accept the error-stub shape — else wait-for-wave deadlocks.
- **Snapshot invariants** (`docs/snapshot-incremental-fetch.md:398-445`) and the shared `artifacts/auto-fix-runs/` layout; only prefixes are per-type.
- **CI magic strings & script locations**: `FULL RUN COMPLETE` (agentic-ci `stream.py:243-250` ↔ `finish.py`), `"No RFE task files found"` byte-stable for rfe forever, all executables stay in `scripts/`.
- **Priority vocabulary and the transactional submit sequence** (deterministic Jira write scripts per CLAUDE.md rationale).

### 2.8 Escape hatches, tiered [companion-skills doctrine grafted from plugin-per-type]

1. **Descriptor data** — labels, dimensions (any count; `_wave_size` already scales waves by parallel-agent count, `pipeline_state.py:406-421`), thresholds, prompts, conditional dimensions via the closed `condition:` vocabulary (`frontmatter_field` + `prefix|equals|exists`).
2. **Declarative rules** — `schema.review.extra_rules`, `split.trigger`, `blocking:` flags, **`pipeline.stages:`** (a type may ship `[create, review]` only; `/rfe.submit STRAT-001` fails fast with "type strategy does not support stage submit") — adopted over the winning design's `submit.enabled` flag.
3. **Companion skills** — the *default* answer for exotic per-type lifecycle (human sign-off gates, cross-type cloning, dual workspaces, issue locking): the provider ships additional user-invocable skills in their own repo/plugin, consuming core's declared-stable script CLIs (`frontmatter.py`, `state.py`, `jira_utils.py` — already type-agnostic save one regex at `jira_utils.py:710` — and `next_rfe_id.py`). **Promotion rule: a capability enters the descriptor schema only on the second requester.**
4. **Core Python PR** — reserved for shared semantics: new named condition hooks, new stages in the fixed graph. Reviewed once, then descriptor-addressable for everyone.

---

## 3. End-user experience (outcome A)

### 3.1 Skill surface: today → target

| Action | Today | Target |
|---|---|---|
| create | `/rfe.create` \| `/initiative-create` | `/rfe.create` (generic; classification §3.3) + `/initiative-create` permanent alias |
| review | `/rfe.review` \| `/initiative-review` | `/rfe.review` (generic; auto-detect) + alias |
| submit / split / auto-fix / speedrun | mirrored pairs | generic `rfe.*` + permanent `initiative-*` aliases |
| dimension skills | `rfe-feasibility-review`, `initiative-feasibility-review`, `strategic-alignment-review` | files under `types/<t>/dimensions/` (not skills) |
| orphaned strat skills (`feasibility-review`, `architecture-review`, `scope-review`, `testability-review`) | dead weight reading unproduced `artifacts/strat-tasks/` | parked as drafts for `types/strategy/dimensions/`, else deleted |
| `rfe-creator.update-deps` | misses `assess-initiative` | regenerated from the bootstrap copy list |

Aliases are ~8-line generated stubs that pre-bind the type ("TYPE=initiative is resolved; skip resolution; read `.claude/skills/rfe.review/SKILL.md` and follow from Step 1") — the repo's most-exercised indirection pattern (dispatch loop, dimension prompts). Alias `allowed-tools` must mirror the generic body's; descriptions keep type-specific routing text for model auto-selection. Every future type ships its alias family from the descriptor, so `/strategy-create` exists day 1.

### 3.2 Type resolution ladder **[panel fix: replaces the contradictory exit-2 / defaults-to-rfe story]**

Implemented in `type_registry.py resolve` (a script — never LLM judgment), invoked as Step 0 of every generic body:

1. **Explicit `--type <t>`** — always wins; unknown type → exit with the registered list. The canonical CI path.
2. **Alias pre-binding** — `initiative-*` aliases prepend `--type initiative`; no detection runs.
3. **Deterministic signals**: batch-file top-level `type:` key (new, validated); Jira/local ID prefixes via `ids.*` maps; artifact frontmatter `type:` field (new, written by create/fetch; prefix/path fallback for pre-migration files); `--parent` key prefix; JQL `project =` (+ `issuetype =` when present) → descriptor match; legacy `--rfe-id`/`--initiative-id` flags.
4. **Interactive classification (create only, calibrated [grafted from plugin-per-type])**: match the idea against each descriptor's `classification` block. Clear match → one-line notice and proceed (`Type: rfe — pass --type to override`). Ambiguous → AskUserQuestion with one option per installed type, labeled from descriptors, so new types appear automatically. **The RFE-only PM typing `/rfe.create <clearly-RFE idea>` never sees a question.**
5. **Headless, still unresolved → grandfathered legacy default `rfe`**, printed as `TYPE RESOLVED: rfe (legacy default)`. This keeps today's `/rfe.speedrun --headless --input batch.yaml` (eval.yaml) and every headless caller byte-compatible. Guard rails: the default is grandfathered to **rfe only** (a new type's batch must carry `type:` or `--type`); any *conflicting* deterministic signal (initiative-prefixed IDs, `parent_key` batch fields under rfe validation) fails loudly via `validate_batch_input.py`/`resolve` with a non-zero exit **before** `pipeline_state.py init` — a visible Bash failure in stream-json, never an AskUserQuestion and never a text-only turn.

**Jira-key ambiguity**: RHOAIENG hosts Epics/Stories/Initiatives, so prefix detection is *provisional* and **verified post-fetch against `jira.issue_type`**; mismatch → interactive: offer correction; headless: fail that ID into the standard error-review stub path (`verify_phase.py:106-127`) rather than mis-process. **Mixed-type invocations are rejected** with a split-the-batch message (assess-rfe PR #10's mixed-run hard-error precedent; the shared `tmp/pipeline-state.yaml` makes mixed runs unsupportable anyway).

### 3.3 What a user sees

```
/rfe.create Users need SSO for the model registry          # notice "Type: rfe", no question
/rfe.create --type initiative --parent RHAISTRAT-42 …      # explicit
/initiative-create Consolidate model-serving stacks         # alias, exactly as today
/rfe.review RHOAIENG-9876       # "Detected type initiative (RHOAIENG + issuetype Initiative).
                                #  Reviewing with the initiative rubric, feasibility + alignment…"
/rfe.speedrun --headless --dry-run --type initiative --input batch.yaml   # CI form
```

### 3.4 Backward compatibility

- All `rfe.*` invocations behave identically for RFEs (legacy default + deterministic detection).
- All `initiative-*` invocations behave identically forever (permanent aliases).
- Genericization fixes the shipped fork drift once: stale rubric paths (`rfe.review:85,227`, `split-agent.md:25`), the dead auto-fix return path (`rfe.review:310` → nonexistent Step 3b / `tmp/autofix-config.yaml`), the hardcoded template path (`split-agent.md:95` → descriptor), and README.md:92's inaccurate "`/rfe.*` → `/initiative.*`" note.

---

## 4. Extension provider experience (outcome B): strat-creator walkthrough

The provider surface is published in **PR-1**, not at extraction time **[grafted]**: `docs/type-provider-guide.md`, the `types/rfe/` skeleton as the official copy-from artifact, and a reusable validate GitHub Action.

**Step 1 — author `types/strategy/`** (one PR to rfe-creator): `type.yaml` with `jira: {project: RHAISTRAT, issue_type: Feature}`, `ids: {field: strat_id, local_prefix: STRAT-, jira_prefix: RHAISTRAT-}`, `dirs: {tasks: artifacts/strat-tasks, …}` (matching what the four orphaned strat skills already read), `pipeline.stages: [create, review]` (strat's Jira writes are unimplemented — strat-creator CLAUDE.md:77-79), four dimensions (feasibility/architecture/scope/testability — the orphaned in-tree skills are ~80% of the prompt drafts), `poll_prefix: strategy-`, `snapshot.prefix: strat-snapshot-`. Plus template, prompt fragments, emulator seed (RHAISTRAT workflow, Cloners/Related link types).

**Step 2 — rubric + scorer** (PR to the assess side): rubric `agent_prompt.md` (real authorship — this is the provider's main quality lever) + 8-line `strat-scorer` agent stub. The descriptor's pinned `rubric:` block tolerates both existing packaging conventions (assess-strat plugin or folded into assess-rfe, §6).

**Step 3 — eval obligations** (contract floor, from PR #143's de-facto precedent): ≥16 anonymized cases (PII policy) incl. ≥1 sparse/adversarial case; **populated type-specific `expected_*` annotations** (the initiative dataset's 16/16 `expected_alignment` is the bar; the RFE dataset's all-null placeholders are the named anti-pattern — and the harness already delivers annotations to judges, `score.py:209-227`, so they become load-bearing for new types); pairwise + quality judge fragments; explicit thresholds; committed generated `eval-strategy.yaml`; one QUICK_MODE run attached to the PR.

**Step 4 — tests, mostly free**: structural suites auto-parametrize over `all_types()` (registry coverage, phase-map consistency, submit label taxonomy vs emulator seed — the latter only if `submit` ∈ stages). Provider writes only genuinely type-specific tests.

**Step 5 — exotic lifecycle stays theirs**: `strategy-pull`/`strategy-push`/`strategy-signoff`, RHAIRFE→RHAISTRAT cloning, `local/` workspace, `cf[10855]`, locking — **companion skills in strat-creator** consuming the declared-stable script CLIs (§2.8). No core PR needed; promotion to descriptor v2 only on a second requester.

**Governance, stated accurately [panel fix — no CODEOWNERS overstatement]:** CODEOWNERS maps `types/strategy/**` to the strat team, which *routes* review to them; repo policy still applies. The practical contract: descriptor-content changes need type-owner review + CI green, and core commits to not gating them beyond that. Ship cadence is rfe-creator's cadence — acceptable because all pipelines pin by branch/image anyway. Distribution: merge → daily image rebuild + branch promotion; `/rfe.create --type strategy` and the generated `/strategy-*` aliases work everywhere the plugin/clone is installed.

---

## 5. CI pipeline impact & migration (outcome C)

Auto-detection never runs interactively in CI: every headless invocation carries `--type`, a deterministic signal, or the grandfathered rfe default; the only interactive affordance (AskUserQuestion in create) is unreachable under `--headless` and linted for (§2.5).

### 5.1 rfe-autofixer — zero required changes

`CLAUDE_PROMPT: '/rfe.auto-fix --announce-complete …'` (.gitlab-ci.yml:75) is unchanged — same name, now the generic body; the JQL `project = RHAIRFE` resolves rfe deterministically anyway. Host-side steps unchanged: `submit.py --auto-approve` (default `--type rfe`), `pipeline_state.py get start_time`, the `grep -q "No RFE task files found"` whitelist (lines 88/117/161/186/220 — literal kept byte-stable for rfe forever). Two additions, own MRs, no deadline **[grafted]**: (a) `submit.py` also emits a stable machine marker `RESULT: NO_TASKS` for all types — future typed jobs grep the marker, never per-type prose; (b) port the assessor's green-run guard: host-side check that `tmp/pipeline-state.yaml` phase == DONE before `submit.py --auto-approve` — the autofixer is the only pipeline where a text-only stop currently flows into real Jira writes. A future `autofix-initiative` job is mechanical (`/rfe.auto-fix --type initiative --jql "project = RHOAIENG AND issuetype = Initiative"`, `submit.py --type initiative`, own `resource_group`) and en route fixes two latent bugs: the `auto-fix-runs/[0-9]*.yaml` REPORT_TS grep missing `initiative-run-*` reports, and `restore-artifacts.sh`'s hardcoded `rfe-tasks`.

### 5.2 rfe-assessor — untouched

It consumes the assess-rfe plugin directly (`/assess-rfe:assess-rfe RHAIRFE-*`) and never touches rfe-creator. Only ask: tag assess-rfe releases so descriptors can pin `rubric.ref`. Its own `assessments/RHAIRFE/current` hardcodings (runner.py:92-135, push scripts) are orthogonal debt for a future initiative-assessor job.

### 5.3 rfe-creator-eval — self-migrating

The pipeline reads `skill:` from the PR's own config (`_read_eval_skill`, runner.py:83-85) — rename-proof by construction. PR-6 flips `eval-initiative.yaml` to `skill: rfe.speedrun` + `--type initiative` (decision rationale §0.1); both types then share `eval/runs/rfe.speedrun/` (runner.py:514 derives the path from the skill name) with distinct MLflow experiments — documented, one-time re-baseline budgeted. **Mandatory follow-up MR to the eval repo [grafted, promoted from optional]:** `jira_cases.py` reads JQL + annotations schema from `types/<t>/type.yaml` in the PR checkout it already clones, killing the hand-synced `TYPES` map (:40-49), the annotations table (:206-230), and the filename sniff (:98-100). `EVAL_CONFIG` option lists (GH workflow + GitLab variables) stay a documented 2-line step per new type. Eval mode is where `settings.json` is enforced: the three new scripts get allow entries **in the same PR** that introduces them, and the stale entries (`initiative_batch_summary.py`, flat `.context/assess-rfe/scripts/*.py`) are purged in PR-1.

### 5.4 Zero-downtime sequence

No production prompt string ever changes. Land PRs on main → promote `ci-stage` → run the existing manual `autofix-rfe-stage-dry` job against the **unchanged** prompt → promote `ci-prod` → watch one scheduled prod run. Assessor: no action. Eval: follows each PR automatically. There is no alias-retirement step — aliases are permanent (§0.1).

---

## 6. Relationship to the assess-rfe plugin

The assess side's type axis must align with the descriptor, without forcing a repo merge:

1. **Descriptors point at assess assets by pinned ref** (`rubric: {repo, ref, path}` + `scorer_agent`), tolerating both current packaging conventions (PR #10 folds initiative *into* assess-rfe; assess-strat is a separate plugin with a diverged layout). **Decision needed before a third rubric ships**: converge on one convention — recommended: per-skill `skills/assess-<t>/scripts/agent_prompt.md` layout inside assess-rfe, since bootstrap validation and `PIPELINE_TYPES` already assume it.
2. **`bootstrap-assess-rfe.sh` becomes descriptor-driven**: clone `rubric.repo` at `rubric.ref`, validate `rubric.path` + scorer agent exist — replacing its hand-mirrored per-type case blocks and retiring the `tests/test_bootstrap_assess.py` "duplication is fine, silent divergence is not" pins.
3. **Scheduled convergence [grafted, promoted from "recommended"]**: assess-rfe's `agent_types.py` `AGENT_TYPES` project→scorer map is generated from (or pinned by a shared test to) the descriptors, and result files gain an explicit type marker — collapsing PR #10's four coexisting detection mechanisms (skill choice, project map with silent rfe default, criterion sniffing, CSV-header sniffing) to one.
4. **`export_rubric.py` generalized** per descriptor `rubric.export` (today it hardcodes `artifacts/rfe-rubric.md`; `initiative-create` never sees its rubric — a real create-quality asymmetry this fixes).
5. **Provenance**: the rubric checkout SHA stamped into review frontmatter (§2.6) makes rubric iteration attributable — the right_sized calibration gap lives entirely in rubric text, so this is the quality lever that most needs version history.

Recommendation (not required): assess-rfe eventually adopts the same `types/`-shaped axis internally, so both repos share one descriptor vocabulary.

---

## 7. Ordered migration plan (PR-sized, each shippable)

Migration mechanic throughout **[grafted from layered-shim]**: the descriptor becomes source of truth **by test before by import**.

1. **PR-1 — registry, inert + provider surface.** `types/{rfe,initiative}/type.yaml` + `_schema/` + `type_registry.py` + `validate_types.py` + **pin tests asserting descriptor projections == every existing dict** (`PIPELINE_TYPES`, `TYPE_CONFIGS`, `SPLIT_CONFIG`, `SNAPSHOT_CONFIG`, all `_TYPE_CONFIG`s, `PHASE_CHECKS`, `SCHEMAS`, both eval yamls' criteria lists). Also: settings.json cleanup + new allow entries; `docs/type-provider-guide.md` + skeleton + validate action; CODEOWNERS; the `RFE_CREATOR_EXTRA_TYPES` CI test; fix `rfe-creator.update-deps`. Zero runtime change.
2. **PR-2 series — scripts adopt the registry, one or few per PR**, pin test shrinking as each private dict dies; argparse `choices` from registry; prefix-sniffers → `detect()`; `_check_condition` reads `condition:` data; the third-copy scan/rename/parse families (`artifact_utils.py:748-1052`) collapse onto the generic seams. `submit.py`/`split_submit.py` go **last**, gated on running the jira-emulator integration suites in CI (today excluded by `-k "not integration"`, lint.yml:48).
3. **PR-3 — detection + self-describing artifacts.** Frontmatter `type:` field (write-on-create/fetch, prefix/path fallback), `resolve` ladder incl. the grandfathered headless default + conflict hard-fail, batch `type:` key in `validate_batch_input.py`.
4. **PR-4 — genericize the six skill bodies + permanent aliases.** Type content moves to `types/<t>/`; dimension skills → dimension files; `PIPELINE_TYPES` view paths, `check_review_progress`/`verify_phase` maps, bootstrap validation and test pins updated in lockstep; speedrun's internal `Skill(skill: "rfe.auto-fix")` gains `--type {TYPE}`; drift fixes (§3.4); orphaned strat skills parked/deleted. Gated on `autofix-rfe-stage-dry` (old prompt) + one eval run before `ci-stage` promotion.
5. **PR-5 — calibrated classification UX** + the AskUserQuestion lint in `validate_types.py`.
6. **PR-6 — eval single-sourcing.** `generate_eval_config.py` + skeleton + per-type `eval/fragment.yaml`; regenerate both eval yamls with the 4 shipped judge drifts either given explicit descriptor homes or deliberately reverted; flip `eval-initiative.yaml` to `skill: rfe.speedrun --type initiative`; fresh baselines for both types.
7. **PR-7 — provenance + SETUP fail-fast.** Pinned `rubric.ref` honored by the descriptor-driven bootstrap; `validate_types.py --verify` in pipeline SETUP; `rubric_version`/`type_version` stamped into review frontmatter; assess-rfe tags a release (coordination, no layout change).
8. **PR-8 — ecosystem MRs (no deadline, independently revertible):** autofixer `RESULT: NO_TASKS` + green-run guard; eval-repo `jira_cases.py` descriptor reads; skills-registry `provides: request-type`; assess-rfe `agent_types.py` convergence; README/AGENTS.md rewrite.

Kept byte-stable throughout: artifact dir names, `FULL RUN COMPLETE`, `"No RFE task files found"`, `tmp/rfe-assess/single/` staging, `scripts/` paths, plugin names, all CI prompt strings.

---

## 8. Risks & open questions

1. **Q1 — Naming (maintainer decision, explicitly flagged).** `rfe.*`-as-generic is ops-optimal and effectively irreversible once a third type ships under it. The permanent per-type alias families remove the human-facing cost, but if the maintainers want neutral branding, a generated `request.*` alias layer over the same bodies costs ~50 lines and zero CI changes — decide before PR-4.
2. **Two-data-point overfit — likely hotspots.** The `condition:` vocabulary (3 operators), `split.trigger`, the dimension model, `index`/`companions` booleans, and the grandfathered empty prefixes are all calibrated on rfe+initiative variance. **Mitigation: prototype `types/strategy/type.yaml` during PR-1's bridge phase as the validating third data point before freezing `schema_version: 1`**, even if the type doesn't ship. The second-requester promotion rule (§2.8) guards against speculative schema growth.
3. **Governance bottleneck.** Type providers ship at rfe-creator's cadence and through its review. Accepted: pipelines pin by branch/image so independent cadence is largely illusory today; the companion-skills doctrine gives providers real autonomy for everything outside the shared engine; extraction to out-of-repo packaging is a transport PR when a second external team arrives (the `RFE_CREATOR_EXTRA_TYPES` test keeps that seam honest). Watch for the named failure mode: "temporary" in-repo types accreting with nobody funding extraction.
4. **Barrier semantics as a hard contract.** Numeric `score` + the error-stub shape is mandatory; types with non-numeric review (pure human sign-off, docs requests) must model as `stages: [create, review]` with a numeric rubric, or wait for a core-level stage extension. The deferred durable barrier fix (max-wait → retry_cycle escalation) should accompany PR-2 — a third type multiplies exposure to the known model-agnostic deadlock family.
5. **RHOAIENG ambiguity.** `(project, issuetype)` verification happens post-fetch; a headless run over a curated JQL is safe, but a mis-keyed Epic fails into an error stub rather than pre-flight rejection. Acceptable; a `jql_query` pre-flight is a later nicety.
6. **Eval cost scales with N types.** Each type owes a ≥16-case dataset and full runs up to the $100 budget cap; PR-6 forces one-time re-baselining. QUICK_MODE gates PRs; full runs stay scheduled/manual; `type_version`/`rubric_version` stamping makes regressions attributable so full runs can be on-demand.
7. **Headless/CI hazards.** Guarded by: resolution in scripts with exit codes; the AskUserQuestion lint; allowlist entries shipped in the same PR as any new script; no relocation of `scripts/`. The autofixer's missing text-only-stop guard is fixed by the PR-8 green-run MR, not papered over by the generic layer.
8. **Shared-workspace concurrency.** `tmp/pipeline-state.yaml` and `tmp/rfe-assess/single/` stay shared; one typed run per workspace remains a documented invariant. Namespacing state per type is out of scope.
9. **Descriptor creep.** The `condition:` vocabulary is closed; anything richer is a named hook in core Python with tests. The single-repo model gives exactly one reviewer gate for schema changes.
10. **Q2 — Who owns `types/initiative/` quality?** Initiatives have no production CI consumer today (zero references in autofixer/assessor). A contract without a consumer rots regardless of architecture — assign an owner or schedule the first initiative CI job.
11. **Q3 — assess packaging convention** (§6.1): one convention must be chosen before a third rubric ships; currently two compete.
