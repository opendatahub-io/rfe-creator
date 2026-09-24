# Work item type provider guide

How a new work item type is added to rfe-creator, what the contract enforces, and where the
authoritative text lives. Design: `design-proposals/work-item-types-unified.md` §3 (contract),
§3.2.1 (binding override), §3.3 (gates), §3.4 (provider obligations), §3.5 (discovery), §3.7
(cross-type chain).

**Status (PR-3a): 28 scripts read the registry at import** — the "Adoption status" table in
[`types/README.md`](../types/README.md) lists them and what is still pending. Since PR-2b the
artifact schemas (`artifact_utils.SCHEMAS`), the scan / rename / parse helpers and the poll phase
table (`check_review_progress.PHASE_CHECKS`) are projections too, so a registered type gets its
`<type>-task` / `<type>-review` schemas, its `<poll_prefix><phase>` rows and the generics without
a code change; since PR-2c so are the snapshot tables (`snapshot_fetch.SNAPSHOT_CONFIG`,
`bootstrap_snapshot.BOOTSTRAP_CONFIG`) and the `fetch_issue.py --fetch-all` layout (`--type`,
default `rfe`), so a registered type gets its hard-filter labels, snapshot / run-report file
prefixes and fetch layout the same way; since PR-2d the Jira write path (`submit.TYPE_CONFIGS`,
`split_submit.SPLIT_CONFIG`) is a projection too, so a registered type submits, approves, splits
and closes with its own binding and conventions. Every per-type value a pending script still carries is pinned by test to its
descriptor projection; each adoption deletes its pin (design §10). Adopted scripts read descriptor
values for everything that is not a tracker binding; the tracker binding the fetch checks, the
writers (`submit.py`, `split_submit.py`, `check_conflicts.py`) and the artifact helpers act on is
the effective one — `resolve`, `assert_registered_binding` and `binding()`, in the registry since
PR-3a and wired in since PR-3b/PR-3c — and with no override set every effective value is the
descriptor value ("Deployment binding override" below).

## The surface, in one table

| What | Where |
|---|---|
| Field reference (every key, with its consuming `file:line`) | `types/_schema/type.schema.json` `description` strings; `types/rfe/type.yaml` and `types/initiative/type.yaml` |
| Extension points vs shared machinery, reserved vocabulary, R7 frozen strings, binding override and trust boundary, gate list | [`types/README.md`](../types/README.md) |
| Copy-from skeleton | `types/rfe/` (`cp -r types/rfe types/<name>`) |
| Registry API and CLI | `scripts/type_registry.py` (`list`, `show`, `get`, `binding`, `candidates`, `resolve`); in code `load()`, `names()`/`choices()`/`get()`, `detect(item_id)`, `candidates(item_id)`, `resolve(registry, ...)`, `assert_registered_binding(desc)`, `is_headless(env, flag)`, `parse_type_arg(registry, argv)` |
| Validator (gates 1–3) | `scripts/validate_types.py` (`make lint` runs gate 1) |
| Anti-regression prefix lint and its ratchet baseline | `scripts/lint_prefix_predicates.py`, `tests/data/prefix_predicate_baseline.json` (only ever shrinks) |
| Recorded gaps (R1): what the v1 vocabulary cannot say | `NOT EXPRESSIBLE AT V1` comments in `tests/fixtures/types/epic/type.yaml`; `not_expressible_at_v1` in `tests/fixtures/types/strategy-inputs.yaml` |
| Contract tests | `tests/test_type_registry.py`, `tests/test_validate_types.py`, `tests/test_lint_prefix_predicates.py`, `tests/test_type_registry_pins.py`; `tests/test_schemas_golden.py` pins the derived artifact schemas byte for byte |

## Adding a type

Follow "Adding a type" in [`types/README.md`](../types/README.md): copy `types/rfe/`, edit only the
extension points, run `python3 scripts/validate_types.py` (gate 1) and, after
`bash scripts/bootstrap-assess-rfe.sh`, `python3 scripts/validate_types.py --with-deps` (gate 2).
Keep `pipeline.rubric.repo` and `pipeline.rubric.ref` identical to the shipped descriptors: the
bootstrap clones that repository once, at that commit, into `.context/assess-rfe`, and gate 1 refuses
a second external rubric repository or a second pin (rule 6) until the bootstrap can keep a checkout
per repository.

