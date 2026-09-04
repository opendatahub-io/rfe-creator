# Work Item Types — Alignment Review (design @ 8888b18 vs main @ fbf96bb, siblings, ecosystem)

**Status:** Review of `design-proposals/work-item-types-unified.md` before PR-1 starts.
**Independent spot-checks (2026-09-03, by the document owner):** epic-creator `submit.py:56-57,:326` creates `(RHAI, Epic)` issues; `generate_run_report.py:212-213` prefix-derives `tracker_ref`; `local_id` present on all four schemas; `rfe.speedrun/SKILL.md:23` still calls `state.py clean`; epic `jira_utils.py` has no certifi; `snapshot_fetch.py:59,:64,:375` vs `jql_query.py:79-90` label-set split; quarantine label composed again at `submit.py:283`; strat `strategy-common/scripts → ../../../scripts` symlink convention (7 links); live RHAISTRAT issue types via Atlassian MCP: Initiative 10103, Feature Request 10111, Outcome 10130, Feature 10142 (2,165 issues), Risk 10146. All confirmed.
**Maintainer disposition (2026-09-03, after this review):** the implementation will be **transparent to production** — no Jira binding, project, issue type, label value, CI prompt, or artifact changes in any §10 PR. Consequences applied to the unified design: PR-9 (the flip) is removed from the plan; §9 becomes target-state analysis; **D6 is no longer a gate for any implementation PR** (it gates only a future decision to schedule the flip, where W1 stays the recorded recommendation); B6's R1 rewrite still applies; the flip-readiness schema shape is kept because it is independently justified by the deployment-binding override and post-fetch verification.
**Date:** 2026-09-03. **Inputs:** five verification lenses (rfe-delta, open-prs, strat-creator, epic-creator, ecosystem-core), each execution-verified against `/tmp/rfe-creator-main` (fbf96bb), `/tmp/strat-creator-main` (1b137c1), `/tmp/epic-creator-main` (5c46fd9), `/tmp/assess-rfe` (e27d7ac / pr10 6602a12), skills-registry, agentic-ci, and `gh` for the 12 open PRs. Line cites below are at those SHAs. Where readers disagreed, this document adjudicates and says why.

---

## 0. Verdict and readiness

**Verdict: the design is sound and can start PR-1 after a bounded set of edits — none of which change its architecture, several of which change the *shape* of descriptor fields and therefore must land before `schema_version: 1` freezes.** Nothing in the 79-commit delta refutes the three distinguishing properties (identity = `(project, issue_type)` with prefix lists; tracker seam; skeleton+tokens+typed files). What the delta and the siblings do is (a) add per-type facts the descriptor does not yet carry, (b) show that `tracker_ref` already exists on main with the right semantics but the wrong *derivation*, (c) prove that two of the design's refusals ("phase graph is machinery", "script locations") are true only inside rfe-creator, and (d) turn R1's hypothetical identity collision into a live, populated one.

**Blocking before PR-1 (shape changes; cannot be added later without a schema bump or artifact migration):**

| # | Item | Why it cannot follow |
|---|---|---|
| B1 | `conventions.labels` closed map → open map with reserved keys; add `split_quarantine` + `split_child_marker` (§1.2) | Main already has three label sets across two query wrappers; a type that omits the quarantine label re-selects failed-split parents forever (snapshot_fetch.py:371-379 vs jql_query.py:79-90) |
| B2 | Base schema triple `type` / `tracker_ref` / `local_id` (not a pair) and the rule "tracker_ref is *read from frontmatter*, never re-derived from an id prefix" | `local_id` already exists on all four schemas (artifact_utils.py:67-76,:114-123,:196-201,:234-239); the prefix derivation at generate_run_report.py:212-213 is a new inverse predicate and mis-attributes composite ids |
| B3 | `identity.local_id_pattern` (regex) alongside `local_prefix`; `detect()` full-matches | `local_prefix` is a constant; sibling ids like `RHAISTRAT-1234-E001` prefix-match RHAISTRAT- silently |
| B4 | Reserve top-level `kind:` (v1 enum `[work-item]`), `inputs:`, `produces:` — schema-only, validated, no consumer | Two requesters already exist for the input relation (strat-creator ← rfe, epic-creator ← strategy); second-requester rule says promote now |
| B5 | Deployment-binding override subsection (descriptor = default binding; effective binding = descriptor ⊕ type-scoped override; lint on effective bindings) | Schema text + lint semantics belong to PR-1; implementation to PR-3. Decides #130/#122 |
| B6 | R1 rewritten: no `types/strategy/` prototype; identity collision moved to §9 as a named external prerequisite with options W1/W2 | R1 as written is unsatisfiable (§3.3 below) |
| B7 | Explicit packaging stance replacing the "script locations" refusal and the "scripts/ byte-stable" promise; disposition of PR #115 | Decides whether PR-5 rewrites 185 script references once or twice |
| B8 | Open-PR coordination (§2) — *downgraded after maintainer review*: not a PR-1 prerequisite | Pin-by-test is self-correcting (later registry changes must update the descriptor in the same PR); the one real constraint is #153 before PR-4's initiative baseline; #146's launcher rewrite must not land in its current shape |

**Can follow PR-1 (additive optional keys or plan-text changes):** seam op additions (§3.6), `pipeline.stages` open list, `condition: {context_exists}` + `context_sources`, `review.extra_scores`, `companions.extra_suffixes`, `reporting` split, `state_map.close_duplicate`, PR-2/PR-5/PR-10 list refreshes, §1.1 cross-repo table, the parity sweep.

**The strat/epic answer in one line:** neither is a *type* of rfe-creator's pipeline; both are pipelines of a different *kind* that share rfe-creator's engine, tracker adapter, and frontmatter/state utilities by copy. The design should (i) scope its refusals to the review/split kind, (ii) reserve `kind`/`inputs`/`produces` so the descriptor can *describe* them without *running* them, (iii) write `type_registry.py` and the PR-2 adapter split as import-clean extraction units, and (iv) name the shared-core follow-up ADR — and stop there.

---

## 1. Drift since baseline (8888b18 → fbf96bb)

### 1.1 Citation corrections (design cite → main today)

