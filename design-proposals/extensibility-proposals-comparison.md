# Comparison: ADR "Pluggable Work Item Types" vs "Request-Type Extensibility"

**Status:** Analysis (input to reconciling the two proposals)
**Date:** 2026-08-12
**Compares:** `design-proposals/adr-pluggable-work-item-types.md` (2026-08-10, incl. its review Appendix A of 2026-08-12) and `design-proposals/request-type-extensibility.md` (2026-08-12)
**Method:** every load-bearing or disputed factual claim in both documents was verified against merged main (`8888b18`), assess-rfe PR #10 (`6602a12`), the three GitLab pipeline repos, and the agent-eval-harness repo — by reading **and executing** code (regex/schema execution against synthetic artifacts, difflib measurements, exhaustive greps). 37 claims checked: 29 confirmed, 8 partially confirmed with corrections, 1 embedded sub-claim refuted. Errata for both documents in §7.

**Update baked in:** backward compatibility for the `initiative-*` skill names is **not required** — they have no users today (confirmed: zero production CI references; the only CI consumer of a typed name is `eval-initiative.yaml:3`, a per-PR config field that self-migrates). Both documents over-weighted alias preservation; the sections below account for that.

---

## 0. TL;DR

The two proposals agree on the entire architectural core and disagree on five mechanisms, of which only one is genuinely hard. They are best read as different document *types*: the ADR is a **decision record** carrying strategic/product commitments (one intake → many destinations, the RHAISTRAT destination flip, a Definition-of-Ready rubric rewrite, classification as a first-class step) plus a review appendix that already amends its two weakest mechanisms. RTE is an **implementation specification** (descriptor schema, validation gates, provenance, migration mechanics, CI impact analysis) that deliberately excludes those strategic moves — and is silently incompatible with one of them (the flip).

Recommended reconciliation (§6): keep the ADR as the decision record and RTE as the implementation spec beneath it; adopt RTE's mechanics for everything both cover; import the flip, the classification end-state, the one-scorer collapse, and the DoR rubric track from the ADR — with the flip re-scheduled onto the prerequisite list that verification produced (§4.2), because **neither document as written survives the flip**.

---

## 1. What each document is

| | ADR | RTE |
|---|---|---|
| Nature | Architecture Decision Record + product direction; Appendix A is an independent review that corrects §4.2 and re-sequences §4.5 without editing the body | Engineering design synthesized from three competing candidate designs scored by a 3-lens panel |
| Extension surface it defines | 10 extension points (§3), descriptor sketch `types/<t>.yaml` | ~19-item variance surface, full descriptor schema `types/<t>/type.yaml` + directory of typed assets, 3 validation gates, versioning/provenance |
| Strategic scope | One intake → many destinations; RHAISTRAT flip ("RFE is a binding, not a concept"); DoR template + 7-criterion rubric replacing PR #10's; classification of *existing* mis-filed issues; assess repo rename + one scorer | None of these — assumes today's project bindings, PR #10's rubric, deterministic type resolution only |
| Engineering scope | Sequencing table (5 phases), alternatives; mechanics mostly deferred or corrected by its own Appendix A | Pin-test migration (8 PR-sized steps), per-pipeline CI impact (zero required changes), eval single-sourcing, escape-hatch tiers, companion-skills doctrine, rubric pinning + provenance stamping |

## 2. Where they agree (verified common ground)

Both proposals independently converge on — and the code evidence supports — the following. None of this needs debate:

