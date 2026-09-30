# AISDLC-59 — remaining work as Epics and Stories (created 2026-09-30)

Parent: Feature [AISDLC-59](https://redhat.atlassian.net/browse/AISDLC-59) "Make RFE Creator support pluggable planning workloads" (Outcome AISDLC-151, Big Rock AISDLC-58). Existing child: Epic AISDLC-62 "Pluggable work item types: one intake, many destinations" (the delivered PR-1 … PR-5 series; one open Story, RHAIFIRST-567). Hierarchy in AISDLC: Feature → Epic → Story. No labels: the `team-ai-first` convention is retired (2026-09-30); the Team field was left unset.

Status: all seven Epics and their 31 Stories were created in AISDLC on 2026-09-30 (keys below), Epics assigned to the Feature owner, Stories unassigned, all in status New.

Drafted 2026-09-30 from `work-item-types-unified.md` §10/§11, the PR-5 plans, the 2026-09-29 incident write-up and the Feature's own scope and success criteria; reviewed and created the same day.

---

## Epic A — Classification stages 1 and 2 (design §6, PR-7) — [AISDLC-181](https://redhat.atlassian.net/browse/AISDLC-181)

Create-time and review-time classification of the free-text idea against every registered type's `classification` block, without routing authority over existing issues (stage 3 stays unplanned while the flip is unscheduled).

Stories:
- A1 (AISDLC-189). Descriptor `classification` block (signals, labels, examples) for `rfe` and `initiative`; `validate_types.py` gate; provider guide section.
- A2 (AISDLC-190). Stage 1 in the generic create body: clear match → one-line notice (`Type: rfe — pass --type to override`); ambiguous → AskUserQuestion built from the installed descriptors; headless → the resolution ladder (rungs 1–2 or the `rfe` default), never a question.
- A3 (AISDLC-191). Stage 2 in the generic review: the launcher passes the *other* descriptors' signals into the review context; when `not_a_task=0` and the text matches another type, the review report and the `needs_attention` note say so ("likely an Initiative: …"); no routing.
- A4 (AISDLC-192). Eval cases for both stages (misfiled initiative described as an RFE, an RFE described as an initiative, an ambiguous idea) in both datasets; thresholds in the fragments; one re-baseline.

Done when: both stages ship behind the generic bodies with the eval cases green; no production prompt string changes.

## Epic B — Provenance and one scorer (design §7.2, §7.4, §7.5, PR-8) — [AISDLC-182](https://redhat.atlassian.net/browse/AISDLC-182)

Stories:
- B1 (AISDLC-193). Stamp `rubric_version` (content hash of the pinned rubric) and `type_version` on task and review frontmatter and in the run report; readers tolerate their absence (older artifacts).
- B2 (AISDLC-194). SETUP fail-fast: `validate_types.py --verify` in the pipeline's SETUP phase (descriptor, rubric pin and vendored assets present) before any wave launches.
- B3 (AISDLC-195). One scorer: a single scorer agent parameterised by type replaces `rfe-scorer` / `initiative-scorer` and the vendored per-type copies; the dispatcher's `subagent_type` comes from the descriptor.
- B4 (AISDLC-196). Assess-detection convergence: retire the four detection mechanisms inherited from assess-rfe #10 in favour of the registry's poll/state prefixes.
- B5 (AISDLC-197). Eval re-baseline for both types after B3/B4 (pairwise against the previous run, as for #206).

Done when: artifacts and run reports carry provenance, SETUP refuses a broken type before spending, and one scorer serves every type with parity on the evals.

## Epic C — Initiative rubric and questionnaire review (content, with the Initiative owners) — [AISDLC-183](https://redhat.atlassian.net/browse/AISDLC-183)

The RHAI Initiative is the first production workload of this Feature; its judgement content has never been reviewed with the people who own initiatives.

Stories:
- C1 (AISDLC-198). Review `types/initiative/prompts/create-guidance.md` (the four clarifying questions, writing rules, don'ts) and `types/initiative/template.md` with the RHOAIENG Initiative owners; agree on what a good Initiative looks like.
- C2 (AISDLC-199). Review the assess-initiative rubric (`assess-rfe` `skills/assess-initiative/scripts/agent_prompt.md`, score fields `what, why, scope, open_to_how, right_sized`) and its calibration against a sample of real RHOAIENG initiatives; changes land upstream in assess-rfe and re-pin `rubric.ref` in the descriptor.
- C3 (AISDLC-200). Review the two initiative dimensions (`alignment` against the RHAISTRAT parent, `feasibility`) and the split rules.
- C4 (AISDLC-201). Refresh the initiative eval dataset and annotations to the agreed content; re-baseline `eval-initiative.yaml`.

Done when: the owners have signed off on questions, template and rubric, and the initiative eval baseline reflects the agreed content.

## Epic D — Initiative pipeline in production, opt-in (decision D4) — [AISDLC-184](https://redhat.atlassian.net/browse/AISDLC-184)

The type, its eval and the generic pipeline exist; nothing consumes the initiative type in production. Start opt-in: only Initiatives carrying an agreed label are selected, dry-run first.

Stories:
- D1 (AISDLC-202). Where initiative results live. Two options, both viable; the shared-tree one is smaller:
  - **Extend the existing results repository with a per-type subtree** (recommended): initiative runs land under `initiative/<run>/` with their own `initiative/latest`; RFE stays at the root exactly as today. Verified against the readers: `snapshot_fetch` and the `bootstrap_snapshot` walk-back take a root directory (`--data-dir`) and follow the `latest` inside it, and both skip any entry whose name is not a `YYYYMMDD-HHMMSS` timestamp, so an `initiative/` directory at the root is invisible to the RFE pipeline; the CI monitor parses job logs, not the repository. Read side: zero code, the initiative job passes `--data-dir <clone>/initiative`. Write side: `push-results.py` gains a destination subdirectory (dest and `latest` under it) and `restore-artifacts.sh` the same, which D3 touches anyway. One repository, one token, no migration of RFE history.
  - **A second repository** (`rfe-autofixer-results-initiative`): cleanest separation, but needs provisioning, a push token and CI variables, a second clone in the job, and the same `restore-artifacts.sh` type awareness; nothing on the read side is simpler than the subtree.
- D2 (AISDLC-203). Autofixer jobs: `autofix-initiative` (scheduled) and `autofix-initiative-stage-dry`, prompt `/rfe-auto-fix --type initiative --announce-complete --jql "project = RHOAIENG AND issuetype = Initiative AND labels = <opt-in label>" …`, ci-stage/ci-prod clones, the green-run guard, results push.
- D3 (AISDLC-204). Make the autofixer scripts and job setup type-aware from the descriptors: the job setup calls `scripts/bootstrap.sh --type <t>` (then the `bootstrap-assess-rfe.sh` forwarder and the old allow rules go), the runner stops enabling the unused image-baked plugin (it doubles the compaction banner once the image is rebuilt), and the three latent bugs are fixed: `restore-artifacts.sh` restores `rfe-tasks` only (initiatives live in `initiatives/`); the submit jobs' report-timestamp grep matches `auto-fix-runs/[0-9]*.yaml` and misses `initiative-run-*.yaml`; `push-results.py` derives the run id from the first non-`issue-snapshot-*` YAML and would pick `initiative-run-*` or `initiative-snapshot-*`.
- D4 (AISDLC-205). Opt-in label: name it, document it for Initiative owners, add it to the descriptor's `conventions.labels`.
- D5 (AISDLC-206). Approval policy for Initiatives: dry-run period, then live with auto-approve off; define the RHOAIENG transition target and comment marker (`[Initiative Creator]`) with the owners before the first live run.
- D6 (AISDLC-207). First production runs monitored (run report, Jira effects), then the opt-in label lifted or kept as a decision.

Done when: a scheduled initiative job runs green on the promoted tree with opt-in scope, and the first live results have been reviewed with the owners.

## Epic E — Eval repository reads types from the descriptors (PR-10) — [AISDLC-185](https://redhat.atlassian.net/browse/AISDLC-185)

`rfe-creator-eval` (`src/rfe_creator_eval/jira_cases.py`) keeps a hand-written `TYPES` map (JQL and project per type) and infers the type from the config filename; it silently falls back to RFE for any unknown type.

Stories:
- E1 (AISDLC-208). Resolve type, JQL and eval config through the checked-out rfe-creator's `type_registry.py` (`identity`, `query_default`, `eval.config`); drop the map; tests.
- E2 (AISDLC-209). Dispatch surface: the GitHub workflow's `eval_config` input derived from the registered types (or a `type` input), so a third type needs no workflow edit.

Done when: a third descriptor with an `eval.config` runs in the eval CI without touching the eval repository.

## Epic F — Registry update for the plugin (skills-registry) — [AISDLC-186](https://redhat.atlassian.net/browse/AISDLC-186)

Stories:
- F1 (AISDLC-210). rfe-creator entry: version `0.2.0` across the entry and both manifests (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`), `author` alignment (entry says `jwforres`, manifest `opendatahub-io`), contract `rubric_ref` pins moved to the current main.
- F2 (AISDLC-211). `provides` metadata for the generic skills (design PR-10) and the `--type` surface described in the entry's contracts.
- F3 (AISDLC-212). Weekly AST-normalised parity sweep across rfe-creator, strat-creator and epic-creator in Upstream Plugin Checks (decision D9, first half).
- F4 (AISDLC-213). spike-executor: #127 (`strict: true` + contract) merged 2026-09-30; what remains is the upstream `repository` field fix in `IKRedHat/SPIKE-executor`.

## Epic G — Pipeline hardening from the 2026-09-29 incident (Feature criterion: "retries, duplicates, partial failures and recovery are tested") — [AISDLC-187](https://redhat.atlassian.net/browse/AISDLC-187)

Stories:
- G1 (AISDLC-214). Stop hook guard — **existing** rfe-creator #209 (in review).
- G2 (AISDLC-215). Submit holds an interrupted revision — **existing** rfe-creator #210 (in review).
- G3 (AISDLC-216). Job-level green-run guard — **existing** rfe-autofixer MR !12 (in review).
- G4 (AISDLC-217). Deterministic zero-work end: `pipeline_state` reaches DONE (or a `RESULT: NO_TASKS` marker) on zero items without the model improvising `set-phase DONE`.
- G5 (AISDLC-218). Auto-approve on a `split` recommendation: decide whether the rubric pass should approve an item the review wants split (the right-sizing gap, `project_right_sized_calibration_gap`), and change `submit.py`'s policy accordingly.
- G6. Approved-item immutability — **existing** RHAIFIRST-397 / RHAIFIRST-398, linked to AISDLC-187 as related (RHAIFIRST-612, the Draft auto-approve epic, linked as well: same approval-policy family as G5).
- G7 (AISDLC-219). Layout guard wording: the checkout still runs the guard once as a no-op (production and dry runs); reword the condition so a skill directory inside the working directory skips it, and pin it.

## Folded or dropped after review (2026-09-30)

- Plugin and packaging follow-ups: the autofixer job setup, forwarder removal and baked plugin moved into D3; the layout-guard wording into G7; the local development marketplace, the marketplace re-test, the Codex test, closing #115/#146 and merging #172 are kept out of Jira for now.
- Validated handoff to Strategy Creator: handled in another Feature.
- Workload selection through Fullsend: what the Feature's scope bullet ("expose workload selection and workload-definition references through Fullsend's config") amounts to under the shipped design is `--type` in the task string, `type` in the result schema, and the validator keyed on the state file; all three are already open review comments on rfe-creator #201 and belong to that PR (and to AISDLC-19, the Fullsend package epic under AISDLC-1), not to a new Epic here.
- Rubric v2 swap (PR-6): dropped for now; re-created when assess-rfe has a DoR rubric to swap in.

## Not proposed as Jira items

- Engine/phase-table split of `pipeline_state.py` for epic-creator and the `creator-core` ADR (D9, second half): epic-creator's need should drive them; suggest a Spike under AISDLC-58 when that consumer is ready rather than an epic here.
- The RHAISTRAT flip (§9): removed from the plan by the 2026-09-03 ruling; an ADR-owner decision with D6 as its gate.

## Suggested order

G (in flight) → E → D (with C running in parallel with the owners) → B → A → F.