Since PR-5b the skills are generic: `/rfe-create`, `/rfe-review`, `/rfe-split`, `/rfe-submit`,
`/rfe-auto-fix` and `/rfe-speedrun` take `--type <name>` (or resolve it, design §5) and read every typed
literal from `python3 scripts/type_registry.py launch-vars <name> <stage>` — ids, dirs, schemas, the
scorer agent, the rubric path, the dimensions, the score-field stubs, the re-split threshold. The
typed-file paths in that block are the descriptor's own relative values while the working directory
carries those files — the checkout is the cwd, or `scripts/bootstrap-assess-rfe.sh` linked the
checkout's `types/` into it (it does so for any working directory without one: the eval harness's run
directory, a marketplace project) — and absolute only when it does not: a drop-in root outside the
checkout (a `types/<name>/...` path then resolves from the descriptor's own directory, so the root
carries its files wherever it lives). One frame on purpose: a subagent whose first instruction names a
file under an absolute root infers that root for every relative path after it — a drop-in root outside
the checkout is the one layout where its own files render absolute next to the shipped relative ones.
Commands and workspace paths are always relative. What a type
author writes is the judgement: `types/<name>/template.md`, `prompts/create-guidance.md`,
`prompts/review-rules.md`, `prompts/review-sections.md`, `prompts/revise-rules.md` (the Tier-2 slots the
shared review/revise skeletons read), `prompts/split-rules.md` (Tier 3 — the whole split prompt, with
the launcher tokens for its mechanical lines) and `dimensions/<dimension>.md` (whole files, launched
directly). `pipeline.prompts.*` and `pipeline.dimensions[].prompt` point at them; gate 1 checks they
exist, that `split-rules.md` carries its tokens, that `template.md` is named when the type creates or
splits, that no typed file names a skill directory, and that every `pipeline.stages` entry has its
generic body (`.claude/skills/rfe-<stage>/SKILL.md` — a type cannot declare a stage no body drives,
and a drop-in root ships no bodies). The headless dispatcher launches the
`create`, `review`, `split` and `auto-fix` stages through the registry, so `pipeline.stages` must
list them: `pipeline_state.py init` refuses a type that omits one before any state is written.
Author the eval prose in `types/<name>/eval/fragment.yaml` (schema
`types/_schema/eval-fragment.schema.json`) and `types/<name>/eval/pairwise-judge.md`, name the
quality threshold `<name>_quality`, and let `python3 scripts/generate_eval_config.py --type <name>`
write the committed config ("Generated eval configs" in `types/README.md`). Then meet the provider
floor (design §3.4): a ≥16-case anonymized eval dataset with at least one sparse or adversarial
case and at least four `revision-expected`-tagged weak-draft cases (each an honest evidence or
scope gap the create step cannot repair, so the first review fails and the revise path runs — see
"Weak-draft cases" in `eval/README.md`; without them the `revision_coverage` gate passes only by
accident), populated `expected_*` annotations, explicit `eval.thresholds`, the committed eval
config, one QUICK_MODE run on the PR, and a seed for the tracker emulator.

Rules that are easy to trip:

