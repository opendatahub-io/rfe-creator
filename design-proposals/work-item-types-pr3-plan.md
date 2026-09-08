# PR-3 plan: the resolution ladder (design §5, §3.2.1, §10 item 3)

**Status:** proposal for maintainer decision, 2026-09-08. Measured on the tip of the PR-2 stack (#176 → #177 → #178 → #179): 96 findings across four inventories (`/tmp/rfe-pr3-inventory/*.json`, kept out of the repo), 40 visible-behavior sites, 40 open questions consolidated into the 12 decisions below.

**Why this is the first behavior-touching step.** Every PR-2 step was proven byte-identical by golden runs. PR-3 cannot be: it prints a resolution line, accepts a new batch form, writes `type:` and `tracker_ref:` into artifacts, verifies fetched issues against their binding, and honours the deployment override. Each of those is listed with its equivalence or eval strategy, and the promotion discipline applies to every PR below (main → ci-stage → autofix-rfe-stage-dry → per-type eval → ci-prod → one watched prod run). Eval-bar reference (recorded 2026-09-08 on main c1df503): rfe 4.16 / 4.64, initiative 3.94 / 3.75; no judge mean may regress by more than 0.3.

**Prerequisites.** The PR-2 stack merged; #172 merged (baselines recorded). Nothing in PR-3 changes a production binding, label value, prompt judgement text or selection query; the transparent-implementation invariant still holds for everything except the artifact fields and the resolve line named below.

## Split: three PRs, in this order

### PR-3a — `resolve` and multi-candidate detection (inert except error paths and one printed line)

Files: `scripts/type_registry.py`, `scripts/validate_types.py`, `scripts/pipeline_state.py` (init only), `scripts/bootstrap-assess-rfe.sh`, `scripts/check_revised.py`, `scripts/check_right_sized.py`, `scripts/check_autofix_complete.py`, tests.

- `TypeRegistry.candidates(item_id, env)` over **effective** bindings (override write prefix first), rungs: every type's `local_id_pattern` full-match → descriptor and effective `key_prefixes` → `local_prefix` (parity rung, kept) → the Jira adapter's generic grammar `^[A-Z][A-Z0-9]+-\d+$` as a *provisional* last rung. `detect()` keeps its Descriptor-or-None contract for the five per-id routers.
- `resolve(...)` + CLI `type_registry.py resolve [--type T] [--batch FILE] [--artifact PATH] [--headless] [IDS...]`: rung 1 explicit `--type`; rung 2 batch mapping `type:`; rung 3 artifact frontmatter `type:` then id grammar; rung 4 interactive fallthrough (interactive only); rung 5 grandfathered `rfe`. Prints `TYPE RESOLVED: <t> (<rung>[; binding override project=…])`; exit non-zero on an unknown type (with the registered list) or conflicting deterministic signals. `JIRA_PROJECT` / `JIRA_ISSUE_TYPE` shorthand applies to the resolved type only; a workspace `rfe-creator.yaml` `bindings:` block is honoured only when not headless (§3.2.1 g).
- One headless predicate: `is_headless(env)` **or** an explicit `--headless`; `pipeline_state.py init --headless` and the headless skill bodies export `RFE_CREATOR_HEADLESS=1` for every subprocess, so the pipeline's flag and the registry's marker cannot disagree.
- Runtime twin of gate-1 rule 1: `assert_registered_binding(desc, env)` (the resolved type's effective `(tracker, project, issue_type)` must be a registered effective binding, sourced from env only when headless). Called by the write scripts in PR-3c; shipped and tested here.
- Unknown-type failures unified on "non-zero with the registered list": `pipeline_state.py init --type` reads `_TYPES.choices()` (pinned equal to `PIPELINE_TYPES` keys until PR-5); `bootstrap-assess-rfe.sh` reads `type_registry.py list`; the hand-parsed `--type` sites validate against the registry.
- **Visible:** error-path text only, plus the resolve line printed by *entry* scripts once PR-3b/3c wire it. **Proof:** unit tests; golden runs on non-error paths; a stage dry run for log shape.

### PR-3b — batch mapping form and parent keys (validator, id allocator, speedrun bodies)

Files: `scripts/validate_batch_input.py`, `scripts/next_rfe_id.py`, `.claude/skills/rfe.speedrun/SKILL.md`, `.claude/skills/initiative-speedrun/SKILL.md`, `tests/test_validate_batch_input.py`, `tests/test_next_rfe_id.py`, pins, baseline.

- Both root forms accepted: the legacy bare list (unchanged, still the harness's form) and `{type: <t>, items: [...]}`. `--type` default becomes `None` so rung 1 and rung 2 can be told apart; absent both → `rfe` printed as `TYPE RESOLVED: rfe (legacy default)`.
- Per-item `type` keys → hard error with the split-the-batch message (design §5); `--type` vs mapping `type:` conflict → hard error (D1).
- Parent-key validation derives per type from `conventions.parent_key_patterns` with the same join `artifact_utils` uses, so batch and task schema agree by construction (Q14 reconciled): initiative batches newly accept `INIT-\d+` parents; rfe entries carrying `parent_key` stay an unknown-field warning (rfe declares no `parent_key` in `batch.extra_fields`); the error text is rendered from the patterns (its two literals leave the prefix baseline). Priority enum per resolved type.
- `next_rfe_id.py --from-batch` accepts the mapping form and derives prefix and directory from its `type:`; explicit `--prefix`/`--dir` that contradict it are a conflict.
- Speedrun bodies document both forms; the Mode A example keeps the list (harness and existing users), the mapping form is described in prose; the pinned body text is updated.
- **Visible:** malformed inputs only, plus INIT- parents accepted for initiatives. **Proof:** unit tests; validator goldens over every shipped and eval batch (list form, byte-identical); the **initiative eval** (parent-key flow runs in every batch); rfe eval optional (list form unchanged). Stage dry run.

### PR-3c — self-describing artifacts, fetch verification, effective binding in the writers

Files: `scripts/artifact_utils.py` (rename stamp, `_type_for` probe, `find_task_file_including_archived`), `scripts/frontmatter.py` (frontmatter rung), `scripts/fetch_issue.py`, `scripts/snapshot_fetch.py` (JQL/binding check only), `scripts/bootstrap_snapshot.py` (report.type cross-check), `scripts/verify_phase.py`, `scripts/submit.py`, `scripts/split_submit.py`, `scripts/check_conflicts.py`, `scripts/generate_run_report.py`, `scripts/generate_review_pdf.py`, the create / split-agent / fetch-agent prompt files of both twins, tests incl. an emulator project import for an overridden-project suite.

- Writers stamp `type:` on **new** artifacts: create (both create skills), split children (both split agents), fetch (`fetch_issue --fetch-all` and both MCP fallbacks), error stubs (`verify_phase` and the two orchestrator stubs), and `rename_to_tracker_key` stamps `tracker_ref:` (plus `type:` if absent) on the two files it already rewrites. Review files are stamped deterministically by `verify_phase` after the review barrier (D8), not by prompt edits. Pre-migration artifacts are not touched (D7); fields are appended, never reordered.
- Readers: `frontmatter.py` chooses the schema from `type:` when present (loud on a type/directory mismatch), path table as fallback; the id-only routers probe `dirs.tasks/<id>.md` for `type:` when candidates are ambiguous, `rfe` default only in interactive runs; `is_existing := tracker_ref present, else key_prefixes-union membership` in `submit.py`, `check_conflicts.py`, `generate_run_report.py` (tracker_ref/role from frontmatter) and the review PDF predicates; `find_task_file_including_archived` gates via `desc.owns()`.
- Post-fetch verification: `fetch_issue` also requests `project` (request-only widening, never persisted) and compares `(project.key, issuetype.name)` with the effective binding before writing; on mismatch headless writes no task file and exits non-zero (the existing `fetch_failed` stub path fires), interactive prints the corrected `--type` to re-run with. The initiative fetch agent switches to `fetch_issue.py --fetch-all --type initiative` (D10). `snapshot_fetch` parses `project =` / `issuetype =` out of `--jql` and fails loudly on a conflict with the resolved binding; `bootstrap_snapshot` cross-checks `report.type` (legacy reports without it tolerated).
- Effective binding in the writers: after `resolve` printed and `assert_registered_binding` passed, `submit.py` and `split_submit.py` take project, issue type and write prefix from `desc.binding(env)`; `_TRACKER` is keyed by type name; the dry-run sentinel is `<PROJECT>-DRY` in one form; run reports gain an additive `binding:` key (rule e). Snapshot header metadata is deferred (D11).
- **Visible:** every new artifact carries `type:` (and `tracker_ref:` after fetch or rename); run reports carry `binding:`; the resolve line in submit/split/fetch output; golden pins in `test_pr1_transparent_edits.py`, `test_fetch_issue.py` and `test_type_registry_pins.py` change deliberately. Dry-run plans otherwise unchanged. **Proof:** unit + the three emulator suites, plus an overridden-project suite (KONFLUX-style import into the emulator); **both full evals**; stage dry run; one watched production run.

## Decisions requested (recommendation first)

| # | Decision | Recommendation |
|---|---|---|
| D1 | `--type` vs mapping `type:` disagree | Hard error always: both are explicit, and headless must not guess. |
| D2 | Per-item `type` key | Hard error (design §5). No shipped or eval input carries it, so this is not a production change. |
| D3 | Who prints `TYPE RESOLVED` | Entry scripts only (`init`, `submit`, `split_submit`, `validate_batch_input`, `fetch_issue`, `next_rfe_id`); subprocesses the pipeline already calls with `--type` stay silent (about 14 lines per phase otherwise). |
| D4 | What "headless" means for resolve and the override | `is_headless(env)` or an explicit `--headless`, with the pipeline and headless bodies exporting `RFE_CREATOR_HEADLESS=1`, so there is one predicate. |
| D5 | `detect()` API | Keep single-or-None for the routers; add `candidates()` for resolve; the frontmatter probe decides among candidates; headless fails loudly on an unowned id, interactive keeps the `rfe` default. |
| D6 | Placeholder local prefixes (rung 3c) | Defer: no shipped type has one; the paper epic descriptor is the only case and PR-3 does not register it. |
| D7 | Back-fill | New artifacts only; rename stamps `tracker_ref` on the files it already rewrites; no retroactive pass; fields appended. |
| D8 | Who stamps `type:` on reviews | `verify_phase` after the review barrier (deterministic, no prompt edits before PR-5); interactive reviews stay unstamped and readers tolerate that. |
| D9 | Project witness for post-fetch verification | Request the `project` field explicitly (request-only, like `issuetype`); do not infer from the key stem. |
| D10 | Initiative fetch path | Switch the agent to `fetch_issue.py --fetch-all --type initiative`: one writer, verification for free. MCP fallbacks gain the fields and the stamp and stay body-verified until PR-5. |
| D11 | Rule (e) metadata | Run reports gain the additive `binding:` key in PR-3c; snapshot header keys deferred to a follow-up that updates `docs/snapshot-incremental-fetch.md` first. |
| D12 | `_TRACKER` / `SPLIT_CONFIG` under an override | Key by type name; take the pair from `binding()`; `assert_registered_binding` is the runtime guard against collisions. |

## Risks

Log shape changes in CI traces (D3 keeps it to one line per entry script). Artifact bytes in the results repo change permanently from PR-3c on (expected and named). PR-3c edits the twin prompts ahead of PR-5's collapse; PR-5 lifts the edited text verbatim. Two evals per PR-3c iteration cost about $125 and three hours; PR-3a and PR-3b need one or none.
