# `types/` — work-item type descriptors

One directory per work-item type, each holding exactly one file, `type.yaml`, validated by
`_schema/type.schema.json` (JSON Schema draft 2020-12; its `description` strings are the field
reference). Design: `design-proposals/work-item-types-unified.md` §3.2 (contract), §3.2.1
(binding override), §3.3 (gates), §3.7 (input chain), §8.2 (worked example).

**Data only.** Nothing executable ever lives under `types/` (`validate_types.py` fails on any
`*.py`/`*.sh`/`*.bash`/`*.zsh` or shebang file under a descriptor root); scripts stay in `scripts/`
and are invoked by their cwd-relative path (`python3 scripts/type_registry.py …`, design §3.5.1).
The provider guide is `docs/type-provider-guide.md`.

**Status (PR-2a): 18 scripts read the registry** (table below). An adopted script does
`import type_registry` and `_TYPES = type_registry.load()` once at import and builds its per-type
table as a comprehension over `_TYPES.names()`, so a drop-in type appears in it without a code
change. Adopted scripts use DESCRIPTOR values only (`Descriptor.get`, `dirs()`, `labels`,
`key_prefixes`, `write_prefix`, `local_prefix`, `id_field`, `score_fields`; the prefix sniffs
became `TypeRegistry.detect()`); the effective binding (`binding()`) and `resolve` are PR-3.
Every value a pending script still carries is pinned by `tests/test_type_registry_pins.py` to its
descriptor projection — source of truth *by test* until it adopts (§10); that file's `MIGRATED`
list names the matrix rows already covered *by import*, and each adoption deletes its pin.

`type_registry.DEFAULT_ROOT` is `__file__`-relative (`<scripts dir>/../types`): a harness that
copies `scripts/*.py` elsewhere must copy or symlink `types/` beside it (symlinking `scripts/`
itself is fine — `resolve()` follows the link).

## Adoption status

| Script | Registry it read from the descriptor | Status |
|---|---|---|
| `batch_summary.py` | `_TYPE_CONFIG` (dirs view of `generate_run_report.TYPE_CONFIG`), `--type` choices | PR-2a |
| `check_conflicts.py` | `_TYPE_CONFIG`, `--type` choices | PR-2a; scanner fork by type name and `startswith(jira_prefix)` → prefix-union pending |
| `check_revised.py` | `_TYPE_CONFIG` | PR-2a |
| `check_right_sized.py` | `_TYPE_CONFIG`, `pipeline.resplit` | PR-2a |
| `collect_children.py` | `id_field`, `--type` choices | PR-2a; scanner fork by type name pending |
| `collect_recommendations.py` | `_review_dir`, `--type` choices | PR-2a |
| `error_collect.py` | `_TYPE_CONFIG`, `--type` choices | PR-2a |
| `filter_for_revision.py` | prefix sniff → `detect()` | PR-2a |
| `generate_review_pdf.py` | `REPORT_CONFIG`, `--type` choices | PR-2a |
| `generate_run_report.py` | `TYPE_CONFIG`, `--type` choices | PR-2a; scanner fork by type name and the `tracker_ref`/role predicates pending |
| `jql_query.py` | default exclusion wrapper, `--project` choices | PR-2a |
| `next_rfe_id.py` | `DEFAULT_PREFIX` / `DEFAULT_DIR` | PR-2a |
| `prep_assess.py` | prefix sniff → `detect()` | PR-2a |
| `preserve_review_state.py` | prefix sniff → `detect()` | PR-2a |
| `reassess_save.py` | `_TYPE_CONFIG`, `--type` choices | PR-2a |
| `split_collect.py` | `_TYPE_CONFIG`, `_set_revise` defaults, `--type` choices | PR-2a |
| `validate_batch_input.py` | `ALLOWED_PRIORITIES`, known fields, `--type` choices | PR-2a; `PARENT_KEY_PATTERN` literal until PR-3 (Q14) |
| `verify_phase.py` | phase tables, `_TYPE_CONFIG`, error-stub score tail, `--type` choices | PR-2a |
| `artifact_utils.py` | `SCHEMAS`, forked scan/rename/parse pairs, sniff dispatchers | pending |
| `bootstrap_snapshot.py` | `BOOTSTRAP_CONFIG`, `issue-snapshot-` probe | pending |
| `check_autofix_complete.py` | `_TYPE_CONFIG` | pending |
| `check_content_preservation.py` | inline dir branch | pending |
| `check_review_progress.py` | `PHASE_CHECKS` | pending |
| `cleanup_partial_split.py` | inline dir branch | pending |
| `compare_review_outputs.py` | `_TYPE_CONFIG` | pending |
| `fetch_issue.py` | rfe-only paths | pending |
| `frontmatter.py` | `_detect_schema_type` | pending |
| `jira_utils.py` | `strip_metadata` prefix regex | pending |
| `pipeline_state.py` | `PIPELINE_TYPES` (prompt/skill entries move in PR-5) | pending |
| `snapshot_fetch.py` | `SNAPSHOT_CONFIG` | pending |
| `split_submit.py` | `SPLIT_CONFIG` | pending (last, with `submit.py`) |
| `submit.py` | `TYPE_CONFIGS` | pending (last; emulator suites in CI) |