| Design cite | Now | Note |
|---|---|---|
| `submit.py:472` is_existing | `:749` | unchanged single-prefix `startswith` |
| `submit.py:675` "Approved" | `:987` (+ `:981`) | unchanged |
| `submit.py:59-126` TYPE_CONFIGS | `:61-128` | + `:40` imports `generate_run_report.TYPE_CONFIG` for `output_prefix` (`:322-331`) — one restatement removed |
| `"No RFE task files found"` `:253` | `:411` | byte-stable for rfe |
| `split_submit.py:61-104` SPLIT_CONFIG | `:109-152` | content unchanged |
| `split_submit.py:187` "Work item split" | `:210,:410,:497,:610,:716` (+ dry-run text `:685`) | 2 → 5 code sites |
| `split_submit.py:355` link / `:458,:475` Closed/Obsolete | `:716` (+ `:610` recovery) / `:820,:837` | unchanged |
| `split_submit.py discover_state :151` | `:275` | rewritten: id-keyed, marker-label search `:436-520`, fingerprint adoption guard; exit codes `:65-67`, `_classify_exit :80-106` |
| `generate_review_pdf.py:80-103` / `:341` | `:82-105` / `:444` | + `_split_outcome :281-298` |
| `generate_run_report.py:68` | `:104` | TYPE_CONFIG `:24` gained `tasks_dir`/`tracker_prefix`/`local_prefix`; rfe `extra_entry_fields` `[]→["needs_attention"]` (`:31`); NEW predicates `:126-134`, `:212-213`, `:241-242` |
| `artifact_utils.py:60-285` SCHEMAS / `:195` parent / `:748-1052` forks | `:60-315` / `:219` / `:778-1101` | forks gained literal `re.fullmatch` guards `:877-880`, `:934-937` and write `local_id` `:907-924`, `:967-983` |
| `snapshot_fetch.py:56-65` / `:95-116` / `:125` | `:56-67` / `:97-118` / `:127` | + `quarantine_label` `:59,:64`; hard filter `:371-379` |
| `jira_utils.py:163-174` reporter / `:710` regex | `:189-200` / `:736` | + `get_myself :110-117`, `search_issues :120-134` |
| `pipeline_state.py:980-1008` launch builder | `:990-1018` | + `:396-397` `--report-stage pre_submit`; `:716-727` ERROR_COLLECT→REPORT |
| `docs/snapshot-incremental-fetch.md:90-95` | `:91-96` | invariants 1-8 at `:412-456` unchanged; bootstrap rules `:384-402` new |
| `rfe.review/SKILL.md:310` dead return | `:319` | still dead; second dead path `rfe.split/SKILL.md:155` |
| `check_conflicts.py:76`, `pipeline_state.py:94-119/:142`, `verify_phase.py`, `check_review_progress.py:18-32`, `check_right_sized.py:52`, `jql_query.py:79-90`, `validate_batch_input.py:36`, `tests/conftest.py:91-121` | unchanged | — |

### 1.2 New per-type facts the descriptor must carry

1. **Quarantine label** — defined per type as a literal (`snapshot_fetch.py:59,:64`) *and* re-composed as `f"{label_prefix}-split-quarantine"` (`submit.py:283`): two definitions of one fact, the exact drift class the design targets. Consumed by the snapshot/bootstrap hard filter (`snapshot_fetch.py:375-379`, `bootstrap_snapshot.py:457-461`) but **not** by `jql_query.py:79-90`, which still excludes only ignore + rubric_pass. Three label sets across two wrappers; §3.6 invariant 4 ("exclude closed, exclude opt-out labels, include unlabeled") is now incomplete.
2. **Split-child marker label** `f"{label_prefix}-split-child-{parent}-{childid}-{12hex}"` (`split_submit.py:233-253`, applied at create `:637-642`) — the recovery signal "that exists from the instant the child does". ~57 chars; GitHub caps label names at 50 → a declared degradation for §3.6.
3. **Run-report schema** — root `report_schema_version: 1`, `type`, `report_stage: pre_submit|final` (`generate_run_report.py:62-72,:356-359`); per entry `tracker_ref`, `role`, `local_id` (`:224-248`). Reports are self-describing today; bootstrap reads `report_stage` only (`bootstrap_snapshot.py:574-586`).
4. **`tracker_ref` semantics reconciliation** — the report's code comment cites this design's §5 (`:256-262`): "the id itself once submitted, null while none exists". Semantics **match**; name **matches**; derivation **does not**: `item_id.startswith(config["tracker_prefix"])` (`:212-213`) is a new single-prefix predicate. Post-flip a RHAISTRAT- item gets `tracker_ref: null` and `role` misclassified; a composite id (`RHAISTRAT-1234-E001`) would be reported as a tracker id. Ruling: keep the name, make PR-2 *project* `tracker_ref` from frontmatter with a prefix-union fallback for pre-migration artifacts; add `:126-134/:212-213/:241-242` to the §9 sweep and `tracker_prefix`/`local_prefix` to the PR-1 pin test. The design's "born with zero existing carriers" sentence is false and must go.
5. **`local_id`** on all four schemas with literal patterns (`^RFE-\d+$`, `^INIT-\d+$`) — pattern must derive from `identity.local_prefix`/`local_id_pattern`.
6. **Seam growth** — `get_myself` (auth preflight, `submit.py:458-475`), single-page `search_issues` (marker recovery), and a failure-classification contract (exit 4 per-parent / 5 systemic / 64 usage, `split_submit.py:65-106`, loop policy `submit.py:503-651`). HTTP-code semantics are adapter-private → the seam needs `classify_failure(exc) → per_item|systemic`.
7. **A new hand-restated prefix** — `bootstrap_snapshot._run_dir_has_snapshots` hardcodes `"issue-snapshot-"` (`:176`) though `SNAPSHOT_CONFIG.snapshot_prefix` exists → for `--type initiative` the partial-clone and walk-back guards never fire. Third latent initiative-CI bug (design lists two).
8. **Rename guards** — `re.fullmatch(r"RHAIRFE-\d+")` etc. (`artifact_utils.py:877-880,:934-937`) reject any post-flip RHAISTRAT- key → `ValueError` → split_submit exit 4 → parent quarantined. Explicit flip-blocker for §9; generics must validate against `local_prefix` + `key_prefixes` union.

### 1.3 Confirmed / refuted

**Confirmed:** 17 type-keyed dicts, 22 `--type` scripts, SCHEMAS + PHASE_CHECKS → 19 registries; `issuetype` fetched nowhere (`snapshot_fetch.py:127`), sent by name on create (`jira_utils.py:175`); `Approved`/`Closed`/`Obsolete` hardcoded; `assess-agent.md` pairs 100% identical; 8 snapshot invariants unchanged; 31 phases; `FULL RUN COMPLETE` (`finish.py:4`); PR #152's fixes applied to all twins; `artifact_utils.py:219` vs `validate_batch_input.py:36` parent-pattern disagreement persists.

