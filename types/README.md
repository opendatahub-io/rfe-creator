# `types/` — work-item type descriptors

One directory per work-item type, each holding exactly one file, `type.yaml`, validated by
`_schema/type.schema.json` (JSON Schema draft 2020-12; its `description` strings are the field
reference). Design: `design-proposals/work-item-types-unified.md` §3.2 (contract), §3.2.1
(binding override), §3.3 (gates), §3.7 (input chain), §8.2 (worked example).

**Data only.** Nothing executable ever lives under `types/` (`validate_types.py` fails on any
`*.py`/`*.sh`/`*.bash`/`*.zsh` or shebang file under a descriptor root); scripts stay in `scripts/`
and are invoked by their cwd-relative path (`python3 scripts/type_registry.py …`, design §3.5.1).
The provider guide is `docs/type-provider-guide.md`.

**Status (PR-2d): 26 scripts read the registry** (table below). An adopted script does
`import type_registry` and `_TYPES = type_registry.load()` once at import and builds its per-type
table over the registry (`_TYPES.names()` or iteration — both in `names()` order), so a drop-in
type appears in it without a code change. Adopted scripts use DESCRIPTOR values only (`Descriptor.get`, `dirs()`, `labels`,
`key_prefixes`, `write_prefix`, `local_prefix`, `local_id_pattern`, `id_field`, `score_fields`;
the prefix sniffs became `TypeRegistry.detect()`); the effective binding (`binding()`) and
`resolve` are PR-3. Since PR-2b the artifact schemas and the poll phase table are projections
too: `artifact_utils.SCHEMAS` builds `<type>-task` / `<type>-review` for every registered type
that declares `identity.{tracker,id_field,local_id_pattern}`, `conventions.parent_key_patterns`,
`schema.task.priority.enum` and `schema.review.score_fields` (plus the optional
`schema.{task,review}.extra_fields`; a partial drop-in gets no schema — and no `frontmatter.py`
path entry — instead of breaking the import, while a shipped type missing one fails the import),
the scan / rename / parse helpers are generics over a `Descriptor` (the historical per-type names
remain as wrappers), and `check_review_progress.PHASE_CHECKS` is `dirs` x `pipeline.poll_prefix`
x `pipeline.dimensions[].name` (a drop-in without `dirs.{tasks,reviews}` and
`pipeline.poll_prefix` contributes no rows). `tests/test_schemas_golden.py` pins the derived
schemas byte for byte. Since PR-2c the snapshot side is a projection too: `snapshot_fetch.SNAPSHOT_CONFIG`
(`conventions.labels.{ignore,split_quarantine}`, `snapshot.prefix`) and
`bootstrap_snapshot.BOOTSTRAP_CONFIG` (`snapshot.report_prefix`, `reporting.item_key`) build over
`names()`, the snapshot reader / writer helpers' default `prefix=` is the rfe `snapshot.prefix`
bound once at import (submit's `''` sentinel keeps relying on it), and `fetch_issue.py --fetch-all`
writes the selected `--type`'s layout (`dirs.{tasks,originals}`, `identity.id_field`, the comments
companion gated on `companions.comments`). Those two tables are built at import for **every**
registered type — and `submit.py` imports `snapshot_fetch` — so a drop-in must carry
`conventions.labels.{ignore,split_quarantine}` and `snapshot.{prefix,report_prefix}` (gate 1
already requires the two `snapshot` keys but not the two labels; copying `types/rfe/` keeps them
all) or the import fails with a `KeyError` naming the type and the field. Since PR-2d the Jira write
path is a projection too: `submit.TYPE_CONFIGS` and `split_submit.SPLIT_CONFIG` build over `names()`
from `identity.jira.{project,issue_type,key_prefixes}`, `identity.{id_field,local_prefix}`,
`dirs.{tasks,reviews,originals}`, `display.{entity,entity_plural}`,
`conventions.{type_label,label_prefix,comment_prefix,removed_context_preamble}`,
`conventions.labels.{rubric_pass,feasibility}` (`alignment` optional), `index.enabled` and
`snapshot.prefix` (all schema-required except the two label keys — a drop-in missing either fails
the import of `submit.py` with the same `KeyError`); the task scan / rename go through the
`artifact_utils` generics keyed on the descriptor, `--auto-approve` transitions to
`identity.jira.state_map.approved` (optional in the schema, read with a `None` default at startup:
a type without it submits, and only `--auto-approve` is refused — a usage error naming the type
and the field, before any scan or Jira call), and a split links with
`identity.jira.split_link_type` and closes the parent with
`identity.jira.state_map.close_superseded` (both optional too, read with a `None` default at
import: a type that never splits may omit them, and `split_submit.py` refuses to split a parent
of such a type before any scan or Jira call — exit 5, the systemic code, so `submit.py`'s split
loop aborts instead of flagging every parent; it recovers the two facts by the
`(project, issue_type)` pair, so two registered types sharing a pair fail its import with a
`RegistryError` naming both). Two rfe
grandfathers stay pinned as residues — submit's `''` `snapshot_prefix` sentinel and the argv
convention that passes no `--type` for rfe to `split_submit.py` and the report scripts — and the
`auto-created` / `auto-revised` / `needs-attention` / `split-quarantine` labels are still composed
from `conventions.label_prefix`. Every value a pending script still carries is pinned by `tests/test_type_registry_pins.py`
to its descriptor projection — source of truth *by test* until it adopts (§10); that file's
`MIGRATED` list names the matrix rows already covered *by import*, and each adoption deletes its
pin.

