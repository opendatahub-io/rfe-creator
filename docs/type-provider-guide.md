# Work item type provider guide

How a new work item type is added to rfe-creator, what the contract enforces, and where the
authoritative text lives. Design: `design-proposals/work-item-types-unified.md` §3 (contract),
§3.2.1 (binding override), §3.3 (gates), §3.4 (provider obligations), §3.5 (discovery), §3.7
(cross-type chain).

**Status (PR-2c): 24 scripts read the registry at import** — the "Adoption status" table in
[`types/README.md`](../types/README.md) lists them and what is still pending. Since PR-2b the
artifact schemas (`artifact_utils.SCHEMAS`), the scan / rename / parse helpers and the poll phase
table (`check_review_progress.PHASE_CHECKS`) are projections too, so a registered type gets its
`<type>-task` / `<type>-review` schemas, its `<poll_prefix><phase>` rows and the generics without
a code change; since PR-2c so are the snapshot tables (`snapshot_fetch.SNAPSHOT_CONFIG`,
`bootstrap_snapshot.BOOTSTRAP_CONFIG`) and the `fetch_issue.py --fetch-all` layout (`--type`,
default `rfe`), so a registered type gets its hard-filter labels, snapshot / run-report file
prefixes and fetch layout the same way. Every per-type value a pending script still carries is pinned by test to its
descriptor projection; each adoption deletes its pin (design §10). Adopted scripts use descriptor
values only; the effective binding override is not consulted until PR-3.

## The surface, in one table

| What | Where |
|---|---|
| Field reference (every key, with its consuming `file:line`) | `types/_schema/type.schema.json` `description` strings; `types/rfe/type.yaml` and `types/initiative/type.yaml` |
| Extension points vs shared machinery, reserved vocabulary, R7 frozen strings, binding override and trust boundary, gate list | [`types/README.md`](../types/README.md) |
| Copy-from skeleton | `types/rfe/` (`cp -r types/rfe types/<name>`) |
| Registry API and CLI | `scripts/type_registry.py` (`list`, `show`, `get`, `binding`); in code `load()`, `names()`/`choices()`/`get()`, `detect(item_id)` |
| Validator (gates 1–3) | `scripts/validate_types.py` (`make lint` runs gate 1) |
| Anti-regression prefix lint and its ratchet baseline | `scripts/lint_prefix_predicates.py`, `tests/data/prefix_predicate_baseline.json` (only ever shrinks) |
| Recorded gaps (R1): what the v1 vocabulary cannot say | `NOT EXPRESSIBLE AT V1` comments in `tests/fixtures/types/epic/type.yaml`; `not_expressible_at_v1` in `tests/fixtures/types/strategy-inputs.yaml` |
| Contract tests | `tests/test_type_registry.py`, `tests/test_validate_types.py`, `tests/test_lint_prefix_predicates.py`, `tests/test_type_registry_pins.py`; `tests/test_schemas_golden.py` pins the derived artifact schemas byte for byte |

## Adding a type

Follow "Adding a type" in [`types/README.md`](../types/README.md): copy `types/rfe/`, edit only the
extension points, run `python3 scripts/validate_types.py` (gate 1) and, after
`bash scripts/bootstrap-assess-rfe.sh`, `python3 scripts/validate_types.py --with-deps` (gate 2).
Then meet the provider floor (design §3.4): a ≥16-case anonymized eval dataset with at least one
sparse or adversarial case, populated `expected_*` annotations, explicit `eval.thresholds`, the
committed eval config, one QUICK_MODE run on the PR, and a seed for the tracker emulator.

Rules that are easy to trip:

- **Data only.** Nothing executable under a descriptor root — `validate_types.py` fails on any
  `*.py`/`*.sh`/`*.bash`/`*.zsh` or shebang file there. Scripts live in `scripts/` and are invoked
  by their cwd-relative path (the working directory is the plugin root, design §3.5.1).