## Adding a type

1. `cp -r types/rfe types/<name>` and set `type: <name>` (must equal the directory name).
2. Edit only the extension points listed below; leave the shared machinery keys as they are.
3. `python3 scripts/validate_types.py` (gate 1); after `bash scripts/bootstrap-assess-rfe.sh`,
   `python3 scripts/validate_types.py --with-deps` (gate 2). Inspect with
   `python3 scripts/type_registry.py show <name>` / `binding <name>`.
4. Meet the provider floor (§3.4): a ≥16-case anonymized eval dataset with at least one sparse or
   adversarial case, populated `expected_*` annotations, explicit `eval.thresholds`, the committed
   eval config, one QUICK_MODE run on the PR, and a seed for the tracker emulator.
5. Need a key the schema lacks? Tiers, in order: descriptor data → declarative rule → a companion
   skill in your own repo consuming the declared-stable script CLIs → core PR. New vocabulary is
   promoted only on the **second requester**.

During development a drop-in root can be registered with `RFE_CREATOR_EXTRA_TYPES` (`os.pathsep`
separated directories, each holding `<name>/type.yaml`). Directories whose name starts with `_`
are skipped; a duplicate type name across roots is an error. The seam is dev/test only: in a
headless or CI run (`RFE_CREATOR_HEADLESS`, `CI` or `GITHUB_ACTIONS` set) an entry is honoured only
when its canonical path is listed in `RFE_CREATOR_EXTRA_TYPES_ALLOWLIST` (a protected CI variable);
the rest are dropped with one stderr line. Explicit `--extra-roots` values are never gated.

## The extension points (= the `rfe` ↔ `initiative` diff)

The acceptance test of the contract (§3.4): the diff between the two shipped descriptors must be
exactly the extension points. `tests/test_type_registry_pins.py` (PR1-32) flattens both files and
asserts that every differing or one-sided leaf falls under one of these keys:

| Group | Dotted keys that differ between the two files |
|---|---|
| Identity | `type`, `display.entity`, `display.entity_plural`, `identity.jira.{project,issue_type,key_prefixes}`, `identity.{local_prefix,local_id_pattern,id_field}` |
| Layout | `dirs.{tasks,originals,reviews}`, `index.enabled`, `companions.comments`, `batch.extra_fields`, `snapshot.{prefix,report_prefix}` |
| Conventions | `conventions.{type_label,label_prefix,comment_prefix,removed_context_preamble,query_default,parent_key_patterns}`, `conventions.labels.*` (every reserved key; `alignment.*` only when an alignment dimension exists) |
| Schema | `schema.task.extra_fields` (rfe adds `size`), `schema.review.{score_fields,extra_fields,extra_rules}` |
| Pipeline | `pipeline.{poll_prefix,state_prefix,scorer_agent}`, `pipeline.prompts.*`, `pipeline.dimensions[]` (name/prompt/blocking/condition/skip_stub), `pipeline.rubric.{ref,path,export}`, `pipeline.context_sources[].args` (the `--type` argument) |
| Reporting | `reporting.{item_key,criterion_labels,criterion_short_labels,before_score_name_map,pdf.extra_fields,run_report.extra_entry_fields}` |
| Eval | `eval.{config,dataset,mlflow_experiment,timeout,thresholds,annotations_extra}` (the quality judge is named per type) |

Identical in both files — shared machinery, **not** extension points: `schema_version`, `kind`,
`identity.tracker`, `identity.jira.{split_link_type,state_map}`, `companions.removed_context`,
`schema.task.priority`, `pipeline.{stages,resplit,rubric.repo}`, `pipeline.context_sources[]`
(name/bootstrap/required), and eight of the ten eval judge thresholds.

## Reserved vocabulary (schema only, no consumer at v1)

