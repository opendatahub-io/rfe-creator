# AISDLC-59 — remaining work as Epics and Stories (drafts for review)

Parent: Feature [AISDLC-59](https://redhat.atlassian.net/browse/AISDLC-59) "Make RFE Creator support pluggable planning workloads" (Outcome AISDLC-151, Big Rock AISDLC-58). Existing child: Epic AISDLC-62 "Pluggable work item types: one intake, many destinations" (the delivered PR-1 … PR-5 series; one open Story, RHAIFIRST-567). Hierarchy in AISDLC: Feature → Epic → Story. Label convention seen on the tree: `team-ai-first`.

Status legend: **new** = to create; **existing** = already in Jira, link only.

Drafted 2026-09-30 from `work-item-types-unified.md` §10/§11, the PR-5 plans, the 2026-09-29 incident write-up and the Feature's own scope and success criteria. Nothing created yet.

---

## Epic A — Classification stages 1 and 2 (design §6, PR-7) — new

Create-time and review-time classification of the free-text idea against every registered type's `classification` block, without routing authority over existing issues (stage 3 stays unplanned while the flip is unscheduled).

Stories:
- A1. Descriptor `classification` block (signals, labels, examples) for `rfe` and `initiative`; `validate_types.py` gate; provider guide section.
- A2. Stage 1 in the generic create body: clear match → one-line notice (`Type: rfe — pass --type to override`); ambiguous → AskUserQuestion built from the installed descriptors; headless → the resolution ladder (rungs 1–2 or the `rfe` default), never a question.
- A3. Stage 2 in the generic review: the launcher passes the *other* descriptors' signals into the review context; when `not_a_task=0` and the text matches another type, the review report and the `needs_attention` note say so ("likely an Initiative: …"); no routing.
- A4. Eval cases for both stages (misfiled initiative described as an RFE, an RFE described as an initiative, an ambiguous idea) in both datasets; thresholds in the fragments; one re-baseline.

Done when: both stages ship behind the generic bodies with the eval cases green; no production prompt string changes.

## Epic B — Provenance and one scorer (design §7.2, §7.4, §7.5, PR-8) — new

Stories:
- B1. Stamp `rubric_version` (content hash of the pinned rubric) and `type_version` on task and review frontmatter and in the run report; readers tolerate their absence (older artifacts).
- B2. SETUP fail-fast: `validate_types.py --verify` in the pipeline's SETUP phase (descriptor, rubric pin and vendored assets present) before any wave launches.
- B3. One scorer: a single scorer agent parameterised by type replaces `rfe-scorer` / `initiative-scorer` and the vendored per-type copies; the dispatcher's `subagent_type` comes from the descriptor.
- B4. Assess-detection convergence: retire the four detection mechanisms inherited from assess-rfe #10 in favour of the registry's poll/state prefixes.
- B5. Eval re-baseline for both types after B3/B4 (pairwise against the previous run, as for #206).

Done when: artifacts and run reports carry provenance, SETUP refuses a broken type before spending, and one scorer serves every type with parity on the evals.

## Epic C — Initiative rubric and questionnaire review (content, with the Initiative owners) — new

The RHAI Initiative is the first production workload of this Feature; its judgement content has never been reviewed with the people who own initiatives.

Stories:
- C1. Review `types/initiative/prompts/create-guidance.md` (the four clarifying questions, writing rules, don'ts) and `types/initiative/template.md` with the RHOAIENG Initiative owners; agree on what a good Initiative looks like.
- C2. Review the assess-initiative rubric (`assess-rfe` `skills/assess-initiative/scripts/agent_prompt.md`, score fields `what, why, scope, open_to_how, right_sized`) and its calibration against a sample of real RHOAIENG initiatives; changes land upstream in assess-rfe and re-pin `rubric.ref` in the descriptor.
- C3. Review the two initiative dimensions (`alignment` against the RHAISTRAT parent, `feasibility`) and the split rules.
- C4. Refresh the initiative eval dataset and annotations to the agreed content; re-baseline `eval-initiative.yaml`.

Done when: the owners have signed off on questions, template and rubric, and the initiative eval baseline reflects the agreed content.

## Epic D — Initiative pipeline in production, opt-in (decision D4) — new

The type, its eval and the generic pipeline exist; nothing consumes the initiative type in production. Start opt-in: only Initiatives carrying an agreed label are selected, dry-run first.

Stories:
- D1. Results repository for initiatives (recommended: a separate repository; the RFE fetch walks `latest` and raises on a report without `per_rfe`, so a shared `latest` would break the RFE pipeline); wire `clone_results_repo.py` / push for it.
- D2. Autofixer jobs: `autofix-initiative` (scheduled) and `autofix-initiative-stage-dry`, prompt `/rfe-auto-fix --type initiative --announce-complete --jql "project = RHOAIENG AND issuetype = Initiative AND labels = <opt-in label>" …`, ci-stage/ci-prod clones, the green-run guard, results push.
- D3. Make the autofixer scripts type-aware from the descriptors, fixing the three latent bugs: `restore-artifacts.sh` restores `rfe-tasks` only (initiatives live in `initiatives/`); the submit jobs' report-timestamp grep matches `auto-fix-runs/[0-9]*.yaml` and misses `initiative-run-*.yaml`; `push-results.py` derives the run id from the first non-`issue-snapshot-*` YAML and would pick `initiative-run-*` or `initiative-snapshot-*`.
- D4. Opt-in label: name it, document it for Initiative owners, add it to the descriptor's `conventions.labels`.
- D5. Approval policy for Initiatives: dry-run period, then live with auto-approve off; define the RHOAIENG transition target and comment marker (`[Initiative Creator]`) with the owners before the first live run.
- D6. First production runs monitored (run report, Jira effects), then the opt-in label lifted or kept as a decision.

Done when: a scheduled initiative job runs green on the promoted tree with opt-in scope, and the first live results have been reviewed with the owners.

## Epic E — Eval repository reads types from the descriptors (PR-10) — new

`rfe-creator-eval` (`src/rfe_creator_eval/jira_cases.py`) keeps a hand-written `TYPES` map (JQL and project per type) and infers the type from the config filename; it silently falls back to RFE for any unknown type.

Stories:
- E1. Resolve type, JQL and eval config through the checked-out rfe-creator's `type_registry.py` (`identity`, `query_default`, `eval.config`); drop the map; tests.
- E2. Dispatch surface: the GitHub workflow's `eval_config` input derived from the registered types (or a `type` input), so a third type needs no workflow edit.

Done when: a third descriptor with an `eval.config` runs in the eval CI without touching the eval repository.

## Epic F — Registry update for the plugin (skills-registry) — new

Stories:
- F1. rfe-creator entry: version `0.2.0` across the entry and both manifests (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`), `author` alignment (entry says `jwforres`, manifest `opendatahub-io`), contract `rubric_ref` pins moved to the current main.
- F2. `provides` metadata for the generic skills (design PR-10) and the `--type` surface described in the entry's contracts.
- F3. Weekly AST-normalised parity sweep across rfe-creator, strat-creator and epic-creator in Upstream Plugin Checks (decision D9, first half).
- F4. Land #127 (spike-executor `strict: true` + contract) and get the upstream `repository` field fixed in `IKRedHat/SPIKE-executor` — existing PR, link only.

## Epic G — Pipeline hardening from the 2026-09-29 incident (Feature criterion: "retries, duplicates, partial failures and recovery are tested") — new

Stories:
- G1. Stop hook guard — **existing** rfe-creator #209 (in review).
- G2. Submit holds an interrupted revision — **existing** rfe-creator #210 (in review).
- G3. Job-level green-run guard — **existing** rfe-autofixer MR !12 (in review).
- G4. Deterministic zero-work end: `pipeline_state` reaches DONE (or a `RESULT: NO_TASKS` marker) on zero items without the model improvising `set-phase DONE`.
- G5. Auto-approve on a `split` recommendation: decide whether the rubric pass should approve an item the review wants split (the right-sizing gap, `project_right_sized_calibration_gap`), and change `submit.py`'s policy accordingly.
- G6. Approved-item immutability — **existing** RHAIFIRST-397 / RHAIFIRST-398, link only.

## Epic H — Plugin and packaging follow-ups (PR-5d/5e leftovers) — new

Stories:
- H1. Autofixer job setup calls `scripts/bootstrap.sh`; remove the `bootstrap-assess-rfe.sh` forwarder and the old allow rules once nothing references them; stop enabling the unused image-baked plugin in the runner (avoids the doubled compaction banner once the image is rebuilt).
- H2. Layout guard wording: the checkout still runs the guard once as a no-op (production and dry runs); reword so a skill directory inside the working directory skips it.
- H3. Local development marketplace (`make install`, the harness's shape) and the marketplace re-test of the plugin path: bare skill names, both hooks after a forced compaction; README naming.
- H4. Codex install test on a Codex machine (`/hooks` trust, a compaction, `$rfe-create` end to end).
- H5. Housekeeping: close #115 and #146 as superseded (design D7, #171/#173); merge #172 as the design record.

## Epic I — Validated handoff to Strategy Creator (Feature success criterion) — new

The Feature promises "artifacts carry explicit identity, provenance, and a validated Strategy Creator handoff". Identity (`type`, `tracker_ref`) shipped in PR-1; provenance is Epic B; the handoff has no design item yet.

Stories:
- I1. Spike: write the handoff contract — what strat-creator reads from an approved RFE / an Initiative (labels, `tracker_ref`, `parent_key` to a RHAISTRAT Outcome, the alignment verdict), where it is validated, and what "consumed" means for the immutability boundary (RHAIFIRST-397/398).
- I2. Implement the validation on the rfe-creator side (schema or submit-time check) and a contract test shared with strat-creator's expectations.

## Epic J — Workload selection through the Fullsend package (Feature scope) — new

In scope per the Feature ("expose workload selection and workload-definition references through Fullsend's config"), separate from the runtime migration (AISDLC-19/20 under AISDLC-1).

Stories:
- J1. Land rfe-creator #201 (the `.fullsend/` package) rebased on the generic skills — **existing** PR, link only.
- J2. Type selection: `--type` in `FULLSEND_TASK` (and a documented default), the result schema carrying `type`, the validator gating on the state file; README.

## Epic K — Rubric v2 swap (design PR-6) — new, blocked

Placeholder so the Feature shows the dependency: when assess-rfe delivers the DoR rubric (`goal, motivation, impact, stakeholders, success_criteria, scope_control, right_sized`), swap `score_fields`, re-pin `rubric.ref`, the DoR template, regenerate the eval configs, one re-baseline, run reports state the measurement change. Blocked on the assess-rfe product track (nothing open there today).

## Not proposed as Jira items

- Engine/phase-table split of `pipeline_state.py` for epic-creator and the `creator-core` ADR (D9, second half): epic-creator's need should drive them; suggest a Spike under AISDLC-58 when that consumer is ready rather than an epic here.
- The RHAISTRAT flip (§9): removed from the plan by the 2026-09-03 ruling; an ADR-owner decision with D6 as its gate.

## Suggested order

G (in flight) → H1/H2/H5 (small) → E → D (with C running in parallel with the owners) → B → A → F → I → J → K when unblocked.