- **`kind: work-item` only.** The schema also admits `strategy` and `decomposition` so sibling
  pipelines' descriptors validate as fixtures, but a registered descriptor of another kind is a
  gate-1 finding: the v1 engine has one phase table.
- **Second-requester rule.** New vocabulary is promoted only when a second type needs it. Tiers, in
  order: descriptor data → declarative rule → a companion skill in your own repo consuming the
  declared-stable script CLIs → core PR.
- **Schemas need every schema fact.** `artifact_utils.SCHEMAS` derives `<type>-task` /
  `<type>-review` from `identity.{tracker,id_field,local_id_pattern}`,
  `conventions.parent_key_patterns`, `schema.task.priority.enum` and `schema.review.score_fields`
  (plus the optional `schema.{task,review}.extra_fields`). Gate 1 does not require all of them,
  so a drop-in that omits one is registered but gets no schemas (and no `frontmatter.py` path
  entry): every read / write / validate against it fails with "Unknown schema type" instead of
  the import failing. That tolerance is for drop-in roots only — a type under `types/` itself is
  built unconditionally, and a missing fact fails the import with a `KeyError` naming the field.
  `check_review_progress.PHASE_CHECKS` applies the same rule to `dirs.{tasks,reviews}` and
  `pipeline.poll_prefix`: a drop-in without them polls no phase. Copying `types/rfe/` keeps them
  all.
- **Snapshot facts are read at import, for every type.** `snapshot_fetch.SNAPSHOT_CONFIG` reads
  `conventions.labels.{ignore,split_quarantine}` and `snapshot.prefix`, and
  `bootstrap_snapshot.BOOTSTRAP_CONFIG` reads `snapshot.report_prefix` and `reporting.item_key`,
  over every registered type when the module is imported — and `submit.py` imports
  `snapshot_fetch`. Gate 1 already requires `snapshot.prefix` and `snapshot.report_prefix`
  (schema `$defs/snapshot`, the `else` branch of the `mode` conditional; the prefix is also
  linted non-empty and collision-free) and `reporting.item_key`, but NOT
  `conventions.labels.ignore` / `.split_quarantine` (`$defs/labels` is an open map with no
  required keys), so a drop-in that omits either label passes `validate_types.py` and then fails
  the import of `snapshot_fetch.py`, `bootstrap_snapshot.py` and `submit.py` with a `KeyError`
  naming the type and the field. Copying `types/rfe/` keeps them; a third type must also pass
  `prefix=` explicitly to the snapshot helpers (their default is the rfe `snapshot.prefix`).
  `fetch_issue.py --fetch-all --type <t>` reads `dirs.{tasks,originals}`, `identity.id_field`
  and `companions.comments` (all schema-required).
- **Frozen strings (R7).** `conventions.labels.rubric_pass`, `conventions.removed_context_preamble`
  and the `{key}-strategy.md` attachment convention are consumed downstream; change them only with
  a migration note.

## Developing against a drop-in root

`RFE_CREATOR_EXTRA_TYPES` (`os.pathsep`-separated directories holding `<name>/type.yaml`) adds
roots after `types/`. The seam is development and test only: in a headless or CI run (any of
`RFE_CREATOR_HEADLESS`, `CI`, `GITHUB_ACTIONS` set) an entry is honoured only when its canonical
path is listed in `RFE_CREATOR_EXTRA_TYPES_ALLOWLIST` — a protected CI variable, the same trust
boundary as the binding override — and every other entry is dropped with one stderr line. Explicit
`--extra-roots` values are never gated. Drop-ins pass the same gates as shipped types.

## Deployment binding override

`identity.<tracker>` is the default binding; `RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,
LOCAL_PREFIX}` overlays it (`python3 scripts/type_registry.py binding <type>` shows the effective
value). The full rule set, including the trust boundary for headless and CI runs, is in
[`types/README.md`](../types/README.md) "Deployment binding override".