**Refuted or stale:** "tracker_ref added with zero carriers" (§3.2/§5); "23 prefix predicates" — the lint pattern now matches **43 lines in 11 files** (was 31; +12: `artifact_utils.py:73,:120,:199,:237,:877,:879,:934,:936`, `generate_run_report.py:36,:37,:55,:56`); base-schema additions "type + tracker_ref" (local_id exists); "two latent initiative-CI bugs" (three); eval configs "66% line-identical" (75.9% verbatim / 83.8% difflib at HEAD — converged by near-identical PR #154/#165 edits pinned by `tests/test_eval_revision_flag_judge.py::test_both_configs_identical`); `split_link_type` "hardcoded at :187" (5 sites); `before_score_name_map: {}` for initiative was wrong *at baseline* — `generate_review_pdf.py:96-105` carries 8 aliases incl. `RS`; "agentic-ci `client.py` almost verbatim the seam list" → it is a superset (attach_file, assign, set_custom_field, set_security_level, get_label_author, get_description_editors, update_comment); "duplicate converter in assess-rfe and another in agentic-ci" → 5 ADF converters (rfe/strat/epic jira_utils, assess-rfe `dump_jira.py`, agentic-ci `jira/adf.py`); PR-5's "in-passing fixes" list is incomplete — **four** stale rubric-path sites (`rfe.review:85,:227`, `rfe.auto-fix:127`, `split-agent.md:25` → `.context/assess-rfe/scripts/agent_prompt.md`, which does not exist in assess-rfe e27d7ac) and **two** dead headless return paths (`rfe.review:319`, `rfe.split:155`, both reading `tmp/autofix-config.yaml` that nothing writes — PR #153's body confirms).

### 1.4 Re-measured divergence

Skill-pair similarity unchanged within noise (auto-fix 93.3→93.3, review 81.9→81.7, split 78.1→77.6, speedrun 78.2, submit 67.8, create 56.3 — coarse normaliser; direction is the finding). Eval yamls converged (above). Shared-script drift *inside* rfe-creator did not grow; drift *across* repos did (§5). PR #150 (parity lint) is closed unmerged and PR #153 is open — nothing guards fork drift until PR-5.

---

## 2. Open PR interactions

| PR | What | Conflict / overlap with design | Recommendation |
|---|---|---|---|
| #153 initiative parity — **merged 2026-09-04 (`f89444a`)** | error-stub emits `score=0`+`scores.*=0`; revise-agent step order; batch_size sentence | All three are *skeleton* mechanics — evidence that step ORDER and error-stub shape belong to the shared skeleton, never `types/<t>/`; the PR-4 baseline constraint is satisfied, no open-PR ordering constraint remains | **Merge before PR-4's initiative baseline** (the one real ordering constraint; not a PR-1 prerequisite) so the baseline reflects the corrected pipeline — or record the caveat beside the baseline; content dies with `initiative-*` in PR-5 but its mechanics land in the skeleton once. Move revise-agent to Tier 2 (§6, item 16) |
| #148 Phase-1 barrier — **merged 2026-09-03 (`446ff58`)** | `check_review_progress --wait --phase create`; AGENTS.md wording fix | Adds a 14th PHASE_CHECKS row (`artifacts/rfe-tasks/{id}.md`) PR-1 now pins directly; the row is rfe-only (no `initiative-create` twin — fork drift until PR-5) and uses a stricter frontmatter-valid check than `fetch`, so the derived projection needs a per-phase check mode; generic speedrun body must inherit it keyed on `dirs.tasks` | Any time — before PR-1 saves a one-line descriptor touch, after PR-1 the pin test requires it in the same PR; adopt its wording ("ending a turn to wait for work that cannot notify ends the run") in §3.3/R3 |
| #149 frontmatter parse/repair — **merged 2026-09-04 (`1781dae`)** | hardened `_FRONTMATTER_RE`; `ValidationError` from `read_frontmatter`; "body only, no `---` block" in both review-agents (identical wording in both twins) | Type-neutral, no registry touched — nothing for PR-1 to pin; the prompt rule belongs in the Tier-2 review skeleton; exception type is a script contract | Any time (no PR-1 path overlap); PR-5's skeleton absorbs the body-only rule; declare the contract in §3.4 |
| #146 RHAIFIRST-320 (conflicts) | REASSESS_FIXUP `ids_file` bug (`pipeline_state.py:299-302` still `pipeline-revise-ids.txt`); `_validate_ids`; list-form commands | Real bug independent of design; the list-form rewrite conflicts with `_phase_definitions()` | **Split** — *corrected 2026-09-04*: the `ids_file` change is **wrong** (REASSESS_RESTORE writes the filter_for_revision subset to `pipeline-revise-ids.txt`, `:566-580`; REASSESS_REVISE `:295` and REASSESS_FIXUP `:302` correctly share it); PR #171 (merged `5d25859`) carries type-neutral id/state validation at every entry boundary, the REASSESS_FIXUP pin tests, and the shell removal (901 unit + 140 integration green); "launcher emits list-form commands" folds into PR-2/R3; #146 stays open for rework |
| #151 report averages (conflict, trivial) | same-population before/after | `reporting:` consumer PR-2 rewrites | Rebase, merge before PR-2 |
| #155 initiative eval cases (clean) | recalibrates case-007/013 | strengthens PR-4 baseline | Merge any time before PR-4 |
| #130 + #122 configurable JIRA_PROJECT / DRAFT- (20 conflicts each; byte-identical shared files) | env-bound project + issue type; `is_jira_key`; RFE-→DRAFT-; `<PROJECT>-DRY` | **First-class design decision (2.1)** | Close as superseded once 2.1 is in the design; cherry-pick `collect_recommendations.py --from-reviews` |
| #115 scripts into skill dirs (16 conflicts, 3 modify/delete) | per-skill script copies (artifact_utils ×7, jira_utils ×6), `${CLAUDE_SKILL_DIR}` refs | **First-class design decision (2.2)** | Close as superseded; absorb `__file__`-relative discovery + skillsaw bump; reject duplication and the deletion of `check_conflicts.py`/`jql_query.py`/`bootstrap_snapshot.py` (all live on main) |
| #100 JTBD registry (conflicts) | conditional non-blocking dimension on `.context/jtbd-registry/`; third bootstrap; nested `jtbd_mapping`; optional `scores.jtbd_alignment` | Third dimension-provider data point R1 asked for; descriptor cannot express condition-on-context, per-dimension setup, nested extra_fields, or a non-rubric score | PR-1 gains `condition: {context_exists}`, `pipeline.context_sources`, nested `extra_fields`, `review.extra_scores`; #100 rebased after PR-5 as `types/rfe/dimensions/jtbd.md` |
| #112 auto-approve split children (conflict) | second copy of the approve rule + rfe-only label literal in split_submit | Gap is real (split children never reach `_maybe_approve`, `submit.py:978-990`); implementation duplicates policy | Approve policy implemented **once** (`state_map.approved` + descriptor labels), consumed by submit and split_submit; #112 rebased after PR-2 |
| #133 rfe.merge design (clean, docs) | new stages dupes/merge; Duplicate link 10002; Closed+Duplicate; `artifacts/rfe-dupes|merges`; per-tracker canonicalization id | `pipeline.stages` is a closed six; binding lacks `duplicate_link_type`/`close_duplicate` | Independent; open `stages` list, add `duplicate_link_type` + `state_map.close_duplicate` (+ GitHub `state_reason: duplicate`), `dirs.dupes/merges`; executor after PR-5 |

PR-1's own paths (`types/`, `_schema/`, `type_registry.py`, `validate_types.py`, settings.json) are touched by **no** open PR.

### 2.1 Deployment binding (the #130/#122 question) — recommended resolution

The design binds `(project, issue_type, key_prefixes)` once per type and has no rebinding layer; under it, Konflux's need (same skills, project KONFLUX) requires a whole `types/konflux-rfe/` carrying the full provider floor for a binding change. #130's own model is also wrong for main: one repo-wide `JIRA_PROJECT` cannot express two `TYPE_CONFIGS` (`submit.py:61-96`, `split_submit.py:110-131`), and its *required* env var with an interactive "ask the user" gate in speedrun/auto-fix bodies is exactly the headless-reachable affordance the design's lint forbids.

**Resolution:** the descriptor's `identity.<tracker>` block is the **default** binding; `type_registry.py` computes an **effective** binding by overlaying an optional, type-scoped override (`RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,LOCAL_PREFIX}` or workspace `rfe-creator.yaml: bindings: {rfe: {jira: {project: KONFLUX}}}`; bare `JIRA_PROJECT` accepted as shorthand for the resolved type only). Overridable = binding-only fields (project, issue_type, key_prefixes with write prefix derived `<PROJECT>-`, query_default re-rendered, link types, state_map, parent_key_patterns, optionally local_prefix); never judgement content, dirs, schema, rubric, eval. Rules: zero-config default preserved; §3.3 uniqueness lint runs on *effective* bindings; `resolve` prints the override; dry-run sentinel becomes `<PROJECT>-DRY`; `is_jira_key`-style token grammar becomes a Jira-adapter `detect()` rung **after** local prefixes and descriptor `key_prefixes` (provisional → post-fetch discrimination). **Do not adopt `DRAFT-` at v1**: before frontmatter `type:` exists the local prefix is the only type carrier; the collision #122 feared (a project literally named `RFE`) is a lint rule on effective bindings, not a rename. PR-1: schema text + lint; PR-3: resolve output.

### 2.2 Packaging layout (the #115 question) — recommended resolution

Both lenses agree on the facts: #115's premise ("installers only copy the skills tree") is **false** for the production installer — Claude Code marketplace `github`-source plugins are full clones (agentic-ci `docs/image/skills.md:55-60` — "full git clone of plugin repo" — and skills-registry ARCHITECTURE.md; not re-verified against a local plugin cache, which holds no rfe-creator install), and rfe-creator's registry entry is a whole-repo source. It is *true* for OpenCode's copy path and subdir sources. The real problem is different and real: every SKILL.md script call is cwd-relative (`python3 scripts/x.py`, 185 refs; 41 allowlist entries; the settings.json hook), so production works only because cwd = an rfe-creator checkout.

The lenses differ on the fix. open-prs proposes moving the source of truth into `.claude/skills/rfe-core/{scripts,types}/` referenced as `${CLAUDE_SKILL_DIR}/../rfe-core/scripts/…`; ecosystem-core proposes keeping root `scripts/` canonical and, only if a copy-only installer is ever required, adopting strat-creator's zero-duplication symlink convention (`skill/scripts → ../strategy-common/scripts → ../../../scripts`, 259 files today, `test_skill_integrity` checks the links). **Adjudication: ecosystem-core, for v1.** Inverting the source of truth breaks the CLAUDE.md/AGENTS.md literal-allowlist rule (`AGENTS.md:62-66`), the eval harness's relative-only allowlist (a known failure family), and the CI/eval command contract, for an installer no production consumer uses. Rulings for the design: (1) declare the convention explicitly — *cwd is the plugin root*; root `scripts/` and `types/` are canonical; (2) `type_registry.py` locates `types/` relative to `__file__` (never cwd) and Python-internal subprocesses use `os.path.dirname(__file__)` (main already does at `pipeline_state.py:1075`); (3) record the installer matrix in §3.5 so this is not re-litigated per repo; (4) if/when a copy-only installer is required, the named path is the strat symlink convention + `${CLAUDE_SKILL_DIR}` in bodies with matching `allowed-tools`, done in one PR-5-style body rewrite; (5) note that rfe-creator ships only `.codex-plugin/plugin.json` (→ `./.claude/skills/`, so Codex users will see generics *and* shims) and no `.claude-plugin/plugin.json`. Close #115.

---

## 3. strat-creator

### 3.1 Lifecycle vs design stages

strat-creator is a full peer pipeline: 12 skills, its own GitLab CI (6h cadence), agent-eval-harness eval (two native steps, 11 judges), jira-emulator suites, embedded assess (PR #70), dashboard, ADR series; 14 commits since baseline. Its own `CLAUDE.md:75-77` ("Write Operations: Not yet implemented") and the RTE §4 claim the design inherits are both stale — seven write classes exist (`clone_issue.py`, `push_strategy.py`, `lock_issues.py`, review/refine/signoff label+comment+attachment writes).

| Design stage | strat counterpart | Fit |
|---|---|---|
| create | `strategy-create` = **derive by clone** from an approved RFE: source gate (`config/pipeline-settings.yaml:2-44`), existing-derivative lookup (`find_strat_for_rfe.py`, Cloners both directions), field copy incl. `cf[10855]` name→id and parent Outcome validation (`clone_issue.py:31-137`), `[DRAFT]` prefix | does not map — needs a `derive` stage + `inputs:` |
| review | `strategy-review`: scorer (embedded assess-strat) + deterministic verdict + 4 prose reviewers; comment/attachment/gate labels written at review time | partial |
| revise | `strategy-refine`: the primary authoring step, **section-scoped** (`## Business Need (from RFE)` / `## Strategy …` / `## SME Input`), `push_strategy.py:372-415` replaces sections, overflow → attachment | does not map |
| submit | dissolved into refine/push/signoff; no Approved transition; backup file instead of conflict check | different write invariant (partial-body ownership) |
| split / auto-fix / speedrun | none; loop is CI-side + human loop (pull → local refine → push → signoff); concurrency via lock labels on the **RFE** (`lock_issues.py:43-56`) | absent |
| — | pull, push, signoff, lock/unlock, `[DRAFT]` lifecycle, `strategy_history`, dual roots `artifacts/` vs `local/` (`workflow: local`) | no vocabulary |

### 3.2 Cross-type input relation (rfe → strategy)

Mechanics: `source_rfe` frontmatter (required, `^(RFE-\d+|RHAIRFE-\d+)$`) + Cloners link (STRAT inward / RFE outward) + gate on the **source** (labels-any `rfe-creator-autofix-rubric-pass|tech-reviewed`, release scope by label or `cf[10855]`, status ∉ Closed/Resolved/Draft) + copy rules + derivative-exclusion (skip labels, excluded statuses, single-open-clone override, ADR-0002) + body reference/reconstruction (`jira_utils.py:1130-1200`). Two rfe-owned literals are consumed as **contracts**: the rubric-pass label (86 occurrences in strat) and the removed-context preamble (`strategy-refine/SKILL.md:123` string-matches `submit.py:84-89`). The design turns both into descriptor data (§3.2:118-121) without marking them frozen — changing either silently breaks strat's intake and Source 1. epic-creator is the second requester of the same relation class one level down.

### 3.3 The (RHAISTRAT, Feature) identity collision — verified, live, populated

strat's production binding **is** `(RHAISTRAT, Feature)` (`CLAUDE.md:83-86`, `clone_issue.py:79`); the ADR's flip target for rfe **is** `(RHAISTRAT, Feature)` (`adr:21-22,:55,:196`); epic-creator's entire *input* is the same pair. Live Jira (strat lens, Atlassian MCP, 2026-09-03): RHAISTRAT issue types Initiative 10103, **Feature Request 10111 (6 issues)**, Outcome 10130, Feature 10142 (2,165), Risk 10146; of the Features, 858 are strat auto-created, 1,443 carry a Cloners link (manual clones outnumber the pipeline's), ~1,279 have no strat label at all. So the pair already holds three populations; the only discriminators are lifecycle markers absent on most rows. Post-flip `query_default: project = RHAISTRAT AND issuetype = Feature` would sweep strat's `[DRAFT]` clones and human strategies into the auto-fixer; the design's uniqueness lint cannot see a type in another repo.

**Does it block §9?** Yes — PR-9 cannot ship without a decision. **Does it block PR-1?** No: the lint is intra-repo and correct as designed; what blocks is R1's instruction to prototype a strategy type that "must not claim (RHAISTRAT, Feature)" — unsatisfiable for a faithful prototype. Options:

- **W1 — flip rfe to `(RHAISTRAT, "Feature Request")`** (type id 10111 exists). Keeps rfe/strategy/initiative pairwise-unique, keeps `issue_type` as the deterministic classifier (§6 stage 3), keeps strat's clone lifecycle and epic's input intact, keeps the one-workspace/one-board thesis (same project). Cost: amend the ADR's "RFE becomes a Feature" sentence. **Recommended default.**
- **W2 — one Feature = RFE + strategy at successive lifecycle stages.** The strat document already models this (Business Need / Strategy / SME sections; push replaces the Business Need with a link). Then identity ≠ type: two pipelines share one binding discriminated by section ownership + label families, the lint re-scopes to "per pipeline-owned write path", and strat's derive becomes "select Features with rubric-pass and no strat labels". This is a product decision needing its own ADR and the approved-RFE immutability work as a precondition.
- **W3 — keep `(RHAISTRAT, Feature)` and discriminate by label** — rejected: ~1,279 unlabeled human Features cannot be separated from RFEs by any negative filter, and existing-issue routing by `issue_type` dies.

Decision owners: ADR owner + strat-creator owner, before PR-9; recorded in §9 as an external prerequisite and in §11 as D6.

### 3.4 Script duplication, assess, seam

Line ratios rfe~strat: frontmatter 0.84 (diff = schema-name path sniff + wrapping, zero strat logic), state 0.94 (strat lacks `copy-ids`), artifact_utils 0.72 (strat still ships rfe-task/rfe-review schema copies at `:27-138`), jira_utils 0.62 (838 vs 1,200; 570 strat-only lines: redirect guard `:33-68`, nextPageToken pagination `:162-187`, JQL builder, processed-RFE exclusion, versions, attachments `:465-579`, summary update, reference/reconstruct; 208 rfe-only: `get_myself`, `swap_labels`, `transition_issue`, `check_description_conflict`), fetch_issue 0.58. strat uses **none** of the §8.4 "free engine" column (no pipeline_state, snapshot, submit, split, batch, waves).

**Assess — a third convention.** ADR-0005/PR #70 embedded rubric + scorer agent + run-management scripts + tests in-repo (`scripts/assess-strat/`, `.claude/agents/strat-scorer.md`, `.claude/skills/{assess-strat,export-rubric}`), citing runtime-clone agent-registration failures. It is a third fork of the run-management scripts (0.46-0.76 vs assess-rfe) and a third scorer agent. Residue: strat's eval still clones standalone assess-strat (`eval/scripts/stage-assets.sh:23-27`); skills-registry still models `depends_on: [assess-strat]` (`registry.yaml:757`) and the standalone plugin (`:852-865`). **D3 must be re-decided as two orthogonal choices:** rubric + scorer agent are type-owned judgement (descriptor accepts `rubric: {repo: self, path: …}`, pinned by content hash; assess-rfe's separate repo is the justified exception because four external assessor CI jobs consume it); run-management scripts are shared library and must stop forking. §3.3 gate 2 must handle the embedded case.

**Seam ops strat needs that §3.6 lacks:** attachments add/list/download/select-newest with append-only + newest-wins + per-issue writer lock (ADR-0004, `push_strategy.py:62-198`); content-limit overflow → attachment + stub (`:400-415`); summary-only update; section-scoped body update; link create/scan by type and direction (Cloners/Related/Incorporates); version/custom-field name→id mapping; cross-type lock labels; redirect hardening rfe lacks. strat also consumes `jira_utils` as a **module API** (`python3 -c "from jira_utils import add_labels…"` in six skill steps), not as CLIs — the companion-skills contract must name the surface it actually exposes.

### 3.5 Verdict on framing + `types/strategy` sketch

**Framing: peer pipeline that should consume a shared core, not a type.** Forcing it into the descriptor needs ~15 schema extensions and four new stage names (`derive`, `refine`, `pull`, `push`, `signoff`) for one type — the second-requester rule would reject most of them. What it *does* share: frontmatter/state/schema engine, tracker adapter (incl. attachments), assess run-management. The one world where strat is rfe-creator type data is W2. R1 must therefore stop asking for a `types/strategy/` prototype; the third data point for identity/labels/tracker blocks is epic-creator's real `(RHAI, Epic)` (§4), and the reserved `inputs:` block is validated against strat's `config/pipeline-settings.yaml`.

```yaml
# types/strategy/type.yaml — sketch under §3.2; `# !!` = inexpressible today
type: strategy
identity: { tracker: jira, jira: { project: RHAISTRAT, issue_type: Feature,   # !! collides post-flip (W1/W2)
            key_prefixes: [RHAISTRAT-], split_link_type: null }, local_prefix: STRAT-, id_field: strat_id }
inputs:                                                                        # !! block missing
  - { name: source_rfe, from_type: rfe, relation: {tracker_link_type: Cloners, direction: derived_is_inward},
      gate: {labels_any: [rfe-creator-autofix-rubric-pass, tech-reviewed], statuses_not: [Closed, Resolved, Draft],
             scope_any: {labels: [strat-creator-3.5, strat-creator-3.6], field: customfield_10855}},
      copy_fields: [summary, description, priority, labels, components, affects_versions, target_versions, parent],
      derivative_exclusion: {skip_labels: [strat-creator-rubric-pass, strat-creator-needs-attention, strat-creator-processing],
                             excluded_statuses: [In Progress, Review, Release Pending, Closed, Resolved], single_open_unlabeled_override: true} }
dirs: { tasks: artifacts/strat-tasks, originals: artifacts/strat-originals, reviews: artifacts/strat-reviews,
        roots: {ci: artifacts, local: local} }                                 # !! dual root + workflow: local
companions: { comments: true, removed_context: false, attachments: true }      # !! attachments
conventions:
  label_prefix: strat-creator
  labels: { rubric_pass: strat-creator-rubric-pass, needs_attention: …, human_sign_off: …,   # !! categories:
            provenance: [auto-created, auto-refined, auto-revised], lock: strat-creator-processing,  # !! written on SOURCE type
            selection: [strat-creator-3.5, strat-creator-3.6], ignore: strat-creator-ignore }
  comment_prefix: "[Strat Creator]"
  query_default: 'project = RHAIRFE AND …'                                     # !! targets the SOURCE project
  summary_prefix: { value: "[DRAFT] ", added_at: derive, removed_at: signoff } # !!
schema:
  task: { extra_fields: {source_rfe, jira_key, workflow: {enum: [local, ci]}, latest_diff, refine_count},
          status_enum: [Draft, Ready, Refined, Reviewed] }                     # !! per-type status enum
  review: { score_fields: [feasibility, testability, scope, architecture],
            verdict_rules: {approve: {total_min: 6, zeros_max: 0}, revise: {total_min: 3, zeros_max: 1}} }  # !!
pipeline:
  stages: [derive, refine, review, pull, push, signoff]                        # !! 4 of 6 names do not exist
  orchestration: external-ci                                                   # !! no in-repo phase machine
  section_ownership: [{heading: "## Business Need (from RFE)", owner: source}, {heading: "## Strategy …", owner: pipeline},
                      {heading: "## Staff Engineer / SME Input", owner: human}]  # !!
  body_overflow: { on: CONTENT_LIMIT_EXCEEDED, attachment: "{key}-strategy.md", policy: append_only }  # !!
  rubric: { repo: self, path: scripts/assess-strat/agent_prompt.md }           # !! repo: self
  dimensions: [{name: architecture, condition: {context_exists: .context/architecture-context}}, …]  # !! condition vocab
snapshot: null                                                                 # !! assumed present
eval: { config: eval/eval.yaml }                                               # !! two native steps, not generatable
```

Post-baseline facts from this lens: rfe-creator still ships four **orphaned strat reviewer skills** (`architecture-review`, `feasibility-review`, `scope-review`, `testability-review`); two were edited 2026-08-24 (PR #163) and did not propagate to strat's live copies (ratios 0.69-0.82; strat's feasibility/scope/testability have zero overlay handling) — drift in both directions; `README.md:96` and `.ambient/ambient.json:6` link `ederign/strat-creator` (canonical is `opendatahub-io/strat-creator`). Add all to PR-5's deletion/sweep list; port the overlay edits to strat first.

---

## 4. epic-creator

### 4.1 Shape

The premise "epic-creator attaches a decomposition file + `decomp-ready`" is **stale** (only in its CLAUDE.md; `attach_decomposition.py` never existed). Since PR #18 it **creates first-class Jira issues**: `(RHAI, Epic)` children of the RHAISTRAT Feature (`submit.py:56-57`, cross-project `parent` `:326-337`), `Blocks` links for every DAG edge (`:68,:395-400`), per-epic `-frontmatter.yaml` attachments (`:544`), branch-plan attachments on gating Investigation epics (`:494`), and `epic-creator-auto-decomposed` on the source only when all epics exist (`:611-616`). So epic-creator **is** a type provider in the identity sense *and* a second pipeline kind: 1:N derivation, set-level review (7 criteria × 0-2, pass ≥ 10 and no zero, keyed by the *input* id), a script-side deterministic dimension (`compute_ai_scores.py` after DECOMPOSE/REVISE/RE_REVISE), no snapshot/hash/originals (fetch overwrites; processed-gate = remote children), create-only submit resumed from a local `jira_key` marker (contradicts §3.6 invariant 1 as worded), composite local ids `RHAISTRAT-1234-E001` / `…-BRANCH-A-E003`, value-templated labels (`epic-creator-impl-<x>`, `-ai-impl-<high|medium|low>`), and a per-type priority enum `P0/P1/P2` mapped to Jira names (`:59-63`). Both repos define a `strat-task` schema with different fields — a name collision any multi-repo registry must namespace.

### 4.2 The `pipeline_state.py` fork vs "the phase graph is fixed"

epic's `pipeline_state.py` is a documented fork ("modeled after rfe-creator's auto-fix architecture", e3b310a): 0.68 similar at fork (2026-05-04), 0.49 now (0.64 AST-normalised). What differs is exactly the **graph**: 13 phases vs 31 (`pipeline_state.py:54-60` vs rfe `:58`), `advance()` 0.27-0.30, different completion predicates and cycle caps. What is shared is the **engine**: `cmd_get/set/status` 0.97-1.00, state load/save 0.92-0.94, `_check_agent_phase_complete` 0.81, `cmd_run_phase` 0.79, `cmd_wait_for_wave` 0.62-0.82, `cmd_next_action` 0.66; the fork even handles the generic PHASE_CONFIG keys (`parallel`, `pre_script`, `post_verify`, `subagent_type`) it never declares. It has absorbed **no** rfe hardening since May (no verify_phase/post_verify, no error-stub guard, no ERROR_COLLECT zero-retryable exit, `--max-wait 90` with no escalation — R2's fix will be one-sided).

**Ruling:** the design's §2/§3.4 sentence is true inside rfe-creator (rfe and initiative share the work-item graph) and false at ecosystem scale. Rewrite it as: *the wave/barrier/resume/dispatch engine is machinery; the phase table is per-pipeline-kind data; within this repo the table is not a type extension point.* Reserve `pipeline.kind` (v1 enum `[review-split]`; future `[decompose]`), record an engine/graph split (`PHASES` + `PHASE_CONFIG` + `advance()` + `PHASE_CHECKS` + cycle caps out of the dispatcher) as a PR-10 follow-up, and implement R2's escalation in engine functions so the fork can lift it verbatim. §8.4's "Free (engine)" column must list engine items, not "31-phase state machine".

### 4.3 Verdict and what the design must add or scope out

**Framing: type provider `(RHAI, Epic)` + second pipeline kind consuming the engine by fork.** Use it as R1's third data point for identity/conventions/schema.task (real, in production twice daily). **Add in PR-1 (shape):** `identity.local_id_pattern`; open `labels` map with `templates: [{from_field, pattern}]` reserved; `schema.task.priority: {enum, map_to_tracker}` reserved; `inputs:`/`produces:`; `kind`. **Add additively:** `companions.extra_suffixes`; seam ops attachments/set-parent/set-assignee/components-list/link-by-type/clear-parent (attachments now have two requesters — strat and epic — with *different* signatures under the same names); §3.6 invariant 1 permits "durable local marker + remote existence check" for create-only kinds; invariant 2 and the snapshot invariants marked kind-specific. **Explicitly out of scope for v1 (say so):** a declarative transition DSL; set-level review in the shared skeleton; `dimensions[].kind: script`; run-report `entry_kind: derivation`; GitHub attachment degradation; the provider floor as applied to engine consumers that bring their own gate (epic has no eval dataset and would fail it). Corroboration for two rulings: epic renamed `epic.decompose → epic-decompose` (f0faefc, "dots aren't valid for the plugin command format") and adopted the canonical plugin layout (a945e31) while still invoking scripts cwd-relative.

---

## 5. Ecosystem shared core

### 5.1 Measured 3-way duplication and concrete divergences

| file | rfe / strat / epic lines | rfe~strat | rfe~epic | strat~epic | note |
|---|---|---|---|---|---|
| jira_utils.py | 838 / 1200 / 870 | 0.62 (AST 0.69) | 0.75 (0.87) | 0.70 (0.67) | 31 common fns, **27 byte-identical after AST normalisation** — that block *is* the adapter |
| artifact_utils.py | 1101 / 946 / 513 | 0.72 (0.80) | 0.34 (0.45) | 0.43 (0.52) | SCHEMAS = 23% / 23% / 45% of each copy |
| frontmatter.py | 279 / 281 / 272 | 0.84 (0.98) | 0.73 (0.85) | 0.86 | only the per-type path table differs |
| state.py | 210 / 185 / 173 | 0.94 | 0.90 | 0.97 | strat/epic strict subsets |
| pipeline_state.py | 1343 / — / 865 | — | 0.49 (0.64) | — | advance 0.30; engine 0.8-0.94 |
| error_collect / generate_run_report | 277,456 / — / 63,78 | — | 0.09 | — | independent rewrites |

strat vendored six files from rfe-creator on 2026-04-11 (1d94dcf) and never re-synced; since the design baseline rfe landed 15 commits on shared files, strat 1, epic 0, zero cross-pollination. One-sided fixes: certifi SSL context in rfe (PR #132) and strat, **missing in epic**; same-origin redirect policy only in strat; HTTPS-only server check only in epic; atomic `swap_labels` only in rfe (strat's review/push/lock do add-then-remove); `state.py clean` (rmtree tmp/) still in rfe and strat — epic removed it after subagents wiped `tmp/` (7204b29) and `rfe.speedrun:23` still calls it; `copy-ids` headless fix only in rfe; Jira error body on HTTPError only in strat; attachment size/host guard only in epic; 429/5xx retry propagated to all three. Pagination is implemented 5× (3 inside rfe-creator); ADF converters 5×.

### 5.2 Boundary and options

The boundary the evidence draws: **engine** (dispatch/waves/barrier/state/compact hook) + **tracker adapter** (the 27 identical functions + attachments/links/clone union) + **frontmatter/state/schema engine** + (candidate) versioned run-report schema and label/gate vocabulary are shared; the **type contract** (template/questionnaire/rules/dimensions/rubric/eval) and the **phase table** are not.

| option | feasibility | blast radius | verdict |
|---|---|---|---|
| (a) copies + weekly AST-normalised parity sweep in skills-registry "Upstream Plugin Checks" (already clones every plugin) | trivial | none | **do now, in parallel with PR-1**; catches epic certifi, `state.clean`, `swap_labels` |
| (b) vendored `creator-core/` snapshot + sync script, each repo pins a core SHA | works under every installer; requires splitting local extensions out of shared files first | per-repo, on sync | **medium-term shape**; strat's 1d94dcf is this minus the sync |
| (c) pip package | runner image installs only agentic-ci deps; one image version for all pipelines contradicts per-repo pinning; rewrites 40/21/17 sibling-import sites | high | not now |
| (d) monorepo | only as one `github`-source plugin (git-subdir does not ship shared code); collapses `AGENT_ENABLED_PLUGINS` granularity and three teams' CI (125/78/47 commits since June, distinct owners) | maximal | reject |

**Recommendation:** independent repos + (a) now; (b) later with strat as first consumer of the Jira adapter (it already vendors it and needs the most ops); never fold strat/epic into rfe-creator as types. **Smallest PR-1-compatible first step:** write `scripts/type_registry.py` and `types/_schema/type.schema.json` import-clean (stdlib + pyyaml, no rfe-module imports, explicit root arg, `__file__`-relative discovery) so they lift verbatim; reserve `kind`/`inputs`/`produces`/open `labels`; pull the PR-2 adapter split (`scripts/trackers/jira.py`) forward as the extraction unit with one pagination helper and the union `create_issue` signature (components, fix/affects/target versions, parent_key, reporter_account_id, assignee_id); add a §0 scope sentence and name the follow-up ADR `creator-core`; version the declared-stable CLI/module surface so consumers pin it instead of copying.

### 5.3 Cross-type input-chain vocabulary (reserved in PR-1, no evaluator in rfe-creator)

```yaml
kind: work-item                       # work-item | strategy | decomposition — selects the engine graph; v1 accepts work-item only
inputs:                               # how items of this type are DERIVED from an upstream type
  - from_type: rfe
    relation: { kind: clones | parent | link, link_type: Cloners, direction: derived_is_inward }
    source_ref_field: source_rfe      # frontmatter field holding the upstream ref (strat: source_rfe; epic: parent_strat)
    gate:
      labels_any: [${types.rfe.labels.rubric_pass}, tech-reviewed]   # upstream EXPORTED readiness signal, by reference
      labels_all: []                  # epic: [strat rubric_pass, human_sign_off]
      statuses_not: [Closed, Resolved, Draft]
      any_of: [{labels_any: [strat-creator-3.5, strat-creator-3.6]}, {fields: {customfield_10855: {name_in: [...]}}}]
    body_source: { attachment: "{key}-strategy.md", fallback: description }   # producer/consumer attachment convention
    copy_fields: [summary, description, priority, labels, components, affects_versions, target_versions, parent]
    skip_if: { labels_any: [strat-creator-rubric-pass, strat-creator-needs-attention, strat-creator-processing],
               statuses: [In Progress, Review, Release Pending, Closed, Resolved], single_open_unlabeled_override: true }
produces:
  - type: epic
    relation: { kind: parent, dependency_link_type: Blocks }
    label_on_input: epic-creator-auto-decomposed
```
Rule: a type's `conventions.labels.rubric_pass` (and `human_sign_off` where declared) and `conventions.removed_context_preamble` are **published cross-pipeline contracts** — frozen strings, changed only with a downstream migration note. skills-registry's informational field becomes `produces:`/`consumes:` (not `provides: work-item-type`) so the chain rfe → strategy → epic can be rendered; note a `types/<t>` shipped via git-subdir receives descriptor data only, never code.

---

## 6. Consolidated required changes and new decisions

Numbered; `[B]` = blocking before PR-1, `[F]` = can follow.

1. `[B]` **§3.2 labels** → open map with reserved keys `{rubric_pass, ignore, needs_attention, split_quarantine, split_child_marker (template), processing, human_sign_off, auto_created, feasibility{}, alignment{}}`, per-consumer notes (jql_query default vs snapshot/bootstrap hard filter); §3.6 invariant 4 rewritten; §8.2 both descriptors updated; PR-2 list gains `snapshot_fetch.py:59/:64`, `submit.py:283`.
2. `[B]` **§5 + §3.2 schema** — triple `type/tracker_ref/local_id`; tracker_ref read from frontmatter, prefix-union fallback; delete "zero existing carriers"; run reports already type-tagged; PR-2 projects report `tracker_ref`; §9 sweep adds `generate_run_report.py:126-134/:212-213/:241-242` and `artifact_utils.py:877-880/:934-937`.
3. `[B]` **§3.2 identity** — `local_id_pattern`; `detect()` full-match, most-specific wins; lint corpus includes `RHAISTRAT-1234-E001`.
4. `[B]` **§3.2 top-level** — reserve `kind`, `inputs`, `produces` (§5.3 block); `[F]` `pipeline.stages` open list; `condition: {context_exists}`; `pipeline.context_sources`; nested `extra_fields`; `review.extra_scores`; `companions.extra_suffixes`; `state_map.close_duplicate` + `duplicate_link_type`; `schema.task.priority` reserved.
5. `[B]` **New §3.2.1 Deployment binding override** (§2.1 text); §3.3 lint on effective bindings + "no local_prefix stem equals any effective project key"; §5 detect order local → descriptor prefixes → adapter grammar; dry-run sentinel `<PROJECT>-DRY`; D8.
6. `[B]` **§11 R1 rewritten** — no strategy prototype; epic `(RHAI, Epic)` as third data point; identity collision → §9 external prerequisite (W1 default / W2 product ADR / W3 rejected); D6.
7. `[B]` **§3.4 / §3.5 / §10 packaging stance** (§2.2): convention "cwd = plugin root", root `scripts/`+`types/` canonical, `__file__`-relative discovery, installer matrix, #115 closed, strat symlink convention named as the future portability path; D7.
8. `[B]` **§10 preamble sequencing** — merge #148, #149, #153, #146(a), #155 before PR-1; #151 before PR-2; close #130/#122/#115; #112 after PR-2; #100/#133 executor after PR-5.
9. `[F]` **§3.3 gate 1** — inventory 43 lines / 11 files; lint also catches literal `issue-snapshot-`/`initiative-run-` outside the registry and text-only headless terminations (`initiative-split:141`); #148 wording; error-stub command generated from `score_fields` in every generic body.
10. `[F]` **§3.6 seam** — add preflight/whoami, search-by-labels, `classify_failure`; reserved: attachments (add/list/download/select-newest, append-only + writer lock), summary-only update, section-scoped body update, link-by-type/direction, set-parent, set-assignee, components, custom-field/version mapping, cross-type lock labels; invariant 1 restated (comments + relations + creation-time marker with fingerprint guard; create-only kinds may use local marker + remote existence check); invariants 2/snapshot marked kind-specific; GitHub degradations: marker label > 50 chars, attachments; ADF count 5; "superset" wording; `:91-96`; adapter split pulled into PR-2 as the extraction unit; port strat's redirect hardening.
11. `[F]` **§2 / §3.4 refusals** — engine vs phase table (§4.2); "transactional submit sequence" scoped to the review/split kind; approve policy implemented once and applied to split children; declared-stable surface = versioned CLI **and** module API; provider floor per-kind/waivable for engine consumers.
12. `[F]` **§4.2** — revise-agent to Tier 2 (fixed step order, tokenised frontmatter-set command, one `revise_rules` slot); split-agent stays Tier 3 with a required-line lint; "body only, frontmatter via frontmatter.py" rule in the skeleton; #149 `ValidationError` contract declared.
13. `[F]` **§4.4 / PR-5** — deletion list adds the four orphaned strat reviewer skills (port PR #163 overlay edits to strat first); sweep adds `README.md:96`, `.ambient/ambient.json:6`, registry `skills` list; paired skills-registry PR registering six `rfe-*` skills with contract blocks + `skill_path` assertions (`validate_registry.py:542-600` errors on missing names); Codex manifest exposes generics + shims — decide whether shims are hidden from listings; "in passing" list = four stale rubric paths + two dead return paths, rewritten against `pipeline-state.yaml`; #153's three mechanics carried into the skeleton.
14. `[F]` **§4.5** — fragments for `score_range`, the `not_relevant` carve-out now inside the transcript judge, `execution.skill` layout; generator retires `test_both_configs_identical`; "66%" → "~76% at fbf96bb"; `batch_pattern` derived from `local_prefix`.
15. `[F]` **§3.2 reporting / §8.2** — split `pdf.extra_fields` vs `run_report.extra_entry_fields`; `tasks_dir` from `dirs.tasks`; initiative `before_score_name_map` = `{WHAT, WHY, Scope, HOW, "Open to HOW", Right-sized, Right-sizing, RS}`; rfe `extra_entry_fields: [needs_attention]`; `submit.py:322-331` as a `report_prefix` consumer; `split_link_type` comment lists five sites; averages over one population (#151).
16. `[F]` **§7 / D3** — two orthogonal choices (rubric+scorer type-owned incl. `rubric.repo: self`; run-management shared); gate 2 handles embedded; §7.2 states strat-scorer is out of scope; PR-10: strat eval staging → embedded copy, registry drops `depends_on: [assess-strat]`.
17. `[F]` **§9** — external prerequisites add: strat identity disposition (D6); strat's RHAIRFE- keyed surfaces (`source_rfe` grammar, Cloners scans in 4 scripts, `reconstruct_business_need`); epic as consumer of `(RHAISTRAT, Feature)`; `strip_metadata` STRAT/RHAISTRAT literals (`jira_utils.py:713,:736`); approved-RFE immutability as precondition; rename guards; report predicates.
18. `[F]` **§10 lists** — PR-1: allowlist entries `Bash(python3 scripts/{type_registry,validate_types,generate_eval_config}.py *)`, pins over `tracker_prefix/local_prefix/local_id` patterns, `test_report_roundtrip.py` → registry projection, import-clean registry, `create` PHASE_CHECKS row. PR-2: report projection, rename guards on registry, `bootstrap_snapshot.py:176`, adapter split, approve-once, list-form launcher commands, quarantine label. PR-3: override in `resolve` output. PR-10: three latent initiative-CI bugs; engine/graph split; parity sweep in skills-registry; `creator-core` ADR.
19. `[F]` **§0 / §1.1** — scope sentence ("eliminates drift *within* rfe-creator; cross-repo core is the named follow-up") + the §5.1 table; §8.4 free column = engine items.

**New decisions for the maintainer:** **D6** identity collision (W1 recommended; W2 needs a product ADR; owners ADR + strat); **D7** packaging convention (cwd = plugin root, root scripts canonical, #115 closed); **D8** deployment-binding override shape (env vs workspace file; both accepted); **D9** shared core (a-now/b-later; who hosts the parity sweep; `creator-core` ADR owner); **D10** revise-agent tier (Tier 2 recommended); **D11** whether the run-report schema is declared ecosystem-wide (`entry_kind`) or rfe/initiative-only (recommend: rfe/initiative-only for v1). **New risk R7:** published cross-pipeline contract strings (`rubric_pass` label values, removed-context preamble, `{key}-strategy.md` attachment convention) — frozen, listed in §3.4.