- **The eval config is generated.** Never edit `eval.config` by hand: the shared structure and
  every check live in `eval/config/skeleton.yaml`, your type's prose in `eval/fragment.yaml`,
  the facts and thresholds in `type.yaml`. `generate_eval_config.py --check` (gate 1, `make lint`,
  CI) fails with the diff when the committed file is stale, and generation refuses an unused
  fragment key, an unresolved slot or a threshold naming a judge the config does not define.

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
  (plus the optional `schema.{task,review}.extra_fields`). `conventions.parent_key_patterns` governs
  both the task schema and the batch validator (`Descriptor.parent_key_pattern` is the one join;
  `batch.extra_fields` decides whether a batch entry may carry `parent_key` at all). Gate 1 does not require all of them,
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
  `snapshot_fetch` (since PR-2d it loads the registry directly as well). Gate 1 already requires
  `snapshot.prefix` and `snapshot.report_prefix`
  (schema `$defs/snapshot`, the `else` branch of the `mode` conditional; the prefix is also
  linted non-empty and collision-free) and `reporting.item_key`, but NOT
  `conventions.labels.ignore` / `.split_quarantine` (`$defs/labels` is an open map with no
  required keys), so a drop-in that omits either label passes `validate_types.py` and then fails
  the import of `snapshot_fetch.py`, `bootstrap_snapshot.py` and `submit.py` with a `KeyError`
  naming the type and the field. Copying `types/rfe/` keeps them; a third type must also pass
  `prefix=` explicitly to the snapshot helpers (their default is the rfe `snapshot.prefix`).
  `fetch_issue.py --fetch-all --type <t>` reads `dirs.{tasks,originals}`, `identity.id_field`
  and `companions.comments` (all schema-required), plus the effective
  `identity.jira.{project,issue_type}` it verifies the fetched issue against.