`type_registry.DEFAULT_ROOT` is `__file__`-relative (`<scripts dir>/../types`): a harness that
copies `scripts/*.py` elsewhere must copy or symlink `types/` beside it (symlinking `scripts/`
itself is fine — `resolve()` follows the link).

## Adoption status

| Script | Registry it read from the descriptor | Status |
|---|---|---|
| `artifact_utils.py` | `SCHEMAS` (`<type>-task` / `<type>-review` per type), `scan_tasks` / `scan_reviews` / `rename_to_tracker_key` / `parse_child` generics over a `Descriptor` (the per-type names are wrappers), `detect()` in `find_review_file` / `find_removed_context_yaml` | PR-2b; name-keyed until PR-3: the rename error label, rfe's slug-tolerant review lookup and `parse_child`'s rfe markdown fallbacks; the `rfes.md` index contract stays literal (`index.enabled`) |
| `batch_summary.py` | `_TYPE_CONFIG` (dirs view of `generate_run_report.TYPE_CONFIG`), `--type` choices | PR-2a |
| `bootstrap_snapshot.py` | `BOOTSTRAP_CONFIG` (`snapshot.report_prefix`, `reporting.item_key`), `--type` choices | PR-2c; the `issue-snapshot-` run-dir probe (`_run_dir_has_snapshots`) reads the rfe descriptor's `snapshot.prefix` and stays rfe-only for every `--type` (grandfathered; the per-type probe is a deliberate follow-up, design §10 PR-10) |
| `check_conflicts.py` | `_TYPE_CONFIG`, `--type` choices; task scan via `artifact_utils.scan_tasks(desc)` | PR-2a, PR-2b; `startswith(jira_prefix)` → prefix-union pending |
| `check_revised.py` | `_TYPE_CONFIG` | PR-2a |
| `check_review_progress.py` | `PHASE_CHECKS`, `check_id` id field + modes by phase base, `--phase` / `--also-phase` choices | PR-2b; `_detect_fast` config allowlist literal until the `initiative-speedrun-config` drift is fixed deliberately; the rfe-only `create` row (`_CREATE_BARRIER_TYPES`, lifted in PR-5) and the initiative row order (`_LEGACY_ROW_ORDER`, CLI choices text) are documented legacy literals |
| `check_right_sized.py` | `_TYPE_CONFIG`, `pipeline.resplit` | PR-2a |
| `collect_children.py` | `id_field`, `--type` choices; task scan via `artifact_utils.scan_tasks(desc)` | PR-2a, PR-2b |
| `collect_recommendations.py` | `_review_dir`, `--type` choices | PR-2a |
| `error_collect.py` | `_TYPE_CONFIG`, `--type` choices | PR-2a |
| `fetch_issue.py` | `--fetch-all` layout (`dirs.{tasks,originals}`, `identity.id_field`, the comments companion and its request gated on `companions.comments`), new `--type` (registry choices, default `rfe`; the no-`--type` invocation is byte-identical) | PR-2c; `status=Ready` and the `Major` priority fallback stay literal (shared pipeline vocabulary, not type facts) |
| `filter_for_revision.py` | prefix sniff → `detect()` | PR-2a |
| `frontmatter.py` | `_detect_schema_type` path table (`_SCHEMA_BY_DIR`), `schema` / `--schema-type` choices through `SCHEMAS` | PR-2b |
| `generate_review_pdf.py` | `REPORT_CONFIG`, `--type` choices | PR-2a |
| `generate_run_report.py` | `TYPE_CONFIG` (`scan_tasks` bound to `artifact_utils.scan_tasks(desc)`), `--type` choices | PR-2a, PR-2b; the `tracker_ref`/role predicates pending (PR-3) |
| `jql_query.py` | default exclusion wrapper, `--project` choices | PR-2a |
| `next_rfe_id.py` | `DEFAULT_PREFIX` / `DEFAULT_DIR` | PR-2a |
| `prep_assess.py` | prefix sniff → `detect()` | PR-2a |
| `preserve_review_state.py` | prefix sniff → `detect()` | PR-2a |
| `reassess_save.py` | `_TYPE_CONFIG`, `--type` choices | PR-2a |
| `snapshot_fetch.py` | `SNAPSHOT_CONFIG` (`conventions.labels.{ignore,split_quarantine}`, `snapshot.prefix`), default `prefix=` of `find_previous_snapshot` / `load_snapshot_from_dir` / `update_snapshot_hashes` (rfe `snapshot.prefix`, bound at import), `--type` choices | PR-2c; the hard-filter JQL wrapper and the `f"{prefix}{ts}.yaml"` file name are composition, still pinned by source form |
| `split_collect.py` | `_TYPE_CONFIG`, `_set_revise` defaults, `--type` choices | PR-2a |
| `split_submit.py` | `SPLIT_CONFIG` (descriptor projection over `names()`: `identity.jira.{project,issue_type}`, `conventions.{comment_prefix,label_prefix}`, `display.{entity,entity_plural}`, `id_field`, `dirs`, `index.enabled`, alignment labels; `scan_fn` / `rename_fn` / `parse_child_fn` bound to the `artifact_utils` generics, `find_review_fn` = `find_review_file`), the split link type and the close-superseded transition / resolution from `identity.jira.{split_link_type,state_map.close_superseded}`, `--type` choices | PR-2d; the feasibility set, the split-child marker and every phase label are still composed from `conventions.label_prefix`; the durable-store comment grammar and the `<PROJECT>-DRY` sentinel are composition, pinned by source form |
| `submit.py` | `TYPE_CONFIGS` (descriptor projection over `names()`), `--type` choices, task scan / rename via `artifact_utils.scan_tasks` / `rename_to_tracker_key(desc)`, approve target from `identity.jira.state_map.approved` | PR-2d; grandfathered: the rfe `snapshot_prefix` `''` sentinel and the `split_type_arg` / report `--type` argv convention (no `--type` for rfe); the `auto-created` / `auto-revised` / `needs-attention` / `split-quarantine` labels are still composed from `conventions.label_prefix` |
| `validate_batch_input.py` | `ALLOWED_PRIORITIES`, known fields, `--type` choices | PR-2a; `PARENT_KEY_PATTERN` literal until PR-3 (Q14) |
| `verify_phase.py` | phase tables, `_TYPE_CONFIG`, error-stub score tail, `--type` choices | PR-2a |
| `check_autofix_complete.py` | `_TYPE_CONFIG` | pending |
| `check_content_preservation.py` | inline dir branch | pending |
| `cleanup_partial_split.py` | inline dir branch | pending |
| `compare_review_outputs.py` | `_TYPE_CONFIG` | pending |
| `jira_utils.py` | `strip_metadata` prefix regex | pending |
| `pipeline_state.py` | `PIPELINE_TYPES` (prompt/skill entries move in PR-5) | pending |

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
templates}`, `conventions.query_default`, `schema.task.priority.map_to_tracker` (the `enum`
beside it feeds `artifact_utils.SCHEMAS` and `validate_batch_input.ALLOWED_PRIORITIES` since
PR-2b / PR-2a; the map has no consumer), `schema.review.{extra_scores,extra_rules}`,
`pipeline.context_sources` (descriptive; SETUP is still hardcoded),
`pipeline.dimensions[].{blocking, condition.context_exists, setup}`,
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