1. **One declarative descriptor as single source of truth**, replacing the scattered per-script registries. Verified counts: **17** module-level dicts keyed `rfe`/`initiative` (one per script — the ADR's number, exact by AST scan) — **19** once `artifact_utils.SCHEMAS` and `check_review_progress.PHASE_CHECKS` are included (RTE's "~20"); **22** scripts accept `--type`; plus ~7 prefix-sniff branch sites and 3 forked function pairs in `artifact_utils.py:748-1052` that **no dict count captures** and both proposals must sweep.
2. **The dispatch/judgement split.** ADR §2's 933/543-line split reproduces *exactly* (and its A.4 correction to 1,595 lines is also exact). Dispatch skeletons are near-identical (auto-fix 88% raw, 97–99% normalized); judgement content is genuinely typed (templates ~17% identical, rubrics 41%, revise 39%). One skill body per dispatch stage, type as a parameter; judgement stays per-type. Both A.2 and RTE land here.
3. **`create` is excluded from the collapse** (53% identical — genuinely divergent both agree).
4. **Classification belongs in create**; interactive confirmation for ambiguity; CI always passes the type explicitly.
5. **Descriptor-first, behaviour-preserving phase 1** with the registries becoming derived views (ADR P1 ≈ RTE PR-1/PR-2, and RTE's pin-test mechanic is a strict superset of ADR P1).
6. **The state machine, snapshot invariants, and numeric scoring contract are not extension points.**
7. **Rubrics stay in the assess repo** (ADR §7 rejects moving them in; RTE pins them by ref).

The drift diagnosis both share is real and verified beyond either document's examples: the stale pre-#5 rubric path exists in **three** rfe-creator files (both docs name only two; `rfe.auto-fix/SKILL.md:127` is the third); the export-rubric skill carries a **third rubric copy that has already drifted** (missing step 6); and the initiative side has an **intra-type contradiction of its own** (initiative-review permits split only at `right_sized=0`, initiative-split-agent proceeds at 1/2 — a branch that can only fire if review broke its own rule).

## 3. The five genuine conflicts

### 3.1 Skill-layer mechanism: generated flat bodies (ADR A.2) vs runtime-generic bodies (RTE §3.1)

The only deep disagreement. Both keep judgement per-type, so the fight is **only over the 5 dispatch SKILL.md pairs (69–95% normalized-identical) plus `assess-agent.md` (100%)**.

| | Generated flat (one authored template → N checked-in flat bodies, CI-verified regeneration) | Runtime-generic (one body + Step-0 `resolve` + script-supplied per-type paths) |
|---|---|---|
| Runtime behavior | Byte-identical to today; zero new model-obeyed steps; typed entry points hop-free | +1 allowlisted script call per skill entry (~3–5 per speedrun); +3–6 fragment Reads/ID **inside isolated subagents** (vs ~6–10 that already happen) |
| New machinery | Generator + conditional DSL + regenerate-and-diff CI gate — **no precedent for generated prompt text in any of the three repos** (skills-registry's 3 CI gates cover data/docs only) | `type_registry.resolve` — structurally identical to what `pipeline_state.py` already does for all 13 agent phases (script picks path, subagent reads file) |
| Drift class eliminated | Yes, by construction (PR #150's parity lint becomes obsolete) | Yes, by deletion (no second copy exists) |
| Cost at N types | ~+920 checked-in generated lines/type; template edits produce N near-identical review diffs | ~0 lines/type on the dispatch layer |
| Failure-mode fit | The repo's shipped failures attach to **model-authoritative prose** — verified natural experiment: the same stale rubric path is broken where the model-read copy is load-bearing (`rfe.review:85`) and harmless where the runtime value is script-supplied (`rfe.auto-fix:127`). Flat generation keeps N model-authoritative copies, but regenerates them | The documented CI failures (stochastic Agent selection, Step-0.5 registration race) were **model-decided-step** failures — Step-0 resolve adds one such step, though on CI paths it degenerates to a deterministic `--type` echo (rung 1, no detection) |

**Assessment.** The evidence cuts *against* the ADR body's original §4.2 (additive-only overrides — killed by the verified review-agent polarity conflict, exactly as its own A.2 found) and *mildly for* runtime-generic over A.2's generated-flat: the repo's most-exercised, deliberately-chosen mechanism is already script-resolved runtime indirection (the thin-dispatcher refactor moved *toward* prompt-files-on-disk after a 36-agent degradation incident), the auto-fix pair is 95% identical *precisely because* type-genericity already lives in the scripts, and flat generation introduces a first-ever prompt-codegen pipeline to reproduce what the script dispatch already achieves. The strongest remaining argument for generated-flat — typed CI entries stay hop-free — lost most of its force with the news that `initiative-*` has no consumers. A defensible hybrid if reviewers still want zero runtime delta on the one production hot path: keep `rfe.auto-fix` flat as-generated, genericize the rest.

### 3.2 The RHAISTRAT destination flip: in scope (ADR §1.1/§5/P3) vs absent (RTE)

This is the ADR's distinctive strategic content, and verification confirms **A.1 in full, and then some**:

- All five A.1 table rows executed and confirmed (duplicate-create at `submit.py:472`; review/removed-context lookups returning `None` with files present on disk; `_has_rhaistrat_parent` over-trigger; both id regexes rejecting `RHAISTRAT-123`; scorer dispatch collapsing to `rfe-scorer`). Exactly **23** prefix-predicate lines. `also_reads` has zero occurrences anywhere — new machinery with undefined semantics, and there is **no rfe-side `also_reads`**, so legacy `RHAIRFE-` items go unreadable post-flip under the ADR's own sketch.
- Two blast-radius sites **neither document lists**: `check_conflicts.py:76` silently skips conflict detection for any non-matching prefix, and the *inverse* predicates at `generate_review_pdf.py:341` / `generate_run_report.py:68` would misclassify Outcome rollups as split children the moment `jira_prefix == RHAISTRAT-`.
- **RTE is not flip-neutral — it is flip-hostile.** Its cross-type lint *requires* unique jira prefixes (and its own strategy walkthrough already assigns `RHAISTRAT-` to a third type, making the flip a three-way collision the registry rejects by design); `detect()` is single-valued per prefix; `is_existing`/`jira_key` appear nowhere in it, so the duplicate-create path survives as a "derived view"; and its data-condition `{prefix: RHAISTRAT-}` reproduces the over-trigger verbatim.

**What the flip actually needs, which neither normative text specifies** (A.1's recommendations, extended by verification): key prefixes as *lists* (write-one/read-many, both types); `is_existing` redefined as *has a Jira key* rather than prefix-match; type identity keyed on `(project, issue_type)` with prefix demoted to a hint (note: **issuetype is fetched nowhere on any read path today** — even RTE's post-fetch verification needs a fetch-field addition); multi-candidate `detect` with post-fetch tie-break; an Outcome-parent predicate replacing the prefix test (inexpressible in RTE's closed `prefix|equals|exists` vocabulary); a dual-project JQL/snapshot story for the ~665 in-place items; and sweeps of `check_conflicts.py:76` and the two inverse predicates.

**Assessment.** A.1's verdict stands: P3 is a data-model change, not a binding change, and it must not precede the discriminator. RTE's ladder (frontmatter `type:` field, `(project, issuetype)` verification, batch `type:` key) *is* structurally the discriminator A.1 demands — the strongest single case for merging the documents: **RTE PR-3 is the prerequisite for ADR P3**, but RTE must first fix its own uniqueness lint and add the `is_existing`/prefix-list items above.

### 3.3 Classification: pipeline step with content-based intake (ADR §4.3) vs deterministic-signals-only (RTE §3.2)

- The signal premise is confirmed (rubric text byte-exact: `(0=task/chore/tech debt, 1=borderline, 2=clear business need)`), but so is A.5's weakness critique, with sharper evidence than A.5 gives: the two rubrics **contradict each other on tech debt** (RFE scores it `not_a_task=0`; the initiative rubric blesses a standing tech-debt burndown as `right_sized=2`, while its own T=0 example is a sub-initiative chore) — so `not_a_task=0` provably mixes three populations and detects "not a business need", not "is an Initiative". No discriminator criterion exists anywhere in code; `classify.driver` and `counter_signals` are both pure proposals.
- **New blocking-grade finding beyond A.5:** content-based classification *at intake of existing issues* is not implementable without re-sequencing the state machine. Type binds at `pipeline_state.py init` (written into `tmp/pipeline-state.yaml`; every phase config derives from it), but issue content first lands on disk in FETCH — which is itself already type-bound (type-named dirs, prompts, poll paths). JQL-mode snapshotting downloads descriptions transiently and discards them; explicit-ID mode downloads nothing. The ADR does not acknowledge this.
- Headless: today's batch inputs carry **zero** type signal (all 36 eval inputs: `{prompt, clarifying_context, priority}`; no `type` field; not even `parent_key` appears). RTE's grandfathered-rfe-default reproduces current behavior byte-for-byte; the ADR's "under `--headless` it classifies and records" would make content classification the sole authority over which **Jira project** gets written to, on a corpus verified to be cross-boundary-ambiguous in both directions — A.5's "needs a guardrail" is substantiated. (Also: RTE's proposed top-level batch `type:` key is a file-format change — the validator exits 2 unless the YAML root is a list.)
- The labeled corpus exists (20+16 cases) but is labeled only by dataset membership and visibly straddles the product/engineering boundary in both directions — usable for smoke-testing a classifier, not for setting an accuracy bar.

**Assessment.** RTE's ladder is the only intake mechanism compatible with today's CI without changes, and should be the base. The ADR's classification ambition survives as (a) create-time classification — both docs already agree, calibrated per RTE; (b) a cheap, safe intermediate the ADR itself points at: **classification as a reported signal** ("this scored not_a_task=0 and matches initiative signals — likely mis-filed") without routing authority; (c) routing authority over existing issues only as a later phase, after a labeled corpus and accuracy bar exist (A.5) and with the state-machine re-sequencing costed honestly.

### 3.4 The assess side: rename + one scorer (ADR §4.4) vs keep layout + pin + per-type scorers (RTE §6)

- **One scorer: ADR is right.** The two scorer agents are a 0.919-similarity pure noun swap, contain no rubric path at all (it arrives via the launch prompt), and duplicate only the containment boundary. Collapse is mechanical *if coordinated* (2 dict values, 1 map, ~14 prose spots, 1 bootstrap gate, 1 test, across both repos); uncoordinated it hard-fails bootstrap or hangs wait-for-wave — the agent name is an unpinned cross-repo contract, which is RTE's provenance/pinning argument applying to the ADR's own proposal.
- **Rename: ADR is wrong.** `assess-rfe → assess` + `rubrics/<t>/` breaks the bootstrap paths, `PIPELINE_TYPES.rubric_path`, the pinning tests, `.gitignore`, `settings.json` — and, decisively, the external assessor pipeline, which pins `/assess-rfe:assess-rfe` in four CI jobs and `AGENT_ENABLED_PLUGINS=assess-rfe` (which hard-fails on unknown plugin names). RTE's per-skill layout is what everything already assumes, verified.
- The ADR's "type-specific there is the rubric text, not the machinery" is overstated: the two assess coordinator SKILL.md bodies are only 67% identical, and PR #10 ships four coexisting type-detection mechanisms (skill choice, project map with silent rfe default, criterion sniffing, CSV-header sniffing) — RTE's convergence item, verified.

**Assessment.** Adopt the ADR's one-scorer collapse as a coordinated cross-repo change (it also dissolves the post-flip scorer-dispatch failure); keep RTE's layout, pinned `rubric.ref`, and detection convergence; drop the rename.

### 3.5 The initiative rubric: DoR-derived rewrite (ADR §5) vs consume PR #10 as-is (RTE)

The ADR's 7-criterion DoR set would obsolete PR #10's rubric wholesale — 33 calibration examples keyed to the 5-criterion set; only `right_sized` survives by name. A.3's "no phase for this" critique is confirmed, and so is its mitigation: the eval dataset is nearly criteria-agnostic (77/80 `expected_scores` null, executed count; the 3 non-null are all `right_sized`), so the blast radius is the enumerated config spots — but note the criteria are restated in **7 machine-readable sites per type** (RTE's count, exact as a floor), of which two (`compare_review_outputs.py` and the eval-yaml validators) are pinned by no test today. This is a *product* decision (what an Initiative must satisfy) sitting inside an architecture document; the descriptor design is indifferent to which criteria set wins, provided the disposition of PR #10 is stated (merge-as-interim / redirect / abandon) before a rubric rewrite discards its fresh calibration work.

### 3.6 Resolved: naming and aliases

With `initiative-*` compat not required: RTE's "permanent alias families" policy is void (its stated justification was muscle memory + CI, both now known absent), and the ADR's §4.2 claim that typed entry points are "in every CI job" was already false for initiatives. What remains true and binding: **`rfe.*` names are pinned by the autofixer's production prompts** and stay. Typed names for new types become an optional discoverability nicety, not a compatibility obligation. The eval config flip (`eval-initiative.yaml` → generic skill + `--type`) loses its only downside.

## 4. Pros and cons, whole-document

### ADR — strengths
1. **Strategic completeness.** It answers *why* extensibility exists (one intake, many destinations) and carries the decisions RTE cannot make: the flip, the DoR bar, classification as product behavior. These need a decision record; RTE explicitly leaves them as open questions or omits them.
2. **The 10-extension-point table** is the crispest statement of the contract surface either doc offers, and its "the diff between two descriptors *is* the contract" framing is the right acceptance test for any schema.
3. **One-scorer insight** — verified correct, missed by RTE, and it pre-solves a flip failure.
4. **Self-correcting:** Appendix A is a genuine adversarial review; A.1/A.2/A.6 fix the body's two biggest flaws before any reviewer arrives. (Cost: the document now disagrees with itself — body §4.2/§4.5 vs A.2/A.6 — and a reader must know the appendix wins.)
5. Names the honest costs (generic skills less directly editable; measurement change from raising the DoR bar mid-flight).

### ADR — weaknesses
1. **Mechanism gaps where it is normative:** additive-only overrides are dead on arrival (own A.2); headless classification lacks a guardrail (own A.5); classify-at-intake is unimplementable without re-sequencing (new finding, §3.3); `also_reads` is semantics-free and one-sided; the assess rename breaks the external assessor.
2. **No validation, versioning, or provenance story** — the descriptor is "declarative and inert" with no schema gate, no rubric pinning (bootstrap floats at default-branch head today), no answer to "which rubric scored this review".
3. **No CI impact analysis** — "parameterizing the assessor and auto-fixer CI jobs" is explicitly out of scope, yet P3 (the flip) lands squarely on live pipelines, dashboards, and saved JQL.
4. Number hygiene: the refuted "raw 19–41%", the 933-line framing that its own A.4 restates, "17 registries" undercounting by 2 (also self-flagged).

### RTE — strengths
1. **Implementation-grade specificity:** full descriptor schema with every field traced to a consuming script, three validation gates with the exact invariants (including snapshot prefix-collision freedom), pin-test migration that never touches 20 scripts at once, provenance stamping that makes rubric drift attributable.
2. **CI impact rigor:** zero-required-changes verified per pipeline; the headless hazards (text-only-stop, allowlist) are designed against, not discovered later.
3. **Provider experience is a first-class deliverable** (guide, skeleton, walkthrough, eval obligations, companion-skills doctrine with the second-requester promotion rule).
4. Its resolution ladder is the discriminator the flip needs (§3.2) — it solved A.1's problem without knowing about the flip.

### RTE — weaknesses
1. **Strategically silent — and accidentally flip-hostile** (§3.2). It optimizes within today's bindings; its uniqueness lint would have to be re-keyed the day the ADR's central strategic decision lands. A spec that cannot represent the destination state of the product decision is incomplete, not neutral.
2. **Alias policy built on a false premise** (now corrected by maintainer input).
3. Descriptor scope-creep risk it acknowledges but samples at N=2: the closed condition vocabulary already fails on the first real future requirement (Outcome-parent).
4. Minor number hygiene: "~20 dicts" conflates dicts with registries (19 is the defensible registry count); revise-agent is 64 lines not 74; the "split heuristics (100/157)" figure is wrong under either reading; "60–70% verbatim" is denominator-sensitive (57–76%).

## 5. Corrections both documents need (verified errata)

| Doc | Location | Correction |
|---|---|---|
| ADR | A.2 | "raw line-identity 19–41%" — **refuted**; measured 49.5–88.4% across the six pairs under every standard metric. The normalized table itself mostly holds (4/6 within ~2 pts; auto-fix is 97–99 not 94; submit 70–73 not 61) |
| ADR | §4.1 | "seventeen registries" → 17 dicts, 19 registries (SCHEMAS, PHASE_CHECKS) — own A-appendix already flags the widening |
| ADR | §2 | "23 predicates infer type" — count exact, but 10 infer type, 13 infer new-vs-existing/same-family |
| ADR | §4.4 | Rename breaks 4 assessor CI jobs + plugin enable-list (§3.4); "rubric text, not machinery" overstated (coordinators 67% identical, 4 detection mechanisms) |
| ADR | §4.3 | Classify-at-intake requires state-machine re-sequencing (§3.3); headless classification needs the A.5 guardrail in the body, not the appendix |
| RTE | §0/§1 | "~20 private per-script type dicts" → "17 type-keyed dicts; 19 per-type registries"; revise-agent 64 lines; drop or fix "split heuristics (100/157)"; "60–70% verbatim" → "~57–76% depending on denominator; ~3/4 of either side's lines" |
| RTE | §0.1/§3.1 | Remove permanent-alias policy and its rationale (no initiative users); note `eval-initiative.yaml:3` was the one typed-name CI consumer |
| RTE | §2.5/§3.2 | Uniqueness lint and single-valued `detect()` must be re-keyed on `(project, issue_type)` with prefix lists, or the RHAISTRAT flip is unrepresentable; add `is_existing := has jira_key`; add issuetype to fetched fields (fetched nowhere today) |
| RTE | §4 (Step 3) | "annotations become load-bearing" is prospective — zero `{{ annotations }}` references exist in any current judge template |
| Both | — | Add the two unlisted flip-blast sites: `check_conflicts.py:76`; inverse predicates `generate_review_pdf.py:341` / `generate_run_report.py:68` |

## 6. Recommended reconciliation

Keep **two documents with a hierarchy**, not a merge into one:

1. **The ADR remains the decision record** for: one intake / many destinations; the RHAISTRAT flip as target state; the DoR-derived initiative rubric (with an explicit disposition for PR #10's rubric before discarding its 33 calibration examples); classification as eventual product behavior. Fold Appendix A's corrections into the body (the body currently loses arguments with its own appendix), and add the §5 errata.
2. **RTE becomes the implementation spec** referenced by the ADR for everything mechanical: descriptor schema and layout, registry/loader, validation gates, provenance, migration mechanics, CI impact. Amend it per §5 — most importantly, make it flip-ready (prefix lists, `(project, issue_type)` identity, `is_existing`, multi-candidate detect), which is a schema change best made *before* `schema_version: 1` freezes.
3. **Sequence merge** (reconciling ADR A.6 with RTE PR-1…8):
   - P1 = RTE PR-1/PR-2 + A.1's widened scope (prefix predicates and schema regexes enter the pin-test sweep) + `work_type`/`jira_key` frontmatter fields.
   - P2 = the rubric track (ADR A.6's placement — before any skill collapse re-baselines eval) with the PR #10 disposition decided.
   - P3 = skill-layer collapse per §3.1's assessment (runtime-generic base; optionally keep `rfe.auto-fix` flat), *excluding create*, eval-gated with the threshold defined (A.5's open point).
   - P4 = RTE PR-3 (detection ladder + self-describing artifacts) — the flip prerequisite.
   - P5 = the flip (ADR P3), gated on a `submit.py --dry-run` plan showing zero unexpected "Would create" (A.6), plus the §3.2 checklist including the two new blast sites and the assess dispatch change (or the one-scorer collapse, which dissolves it).
   - P6 = classification with routing authority, gated on a labeled corpus and accuracy bar (A.5) — signal-only reporting can ship any time after P2 for free.
4. **Cross-repo:** adopt one type-agnostic scorer (coordinated, both repos, no rename); pin rubric refs; converge PR #10's four detection mechanisms onto the explicit type marker.

The ADR's closing warning is the right note to end on, and it applies to both documents: landed separately and unreconciled, these are two partial refactors. The descriptor makes the skill layer collapsible; the ladder makes the flip survivable; the flip makes classification necessary. The dependency chain only compounds if both documents agree on which link is which.

---

*Verification artifacts: 8 agent reports (dict-count, overlap-numbers, contradictory-prompts, flip-blast-radius, eval-rubric-claims, assess-side, classification-signal, flat-vs-generic), each with executed proofs and file:line citations, available in the session transcript.*
