# Work Item Types: Unified Design

**Status:** Proposed — synthesis of the two prior proposals, intended to supersede `request-type-extensibility.md` (RTE) and the mechanism sections (§4) of `adr-pluggable-work-item-types.md` (ADR). The ADR's context, extension-point inventory, and product decisions (§§1–3, 5) are incorporated by reference and remain the decision record.
**Date:** 2026-08-12
**Baseline:** merged main `fbf96bb` (2026-09-03; originally drafted against `8888b18`, post PR #143 — see `work-item-types-alignment-review.md` §1 for the citation refresh; PRs #148 (`446ff58`), #149 (`1781dae`) and #153 (`f89444a`) merged afterwards — `check_review_progress.py`, `rfe.speedrun/SKILL.md`, `AGENTS.md`, `artifact_utils.py`, `collect_recommendations.py`, both review-agent prompts, the three initiative skill files, tests — accounted for in §10; main is `f89444a`), assess-rfe PR #10 (`6602a12`, still open)
**Inputs:** ADR (incl. review Appendix A), RTE, `extensibility-proposals-comparison.md` (execution-verified; its §5 errata applied here), maintainer ruling that `initiative-*` skill names need **no** backward compatibility (zero users). This revision additionally incorporates a three-lens adversarial review of the first draft (code-grounding, implementability, parent-fidelity — 40 findings applied), and the 2026-09-03 pre-implementation alignment review (`work-item-types-alignment-review.md`: rfe-creator delta, open PRs, strat-creator, epic-creator, cross-repo core — 70 findings, 27 edits applied).

---

## 0. Executive summary

Every work item type is a declarative directory `types/<type>/` in rfe-creator — one `type.yaml` descriptor plus the genuinely typed content (template, questionnaire guidance, per-stage rule fragments, review dimensions, eval fragment, emulator seed). A single loader, `scripts/type_registry.py`, replaces the 17 per-type dicts, 2 additional registries, ~7 prefix-sniff branches, and 3 forked function pairs that today restate type facts across 22 scripts. The generic type-agnostic entry points — **one intake** — land under new spec-compliant names, **`rfe-*`** (`rfe-create`, `rfe-review`, `rfe-submit`, `rfe-split`, `rfe-auto-fix`, `rfe-speedrun`): the existing dot names are not legal under the agent-skills spec, so they stay behind as thin backward-compatibility shims pointing at the new bodies (§4.4). Calibrated classification lives in `create`, deterministic script-side resolution everywhere else, and `--type` always wins (the CI path). The `initiative-*` skill family is **deleted, not aliased**. The assess side keeps its repo name and layout but collapses to **one type-agnostic scorer** and gains pinned, provenance-stamped rubric references.

Three properties distinguish this design from both parents:

1. **Type identity is `(jira.project, jira.issue_type)`, and key prefixes are lists.** This makes the descriptor schema *flip-ready from day 1*: the ADR's RHAISTRAT destination flip — its central strategic decision — becomes a descriptor edit **plus the pre-listed §9 sweep and external confirmations**, instead of the data-model breakage the comparison verified (43 literal prefix-predicate lines in 11 scripts at fbf96bb — 31 at 8888b18 — duplicate-create in `submit.py:749`, silent lookup misses, plus `check_conflicts.py:76` and the report-side inverse predicates that neither parent listed). RTE as written could not even *represent* the flipped world (its unique-prefix lint); the ADR as written shipped the flip before its discriminator. Here the discriminator (self-describing artifacts + the resolution ladder) is core infrastructure, and the flip is severable and **unscheduled**: the implementation (§10) is transparent to production — no binding, project, issue type, CI prompt, or artifact changes — and the flip remains the ADR owner's separate decision (§9).
2. **The issue tracker is an adapter behind a ~20-operation seam, and the descriptor's identity block is tracker-discriminated.** Jira is the only adapter today; the requirement that types may eventually bind to other trackers — GitHub Issues first — is absorbed the same way as the flip: *schema-ready at v1* (identity as a `tracker:`-discriminated union, a neutral `tracker_ref` frontmatter field, tracker-scoped conventions and priority vocabulary), *adapter built on the first GitHub-backed type* (§3.6). The transactional write sequences, the recovery-from-remote-reads invariant, and the snapshot hash discipline are restated tracker-neutrally; ADF, JQL, workflow transitions, and Jira key grammar become Jira-adapter internals.
3. **The skill layer composes at runtime from shared skeletons, registry-supplied tokens, and per-type judgement files — no prompt codegen, no additive-only overrides.** Additive-only died on the verified review-agent polarity conflict (an addition cannot retract the RFE split rule that the initiative rule contradicts). Generated-flat would introduce the ecosystem's first prompt-codegen pipeline to reproduce what the scripts' dispatch already achieves. The composition mechanism used here — the launcher supplies file paths and pre-rendered variables, the subagent reads them — is byte-for-byte the `{PROMPT_PATH}`/`vars` idiom the pipeline already exercises in all 13 agent phases and hardened deliberately after the 36-agent degradation incident (`plan-a-thin-dispatcher.md`). (One deliberate codegen exception exists — eval *configs*, not prompts — see §4.5.)

**CI impact: zero required changes to production pipelines.** The autofixer's `CLAUDE_PROMPT: '/rfe.auto-fix …'` keeps resolving forever through the legacy compat shim; the assessor never touches rfe-creator; the eval pipeline reads the skill name from each PR's own config and flips with it. An *optional* one-line autofixer MR moves the prompt to `/rfe-auto-fix` to take the shim hop off the production hot path (§4.4, PR-10). Promotion follows the standard zero-downtime sequence (§10 preamble).

**Transparent implementation (maintainer ruling 2026-09-03).** Every implementation PR (§10) leaves production exactly as it is: RFEs stay bound to `(RHAIRFE, Feature Request)`, initiatives to `(RHOAIENG, Initiative)`; no Jira project, issue type, key prefix, label value, CI prompt string, or existing artifact changes. The RHAISTRAT flip is **not part of the implementation** — §9 keeps it as target-state analysis for whenever the ADR owner schedules it, and the identity collision with strat-creator (D6) is a gate for *that* decision only, never for a §10 PR. Flip-readiness in the schema (prefix lists, tracker-discriminated identity, `tracker_ref`) is kept because it costs nothing and is independently justified by the deployment-binding override (§3.2.1) and post-fetch verification (RHOAIENG already mixes Epics, Stories and Initiatives).

**Scope.** This design eliminates the drift surface *within* rfe-creator. The same disease exists across rfe-creator, strat-creator and epic-creator (§1.1) and is explicitly out of scope: the cross-repo core is the named follow-up ADR `creator-core` (§3.5, D9); what this design does for it is keep `type_registry.py` and the tracker adapter import-clean so they can be extracted. strat-creator and epic-creator are **not** types of this pipeline — they are pipelines of a different *kind* (§3.7, D6, D9).

### 0.1 Provenance ledger — what came from where

| Element | Source | Modification here |
|---|---|---|
| One intake / many destinations; "RFE is a binding, not a concept"; classification as an extension point | ADR §1.1 | Adopted as target state; classification staged (§6) |
| 10-extension-point inventory; "the diff between two descriptors is the contract" | ADR §3, §4.1 | Adopted as the schema acceptance test (§3.4) |
| DoR template + rubric re-derivation for initiatives | ADR §5 | Adopted as rubric track v2 with explicit PR #10 disposition; **swap sequenced after the skill collapse** so there is exactly one re-baseline and no floating-rubric window (§7.5) |
| One type-agnostic scorer | ADR §4.4 | Adopted, as a *coordinated* cross-repo change; repo rename **dropped** (breaks 4 assessor CI jobs — verified) |
| Dry-run flip gate; identifier work in phase 1; live-Jira confirmation before the flip; rollback plans; a defined eval bar | ADR A.6, §8.1, A.5 | Identifier work, rollback plans and the eval bar adopted into §10; the flip gates kept in §9 as target-state analysis — the flip itself is unscheduled (maintainer ruling 2026-09-03) |
| `types/<t>/` layout, descriptor schema, `type_registry.py`, three validation gates, data-only rule, discovery seam + extraction path, eval-config generator, provider obligations floor, zero-downtime promotion | RTE §2, §4, §5.4 | Identity re-keyed on `(project, issue_type)`; prefixes become lists (§3.2); `scorer_agent` retained until the collapse |
| Resolution ladder; frontmatter `type:`; batch `type:`; grandfathered headless rfe default; interactive-affordance lint | RTE §3.2, §2.5 | `detect()` becomes multi-candidate; `tracker_ref`/`is_existing` added; interactive fallthrough rung added (§5) |
| Pin-test migration; pinned `rubric.ref` + provenance stamping; SETUP fail-fast; companion-skills doctrine; second-requester rule | RTE §2.5–2.8, §7 | Pin-honoring moved early (PR-2) to close the floating-rubric window |
| Skeletons + tokens + per-type judgement files | New (synthesis of ADR A.2's one-authored-skeleton intent + RTE's runtime mechanics + the verified `{PROMPT_PATH}`/`vars` precedent), re-scoped after implementability review | §4.2 |
| Classification-as-signal intermediate; corpus/accuracy-bar gate for routing authority | ADR §4.3 + A.5, made implementable per the verified init/FETCH sequencing constraint | §6 |
| `initiative-*` deleted; generic entry points named `rfe-*` (dot names violate the agent-skills spec); legacy `rfe.*` kept as compat shims; no alias families for new types | Maintainer rulings 2026-08-12 | §4.4 |
| Flip prerequisite checklist incl. `check_conflicts.py:76`, `generate_review_pdf.py:444`/`generate_run_report.py:104` inverse predicates, issuetype-not-fetched gap | Verification (comparison §3.2) | §9 |
| Tracker abstraction: identity union + adapter seam + `tracker_ref`; GitHub adapter deferred to first requester | Maintainer requirement 2026-08-12 + 3-lens coupling inventory (execution-verified) | §3.6 |
| Open `labels` map (quarantine + split-child marker), `local_id_pattern`, reserved `kind`/`inputs`/`produces`, deployment-binding override (supersedes PR #130/#122), packaging stance (closes PR #115), engine-vs-phase-table scoping, D6 identity collision, `creator-core` follow-up | 2026-09-03 alignment review (5 lenses, execution-verified) | §3.2, §3.2.1, §3.5.1, §3.7, §9, §11 |

---

## 1. Current state (brief; measured)

PR #143 delivered the second type by mirroring. The measured cost, all figures from the execution-verified comparison:

- **1,595 lines** of initiative-side skill surface; the 5 dispatch SKILL.md pairs are ~70–99% identical after token normalization (auto-fix 97–99%, submit ~70–73%); `assess-agent.md` is 100% identical; judgement content is genuinely typed (templates ~17% identical, rubrics 41%, revise 39%).
- **17 module-level type-keyed dicts** (one per script) + `artifact_utils.SCHEMAS` + `check_review_progress.PHASE_CHECKS`, **22 scripts** accepting `--type`, ~7 prefix-sniff branches, and 3 forked function pairs in `artifact_utils.py:778-1101` (`scan_task_files`, `rename_to_jira_key`, `parse_child_artifact` and their initiative twins; `scan_review_files` stays deliberately unforked — its only caller is `rebuild_index`, and the initiative index is disabled).
- **The copies already rot**: the pre-#5 rubric path survives in three rfe-side files while the initiative twins use the new one; the initiative eval config silently dropped the rm-artifacts check; the initiative side contradicts *itself* on split rules (review permits split only at `right_sized=0`; the split agent proceeds at 1/2); the export-rubric skill carries a third rubric copy that has already drifted.

Success metric (unchanged from both parents): **eliminate the drift surface**. Every shipped drift bug lives in copied skeleton or hand-restated type facts; the typed content that is real authorship is supposed to differ and stays per-type.

### 1.1 The same disease across repos (measured, origin/main of each, 2026-09-03)

| file | rfe / strat / epic lines | rfe~strat | rfe~epic | strat~epic | divergence |
|---|---|---|---|---|---|
| `jira_utils.py` | 838 / 1200 / 870 | 0.62 | 0.75 | 0.70 | 31 common functions, 27 byte-identical after AST normalisation; 15 strat-only, 3 rfe-only |
| `artifact_utils.py` | 1101 / 946 / 513 | 0.72 | 0.34 | 0.43 | SCHEMAS = 23% / 23% / 45% of each copy |
| `frontmatter.py` | 279 / 281 / 272 | 0.84 | 0.73 | 0.86 | per-type path table only |
| `state.py` | 210 / 185 / 173 | 0.94 | 0.90 | 0.97 | strict subsets; `clean` removed only in epic |
| `pipeline_state.py` | 1343 / — / 865 | — | 0.49 | — | `advance()` 0.27; engine functions 0.8–1.0 |

strat-creator vendored six scripts from rfe-creator on 2026-04-11 and never re-synced; since `8888b18` rfe landed 15 commits on shared files, strat 1, epic 0. One-sided fixes: the certifi SSL context is missing in epic; the same-origin redirect policy exists only in strat; atomic `swap_labels` only in rfe; `state.py clean` (`rmtree tmp/`) is still called by rfe (`rfe.speedrun/SKILL.md:23`) and strat after epic removed it for wiping `tmp/` under subagents. This is out of scope here and owned by the `creator-core` follow-up (D9); the parity sweep (§10 PR-10) starts in parallel with PR-1.

---

## 2. Architecture at a glance

```
rfe-creator/
  types/
    _schema/type.schema.json          # JSON Schema; schema_version gate
    rfe/                              # descriptor directory (data only — no executable code, ever)
      type.yaml
      template.md
      prompts/{create-guidance,review-rules,review-sections,revise-rules,split-rules}.md
      dimensions/feasibility.md
      eval/{fragment.yaml,pairwise-judge.md}
      jira-emulator-seed.yaml
    initiative/                       # same shape; full worked example in §8
  scripts/
    type_registry.py                  # the ONLY import point for type data
    validate_types.py                 # 3 gates: lint / cross-repo / pipeline SETUP
    generate_eval_config.py           # the one codegen exception: eval CONFIGS (§4.5)
  .claude/skills/
    rfe-create|rfe-review|rfe-submit|rfe-split|rfe-auto-fix|rfe-speedrun
                                      # type-generic bodies (one intake; new spec-compliant names — §4.4)
    rfe.create|rfe.review|...         # legacy names kept as ~8-line backward-compat shims (§4.4)
    rfe-review/prompts/*.md           # stage SKELETONS (shared); typed judgement in types/<t>/
```

Data flow: skills and `pipeline_state.py` never restate type facts — they ask the registry; the registry reads descriptors; descriptors point at typed files; subagents read the files and variables the scripts hand them. The wave/barrier/resume/dispatch **engine**, snapshot invariants, numeric scoring contract, and the review/split submit transaction are **machinery, not extension points** for work-item types. The **phase table** (`PHASES`, `PHASE_CONFIG`, `advance()`, `PHASE_CHECKS`, cycle caps) is per-pipeline-*kind* data: rfe and initiative share the 31-phase review/split graph, so within this repo it is not a type extension point — but epic-creator's production pipeline needed a 13-phase decompose graph and forked `pipeline_state.py` to get it (`advance()` 0.27 similar, engine functions 0.8–1.0 similar). The descriptor reserves `kind:` (§3.2) and PR-10 records the engine/graph split so a second kind can consume the engine instead of forking it; R2's wait-for-wave escalation is implemented in engine functions for the same reason.

---

## 3. The extension contract

### 3.1 What a type provides

Exactly the ADR's ten extension points, plus the two the verification showed were missing homes: **identity** (beyond a prefix) and **eval thresholds**. A type is: one descriptor + one template + one questionnaire-guidance file + per-stage judgement files + dimension prompts + one rubric (assess side) + one eval dataset & fragment + one emulator seed.

### 3.2 The descriptor

Schema highlights (full worked example in §8.2; every field traced to a consuming script):

```yaml
schema_version: 1
type: <name>
display: { entity: ..., entity_plural: ... }
kind: work-item                  # RESERVED. Pipeline kind — selects the engine's phase table. v1 accepts only work-item (the
                                 #   review/split graph rfe and initiative share). strategy / decomposition are recorded for the
                                 #   sibling pipelines (strat-creator, epic-creator) so v1 descriptors need no migration; no consumer here.
inputs: []                       # RESERVED, optional. How items of this type are DERIVED from an upstream type — schema in §3.7.
                                 #   Two requesters exist today (strat-creator ← rfe via Cloners; epic-creator ← strategy via parent),
                                 #   which satisfies the second-requester rule for the SCHEMA; rfe-creator ships NO gate evaluator.
produces: []                     # RESERVED, optional. Downstream types + the label this pipeline sets on the input when done.

identity:                        # ← the flip- AND tracker-ready core (differs from both parents)
  tracker: jira                  # discriminator; exactly one binding block below (JSON-Schema oneOf) — §3.6
  jira:
    project: RHOAIENG            # (project, issue_type) IS the type identity under the jira tracker;
    issue_type: Initiative       #   the cross-type lint enforces per-tracker BINDING uniqueness
    key_prefixes: [RHOAIENG-]    # Jira key grammar (binding-owned): first entry is the write prefix,
                                 #   all entries are read/detect; token grammars are adapter-derived
    split_link_type: "Work item split"   # tracker-mechanical residue lives in the binding
    duplicate_link_type: { name: Duplicate, outward: duplicates, inward: "is duplicated by" }   # PR #133 (merge/dupes stages)
    state_map: { approved: Approved, close_superseded: { transition: Closed, resolution: Obsolete },
                 close_duplicate: { transition: Closed, resolution: Duplicate }, ready?: "To Do" }   # §3.4 workflow-states note
  local_prefix: INIT-            # write prefix for new local ids (next_rfe_id --prefix); derived convenience — tracker-neutral
  local_id_pattern: '^INIT-\\d+$' # AUTHORITATIVE local-id grammar (anchored regex). detect() full-matches against every type's
                                 #   local_id_pattern BEFORE any tracker key grammar and prefers the most specific match, so a composite
                                 #   sibling id such as RHAISTRAT-1234-E001 can never prefix-resolve to a RHAISTRAT- type. Also the
                                 #   source of the base-schema `local_id` pattern (today literal at artifact_utils.py:73,:120,:199,:237)
  id_field: initiative_id        # frontmatter field name

classification: { summary, signals[], counter_signals[], driver }
                                 # consumed by interactive create (§6 stage 1)
                                 #   and by the Stage-2 mis-filed report signal (§6 stage 2)

dirs: { tasks, originals, reviews, dupes?, merges? }   # dupes/merges: PR #133's rfe-dupes / rfe-merge stages
index: { enabled: bool }                     # artifacts/rfes.md analog; rebuild_index runs only when true
companions: { comments: bool, removed_context: bool, extra_suffixes: [] }   # e.g. a decompose kind lists -decomposition, -ai-signals

conventions:                     # tracker-NEUTRAL (labels are concepts on every tracker; renamed
  type_label / label_prefix      #   from the earlier draft's jira_conventions — §3.6)
  labels:                        # OPEN map. Reserved keys get a label_prefix-derived default; types may add keys.
    rubric_pass: <prefix>-autofix-rubric-pass   # jql_query.py default wrapper (:79-90) excludes it; ALSO the EXPORTED
                                                #   readiness signal downstream types gate on (§3.7) — a published contract
    ignore: <prefix>-ignore                     # jql_query default wrapper AND snapshot/bootstrap hard filter
    split_quarantine: <prefix>-split-quarantine # snapshot_fetch.py:371-379 / bootstrap_snapshot.py:457-461 hard filter ONLY
                                                #   (jql_query does not exclude it); today defined TWICE — SNAPSHOT_CONFIG
                                                #   .quarantine_label (:59,:64) and composed at submit.py:283 — the drift class
    split_child_marker: "<prefix>-split-child-{parent}-{child_id}-{fingerprint}"   # creation-time recovery marker,
                                                #   split_submit.py:233-253; a TEMPLATE, not a value
    needs_attention: <prefix>-needs-attention
    feasibility: { feasible, infeasible, indeterminate }
    alignment:   { strong, partial, weak }      # only when the alignment dimension is declared
    # reserved for other kinds/pipelines, no rfe-creator consumer: processing, human_sign_off, auto_created,
    #   auto_revised, templates: [{from_field, pattern}]
  comment_prefix / removed_context_preamble     # removed_context_preamble is a PUBLISHED CONTRACT (strat-creator's refine
                                                #   step string-matches it) — frozen, §3.4 / R7
  query_default: '...'           # replaces jql_default — the query LANGUAGE is owned by identity.tracker
  parent_key_patterns: [...]     # becomes authoritative for BOTH the task schema and the batch
                                 #   validator (today they disagree: artifact_utils.py:219 accepts
                                 #   INIT- parents, validate_batch_input.py:36 does not — deliberate
                                 #   reconciliation, batch validator adopts the descriptor list)

schema:
  task.extra_fields: {...}                   # e.g. rfe adds size: {enum: [S,M,L,XL]}; nested dict / list-of-dict specs with
                                             #   enum/min/max are supported (frontmatter.py set takes dot paths — absorb #100's _deep_set)
  task.priority: { enum, map_to_tracker }    # RESERVED; today's Blocker..Undefined enum lives in the Jira binding (§3.4)
  review.score_fields: [...]                 # rubric criteria — single source for the 7 restatement sites
  review.extra_fields / extra_rules: [...]
  review.extra_scores: [...]                 # optional, nullable, NOT rubric criteria, excluded from the total (e.g. jtbd_alignment, PR #100)

pipeline:
  stages: [create, review, submit, split, auto-fix, speedrun]   # OPEN list validated against registered generic skills;
                                             #   a type may ship fewer; PR #133 adds dupes, merge
  context_sources:                           # declared bootstrap steps the SETUP phase runs from the registry
    - { name: assess-rubric, bootstrap: scripts/bootstrap-assess-rfe.sh, required: true }     #   (today hardcoded, pipeline_state.py:223-228)
    - { name: architecture-context, bootstrap: scripts/fetch-architecture-context.sh, required: false }
  poll_prefix / state_prefix                 # "<type>-"; rfe grandfathers ""
  scorer_agent: <name>                       # subagent for ASSESS-family phases (pipeline_state.py:98/:110);
                                             #   per-type until the one-scorer collapse (§7.2), shared after
  prompts:                                   # per-type judgement files consumed via launcher vars (§4.2)
    create_guidance / review_rules / review_sections / revise_rules / split_rules
  dimensions: [ {name, prompt, blocking, condition?: {frontmatter_field, prefix} | {context_exists: <path>}, setup?, skip_stub?} ]
                                             #   context_exists + setup: PR #100's JTBD dimension (.context/jtbd-registry/index.yaml)
  rubric: { repo, ref, path, export }        # PINNED (tag/SHA); bootstrap honors it from PR-2 (§10)
  resplit: { score_field: right_sized, below: <n> }
                                             # parameterizes check_right_sized.py:52's RESPLIT-children
                                             #   threshold (a script consumer — NOT the review-time
                                             #   split-recommendation rule, which lives in review_rules)

batch: { extra_fields: [...] }
snapshot: { prefix, report_prefix }          # cross-type prefix-collision lint
reporting:
  item_key
  criterion_labels / criterion_short_labels / before_score_name_map
  pdf.extra_fields: [...]                    # generate_review_pdf REPORT_CONFIG.extra_fields (rfe [] / initiative [alignment])
  run_report.extra_entry_fields: [...]       # generate_run_report TYPE_CONFIG (rfe [needs_attention] / initiative [alignment, feasibility, needs_attention])
                                             # tasks_dir derives from dirs.tasks; report_title from display.entity; output filename
                                             #   from snapshot.report_prefix (consumers: generate_run_report, submit.py:322-331)
eval: { config, dataset, mlflow_experiment, thresholds, timeout, annotations_extra }
                                             # eval.thresholds here is AUTHORITATIVE; eval/fragment.yaml
                                             #   carries judge prose/deltas only (§4.5)
```

**Changes vs RTE's schema, forced by verification and the tracker requirement:** the `identity` block replaces `ids` as a tracker-discriminated union (binding-keyed identity, prefix lists — so the RHAISTRAT flip is representable: post-flip both types carry `key_prefixes: [RHAISTRAT-, <legacy>-]` and remain lint-valid because their `issue_type`s differ — and a GitHub binding slots in as a schema-internal `oneOf` branch, not a schema-version bump); a shared, tracker-neutral `tracker_ref` frontmatter field is added to the base schemas (§5 — the name already exists on main's run-report entries with these semantics, `generate_run_report.py:212-262`; frontmatter carries it from PR-1 and renaming it later would mean migrating every artifact); the alignment condition uses the closed vocabulary *today* (`{frontmatter_field: parent_key, prefix: RHAISTRAT-}`) and is pre-declared to migrate to the named hook `parent_is_outcome` at flip time, because the closed vocabulary provably cannot express an issue-type test (§9).

**Changes vs the ADR's sketch:** `also_reads` is subsumed by `key_prefixes` lists on *both* types with defined semantics (every read predicate consults the union via the registry; the write prefix is the list head) — the ADR's version was one-sided and semantics-free (verified: zero occurrences, no rfe-side coverage).

### 3.2.1 Deployment binding override (supersedes PR #130 / #122)

The descriptor's `identity.<tracker>` block is the **default** binding. `type_registry.py` computes an **effective** binding by overlaying an optional, explicit, type-scoped override: env `RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,LOCAL_PREFIX}` or a workspace `rfe-creator.yaml` (`bindings: {rfe: {jira: {project: KONFLUX, issue_type: "Feature Request"}}}`); a bare `JIRA_PROJECT`/`JIRA_ISSUE_TYPE` is accepted as shorthand for the *resolved* type only. Overridable fields are binding-only: `project`, `issue_type`, `key_prefixes` (write prefix derived as `<PROJECT>-`, descriptor prefixes kept as read prefixes), `query_default` (re-rendered by the adapter), link types, `state_map`, `parent_key_patterns`, optionally `local_prefix`. Never judgement content, dirs, schema, rubric, eval. Rules: (a) zero-config default preserved — no required env var (a required `JIRA_PROJECT` would break the autofixer and every existing user; an interactive "ask the user" gate in speedrun/auto-fix bodies is the headless-reachable affordance the §3.3 lint forbids); (b) the §3.3 uniqueness lint runs on *effective* bindings, plus: no type's `local_prefix` stem may equal any effective project key (the collision #122 feared — a lint, not a `DRAFT-` rename); (c) `resolve` prints the override: `TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)`; (d) the dry-run sentinel is binding-derived (`<PROJECT>-DRY`, replacing the `RHAIRFE-DRY` literal); (e) run reports and snapshot metadata record the effective binding; (f) the Jira adapter contributes a generic key grammar (`^[A-Z][A-Z0-9]+-\\d+$`) to `detect()` as a rung AFTER every type's `local_id_pattern` and descriptor `key_prefixes` — a provisional candidate discriminated post-fetch — so keys from an overridden project still resolve. **`DRAFT-` is not adopted at v1**: before frontmatter `type:` exists the local prefix is the only type carrier for local ids; a neutral shared local prefix becomes a per-deployment `local_prefix` override after PR-3, not a repo-wide rename. Lands as schema text + lint in PR-1, `resolve` output in PR-3. Absorbed from #130: `collect_recommendations.py --from-reviews` (cherry-pick), the `strip_metadata` heading regex rendered from the registry prefix union, doc genericization; rejected: required env, the single-project model (main has two `TYPE_CONFIGS`), `DRAFT-`.

### 3.3 Rules the contract enforces (validation gates)

From RTE §2.5, with additions (†):

1. **Lint-time** (`make lint` / CI): JSON-Schema; referenced files exist; `score_fields` non-empty and review schema accepts the error-stub shape (`verify_phase.py:106-127` — the wait-for-wave deadlock guard); cross-type invariants — **unique tracker binding per type† (jira: the `(project, issue_type)` pair; github later: the `(repo, kind)` pair)**, unique local prefixes, unique poll/state prefixes, snapshot prefixes mutually prefix-collision-free; generated eval configs in sync (regenerate + `git diff --exit-code`); no AskUserQuestion or interactive affordance in any skill body outside sections marked interactive-only (targets the headless text-only-stop family); †**a code lint rejecting new literal prefix predicates** (`startswith("RHAIRFE-"|"RHOAIENG-"|"RFE-"|"INIT-")` outside the registry — ADR A.1's anti-regression guard, so the migrated predicate class cannot be reintroduced). The lint's initial inventory at fbf96bb is 43 lines in 11 scripts (artifact_utils 21 incl. the `re.fullmatch` rename guards `:877-880,:934-937` and the `local_id` patterns; generate_run_report 6 incl. `tracker_prefix`/`local_prefix`; submit 4; generate_review_pdf 4; check_conflicts 2; filter_for_revision, jira_utils `:736`, pipeline_state `:142`, prep_assess, preserve_review_state, validate_batch_input 1 each) — every one is a PR-2 migration target, none is whitelisted. The lint also rejects literal snapshot/report prefixes (`issue-snapshot-`, `initiative-run-`) outside the registry (live instance: `bootstrap_snapshot.py:176`) and, in the interactive-affordance check, text-only headless terminations (the `initiative-split/SKILL.md:141` pattern — per PR #148: ending a turn to wait for work that cannot produce a notification ends the run). The uniqueness lint is evaluated on *effective* bindings (§3.2.1). The error-stub `frontmatter.py set` command in every generic body is generated from `schema.review.score_fields` (the same registry row `verify_phase.py:106-127` consumes).
2. **Cross-repo** (`--with-deps`, post-bootstrap): `rubric.path` exists at `rubric.ref`; the agent file named by `pipeline.scorer_agent` is present in the assess checkout. Replaces the hand-mirrored gates in `bootstrap-assess-rfe.sh` and retires the duplication pins in `tests/test_bootstrap_assess.py`.
3. **Pipeline SETUP** (runtime): `validate_types.py --verify --type <t>` hard-fails with a diagnosis before any agent wave.

### 3.4 Acceptance test, refusals, and provider obligations

The ADR's framing is the acceptance test: **the diff between `types/rfe/` and `types/initiative/` must consist of exactly the extension points and nothing else.** The contract refuses to customize: the phase graph and barrier semantics of the work-item kind (the engine/phase-table split is §2); numeric scoring; snapshot invariants; **the tool defining or enforcing a workflow — per-column (kanban) exit criteria and lifecycle beyond "ready" are a named future extension point and a separate decision** (ADR §8.3), out of scope here (see the workflow-states note below for what the tool *does* touch); CI magic strings (`FULL RUN COMPLETE`, `"No RFE task files found"` byte-stable for rfe); **priority vocabulary — fixed per tracker binding, not per type** (Jira supplies today's enum; a tracker without a priority field declares its degradation in the binding, §3.6); tracker mechanics beyond the binding's declared maps; the review/split submit transaction (a decompose kind has its own create-N → links → attachments → label-source sequence — out of scope). Script *locations* are no longer a refusal but a stated convention (§3.5.1). The approve policy (`state_map.approved` + descriptor labels) is implemented **once** and applied to split children too (today they never reach `_maybe_approve`, `submit.py:978-990` — PR #112's gap).

Escape hatches stay tiered: descriptor data → declarative rules → **companion skills in the provider's own repo** consuming core's declared-stable script CLIs (`frontmatter.py`, `state.py`, `next_rfe_id.py`, plus the per-tracker adapter CLI — `jira_utils.py` today; the declaration *is* the companion-skills contract, scoped per tracker and covering both the CLI **and** the module API — strat-creator imports `jira_utils` functions directly from six skill steps) → core PR; promotion into the schema only on the second requester. **Published cross-pipeline contracts (R7):** `conventions.labels.rubric_pass` values (strat-creator's intake gate, 86 occurrences), `conventions.removed_context_preamble` (string-matched by `strategy-refine/SKILL.md:123`), and the `{key}-strategy.md` attachment convention are consumed by downstream pipelines — frozen strings, changed only with a downstream migration note (§3.7). The provider floor below applies per *kind*; an engine consumer that brings its own gate (epic-creator has no eval dataset) negotiates a waiver rather than failing it.

**Provider obligations (the contract floor, normative):** a ≥16-case anonymized eval dataset (PII policy) including at least one sparse/adversarial case; populated type-specific `expected_*` annotations (prospective — no judge consumes `{{ annotations }}` today, but new-type judges are written to); explicit `eval.thresholds`; the committed generated eval config; one QUICK_MODE run attached to the provider PR; a seed for the type's **tracker** emulator where one exists (jira-emulator today; a tracker without an emulator satisfies the obligation per its binding's declared degradation, §3.6 — otherwise the first non-Jira type would be contractually blocked).

**Workflow states & the `state_map`.** Three distinct things get called "state," and the refusal above concerns only one: (1) **tracker workflow states** (`To Do`, `In Progress`, `Done`) belong to the tracker's project/workflow-scheme config, *not* to the tool; (2) the **pipeline phase machine** (`tmp/pipeline-state.yaml`) is tool-internal machinery, invisible to the tracker; (3) the tool's **readiness signal** (the DoR guarantee, surfaced as the `*-autofix-rubric-pass` label) is the tool's *output*. What the tool refuses is *owning or enforcing* the tracker workflow (#1) and gating column-to-column transitions — **not** which workflow a type binds to. Types sharing one workflow (e.g. RHAISTRAT Features and Initiatives both using a common scheme with a `To Do` "on deck / ready but not started" state) is the *preferred*, low-customization outcome, and it maps cleanly onto the tool: **"ready" (DoR/rubric-pass) is exactly the entry condition for a `To Do` column.** The only transitions the tool performs are declared per binding in **`state_map`** — today's hardcoded `Approved` (`submit.py:987`) and `Closed`/`Obsolete` (`split_submit.py:820,:837`) become binding data (§3.6), and each must name a transition that exists in the bound workflow. An optional `state_map.ready` entry lets the tool move a passing item into the shared on-deck state (e.g. `ready: "To Do"`); equivalently and more decoupled, the tool sets only its label and a single tracker-side automation moves labeled items to `To Do` — pick one owner for the move to avoid two sources of truth. Everything past `To Do` (In Progress → Done, per-column exit criteria) stays the refused/future extension point. One operational consequence to settle per deployment: the selection query is `statusCategory != Done AND labels not in (ignore, rubric-pass)`, so `To Do` items remain in the auto-fixer's scope unless the readiness label (or a status filter) excludes them — decide whether `To Do` means "hands off, human owns it" (exclude) or "still auto-maintainable" (leave in scope).

### 3.5 Discovery and future packaging

Static enumeration of `types/*/type.yaml` at import, plus the `RFE_CREATOR_EXTRA_TYPES` environment seam for drop-in descriptor directories during local/provider development — **exercised by a CI test from day 1** so the seam never bit-rots. Extraction to out-of-repo packaging (a type shipped as its own plugin via the skills-registry marketplace) is deliberately deferred until a **second external team** appears; the seam plus the published provider surface (guide, `types/rfe/` skeleton as the copy-from artifact, reusable validate GitHub Action) make that a transport PR, not a redesign — this is the ADR §4.5 phase-5 follow-up commitment, kept. skills-registry gains informational `produces:`/`consumes:` metadata (§3.7) so the rfe → strategy → epic chain can be rendered.

### 3.5.1 Packaging stance (settles PR #115)

**Convention: the working directory is the plugin root.** Root `scripts/` and `types/` are canonical; every SKILL.md invokes `python3 scripts/<x>.py` by that relative path (the literal-match allowlist rule in `AGENTS.md:62-66` and CLAUDE.md), and `pipeline_state.py` builds its command strings the same way (`:171,:211,:217`). Python-side code never assumes cwd: `type_registry.py` locates `types/` relative to `__file__`, internal subprocesses use `os.path.dirname(__file__)` (as `pipeline_state.py:1075` already does).

Installer matrix (2026-09-03): Claude Code marketplace `github`-source plugins (rfe-creator, strat-creator, assess-*) are **full clones** — root `scripts/` is present after install (agentic-ci `docs/image/skills.md:55-60`, "full git clone of plugin repo"; skills-registry ARCHITECTURE.md); subdir/`git-subdir` sources ship the subdir only; OpenCode's installer copies the skills dirs only (copytree follows symlinks); Codex's documented path is clone + symlink the skills dir; CI/eval run with cwd = checkout. rfe-creator ships `.codex-plugin/plugin.json` (`skills: ./.claude/skills/`) and **no** `.claude-plugin/plugin.json`; epic-creator renamed `epic.decompose → epic-decompose` for the same dot-name reason (§4.4) and ships a `.claude-plugin` manifest while still invoking scripts cwd-relative.

PR #115 (per-skill copies of shared scripts: `artifact_utils` ×7, `jira_utils` ×6) is **closed as superseded**: its premise (installers copy only the skills tree) is false for the production installer, and duplication is the drift class this design exists to remove. Absorbed: `__file__`-relative discovery, the skillsaw bump. If a copy-only installer is ever required, the named path is strat-creator's zero-duplication symlink convention (`<skill>/scripts → ../<common>/scripts → ../../../scripts`, 7 links today, `test_skill_integrity` checks them) with `${CLAUDE_SKILL_DIR}/scripts/<x>.py` in bodies and matching per-skill `allowed-tools` — done in one PR-5-style body rewrite, with the launcher pre-rendering absolute `prompt_file`/`RULES_PATH` values because `${CLAUDE_SKILL_DIR}` is not substituted inside prompts subagents read.

### 3.6 Tracker abstraction: Jira today, GitHub-ready

**Requirement (2026-08-12):** types should eventually be able to bind to issue trackers other than Jira — GitHub Issues first. **Posture — identical to the flip and to marketplace extraction:** the schema is tracker-shaped *before* `schema_version: 1` freezes; the actual GitHub adapter, its emulator, and its CI wiring are built when the **first GitHub-backed type** appears. Grounded in a 3-lens execution-verified inventory of the entire Jira coupling surface (write ops, read ops + snapshot invariants, assess/emulator/CI ecosystem).

**The seam.** The pipeline's tracker surface reduces to ~20 operations — create / update title+body / add & list comments / label add-remove-swap (+ idempotent ensure-labels) / split-relation create & scan / close-as-superseded / get / list-container / search-with-exclusions / paginate / retry / build URL / canonical body + content hash / query build & parse / ref grammar + file-stem encoding. **Markdown is the body type at the seam**: `markdown_to_adf`/`adf_to_markdown` (~420 lines, plus four more copies: strat-creator and epic-creator `jira_utils`, assess-rfe `dump_jira.py`, agentic-ci `jira/adf.py`), JQL dialect, workflow transitions, Basic auth, and `/rest/api/3` grammar are all Jira-adapter-private. The interface template already exists in the ecosystem: agentic-ci's `jira/client.py` is a superset of this list (adds attach_file, assign, set_custom_field, set_security_level, get_label_author, get_description_editors, update_comment), and its `GitHubForge` + the eval repo's `github.py` provide production GitHub App-auth patterns. `_fetch_paginated` and `api_call_with_retry` (`snapshot_fetch.py:97-118`, `jira_utils.py:60-88`) are the two functions the adapter interface is shaped around — everything above them is already tracker-agnostic.

**Added since baseline (rfe/initiative consumers):** auth preflight / whoami (`jira_utils.py:110-117`, `submit.py:458-475`); search-by-labels, single page (`jira_utils.py:120-134`, marker recovery `split_submit.py:436-520`); `classify_failure(exc) → per_item | systemic` — HTTP-code semantics are adapter-private; the exit-code contract (4 per-parent / 5 systemic / 64 usage, `split_submit.py:65-106`) and `submit.py`'s loop policy (`:503-651`) consume the classification. **Reserved — in production in sibling pipelines, required before strat/epic can consume the adapter, not scheduled here:** attachments add/list/download/select-newest (strat `jira_utils.py:465-579` with append-only + newest-wins + per-issue writer lock per its ADR-0004; epic `:113,:165` with different signatures), summary-only update, section-scoped body update (partial-body ownership as a write mode beside whole-body + conflict check), link create/scan parameterised by type and direction (Cloners, Blocks, Incorporates, Related), set-parent on create (cross-project), set-assignee, components list, project-version / custom-field name→id mapping, cross-type lock labels, same-origin redirect policy (strat `:33-68`, absent in rfe). The adapter module split (`scripts/trackers/jira.py`) is pulled forward into the PR-2 series as the extraction unit: no imports from rfe-specific modules, one pagination helper (today 3 inside rfe-creator, 5 ecosystem-wide), `create_issue` signature = the union of the three repos (components, fix/affects/target versions, parent_key, reporter_account_id, assignee_id).

**Tracker-neutral invariants every adapter must satisfy** (restated from what today's code implements Jira-specifically):
1. *Recovery from remote reads alone*: split/submit progress must be re-derivable from durable remote markers — comments + relations + **a creation-time marker that exists from the instant the child does** (`labels.split_child_marker`, content-fingerprint adoption guard, `split_submit.py:233-253`) — never from local state or unguarded search (`split_submit.py discover_state :275`; abort is local-only by design). Create-only kinds (decompose) may use a durable local marker plus a remote existence check; that discipline is theirs, not this kind's.
2. *Hash round-trip* (work-item kind; a kind without snapshots declares `snapshot: {mode: none, processed_gate: ...}`): the post-submit content hash MUST equal what the next fetch computes from the tracker's stored representation (the tracker-neutral restatement of the ADF round-trip clause, `docs/snapshot-incremental-fetch.md:91-96`). Hash domains are per-tracker and never compared across trackers; this clause is added to the snapshot invariants doc. All eight numbered snapshot invariants are already tracker-agnostic as written.
3. *Deterministic writes, host/script-side*: writes go through the adapter scripts only (today's MCP-write ban, `rfe.submit/SKILL.md:10`, generalizes); reads may use an interactive fallback (Atlassian MCP today; `gh` CLI or GitHub MCP later), whose invocation is registry-supplied, never skill-body literals (today's hardcoded `cloudId` is the counterexample).
4. *Query semantics*: the default wrapper's meaning — exclude closed, exclude the opt-out label (`labels.ignore`) **and, on the snapshot/bootstrap selection path, the failed-split quarantine label (`labels.split_quarantine`)**, include unlabeled — is contract; its syntax (JQL vs search qualifiers) is the adapter's. Today three label sets are spread across two wrappers (`jql_query.py:79-90` excludes ignore + rubric_pass; `snapshot_fetch.py:375-379` / `bootstrap_snapshot.py:457-461` exclude ignore + quarantine); the registry makes each wrapper name the descriptor keys it consumes.

**GitHub mapping, honestly.** Clean or better: get/update/comments/labels map 1:1; bodies are already GFM (the whole ADF layer deletes); `-label:` negation includes unlabeled issues (the JQL `OR labels is EMPTY` epicycle disappears); `closed` + `state_reason: not_planned` is a cleaner "Obsolete" than Jira's transition-plus-resolution dance; sub-issues give split lineage a visible hierarchy Jira's link type never had; `#123` auto-links replace inlineCard construction; `gh` is already in the sandbox image and agentic-ci's default network policy already allows `github.com` (Jira needed per-repo policy entries). Declared degradations (each a binding field, never silent): **no priority field** (label taxonomy or omit — why priority vocabulary moved out of the base schema, §3.4); **no components** (label namespace or drop); **reporter immutable** (attribution line — already best-effort on Jira, `jira_utils.py:189-200`); **no workflow engine** (the `state_map` — the general per-binding transition vocabulary, Jira included, §3.4 — collapses to labels + `state_reason`: approved → label, close-as-superseded → `not_planned`; where Jira names a real transition, GitHub names a label or `state_reason`); **no atomic label swap** (single GraphQL request, or documented two-call window); **65,536-char comment cap** threatens archival comments (split-across-comments fallback); **labels are repo-level objects** (adapter gains an idempotent ensure-labels setup step; the binding's label vocabulary becomes definitions, not just names); **list endpoint returns PRs** (filter `pull_request`-keyed items); **search API 1000-result cap + 30 req/min** (full enumeration uses REST list; search only for filtered queries); **no body-edit history** (bootstrap becomes a Jira-only optional capability — skipping it is Invariant-4-safe: everything-NEW routes through the resume check); **label names cap at 50 chars** — the ~57-char split-child marker becomes a hidden body marker or a sub-issue relation; **no attachment API on issues** — the reserved attachment ops degrade to gist/commit-to-repo or `unsupported`; **duplicate handling** maps to `state_reason: duplicate` (`state_map.close_duplicate`).

**Key grammar and filenames.** GitHub refs (`org/repo#123`) are not filesystem-safe and match none of the existing key regexes. Resolution: a GitHub binding declares exactly one `(owner, repo)` (mirroring one `(project, issue_type)` per type) and a short uppercase **alias prefix**; artifact keys and filenames use `<ALIAS>-<number>` (e.g. `MRDOC-123.md`), and the frontmatter `tracker_ref` (`org/repo#123`) is the source of truth — the exact analog of §5's `tracker_ref`-presence redefinition of `is_existing`. Every existing regex, scan function, prefix-union predicate, snapshot key, cache filename, companion-suffix join, and numeric-suffix sort then works by *adding a list entry*, not a second grammar. One repo per type identity is a v1 rule (multi-repo types deferred, same posture as R1).

**Emulator.** No GitHub emulator exists anywhere in the ecosystem — the one genuinely new artifact a GitHub-backed type requires (strictly smaller than jira-emulator: no ADF, no workflows, no metadata registries). Until it exists, the binding's declared degradation for the §3.4 seed obligation is recorded fixtures or a throwaway-repo run. Longer term, the better target is an emulator of the *adapter seam* rather than the REST API — one emulator for all trackers, and it makes the seam testable by construction.

**Sequencing.** PR-1 carries the schema readiness (identity union, `tracker_ref`, `conventions:` rename, priority relocation) — all naming decisions with zero behavior change. PR-2 keeps the seam boundaries visible while scripts adopt the registry: no new tracker calls outside the adapter module (the autofixer's `label-rfes.py` is the named non-conforming writer to fold in or delete), and `--jql` grows a neutral `--query` alias. The adapter module split (`scripts/trackers/jira.py`), the GitHub adapter, its emulator, `GITHUB_TOKEN` in both CI runners' `_CI_ENV_VARS` (+ the eval harness env allowlist — the same hole JIRA_* has today), and the assess-side `dump_github.py` twin are all **first-requester work**, not scheduled here.

### 3.7 Cross-type input chain (reserved vocabulary; no evaluator in rfe-creator)

rfe → strategy → epics is a pipeline of types across repos, with hardcoded per-repo gates today (strat-creator `strategy-create/SKILL.md:62-93`, `config/pipeline-settings.yaml`, `jira_utils.build_jql_from_config`; epic-creator `docs/human-review-guide.md:53-57`, `fetch_strategy.py:150-163`). The descriptor reserves the vocabulary so the chain can be *described* and lint-checked; evaluating gates stays in the consuming pipeline.

```yaml
inputs:
  - from_type: rfe
    relation: { kind: clones | parent | link, link_type: Cloners, direction: derived_is_inward }
    source_ref_field: source_rfe          # frontmatter field holding the upstream ref
    gate:
      labels_any: [${types.rfe.labels.rubric_pass}, tech-reviewed]   # upstream EXPORTED readiness signal, by reference not literal
      labels_all: []
      statuses_not: [Closed, Resolved, Draft]
      any_of: [{labels_any: [...]}, {fields: {customfield_10855: {name_in: [...]}}}]
    body_source: { attachment: "{key}-strategy.md", fallback: description }
    copy_fields: [summary, description, priority, labels, components, affects_versions, target_versions, parent]
    skip_if: { labels_any: [...], statuses: [...], single_open_unlabeled_override: true }
produces:
  - type: epic
    relation: { kind: parent, dependency_link_type: Blocks }
    label_on_input: epic-creator-auto-decomposed
```

**Where the siblings actually sit.** strat-creator is a *peer pipeline of a different kind* — derive-by-clone from an approved RFE, section-scoped refine, human pull/push/sign-off loop, external CI, attachments, lock labels, its own eval and embedded assess; forcing it into this descriptor would need ~15 schema extensions and four foreign stage names (`derive`, `refine`, `pull`, `push`, `signoff`), which the second-requester rule rejects. epic-creator is a *type provider in the identity sense* (`(RHAI, Epic)` — it creates first-class issues, children of the RHAISTRAT Feature, with `Blocks` links per DAG edge) **and** a second pipeline kind (1:N decomposition, set-level review, a script-side deterministic dimension, no snapshot, create-only submit resumed from a local marker) that forked the engine because it needed a different phase table (§2). Neither becomes a type of this pipeline (D9). The reserved `inputs:` block is validated against strat-creator's `config/pipeline-settings.yaml` and epic-creator's real binding during PR-1 (R1); the `creator-core` follow-up owns the shared engine/adapter. skills-registry's informational field is `produces:`/`consumes:` (replacing `provides: work-item-type`) so the chain can be rendered; a `types/<t>` shipped via `git-subdir` receives descriptor data only, never code.

---

## 4. The skill layer: one intake, skeletons + tokens + typed judgement

### 4.1 Entry points

The six generic skills (`rfe-*`, named per §4.4) are the type-generic surface. Step 0 of every body is one allowlisted script call:

```
python3 scripts/type_registry.py resolve <args...>   # prints: TYPE RESOLVED: <type> (<how>)
```

`resolve` is deterministic and never interactive (§5). On CI paths it degenerates to echoing `--type` — the same class of call as the 16 script invocations `auto-fix` already makes. The body then proceeds generically, reading typed content only through registry-supplied paths and variables.

### 4.2 Composition: three tiers, honestly scoped

For the five dispatch SKILL.md bodies (~70–99% identical) the collapse is straightforward: one generic body per stage, mechanical differences carried by registry-supplied values. The judgement prompts are where the parents disagreed, and the adversarial review showed a single `{RULES_PATH}` placeholder cannot reproduce the real files (review-agent has ≥6 disjoint typed regions; split-agent interleaves typed content through all five steps). The honest mechanism has three tiers:

- **Tier 1 — fully shared skeletons** for prompts whose divergence is mechanics, not judgement: `assess-agent.md` (100% identical) and `fetch-agent.md`. The fetch skeleton makes the **MCP fallback unconditional** (fixing the initiative side's silently missing fallback) and gates only the *comments sub-steps* on `companions.comments`; its primary invocation uses a type-aware `fetch_issue.py --fetch-all` (a PR-2 script change — today the initiative agent hand-builds frontmatter from `--fields` output), and the fallback field list includes `issuetype` for post-fetch verification.
- **Tier 2 — skeleton + named fragment slots** where a shared procedural skeleton is real but typed regions are multiple: `review-agent.md` keeps the shared coverage-check algorithm, status-file format, 0–2 scale, and self-check scaffold, and takes **two fragment slots** (`review_rules` — recommendation/split/reject rules, where the verified polarity conflict lives; `review_sections` — typed body headings) plus registry **tokens** for the schema name, id field, score-field list, and frontmatter-set command. Dimension wiring (alignment read/parse/needs_attention steps) is emitted by the launcher only when the descriptor declares the dimension. `revise-agent.md` joins this tier: a shared skeleton with the **fixed** five-step order (Read Context / Revise / Update Frontmatter / Content Preservation / Revision History), the frontmatter-set command rendered from tokens (`ID_FIELD`, `SCHEMA`, dirs), and one `revise_rules` fragment slot for the typed reframing guidance — moved from Tier 3 because PR #153 showed the fork drift was *mechanical*: the initiative copy ran Content Preservation before Update Frontmatter, so budget-exhausted agents never set `auto_revised=true`. Step order and error-stub shape are skeleton, never `types/<t>/`. Tier-1/2 skeletons and the fetch/create bodies carry the rule *agents write the BODY only — no hand-written `---` block; frontmatter is created by `frontmatter.py set` in a later step* (PR #149, merged `1781dae`: `artifact_utils.py:321` `ValidationError`, raised by `read_frontmatter` `:586` on unparseable blocks while `update_frontmatter` `:672-712` repairs — a declared-stable script contract, §3.4).
- **Tier 3 — majority-typed whole files that stay per-type**, descriptor-pointed: `split-rules.md` effectively *is* the split agent prompt (measured 56% identity — majority typed). Tier-3 files are covered by a required-line lint (template-path token, `next_rfe_id --prefix/--dir` token, output-dir tokens present) since no shared skeleton protects them. Drift-prone mechanical lines inside them (template path, `next_rfe_id --prefix/--dir` invocation, output dirs, id/size fields) are replaced by launcher-substituted variables, so the copyable-mistake class is script-supplied even where the prose is typed.

Placeholder resolution is the existing idiom: launch directives carry `prompt_file` + pre-rendered `vars` (see the real shape in §8.3); the dispatcher renders `vars + "\n\nRead <prompt_file> and follow all instructions exactly."` exactly as `rfe.auto-fix/SKILL.md:101` does today. No contradictory shared base ever exists: the rule content *is* the typed file.

- `create` keeps its own body (53% identical — both parents exclude it from the collapse) but becomes generic the same way: template and questionnaire guidance are registry-supplied paths.

Net new runtime surface, measured against today: +1 `resolve` call per skill entry, +1–2 file reads per subagent launch **inside isolated subagent contexts** (baseline is already 6–10 reads/ID; the scorer already does exactly this two-file read).

### 4.3 What stays per-type, deliberately

Templates, questionnaire guidance, rule fragments, split/revise judgement files, dimension prompts, rubrics, eval fragments, calibration examples. This is real authorship — the extension provider's actual job (§8.4).

### 4.4 Naming: new `rfe-*` generics, legacy `rfe.*` compat shims

- The generic entry points land under **new, spec-compliant names**: `rfe-create`, `rfe-review`, `rfe-submit`, `rfe-split`, `rfe-auto-fix`, `rfe-speedrun`. The existing dot names (`rfe.create`, …) are not legal under the agent-skills spec (skill names are lowercase alphanumerics and hyphens), so the collapse — which rewrites every body anyway — is the moment the canonical surface moves to `rfe-*`. (The separate neutral-branding question, `request-*`, remains deferred — D1, §11.)
- **The six legacy `rfe.*` skills are kept for backward compatibility**, their bodies replaced by ~8-line shims ("Read `.claude/skills/rfe-<stage>/SKILL.md` and follow from Step 0") — the repo's established pointer idiom. They cost ~50 lines total, carry no type facts (nothing to drift), and keep every existing invocation working: human muscle memory, and the autofixer's `CLAUDE_PROMPT: '/rfe.auto-fix …'`, which must never depend on the model guessing a renamed skill in a headless run (the stochastic-selection hazard PR #143 hardened against). An **optional** one-line autofixer MR flips the prompt to `/rfe-auto-fix` to take the one shim hop off the production hot path (PR-10, no deadline); the shims stay regardless. Both eval configs flip to `skill: rfe-speedrun` inside the collapse PR (per-PR config, hop-free where weak models run; initiative adds `--type initiative` to the arguments). New docs and skill descriptions advertise only the `rfe-*` names.
- The `initiative-*` skills, their prompts, and all three dimension-skill directories (`rfe-feasibility-review`, `initiative-feasibility-review`, `strategic-alignment-review`) are **deleted** in the collapse PR; typed content moves into `types/<t>/`. No users exist (maintainer ruling; verified zero production CI references). The same PR sweeps every reference: `.ambient/ambient.json`'s prompts advertising `/initiative-create|review|submit`, and the command lists in `README.md`, `AGENTS.md`, and `eval/README.md` (deep rewrite can wait; command references cannot). The same PR deletes **the four orphaned strat reviewer skills** (`architecture-review`, `feasibility-review`, `scope-review`, `testability-review`) — their owner is strat-creator; two were edited by PR #163 (2026-08-24) without propagating to strat's live `strategy-*-review` copies (0.69–0.82 similar), so the overlay edits are ported to strat-creator *first*, then the orphans go. The sweep also fixes `README.md:96` and `.ambient/ambient.json:6` (`ederign/strat-creator` → `opendatahub-io/strat-creator`) and ships a **paired skills-registry PR**: register the six `rfe-*` skills with contract blocks and `source_assertions.skill_path`, keep the `rfe.*` entries as compat, drop the four orphan entries (`validate_registry.py:542-600` errors on registered-but-missing names; run `--check-codex-manifests`). `.codex-plugin/plugin.json` points at the whole `./.claude/skills/`, so Codex listings will show generics *and* shims — mark shims non-listed if the frontmatter supports it.
- Future types get no automatic alias family (the `rfe.*` shims are legacy compatibility, not a pattern). A type owner who wants a branded command ships it as a companion skill (~8 lines) in their own repo.

### 4.5 The one codegen exception: eval configs

Eval *configs* (not prompts) are generated: `scripts/generate_eval_config.py` + a shared eval skeleton + per-type `types/<t>/eval/fragment.yaml` produce the checked-in `eval.yaml`/`eval-<type>.yaml`, with a regenerate-and-diff CI gate (the skills-registry validate.yml precedent). This is deliberate and scoped: eval configs are structured data with embedded judge prose, already duplicated ~76% line-identical at fbf96bb (66% at 8888b18; converged by near-identical PR #154/#165 edits pinned by `tests/test_eval_revision_flag_judge.py::test_both_configs_identical`, which the generator retires) with four verified silent behavioral drifts (the rm-artifacts check drop, the frontmatter pass-logic escape, the `not_relevant` hatch + 0.85 threshold relaxation, the weakened phase-detection signals) — each becomes an explicit, reviewable fragment entry or is deliberately reverted at regeneration time. `type.yaml`'s `eval.thresholds` is authoritative; the fragment carries judge prose and check deltas only. Since baseline the configs converged by near-identical additions — `revision_flag_consistency` (PR #154, byte-identical), the transcript-based `architecture_context_used` (PR #165), `score_range: [1,5]` on LLM judges and `skill:` moved under `execution:` (PR #164) — so the skeleton emits the `execution.skill` layout, PR-5 switches `execution.skill` to `rfe-speedrun`, fragment entries cover the initiative `not_relevant`/`declared_irrelevant` carve-out (now inside the transcript judge) and `score_range`, and `batch_pattern` is derived from `identity.local_prefix`.

---

## 5. Type resolution (deterministic core)

`type_registry.py` implements, in order — `resolve` is a script; the model only obeys its output:

1. **`--type <t>`** — always wins; unknown → exit non-zero with the registered list. The CI path.
2. **Batch `type:`** — batch files accept two forms: the legacy bare list (resolves per rungs 4/5) and a mapping `{type: <t>, items: [...]}`. Per-item types are rejected: one typed run per workspace is an invariant (`tmp/pipeline-state.yaml` is single-type). The mapping form touches every batch-root consumer: `validate_batch_input.py`, `next_rfe_id.py --from-batch`, and the speedrun body's batch parsing (all currently exit 2 on a non-list root — enumerated in PR-3).
3. **Deterministic artifact/ID signals** — frontmatter `type:` field (new; written by create/fetch; **prefix/path fallback for pre-migration files**: an artifact without `type:` resolves via its id prefix and containing directory); key membership in the union of `identity.local_prefix` + the bindings' key grammars across types — token grammars are **adapter-derived** (Jira: `key_prefixes` prefix match; a GitHub binding later contributes its ref forms — `org/repo#123`, issue URLs, `ALIAS-123` — to the same `detect()`, §3.6). `detect(token)` returns a **candidate set**: singleton → resolved; multiple (shared prefix, e.g. post-flip RHAISTRAT) → provisional, tie-broken post-fetch on the binding discriminator (`issue_type` for Jira); query-string parsing (JQL `project =`/`issuetype =`; search qualifiers later) is likewise adapter-delegated → descriptor match.
4. **Interactive fallthrough** — `create`: calibrated classification (§6). Other skills, interactive and still unresolved: an AskUserQuestion type picker (permitted — the session is interactive; the lint forbids it only in headless-reachable sections).
5. **Headless, unresolved → grandfathered `rfe` default**, printed as `TYPE RESOLVED: rfe (legacy default)`. Grandfathered for rfe only; any *conflicting* deterministic signal fails loudly, pre-init, as a visible non-zero Bash exit — never a question, never a text-only turn.

**Self-describing artifacts.** Base task/review schemas carry the triple `type:` (new), `tracker_ref:` (new; optional, nullable; the canonical remote reference — `RHOAIENG-9876` for Jira, `org/repo#123` for GitHub — whose grammar the type's tracker binding owns) and `local_id:` (**already on all four schemas** since 903ca45, `artifact_utils.py:67-76,:114-123,:196-201,:234-239`, written at rename time `:907-924,:967-983`; its pattern becomes `identity.local_id_pattern`). **`tracker_ref` is read from frontmatter, never re-derived from an id prefix.** Run reports (schema v1: `report_schema_version`, `type`, `report_stage`, per-entry `tracker_ref`/`role`/`local_id`, `generate_run_report.py:62-72,:212-248,:356-359`) already use the name with these exact semantics — the code comment cites this section — but derive it by `item_id.startswith(config["tracker_prefix"])` (`:212-213`), a new single-prefix predicate that returns null for any post-flip RHAISTRAT- item and mis-attributes composite ids. PR-2 makes the report *project* `tracker_ref`/`role` from frontmatter with a prefix-union fallback for pre-migration artifacts; `TYPE_CONFIG.tracker_prefix/local_prefix` migrate to the registry and the `:212` site is migrated, not whitelisted. `is_existing` in `submit.py` is redefined as *`tracker_ref` present* (prefix-union membership as migration fallback) — retiring the single-prefix `startswith` at `submit.py:749`. **Fetch gains the `issuetype` field** (`fetch_issue.py`, `snapshot_fetch.py:127` — verified fetched nowhere today), and post-fetch verification checks `(project, issue_type)` against the resolved descriptor: mismatch → interactive correction, or the standard headless error-stub path. Bootstrap and other report consumers cross-check `report.type` against `--type` (PR-3). Mixed-type invocations are rejected with a split-the-batch message.

---

## 6. Classification (staged; the ADR's ambition made implementable)

The verified constraint that shapes this: type binds at `pipeline_state.py init`, issue content first exists at FETCH — so content-based classification cannot gate intake without re-sequencing the state machine, and today's rubric signal (`not_a_task=0`) provably detects "not a business need", which mixes well-formed initiatives with chores and rewritable tasks.

- **Stage 1 — create-time (ships with the generic create).** Classify the free-text idea against every descriptor's `classification` block. Clear match → one-line notice and proceed (`Type: rfe — pass --type to override`); ambiguous → AskUserQuestion with one option per installed type, labels from descriptors (new types appear automatically); headless → forbidden (rungs 1–2 or the rfe default apply). The PM who only ever files RFEs never sees a question.
- **Stage 2 — classification as a reported signal (no routing authority).** The review already computes and discards the signal: when a review scores `not_a_task=0` *and* the text matches another type's `signals` (the launcher passes the other descriptors' classification blocks into the review context — a one-line vars addition), the review report and `needs_attention` note say so — "likely an Initiative at the wrong door; consider `/rfe-create --type initiative`". Ships after the collapse PR; changes no routing, needs no guardrail.
- **Stage 3 — routing authority over existing issues (only if a flip is ever scheduled; gated).** In a flipped world `issue_type` would be the deterministic classifier for existing issues, so most of the ADR's re-routing intent would be satisfied without content classification; with today's bindings unchanged (§0), this stage has no trigger and is not planned. Content-based reclassification (moving an item across types) remains gated on: a labeled corpus and stated accuracy bar (the 36 eval cases are labeled only by dataset membership and straddle the boundary in both directions — smoke-test material, not a bar), a confirmation guardrail for anything that picks a Jira destination, and an honest costing of per-item typing against the single-type-run invariant.

---

## 7. The assess side

**7.1 No rename.** The repo stays `assess-rfe`, the layout stays per-skill `skills/assess-<t>/scripts/agent_prompt.md` — verified as what the bootstrap, `PIPELINE_TYPES.rubric_path`, the pinning tests, and four external assessor CI jobs (`/assess-rfe:assess-rfe`, `AGENT_ENABLED_PLUGINS=assess-rfe`) all assume.

**7.2 One type-agnostic scorer** (ADR §4.4, verified: the two agents are a 0.919-similarity noun swap containing no rubric path — the fork duplicates only the containment boundary). Executed as a *coordinated* cross-repo change: the descriptors' `scorer_agent` values converge on the shared name, plus the `agent_types.py` map, ~14 prose spots, the bootstrap gate, one test, and the two assess SKILL.md coordinators — with rfe-creator tolerating both agent names for one release to avoid the uncoordinated-collapse hang (bootstrap exit-1 / wait-for-wave spin) the verification demonstrated. Until this lands, `pipeline.scorer_agent` stays per-type.

**7.3 Pinning + provenance** (RTE): descriptors pin `rubric: {repo, ref, path}`; the bootstrap honors the pin **from PR-2** (env overrides win, as the eval pipeline already plumbs) — moved early because the bootstrap floats at assess-rfe main today, and a floating rubric plus a descriptor-pinned criteria list is a silent-zero window (the design's own named failure mode). At assess time the rubric checkout SHA and descriptor content hash are stamped into review frontmatter (`rubric_version`, `type_version`). `export_rubric.py` writes `artifacts/<type>-rubric.md` per the descriptor's `rubric.export` — fixing the verified asymmetry (initiative create never sees its rubric) and retiring the drifted third copy in export-rubric.

**7.4 Detection convergence**: PR #10's four coexisting mechanisms (skill choice, project map with silent rfe default, criterion sniffing, CSV-header sniffing) collapse onto an explicit type marker in result files plus a dispatch map generated from (or test-pinned to) the descriptors — keyed on `(project, issue_type)`, not project. **This must land before the flip** (verified: `agent_type_for_project("RHAISTRAT") → rfe-scorer` for both types); the one-scorer collapse (§7.2) dissolves the *scorer* half of that failure, the result-file marker fixes the parsing half.

### 7.5 Rubric track (the ADR's §5, with the PR #10 disposition decided)

**Disposition: merge PR #10 as the interim v1.** Its 5-criterion rubric is already wired end-to-end (schemas, eval config, 33 calibration examples), and the descriptor makes a criteria swap cheap later — `schema.review.score_fields` is the single source for what are today 7 restatement sites per type (2 of them pinned by no test). The DoR-derived v2 (`goal, motivation, impact, stakeholders, success_criteria, scope_control, right_sized`; `open_to_how` deliberately absent) is authored in assess-rfe on its own product track, but the **swap lands after the skill collapse** (PR-6 in §10): the collapse gates on the stable, shipped v1 instrument (honoring ADR A.3's don't-gate-on-a-moving-instrument point via non-concurrency rather than ordering), the DoR template is by then consumed from `types/initiative/template.md` (before the collapse the live create path reads the skill-dir copy — swapping earlier would force a second re-baseline), and there is exactly **one** re-baseline. Blast radius is small by measurement (77/80 `expected_scores` null; the 3 populated ones are all `right_sized`, which survives v2 by name). The run reports must state the measurement change (ADR §6's honesty clause).

---

## 8. Worked example: the initiative extension under this design

This section is the contract made concrete — every file the initiative type consists of, where its content comes from in today's tree, and what its author owns going forward.

### 8.1 Directory

```
types/initiative/
  type.yaml                     # §8.2 — the single home for the initiative rows of all 19 registries
  template.md                   # from .claude/skills/initiative-create/initiative-template.md
                                #   (26 lines today; replaced by the DoR template at rubric-v2 time — §7.5)
  prompts/
    create-guidance.md          # the questionnaire: clarifying-question guidance extracted from
                                #   initiative-create/SKILL.md Step 2 (objective, problem, scope, parent)
    review-rules.md             # from initiative-review/prompts/review-agent.md — recommendation rules:
                                #   split ONLY at right_sized=0; "breadth across domains or personas
                                #   is not, by itself, grounds to split"; reject at 3+ zeros
    review-sections.md          # typed body headings ("Strategic Alignment", "Execution Considerations")
    revise-rules.md             # typed reframing guidance — the ONE fragment slot of the shared revise
                                #   skeleton (tier 2; fixed step order lives in the skeleton, PR #153)
    split-rules.md              # from initiative-split/prompts/split-agent.md (tier 3): decomposition by
                                #   "genuinely different objectives", never by audience breadth
                                #   (fixing the verified intra-type contradiction in the same edit:
                                #   the 1/2 proceed-branch is removed, matching review-rules)
  dimensions/
    feasibility.md              # body of initiative-feasibility-review/SKILL.md
    alignment.md                # body of strategic-alignment-review/SKILL.md
  eval/
    fragment.yaml               # judge prose/deltas for generate_eval_config.py (§4.5): the initiative
                                #   quality judge, the alignment check, and explicit entries for the
                                #   deltas that were silent drift (not_relevant hatch, pass-logic escape)
    pairwise-judge.md           # from eval/config/initiative-pairwise-judge.md
  jira-emulator-seed.yaml       # RHOAIENG project, Initiative workflow, "Work item split" link type
                                #   (tests/conftest.py:91-121 pattern)
```

Deleted when the collapse PR lands: the six `initiative-*` skill directories, `initiative-feasibility-review`, `strategic-alignment-review`, **and** `rfe-feasibility-review` (its body moves to `types/rfe/dimensions/feasibility.md` — leaving it would keep a second live copy of the rfe feasibility prompt, the exact drift class this design kills), plus the four orphaned strat reviewer skills (`architecture-review`, `feasibility-review`, `scope-review`, `testability-review` — after porting PR #163's overlay edits to strat-creator), plus `eval-initiative.yaml`'s hand-maintained body (regenerated). The initiative dataset stays at `eval/initiative-dataset/`.

### 8.2 `types/initiative/type.yaml` — complete

```yaml
schema_version: 1
type: initiative
display: { entity: Initiative, entity_plural: Initiatives }
kind: work-item                  # reserved (§3.2); inputs/produces omitted = []

identity:
  tracker: jira
  jira:
    project: RHOAIENG            # unchanged by the implementation; a future flip (§9, unscheduled) would set RHAISTRAT
    issue_type: Initiative
    key_prefixes: [RHOAIENG-]    # a future flip would prepend RHAISTRAT- (write-first, read-all); unchanged today
    split_link_type: "Work item split"   # today hardcoded at split_submit.py:210,:410,:497,:610,:716
    state_map: { approved: Approved, close_superseded: { transition: Closed, resolution: Obsolete } }   # submit.py:987; split_submit.py:820,:837
  local_prefix: INIT-
  local_id_pattern: '^INIT-\\d+$'
  id_field: initiative_id

classification:
  summary: "A strategic multi-team objective, often parented to a RHAISTRAT strategy."
  driver: engineering            # architectural/platform work, no direct external requester
  signals: ["multi-team or multi-quarter objective", "workstreams", "platform or process improvement",
            "clear start and stop within ~2 quarters", "RHAISTRAT parent"]
  counter_signals: ["single customer-facing capability gap (→ rfe)"]

dirs: { tasks: artifacts/initiatives, originals: artifacts/initiative-originals,
        reviews: artifacts/initiative-reviews }
index: { enabled: false }
companions: { comments: false, removed_context: true }

conventions:
  type_label: Initiative
  label_prefix: initiative                       # e22120c: no -creator suffix
  labels:
    rubric_pass: initiative-autofix-rubric-pass   # published contract (R7): downstream gates read it by reference
    ignore: initiative-ignore                       # jql_query default wrapper + SNAPSHOT_CONFIG.ignore_label
    split_quarantine: initiative-split-quarantine   # SNAPSHOT_CONFIG.quarantine_label (snapshot_fetch.py:64) + submit.py:283
    split_child_marker: "initiative-split-child-{parent}-{child_id}-{fingerprint}"   # split_submit.py:233-253
    needs_attention: initiative-needs-attention
    feasibility: { feasible: initiative-feasibility-pass, infeasible: initiative-feasibility-fail,
                   indeterminate: initiative-feasibility-unknown }
    alignment:   { strong: initiative-alignment-strong, partial: initiative-alignment-partial,
                   weak: initiative-alignment-weak }
  comment_prefix: "[Initiative Creator]"
  removed_context_preamble: "*[Initiative Creator]* The following technical implementation details…"
  query_default: 'project = RHOAIENG AND issuetype = Initiative'   # JQL: this type's tracker is jira
  parent_key_patterns: ["RHAISTRAT-\\d+", "RHOAIENG-\\d+", "INIT-\\d+"]

schema:
  task: { extra_fields: {} }                     # (rfe adds size: {enum: [S,M,L,XL]})
  review:
    score_fields: [what, why, scope, open_to_how, right_sized]   # v1 = PR #10 rubric;
                                                 # rubric-v2 edit (§7.5): the 7-criterion DoR set
    extra_fields:
      alignment: { enum: [strong, partial, weak, not_assessed], default: not_assessed }
    extra_rules:
      - { when: { field: alignment, equals: weak }, then: needs_attention }

pipeline:
  stages: [create, review, submit, split, auto-fix, speedrun]
  poll_prefix: initiative-
  state_prefix: initiative-
  scorer_agent: initiative-scorer                # converges to the shared scorer at §7.2; until then per-type
  prompts:
    create_guidance:  types/initiative/prompts/create-guidance.md
    review_rules:     types/initiative/prompts/review-rules.md
    review_sections:  types/initiative/prompts/review-sections.md
    revise_rules:     types/initiative/prompts/revise-rules.md
    split_rules:      types/initiative/prompts/split-rules.md
  dimensions:
    - { name: feasibility, prompt: types/initiative/dimensions/feasibility.md, blocking: true }
    - name: alignment
      prompt: types/initiative/dimensions/alignment.md
      blocking: false
      condition: { frontmatter_field: parent_key, prefix: RHAISTRAT- }   # a future flip would switch to the parent_is_outcome hook (§9)
      skip_stub: { result: not_assessed }
  rubric:
    repo: opendatahub-io/assess-rfe
    ref: v1.2.0                                  # pinned; bootstrap honors it from PR-2 (§7.3)
    path: skills/assess-initiative/scripts/agent_prompt.md
    export: artifacts/initiative-rubric.md
  resplit: { score_field: right_sized, below: 1 }    # only right_sized=0 queues a RESPLIT
                                                 # (rfe: below: 2 — both 0 and 1 trigger; makes
                                                 #  check_right_sized.py's threshold per-type and
                                                 #  consistent with review-rules' split-only-at-0)

batch: { extra_fields: [parent_key] }
snapshot: { prefix: initiative-snapshot-, report_prefix: initiative-run- }
reporting:
  item_key: per_initiative
  criterion_labels: { what: WHAT, why: WHY, scope: Scope, open_to_how: HOW,
                      right_sized: Right-sized }     # generate_review_pdf.py:82-88 values, verbatim
  criterion_short_labels: { right_sized: Scope-fit }  # :89-95; omitted keys default to criterion_labels
  before_score_name_map: { WHAT: what, WHY: why, Scope: scope, HOW: open_to_how, "Open to HOW": open_to_how,
                           Right-sized: right_sized, Right-sizing: right_sized, RS: right_sized }
                                                  # generate_review_pdf.py:96-105 — 8 aliases; the earlier '{ }' was wrong at baseline
  pdf.extra_fields: [alignment]                   # generate_review_pdf REPORT_CONFIG.extra_fields
  run_report.extra_entry_fields: [alignment, feasibility, needs_attention]   # generate_run_report TYPE_CONFIG
  # report_title derives from display.entity; output filename from snapshot.report_prefix (submit.py:322-331 consumer)
eval:
  config: eval-initiative.yaml                   # generated by generate_eval_config.py (§4.5)
  dataset: eval/initiative-dataset
  mlflow_experiment: initiative-speedrun-eval
  thresholds: { architecture_context_used: 0.85, quality_min_mean: 3.5 }   # authoritative (§4.5)
  timeout: 8100
  annotations_extra: [expected_alignment]
```

The rfe descriptor is the same shape with `score_fields: [what, why, open_to_how, not_a_task, right_sized]`, `index.enabled: true`, `companions.comments: true`, `size` in task extras, the `rfe-creator-*` label family (incl. `ignore: rfe-creator-ignore`, `split_quarantine: rfe-creator-split-quarantine`), `local_id_pattern: '^RFE-\\d+$'`, `pdf.extra_fields: []`, `run_report.extra_entry_fields: [needs_attention]`, `snapshot: { prefix: issue-snapshot-, report_prefix: "" }`, `resplit.below: 2`, and grandfathered empty `poll_prefix`/`state_prefix`/`report_prefix` (allowed for `type: rfe` only — note the rfe *snapshot* prefix is **not** empty; an empty snapshot prefix would collide with every other type's snapshots under the §3.3 lint). **The diff between the two files is exactly the extension-point list — the §3.4 acceptance test.**

### 8.3 What users see

```
/rfe-create Consolidate the three model-serving stacks into one platform
  → TYPE RESOLVED: initiative (classification: matched "platform improvement",
     "multi-team objective"; counter-signals: none)
  → proceeds with types/initiative/template.md + prompts/create-guidance.md

/rfe-create Users need SSO for the model registry
  → TYPE RESOLVED: rfe (classification)          # one-line notice, no question

/rfe-create Add a webhook when training jobs finish
  → AskUserQuestion: [RFE — customer-facing capability | Initiative — platform objective]

/rfe-review RHOAIENG-9876
  → TYPE RESOLVED: initiative (key prefix RHOAIENG- → candidate {initiative};
     post-fetch verified issuetype=Initiative)
  → review runs the shared skeleton + initiative rules/sections, feasibility, and alignment
     (parent_key RHAISTRAT-42 satisfies the condition)

/rfe-speedrun --headless --dry-run --type initiative --input batch.yaml    # CI form
# batch.yaml:  { type: initiative, items: [ {prompt: ..., parent_key: RHAISTRAT-42}, ... ] }
```

A REVIEW-wave launch directive, in the shape `pipeline_state.py` actually emits (`prompt_file` + pre-rendered `vars`; the dispatcher renders them into the prompt exactly as `rfe.auto-fix/SKILL.md:101` specifies; `subagent_type` appears only on ASSESS-family entries, where it carries the descriptor's `scorer_agent`):

```yaml
- prompt_file: .claude/skills/rfe-review/prompts/review-agent.md   # shared skeleton
  vars: |
    ID=RHOAIENG-9876
    SCHEMA=initiative-review
    ID_FIELD=initiative_id
    SCORE_FIELDS=what,why,scope,open_to_how,right_sized
    RULES_PATH=types/initiative/prompts/review-rules.md
    SECTIONS_PATH=types/initiative/prompts/review-sections.md
    ASSESS_PATH=artifacts/initiative-reviews/RHOAIENG-9876-assess.md
    FEASIBILITY_PATH=artifacts/initiative-reviews/RHOAIENG-9876-feasibility.md
    ALIGNMENT_PATH=artifacts/initiative-reviews/RHOAIENG-9876-alignment.md
    FIRST_PASS=true
```

### 8.4 What the initiative author owns vs gets for free

| Owned (typed authorship) | Free (engine) |
|---|---|
| `type.yaml` (~100 lines) | dispatch engine (waves, barriers, resume, error stubs, compaction hook) + the work-item phase table |
| template + create guidance (~60 lines) | snapshot/incremental fetch, conflict detection, content preservation |
| review rules + sections (~50 lines); split/revise judgement files (~200 lines) | transactional submit/split/link/close Jira sequences |
| 2 dimension prompts (~150 lines) | run reports, PDFs, batch summaries (labels from `reporting:`) |
| rubric + calibration (assess repo, ~165 lines) | scorer agent, bulk assess loop, export |
| eval fragment + pairwise judge (~90 lines) + 16-case dataset | eval config generator, check judges, CI wiring |

≈ **720 owned lines** (nearly all judgement) versus the **1,595 mirrored lines** PR #143 wrote, of which 933 were dispatch skeleton that now cannot drift because it no longer exists twice. Pipeline fixes reach every type at once — the ADR §6 consequence, realized.

### 8.5 The two future edits this file absorbs (one planned, one unscheduled)

1. **Rubric v2 (§7.5):** change `score_fields` to the DoR set, bump `rubric.ref`, swap `template.md`, regenerate the eval config, re-baseline once. No script changes.
2. **The flip (§9 — unscheduled, not part of the implementation):** change `identity.jira.project` to `RHAISTRAT`, prepend `RHAISTRAT-` to `key_prefixes`, update `query_default`, switch the alignment condition to `parent_is_outcome`. Every registry consumer follows automatically; the *non-registry* flip work is the pre-listed §9 checklist — the descriptor edit is necessary, not sufficient.

### 8.6 Sketch: what a GitHub-backed type's binding looks like (§3.6)

Hypothetical future type "docs request", tracked as GitHub Issues — only the identity/conventions deltas shown; every other block (dirs, schema, pipeline, eval) is tracker-free and identical in shape:

```yaml
identity:
  tracker: github
  github:
    repo: opendatahub-io/model-registry        # exactly one repo per type (v1 rule, §3.6)
    kind: { type_label: "type: docs-request" } # or { issue_type: Task } where org-level types exist —
                                               #   type_label is the default; native types are opt-in
    alias_prefix: MRDOC-                       # artifact keys/filenames: MRDOC-123.md; the frontmatter
                                               #   tracker_ref (opendatahub-io/model-registry#123) is truth
    labels_defined:                            # GitHub labels are repo objects — definitions, not names
      - { name: docs-request-ignore, color: "ededed" }
    state_map: { approved: docs-request-approved,      # no workflow engine: approve → label,
                 close_superseded: not_planned }       #   close-as-superseded → state_reason
    priority: omit                             # no priority field — declared degradation, not silent
  local_prefix: DOC-
  id_field: doc_id
conventions:
  query_default: 'repo:opendatahub-io/model-registry is:issue label:"type: docs-request"'
```

`/rfe-review MRDOC-123`, `opendatahub-io/model-registry#123`, and the issue URL all resolve to this type via the adapter-supplied token grammars; the rest of the pipeline never learns the difference.

---

## 9. The RHAISTRAT flip (target-state analysis — unscheduled)

**Not part of the implementation** (maintainer ruling 2026-09-03: the implementation is transparent to production and touches no binding). The flip stays the ADR owner's separate decision; this section records what the registry makes cheap and what would still have to happen so that, if it is ever scheduled, it is a *planned change* instead of an incident. Everything in §10 ships with RFEs at `(RHAIRFE, Feature Request)` and initiatives at `(RHOAIENG, Initiative)`; strat-creator's `(RHAISTRAT, Feature)` binding is never contended.

**Prerequisites the implementation delivers anyway** (each independently justified — post-fetch `(project, issue_type)` verification matters today because RHOAIENG mixes Epics, Stories and Initiatives; prefix unions and `tracker_ref`-based `is_existing` serve the §3.2.1 deployment override; detection convergence retires PR #10's four mechanisms)**:** self-describing artifacts + multi-candidate detection (§5); `is_existing := tracker_ref` (§5); tracker-discriminated `(project, issue_type)` identity + prefix lists (§3.2); issuetype in fetch fields (§5); `check_conflicts.py` on prefix-unions (PR-2); assess detection convergence + one scorer (§7.2/§7.4 — must precede the flip).

**Flip-phase work, from the verified blast-radius inventory:** the `parent_is_outcome` named hook (parent issue-type test — the closed condition vocabulary cannot express it; today's prefix condition would over-trigger on every post-flip parent); sweeps of the inverse predicates in `generate_review_pdf.py:444` / `generate_run_report.py:104` (Outcome rollups vs split children); dual-project JQL/snapshot coverage for the ~665 in-place RHOAIENG items and the RHAIRFE backlog (no bulk migration); the `re.fullmatch` rename guards `artifact_utils.py:877-880,:934-937` (today they reject any non-RHAIRFE-/RHOAIENG- key → `ValueError` → split_submit exit 4 → parent quarantined — an explicit flip blocker until PR-2 migrates them to the registry); the report predicates `generate_run_report.py:126-134,:212-213,:241-242`; the `strip_metadata` STRAT/RHAISTRAT literals (`jira_utils.py:713,:736`); descriptor edits per §8.5.

**External prerequisite 0 — strat-creator identity disposition (D6; relevant only if the flip is scheduled).** strat-creator's production binding **is** `(RHAISTRAT, Feature)` (`CLAUDE.md:83-86`, `clone_issue.py:79`, Cloners link to the RHAIRFE source) and epic-creator's entire *input* is the same pair; the ADR's flip target for rfe is also `(RHAISTRAT, Feature)`. Live RHAISTRAT (Atlassian MCP, 2026-09-03): Feature 10142 = **2,165 issues** (858 strat auto-created, 1,443 with a Cloners link, ~1,279 with no strat label at all), **Feature Request 10111 = 6 issues**, Initiative 10103, Outcome 10130, Risk 10146. The §3.3 uniqueness lint cannot see a type in another repo, so this is a decision, not a check. Options: **W1 (recommended default)** — flip rfe to `(RHAISTRAT, "Feature Request")`: the type exists, is pairwise-unique with strategy and initiative, keeps `issue_type` as the deterministic classifier (§6 stage 3), keeps strat's clone lifecycle and epic's input intact, same project and board; amend the ADR's 'RFE becomes a Feature' sentence. **W2** — one Feature = RFE + strategy at successive lifecycle stages (the strat document already has Business Need / Strategy / SME sections and its push replaces the Business Need with a link): identity ≠ type, two pipelines discriminated by section ownership + label families — a product decision needing its own ADR and the approved-RFE immutability work as precondition. **W3** — keep `(RHAISTRAT, Feature)` and discriminate by label: rejected, ~1,279 unlabeled human Features cannot be excluded by any negative filter. Owners: ADR owner + strat-creator owner; decided before any flip is scheduled — nothing in §10 waits on it.

**External prerequisites (ADR §8.1/§6, kept):** confirm the live RHAISTRAT issue-type *names*, workflows, transitions, and required fields before the flip (the emulator seed is self-authored — it validates the design against its own assumptions, not against live Jira; issue types are sent by name, so name collisions are the real risk, per A.4); inventory and update the external surfaces that assume today's projects — dashboards, scheduled JQL, data-repo partitioning, saved filters, reporting; strat-creator's RHAIRFE-keyed surfaces (`source_rfe` pattern `^(RFE-\\d+|RHAIRFE-\\d+)$`, Cloners scans in `find_strat_for_rfe.py`/`lock_issues.py`/`pull_strategy.py`/`jira_utils.py`, `reconstruct_business_need`); epic-creator as a consumer of `(RHAISTRAT, Feature)`; and `design-proposals/approved-rfe-immutability.md` as the precondition for a stable RFE → strategy contract (post-approval mutations flow into strategies on the next pull/import). **Workflow decision (see §3.4's workflow-states note):** decide whether RHAISTRAT applies one workflow scheme to both Feature and Initiative issue types (the preferred unified outcome — gives both tool-managed types a shared `To Do` "on deck" state and reinforces the one-workspace prioritization thesis); if so, reconcile the initiative binding's `state_map` transition names against that workflow, decide whether readiness maps to `To Do` (tool `state_map.ready` vs a tracker-side automation), and settle whether `To Do` items stay in the auto-fixer's `statusCategory != Done` selection scope. Fold the chosen workflow (incl. `To Do` and the state_map transitions) into the RHAISTRAT emulator seed so the gate below exercises it.

**Gate (ADR A.6, adopted):** a full `submit.py --dry-run` pass over both backlogs showing **zero unexpected "Would create"**, plus one green emulator integration run against the RHAISTRAT seed *after* the live-config confirmation has been folded into the seed. **Rollback:** reverting the descriptor edits restores the old bindings; items created in RHAISTRAT during the window stay readable (their prefix remains in `key_prefixes`) and writable-in-place (`tracker_ref` presence, not prefix, drives updates) — no artifact rewrite needed.

Nothing in §10 depends on it — the registry, the collapse, classification stages 1–2, the rubric track, and the tracker seam are all independently valuable, and all ship with production bindings untouched.

---

## 10. Migration plan (PR-sized; each step shippable; pin-test mechanic throughout)

The descriptor becomes source of truth **by test before by import**: PR-1 pins every existing dict equal to descriptor projections; scripts then migrate as the pin test shrinks. **Transparent-implementation invariant (every PR):** no production binding changes — Jira projects, issue types, key prefixes, label values, CI prompt strings, and existing artifacts stay exactly as they are; each PR is verifiable by the byte-stable list at the end of this section plus a `submit.py --dry-run` diff over the rfe and initiative backlogs showing zero behavioral change. **Promotion discipline for every behavior-touching PR (RTE §5.4, restored):** land on main → promote `ci-stage` → run `autofix-rfe-stage-dry` (its `/rfe.auto-fix` prompt keeps resolving throughout via the §4.4 compat shim) and the per-type eval → promote `ci-prod` → watch one scheduled prod run. **Eval bar (A.5's open point, closed):** a phase gate passes when the descriptor's `eval.thresholds` are met and no judge mean regresses by more than 0.3 versus the phase's recorded baseline.

**Open-PR coordination (2026-09-03; none of these is a hard prerequisite for PR-1).** The pin-by-test mechanic makes ordering self-correcting: PR-1 pins whatever main holds at merge time, and any later change to a pinned registry must update the descriptor projection in the same PR. #148 (Phase-1 create barrier) **merged 2026-09-03 as `446ff58`** — PR-1 pins its `create` row directly. Two consequences for PR-1's author: the row is **rfe-only** (`check_review_progress.py PHASE_CHECKS` has no `initiative-create` twin, so `initiative-speedrun` has no Phase-1 barrier until PR-5's generic body inherits it keyed on `dirs.tasks` — one more instance of the fork-drift class), and `create` uses a **stricter check than `fetch` on the same path** (`check_id()` waits for frontmatter, not file existence, because create agents write the body first), so the derived `PHASE_CHECKS` projection carries a per-phase `check: exists | frontmatter_valid` mode, not just path templates. #149 (frontmatter parse/repair) **merged 2026-09-04 as `1781dae`** — no type-keyed registry touched, nothing new to pin; its body-only rule landed with identical wording in both `review-agent.md` twins (`rfe.review:23-24`, `initiative-review:26-27`), confirming it is Tier-2 skeleton text PR-5 lifts verbatim, and the `ValidationError` contract now lives at `artifact_utils.py:321` (raised by `read_frontmatter` `:586`, `read_frontmatter_validated` `:620-652`, `update_frontmatter` `:712`). The single real ordering constraint — #153 (initiative parity) before PR-4 records the initiative eval baseline — is **satisfied: merged 2026-09-04 as `f89444a`** (three initiative skill files only, no scripts). Its three mechanics are now byte-comparable across the twins and go straight into the shared skeleton in PR-5: the error-path stub emits `score=0` plus the five zeroed criteria in both pipelines (`initiative-review/SKILL.md:53,:134`) — the concrete instance of §3.3's "error-stub command generated from `schema.review.score_fields`"; the revise-agent step order is identical (Read Context / Revise / Update Frontmatter / Content Preservation / Revision History) in both `revise-agent.md` files, confirming the Tier-2 classification (§4.2); and `batch_size` is always a concrete integer (`initiative-speedrun/SKILL.md:20`). No open-PR ordering constraint remains. #146 (bot-authored, 2026-07-28, 573 lines against a five-week-stale `pipeline_state.py`) is **superseded by PR #171** (merged 2026-09-04 as `5d25859`) carrying its ID validation, generalized, plus the shell removal. Its `REASSESS_FIXUP` `ids_file` change is **wrong, not a fix**: `advance()` at `REASSESS_RESTORE` (`pipeline_state.py:566-580`) runs `filter_for_revision` over the reassess set and writes the still-needs-revising subset to `tmp/pipeline-revise-ids.txt`; `REASSESS_REVISE` (`:295`) revises that subset and `REASSESS_FIXUP` (`:302`) runs `check_revised --batch` over the same file — the correct one. Pointing it at the full reassess set would run the revision check over items nobody revised that cycle; the new PR pins the correct file with a test. Its `_validate_ids` regex (`^(RFE-\\d+|RHAIRFE-\\d+)$`) is RFE-only and would reject every initiative id, so the port validates a type-neutral grammar (`^[A-Z][A-Z0-9]*-\\d+$`) inside `_read_ids` — the single helper every consumer uses before interpolating ids into `shell=True` commands (`advance()` `:558/:573/:585`, `cmd_run_phase` `:829`, the wave builder) — and the registry takes ownership of the grammar in PR-2. Its 573-line rewrite of every `PHASE_CONFIG` entry into list form is **not** carried over; #171 reaches the same outcome (no `shell=True` anywhere, asserted by a test) by splitting at the two execution sites and turning SETUP into a concurrent `commands` list with preserved exit semantics, leaving the string templates for the registry to regenerate. #146 stays open for rework after #171 lands. #155 any time before PR-4. Rebase #151 before PR-2 touches `generate_run_report.py`. Close as superseded: #130/#122 (→ §3.2.1; cherry-pick `collect_recommendations.py --from-reviews`), #115 (→ §3.5.1). After the registry: #112 rebased after PR-2 (approve policy implemented once), #100 after PR-5 as `types/rfe/dimensions/jtbd.md`, #133's executor after PR-5 as generic `rfe-dupes`/`rfe-merge`. PR-1's own paths (`types/`, `_schema/`, `type_registry.py`, `validate_types.py`, settings.json) are touched by no open PR. Run the cross-repo parity sweep (§1.1, D9) in skills-registry in parallel with PR-1.

1. **PR-1 — registry, inert.** `types/{rfe,initiative}/` + `_schema/` + `type_registry.py` + `validate_types.py` + pin tests over all 19 registries **and** (A.1's widening) the prefix predicates and schema regexes; base-schema additions `type:`/`tracker_ref:` (optional — no behavior change); the §3.6 tracker-readiness naming (identity as a `tracker:`-discriminated union, `conventions:` block, `query_default`, priority scoped to the binding — all zero-behavior schema shape); `issuetype` added to fetch field lists; settings.json stale-entry purge + new allowlist entries; provider guide + `types/rfe/` skeleton as the copy-from artifact; the `RFE_CREATOR_EXTRA_TYPES` CI test; the anti-regression prefix-predicate lint; the `rfe-creator.update-deps` fix; CODEOWNERS. Pin tests additionally cover `generate_run_report.TYPE_CONFIG.tracker_prefix/local_prefix`, the four `local_id` schema patterns, `SNAPSHOT_CONFIG.quarantine_label`, the 14-row `PHASE_CHECKS` incl. #148's rfe-only `create` row and its `frontmatter_valid` check mode (the projection = `dirs` × poll_prefix × dimension names × per-phase check mode), and `tests/test_report_roundtrip.py` becomes a registry-projection test (`BOOTSTRAP_CONFIG.report_prefix/item_key := snapshot.report_prefix/reporting.item_key`); the new allowlist entries are exactly `Bash(python3 scripts/type_registry.py *)`, `Bash(python3 scripts/validate_types.py *)`, `Bash(python3 scripts/generate_eval_config.py *)` (relative form only — `AGENTS.md:62-66`); `type_registry.py` and `types/_schema/type.schema.json` are **import-clean** (stdlib + pyyaml, no rfe-module imports, explicit root arg, `__file__`-relative discovery) so the `creator-core` follow-up can lift them; the schema reserves `kind`, `inputs`, `produces`, the open `labels` map, `local_id_pattern`, `task.priority`, and the binding-override text (§3.2.1); the descriptor gains `context_sources`, `condition.context_exists`, `review.extra_scores`, nested `extra_fields` (schema only); a paper `types/epic/type.yaml` for epic-creator's real `(RHAI, Epic)` binding is written as the third data point (R1) and not shipped.
2. **PR-2 series — scripts adopt the registry**, one or few per PR: 16 of the 17 dicts (`pipeline_state.py`'s prompt/skill-path and dispatch entries migrate in PR-5 with the file moves — its pin test persists until then), SCHEMAS/PHASE_CHECKS, argparse `choices` → `registry.choices()`, the 7 sniff sites → `detect()`, the 3 forked function pairs → single generics, `check_conflicts.py` prefix test → prefix-union, `is_existing` → `tracker_ref`-aware, `fetch_issue.py --fetch-all` made type-aware, `--jql` gains the neutral `--query` alias, no new tracker calls land outside the adapter module (§3.6 seam discipline; `label-rfes.py` flagged as the non-conforming writer), **the bootstrap made descriptor-driven and `rubric.ref`-pinning (§7.3)** with assess-rfe tagging a release; `generate_run_report` projects `tracker_ref`/`role` from frontmatter (prefix-union fallback), retiring the `:212-213` predicate and `TYPE_CONFIG.tracker_prefix/local_prefix`; the three forked pairs' `re.fullmatch` guards validate against `identity.local_id_pattern` + the `key_prefixes` union; `bootstrap_snapshot.py:176` reads `snapshot.prefix`; the quarantine label is read from `conventions.labels.split_quarantine` at `snapshot_fetch.py:59/:64` and `submit.py:283`; the adapter module split `scripts/trackers/jira.py` lands in this series (extraction unit, §3.6); the approve policy (`submit.py:806-814,:978-990`) is implemented once and applied to split children (supersedes PR #112's shape); the registry-driven launcher keeps commands shell-free (PR #171 (merged `5d25859`) removed `shell=True` from `_run_script`/`cmd_run_phase` via `shlex` argv and made SETUP a concurrent `commands` list; regeneration must not reintroduce a shell) and validates IDs before `{ID}` substitution (landed in #171 as `_validate_ids` in `_read_ids`). `submit.py`/`split_submit.py` last, gated on the emulator integration suites running in CI. The deferred wait-for-wave max-wait→retry escalation ships alongside (R2).
3. **PR-3 — resolution ladder.** `resolve`/`detect` multi-candidate; batch mapping form across all three batch-root consumers (`validate_batch_input.py`, `next_rfe_id.py --from-batch`, the speedrun body); frontmatter `type:` written on create/fetch; post-fetch `(project, issue_type)` verification; headless conflict hard-fail; interactive-fallthrough rung.
4. **PR-4 — eval single-sourcing + rubric v1.** `generate_eval_config.py` + shared eval skeleton + fragment schema; **both** eval yamls regenerated (rfe byte-stable modulo the explicit drift dispositions of §4.5); the in-sync lint added to gate 1; assess-rfe PR #10 merged as rubric v1; baselines recorded per type. (DoR v2 authoring proceeds in parallel on the product track.)
5. **PR-5 — skill collapse.** Generic bodies land under the new `rfe-*` names (§4.4) + Step-0 resolve; the six legacy `rfe.*` skills become ~8-line compat shims; skeletons live with the new skills (`.claude/skills/rfe-review/prompts/`), typed judgement extracted to `types/<t>/` per the §4.2 tiers; `assess-agent`/`fetch-agent` unified; the six `initiative-*` directories and **all three** dimension-skill directories deleted; the reference sweep of §4.4 (`.ambient/ambient.json`, README/AGENTS/eval-README command lists, both eval configs regenerated and flipped to `skill: rfe-speedrun` — initiative adding `--type initiative`); the four stale rubric-path sites (`rfe.review/SKILL.md:85,:227`; `rfe.auto-fix/SKILL.md:127`; `rfe.split/prompts/split-agent.md:25` — all point at `.context/assess-rfe/scripts/agent_prompt.md`, which does not exist in assess-rfe e27d7ac) and the two dead headless return paths (`rfe.review:319` 'Step 3b', `rfe.split:155` 'Step 3d' — both read `tmp/autofix-config.yaml`, which nothing writes) fixed by the collapse: the shared skeleton's headless-return block is rewritten against `pipeline-state.yaml`; the generic speedrun body inherits #148's Phase-1 barrier keyed on `dirs.tasks` + `id_field`. Gate: the promotion discipline above, with both types' evals meeting the bar against the PR-4 baselines. **Rollback:** a single revert restores the deleted skill trees and the previous eval configs; artifacts and descriptors are untouched by the revert.
6. **PR-6 — rubric v2 swap** (when the product track delivers): descriptor `score_fields` → DoR set, `rubric.ref` bump, DoR `template.md`, regenerated eval config, one re-baseline; run reports state the measurement change (§7.5).
7. **PR-7 — classification stages 1–2** (§6) + their eval cases.
8. **PR-8 — provenance + one scorer.** `rubric_version`/`type_version` stamping; SETUP fail-fast (`validate_types.py --verify`); the coordinated scorer collapse (§7.2) and assess detection convergence (§7.4) — both independently justified (the collapse removes a duplicated containment boundary; the convergence retires PR #10's four detection mechanisms), and both would be prerequisites should a flip ever be scheduled.
9. ~~PR-9 — the flip~~ — **removed from the plan** (maintainer ruling 2026-09-03: transparent implementation, no production binding change). §9 stays as target-state analysis; scheduling a flip is a separate ADR-owner decision with D6 as its gate. The slot is left vacant so PR-10 references elsewhere stay valid.
10. **PR-10 — ecosystem, no deadline** except where noted: the *optional* autofixer prompt MR (`/rfe.auto-fix` → `/rfe-auto-fix`, one line — removes the compat-shim hop from the production hot path; the shims stay either way); autofixer `RESULT: NO_TASKS` marker + green-run guard MRs; eval-repo `jira_cases.py` reads JQL/annotations from the descriptors — **required before any third type's eval runs there** (its hand-synced `TYPES` map breaks for unknown types); skills-registry `provides` metadata; deep docs rewrite; the three latent initiative-CI bugs (`REPORT_TS` grep missing `initiative-run-*`; `restore-artifacts.sh` hardcoded `rfe-tasks`; `bootstrap_snapshot._run_dir_has_snapshots` hardcoded `issue-snapshot-` at `:176` — the last fixed in PR-2) fixed when D4's initiative CI job is scheduled; the engine/phase-table split of `pipeline_state.py` (`PHASES` + `PHASE_CONFIG` + `advance()` + `PHASE_CHECKS` + cycle caps out of the dispatcher) so epic-creator can become an engine consumer; the follow-up ADR `creator-core` (vendored core + sync script, strat-creator as the first adapter consumer) and the weekly AST-normalised parity sweep hosted in skills-registry's Upstream Plugin Checks (D9); strat-creator's eval staging switched to its embedded assess and the registry's `depends_on: [assess-strat]` dropped (D3).

Kept byte-stable throughout: artifact dir names, `FULL RUN COMPLETE`, `"No RFE task files found"` (rfe), `tmp/rfe-assess/single/` staging, the cwd-relative `scripts/` invocation convention (§3.5.1), plugin names, every production CI prompt string (the autofixer's `/rfe.auto-fix` resolves via the §4.4 compat shim; an optional MR later removes the hop).

---

## 11. Decisions needed & risks

| # | Item | Recommendation |
|---|---|---|
| D1 | Generic-surface naming | **Resolved: new generics named `rfe-*`** (dot names violate the agent-skills spec), legacy `rfe.*` kept as compat shims — maintainer ruling 2026-08-12. The separate neutral-branding question (`request-*` vs rfe-branded) remains deferred; revisit alongside the flip, when "RFE" stops being a project name anyway |
| D2 | PR #10 disposition | Merge as interim v1 (§7.5); DoR v2 is a descriptor edit after the collapse — decide before PR-4 |
| D3 | assess packaging | **Re-decided as two orthogonal choices** now that strat-creator has embedded assess-strat (its ADR-0005 / PR #70, 2026-09-01: `scripts/assess-strat/`, `.claude/agents/strat-scorer.md`, in-repo `assess-strat`/`export-rubric` skills — a third fork of the run-management scripts at 0.46–0.76 similarity and a third scorer agent). (1) Rubric + scorer agent are *type-owned judgement*: the descriptor accepts `rubric: {repo: self, path: types/<t>/rubric/agent_prompt.md}` pinned by content hash (`rubric_version` = sha) as well as `{repo, ref, path}`; assess-rfe's separate repo stays the justified exception because four external assessor CI jobs consume it (§7.1); §3.3 gate 2 handles the embedded case. (2) Run-management scripts (setup_run / next_action / parse_results / summarize_run) are *shared library* and stop forking — a `creator-core` item (D9). §7.2's one-scorer collapse covers rfe/initiative only; strat-scorer is out of scope |
| D4 | `types/initiative/` ownership | Assign an owner or schedule the first initiative CI job — a type without a consumer rots regardless of architecture |
| D5 | GitHub binding shape (when the first GitHub-backed type arrives) | Tracker binding is **per type**, one repo per type at v1; `kind` defaults to a discriminator **type label** (org-level native issue types are opt-in — they need org-admin setup and are plan-dependent); alias-prefix key convention per §3.6 |
| R1 | Two-data-point overfit (condition vocabulary, `resplit`, booleans) | **Do not prototype `types/strategy/`** — strat-creator is a peer pipeline of a different kind (§3.7; ~15 schema extensions and four foreign stage names would be needed), and its real binding `(RHAISTRAT, Feature)` makes the earlier 'must not claim' caveat unsatisfiable — that collision is now D6 / §9. The validating third data point during PR-1 is a **paper descriptor for epic-creator's real `(RHAI, Epic)`** (in production twice daily): identity / conventions / `schema.task` must express its value-templated labels (`epic-creator-impl-<x>`, `-ai-impl-<high|medium|low>`), its `P0/P1/P2 → Critical/Major/Minor` priority map, composite local ids (`RHAISTRAT-1234-E001`), and `inputs:` from strategy; the reserved `inputs:` block is additionally validated against strat-creator's `config/pipeline-settings.yaml`. Anything either needs beyond the reserved keys is recorded, not built |
| R2 | Barrier semantics as hard contract (numeric scores + error-stub shape) | Unchanged from both parents; the wait-for-wave escalation fix ships with PR-2 — a third type multiplies exposure |
| R3 | Headless hazards | Resolution in scripts with exit codes; the interactive-affordance and prefix-predicate lints; allowlist entries in the same PR as any new script |
| R4 | Rubric-v2 measurement change | Run reports state the baseline change explicitly (ADR §6) |
| R5 | Classification wrong calls | Stage 1 is confirm-on-ambiguity and interactive-only; routing authority stays gated behind Stage 3's corpus + accuracy bar |
| R6 | GitHub emulator gap | No GitHub emulator exists anywhere in the ecosystem — the one genuinely new artifact a GitHub-backed type needs (§3.6); until it exists the seed obligation is satisfied by the binding's declared degradation (fixtures / throwaway repo). Longer term, emulate the adapter *seam*, not the REST API — one emulator for all trackers |
| D6 | RHAISTRAT identity collision (rfe flip vs strat-creator's `(RHAISTRAT, Feature)`) | **Not a gate for the implementation** — maintainer ruling 2026-09-03: the implementation is transparent to production and the flip is unscheduled, so the collision never materializes. Recorded for whenever a flip is considered: **W1** flip rfe to `(RHAISTRAT, "Feature Request")` (type 10111 exists, 6 issues); W2 (one Feature, two pipelines) only via a product ADR; W3 (label discrimination) rejected. Owners then: ADR + strat-creator (§9) |
| D7 | Packaging convention / PR #115 | cwd = plugin root; root `scripts/` + `types/` canonical; `__file__`-relative discovery; #115 closed; strat's symlink convention is the named portability path (§3.5.1) |
| D8 | Deployment-binding override shape (PR #130/#122) | Descriptor = default; type-scoped env or workspace-file override; lint on effective bindings; no `DRAFT-` at v1 (§3.2.1); schema in PR-1, `resolve` output in PR-3 |
| D9 | Shared core across rfe / strat / epic | Independent repos + AST-normalised weekly parity sweep now (skills-registry Upstream Plugin Checks); vendored `creator-core` + sync script later, with strat-creator as the first adapter consumer; pip package and monorepo rejected for now; name the `creator-core` ADR owner (§1.1, §3.7) |
| D10 | revise-agent composition tier | Tier 2 (shared step order + one `revise_rules` slot); split-agent stays Tier 3 with a required-line lint (§4.2) |
| D11 | Run-report schema scope | rfe/initiative-only at v1 (no cross-repo consumer of `report_schema_version` exists; epic writes ad-hoc YAML); `entry_kind: derivation` deferred |
| R7 | Published cross-pipeline contract strings | `labels.rubric_pass` values, `removed_context_preamble`, the `{key}-strategy.md` attachment convention are consumed by strat-creator (86 occurrences) / epic-creator — frozen, listed in §3.4, changed only with a downstream migration note |

The parents' shared closing point stands, sharpened: the descriptor makes the skill layer collapsible; the ladder would make a flip survivable if one is ever scheduled; and the whole implementation ships transparently, with production bindings untouched. In this sequencing each phase is independently shippable *and* each one makes the next one smaller.