`kind` (`work-item` | `strategy` | `decomposition` — the engine runs `work-item` only, and gate 1
rejects a *registered* descriptor of any other kind), `inputs[]`
and `produces[]` (§3.7 cross-type chain; rfe-creator ships no gate evaluator),
`identity.jira.{duplicate_link_type, state_map.close_duplicate, state_map.ready, priority}`, the whole
`identity.github` branch (§8.6 shape; adapter/emulator are first-requester work), `classification`,
`dirs.{dupes,merges}`, `companions.extra_suffixes`, `conventions.labels.{processing,human_sign_off,
templates}`, `conventions.query_default`, `schema.task.priority` (task-side vocabulary +
`map_to_tracker`; today's enum is pinned equal to `artifact_utils.SCHEMAS`),
`schema.review.{extra_scores,extra_rules}`, `pipeline.context_sources` (descriptive; SETUP is still
hardcoded), `pipeline.dimensions[].{blocking, condition.context_exists, setup}`,
`snapshot.{mode,processed_gate}`.

The third data point (R1) is the paper descriptor `tests/fixtures/types/epic/type.yaml` for
epic-creator's real `(RHAI, Epic)` binding. It is validated by the schema and never enumerated by
the registry; its `NOT EXPRESSIBLE AT V1` comments are the recorded list of what the reserved
vocabulary cannot say (composite local ids, value transforms in label templates, set-level review
without criteria, remote-children skip checks, sibling `Blocks` links, attachments/components).

## Deployment binding override (§3.2.1)

`identity.<tracker>` is the **default** binding. `type_registry.Descriptor.binding()` returns the
**effective** binding: the descriptor overlaid with `RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,
LOCAL_PREFIX}` (`TYPE` upper-cased). Overridable: `project`, `issue_type`, `key_prefixes` (write
prefix becomes `<PROJECT>-`, descriptor prefixes stay as read prefixes), `query_default`, link
types, `state_map`, `parent_key_patterns`, optionally `local_prefix`. Never: judgement content,
`dirs`, `schema`, `rubric`, `eval`. Rules: zero-config default (no required env var); the uniqueness
lint runs on *effective* bindings and no `local_prefix` stem may equal any effective project key; the
dry-run sentinel is `<PROJECT>-DRY`; run reports and snapshots record the effective binding.
**Trust boundary:** headless and CI runs honour an override only from the environment (protected CI
variables), never from a workspace file the checkout could carry; the effective `(project,
issue_type)` pair is printed at resolve time and checked against the registered bindings before the
first tracker write — an untrusted source or an unregistered pair is a hard failure. `DRAFT-` is not
adopted at v1.

## Lint gates (§3.3)

1. **Gate 1 — lint time** (`python3 scripts/validate_types.py`, in `make lint` and CI): at least one
   descriptor is discovered (an empty root fails, never a vacuous pass); JSON Schema; `kind` is
   `work-item`; every repo-relative reference exists (`pipeline.prompts.*`, `dimensions[].prompt`,
   `eval.config`, `eval.dataset`); `score_fields` non-empty and the review schema accepts the
   `verify_phase` error stub; no executable code under a descriptor root; cross-type: unique
   effective `(tracker, project, issue_type)`, unique `local_prefix`, `local_id_pattern`,
   `id_field`, poll/state prefixes (empty allowed for `rfe` only), snapshot prefixes non-empty and
   pairwise not prefix-of-each-other, `report_prefix` non-empty except `rfe`, no `local_prefix`
   stem equal to an effective project key, `rubric.ref` a 7–40-char hex SHA (or, for the D3
   embedded rubric `rubric.repo: self`, `rubric.rubric_version` a 7–64-char hex content hash).
2. **Gate 2 — `--with-deps`** (after bootstrap): `rubric.path` exists under `--assess-dir` (under
   the repo root when `rubric.repo` is `self`) and the agent file `agents/<scorer_agent>.md` is
   present in the assess checkout.
3. **Gate 3 — `--verify --type <t>`**: gate 1 for one type with a one-line diagnosis; a pipeline
   SETUP fail-fast that nothing wires until PR-8.

Alongside: `python3 scripts/lint_prefix_predicates.py` rejects new literal key/snapshot prefixes
(`RHAIRFE-`, `RHOAIENG-`, `RFE-`, `INIT-`, `RHAISTRAT-`, `issue-snapshot-`, `initiative-snapshot-`,
`initiative-run-`) in code outside the registry; the per-file baseline in
`tests/data/prefix_predicate_baseline.json` may only shrink.

## Published contract strings (R7) — never change without a downstream migration note

| Key | rfe | initiative |
|---|---|---|
| `conventions.labels.rubric_pass` | `rfe-creator-autofix-rubric-pass` (strat-creator's intake gate, 86 occurrences) | `initiative-autofix-rubric-pass` |
| `conventions.comment_prefix` | `[RFE Creator]` | `[Initiative Creator]` |
| `conventions.removed_context_preamble` | `*[RFE Creator]* The following technical implementation details were removed from the RFE description during review. This content is better suited for a RHAISTRAT and is preserved here for reference:` (string-matched by strat-creator `strategy-refine/SKILL.md`) | `*[Initiative Creator]* The following technical implementation details were removed from the Initiative description during review. This content may be useful as strategy context and is preserved here for reference:` |
| `inputs[].body_source.attachment` | `{key}-strategy.md` (strat-creator push/pull convention) | — |

## Where the descriptors deliberately differ from the design text

`main` wins over the prose; each is commented inline: `resplit.below: 2` for both
(`check_right_sized.py` reads it since PR-2a, so the initiative `1` of §8.2 is a deliberate later
behaviour change, Q3), `rubric.ref` values are documentary until the
bootstrap pins refs (PR-2), `rubric.repo` carries the full URL the bootstrap holds, rfe
`snapshot.prefix` is `issue-snapshot-` (submit's `''` is a grandfathered sentinel projection),
`query_default` has no consumer, `auto_created`/`auto_revised`/`split_result`/`split_original` are
emitted labels (first-class keys), `split_child_marker` lower-cases `{parent}`/`{child_id}`,
`skip_stub` carries both `result` and `reason`, prompts point at today's live skill paths until
PR-5, `local_id_pattern` uses one backslash, and `eval.thresholds` is the verbatim judge map.