- **Submit facts are read at import, for every type.** `submit.TYPE_CONFIGS` and
  `split_submit.SPLIT_CONFIG` read `identity.jira.{project,issue_type,key_prefixes}`,
  `identity.{id_field,local_prefix}`, `dirs.{tasks,reviews,originals}`,
  `display.{entity,entity_plural}`,
  `conventions.{type_label,label_prefix,comment_prefix,removed_context_preamble}`,
  `conventions.labels.{rubric_pass,feasibility}` (`alignment` optional), `index.enabled` and
  `snapshot.prefix` over every registered type when either module is imported (descriptor
  values; `main()` overlays the resolved type's effective `project`, `issue_type` and write
  prefix on its entry after `resolve` and `assert_registered_binding`, "Deployment binding
  override" below). Everything but
  the two label keys is schema-required, so a drop-in that omits `labels.rubric_pass` or
  `labels.feasibility` passes gate 1 and fails the import of `submit.py` with a `KeyError`
  naming the type and the field. `identity.jira.split_link_type` and
  `identity.jira.state_map.{approved,close_superseded}` are optional in the schema, so their
  absence is refused when it matters rather than at import: `split_submit.py` reads the link
  type and the close-superseded transition / resolution with a `None` default at import and
  refuses to split a parent of a type missing any of them before any scan or Jira call (exit 5,
  the systemic code, so `submit.py`'s split loop aborts instead of flagging every parent);
  because it recovers those two facts by the `(project, issue_type)` pair, two registered types
  sharing a pair fail its import with a `RegistryError` naming both. `submit.py` reads
  `state_map.approved` with a `None` default at startup and only `--auto-approve` requires it
  (a usage error naming the type and the field, before any scan or Jira call). Copying
  `types/rfe/` keeps them all.
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

`identity.<tracker>` is the default binding; three sources overlay it, highest precedence first:
`RFE_CREATOR_BINDING_<TYPE>_{PROJECT,ISSUE_TYPE,LOCAL_PREFIX}` (env — the only source a headless
or CI run honours), the bare `JIRA_PROJECT` / `JIRA_ISSUE_TYPE` shorthand (applied by `resolve` to
the resolved type only — a `resolve`-CLI verdict: every writer refuses a shorthand-sourced binding
with one `Error:` line, because the artifact layer does not read it), and the `bindings:` block of a
workspace `rfe-creator.yaml` (read only from an explicit `--workspace-root`, honoured in interactive
runs of the `resolve` CLI only, never by a writer). `python3
scripts/type_registry.py binding <type>` shows the effective value (env and descriptor; the
shorthand and the workspace file are resolve-time sources; `overrides` and a re-rendered
`local_id_pattern` are printed only when something is overridden, so the zero-override output is
the PR-1 output). An overridden `local_prefix`
re-renders the effective `local_id_pattern` (D13) and is a hard error when the descriptor pattern
does not start with `^` + the descriptor prefix. The full rule set, including the trust boundary
for headless and CI runs and the ownership check before a tracker write, is in
[`types/README.md`](../types/README.md) "Deployment binding override" and "Effective binding in
the writers".

### Pointing a type at another project (how-to)

To run the `rfe` pipeline against, say, a `KONFLUX` project, set the type-scoped variables —
`<TYPE>` is the type name upper-cased with non-alphanumerics mapped to `_` — as protected CI
variables for a headless run, or in the shell for a local one. Nothing is required: an unset
variable means the descriptor value.

```bash
export RFE_CREATOR_BINDING_RFE_PROJECT=KONFLUX                # the tracker project key
export RFE_CREATOR_BINDING_RFE_ISSUE_TYPE="Feature Request"   # only when it differs from the descriptor
```

Set the typed variables, never the bare `JIRA_PROJECT` / `JIRA_ISSUE_TYPE` shorthand: the writers
refuse a shorthand-sourced binding (`Error: JIRA_PROJECT / JIRA_ISSUE_TYPE shorthand is not honoured
by the artifact layer; set RFE_CREATOR_BINDING_RFE_PROJECT / _ISSUE_TYPE instead`) because the
artifact id grammar and the rename guard do not read it. `RFE_CREATOR_BINDING_RFE_LOCAL_PREFIX` is
not a deployment knob yet: the registry honours it (`binding()`, `candidates()`, `owns_effective()`),
but the artifact id grammar and the rename guard's local-id check stay the descriptor's until the
grammar reads `binding()["local_id_pattern"]` (D6 deferred).

What changes — and nothing else: judgement content, `dirs`, schemas, rubric and eval are never
overridable, and every other type keeps its own binding.

- **Fetch and JQL checks** compare against `(KONFLUX, Feature Request)`: `fetch_issue.py
  --fetch-all` writes a `KONFLUX-N` issue into the rfe layout (`artifacts/rfe-tasks/KONFLUX-N.md`
  with `type: rfe` and `tracker_ref: KONFLUX-N`, plus the original and the comments companion),
  `snapshot_fetch.py` / `bootstrap_snapshot.py` accept a `project = KONFLUX` JQL and refuse one
  naming `RHAIRFE`.
- **Writes** (`submit.py`, `split_submit.py`) create under `KONFLUX` with the effective issue
  type, treat a task as an existing issue when its `tracker_ref` starts with `KONFLUX-` — or
  `RHAIRFE-`, the descriptor prefix kept as a read prefix: an item submitted before the override
  is still updated in place, its `(RHAIRFE, Feature Request)` pair being accepted by the
  pre-update check for a key that carries the descriptor prefix (`Descriptor.accepted_pairs`;
  `check_conflicts.py`, which only reads and reports, applies the same ownership rule and the
  same check ahead of a submit), while a `KONFLUX-N` whose issue type is not the binding's is
  skipped — split a parent only after the same check on it, rename a submitted draft to
  `KONFLUX-N`, use `KONFLUX-DRY` as the dry-run key and record the effective binding in the run
  report (`binding: {tracker: jira, project: KONFLUX, issue_type: Feature Request, source: env}`).
- **Artifacts**: the `rfe-task` / `rfe-review` id grammar admits `KONFLUX-\d+` next to `RFE-\d+`
  and `RHAIRFE-\d+` (`artifact_utils.SCHEMAS`, built from the environment at import — export the
  variables once, so `frontmatter.py` sees what the script that spawns it sees), and so does the
  `parent_key` grammar (`Descriptor.parent_key_pattern_effective`, applied by the task schema and
  by `validate_batch_input.py`), so the children of a fetched `KONFLUX-N` validate and can be split.
- An override that selects the pair another registered type owns (`RHOAIENG` / `Initiative` for
  `rfe`) is refused by every entry script before any access, and so are a value that fails the
  grammar (`lower` is not a project key) and the bare `JIRA_PROJECT` / `JIRA_ISSUE_TYPE`
  shorthand: one `Error:` line, non-zero exit.

Verify before the first real run:

```bash
python3 scripts/type_registry.py binding rfe            # project: KONFLUX, key_prefixes: [KONFLUX-, RHAIRFE-], source: env, overrides: [project]
python3 scripts/type_registry.py resolve --type rfe     # TYPE RESOLVED: rfe (--type; binding override project=KONFLUX)
python3 scripts/type_registry.py candidates KONFLUX-12  # key_prefix: rfe  (without the override: tracker_grammar (provisional): rfe initiative)
python3 scripts/submit.py --dry-run --type rfe          # the same resolve line on stderr, then the plan against KONFLUX; no tracker write
```

Unset the variables and the same commands print the descriptor values (`source: descriptor`, no
resolve clause) and the dry-run plan of a stock deployment, byte for byte.

## Resolution

`python3 scripts/type_registry.py resolve [--type T] [--batch FILE] [--artifact PATH] [--headless]
[--workspace-root DIR] [--json] [ID ...]` decides the type of a run and prints
`TYPE RESOLVED: <type> (<rung>[; binding override project=...])`. Rungs: `--type` > batch mapping
`type:` > deterministic signals (artifact frontmatter `type:`, the artifact's directory, id grammar
through `candidates()` over effective bindings, with the Jira key grammar as a provisional last
rung) > the legacy `rfe` default. An unknown type, a `--type` disagreeing with the batch `type:`
(D1), a per-item `type` key (D2) and conflicting deterministic signals exit 1 with an `ERROR:`
line; an ambiguous run exits 3 (`TYPE AMBIGUOUS: rfe, initiative - pass --type` interactively, an
`ERROR:` line when headless). A batch file is either the legacy bare list (the type is `--type`, else
`rfe`) or `{type: <t>, items: [...]}`; `validate_batch_input.py` and `next_rfe_id.py --from-batch`
accept both through `type_registry.read_batch` — read once, then `resolve(..., batch_items=,
items_are_ids=False, binding=False)`: string items are entries, not ids, and the binding is not
evaluated — and print the resolve line on stderr only for a non-default rung (`types/README.md`
"Batch input forms"). Headless is `is_headless(env, flag)`: `RFE_CREATOR_HEADLESS` (the
headless pipeline exports it), `CI` or `GITHUB_ACTIONS`, or `--headless` — which also gates the
`RFE_CREATOR_EXTRA_TYPES` seam and the workspace file for that run (`load(headless=True)`). A drop-in type takes part
in every rung automatically: its `local_id_pattern`, `key_prefixes`, `local_prefix` and `dirs` are
the signals, and its Jira binding makes it a provisional candidate for any Jira-shaped key. The rung
table, the workspace file format and the shorthand scope are in
[`types/README.md`](../types/README.md) "Resolution".

New artifacts are self-describing: the writers that mint them append `type: <t>` (and, once an
artifact refers to a tracker issue, `tracker_ref: <key>`), so a drop-in type's artifacts take the
frontmatter rung without any id-grammar work. Which writers stamp which field, the no-back-fill rule
(D7), the review stamping by `verify_phase` (D8) and how readers fall back for pre-migration artifacts
are in [`types/README.md`](../types/README.md) "Self-describing artifacts".

Fetches verify against the effective binding before they write: `fetch_issue.py --fetch-all
--type <t>` compares the issue's `(project, issuetype)` with `binding()` and on a mismatch writes
nothing and exits non-zero (headless: the `fetch_failed` error-stub path; interactive: the stderr
line names the `--type` to re-run with; an override that binds the type to another registered
type's pair is refused before the fetch), `snapshot_fetch.py` refuses a `--jql` whose positive
`project` / `issuetype` (or `type`) clauses — `= X`, or any member of `in (X, Y)`; a `NOT`-negated
clause is not read — name a key or type other than it (a project given by name or id is left to
Jira), `bootstrap_snapshot.py` runs the same check over its own JQL positional and refuses a run
report whose `type:` is not `--type` (a report without one is read as today). A drop-in type gets
all of these checks from its `identity.jira` block with nothing to write
([`types/README.md`](../types/README.md) "Fetch verification").
