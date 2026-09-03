# /rfe.merge Design

## Status

- Status: Proposed
- Jira capability validation: Sampled capability probe complete (section 5; 3 statuses, n=2 for link frequency); broader inventory and manual rehearsal pending
- Manual rehearsal: Pending

### Decision log

| Date | Decision |
|------|----------|
| 2026-07-20 | Initial design drafted, prior to Jira capability probe. Section 5 and part of section 19 hold placeholders the probe must fill; a smaller set in each is reserved for the manual rehearsal, since it requires an actual write. Everything else is a decision, not an open question. |
| 2026-07-20 | Connector approvals were interrupted mid-probe; one call (`issueLinkType`) completed against redhat.atlassian.net before they stopped. Section 5's `duplicate_link` and `link_type_inventory` are filled from that result; link orientation is recorded as a hypothesis pending the rehearsal, not a conclusion (per invariant 7). Section 12's dependency gate now reads from `link_type_inventory` instead of a hardcoded name list. Closure, editability, and resolutions remain PENDING — the script run (`design-proposals/rfe-merge-capability-probe.py`) covers those; the MCP has no editmeta equivalent regardless. |
| 2026-07-20 | Full script run against three representative RHAIRFE tickets — one New, one Stakeholder review, one Closed (ticket keys withheld from this public design doc; see note below) — landed. Redaction verified clean on real data (zero `displayName`/`emailAddress`/`accountId`/`avatarUrls`/email matches across the raw 2691-line output). Headline findings: (1) transitions are **not** status-dependent — all 3 sampled statuses expose the identical 14-transition set including `Closed` (id 21, resolution required, `Duplicate` an allowed value); (2) the actual `Related` link type was previously miswritten as "Relates" in section 12 — fixed; (3) this instance has a second merge-shaped link type, `Polaris merge work item link` (10188), not anticipated by the original design — recorded as a new open decision, not a data gap, in section 5's `merge_link_candidates`. Most of section 19's probe-only questions are now resolved and moved into section 5; two new ones replace them. `absorbed_to_survivor_request_orientation` and all `rehearsal:` fields remain pending the manual rehearsal — none of this required a write. |
| 2026-07-20 | Review pass tightened two of the above findings and closed a policy gap: (1) the status-independence finding is scoped to "within the Feature Request workflow," not the project generally — Jira workflows attach per issue-type, and section 6 already requires same-type, so this scope is exactly what merge needs, not an overgeneralization; the corresponding failure-matrix row now reads as a workflow-scheme-change signal rather than routine variance. (2) The Polaris-link open question now carries a recommendation, not neutrality: Polaris-* types are JPD's internal automation for merging *ideas*, not a RHAIRFE human convention, so `Duplicate` is the default absent evidence otherwise — two cheap JQL count queries (`issueLinkType = "Polaris merge work item link"` vs. `= Duplicate`) will confirm, and that survey folds into `/rfe.dupes`'s snapshot pass rather than being throwaway. (3) `merge-capability-probe.json` was deleted from the repo root (its data is now in section 5) and added to `.gitignore` in the same commit as the probe script, per section 15 — not just "don't commit it," but "cannot be committed accidentally," including by a future maintainer running the script on their own instance. |
| 2026-07-20 | Adversarial review found 4 real blockers and several correctness gaps, all independently verified (synthetic-payload test, code inspection, git/PR history) before fixing, not taken on faith: (1) **Fixed a real PII leak** — `redact_issue()` only blanked top-level reporter/assignee and passed the rest of Jira's raw response through unchanged, so a nested `assignee` inside a linked issue, or a customer name in a `parent`/linked-issue summary, survived untouched (confirmed with a synthetic payload before and after). Replaced with `summarize_issue()`/`summarize_link()`, which build output field-by-field from an explicit allowlist instead of redacting a copy — closes the whole class of "didn't anticipate this nested field" leaks, not just the one demonstrated. (2) **Fixed a crash bug** — a 200 response with a non-JSON body (e.g. an SSO proxy's HTML login page) hit an uncaught `json.JSONDecodeError` and would abort the entire probe run instead of failing just that call; now caught and degrades to a per-call `_error`. (3) **Tightened error-body redaction** — the prior fix (previous decision-log entries) kept Jira's `errorMessages`/`errors` verbatim, which can echo request content back into a file meant to be shareable; now only `status`/`reason` are kept, matching the "no flag disables it" claim in the docstring. (4) **Fixed the fingerprint gap** — `merge_state_hash` omitted `assignee` and `issuelinks` despite section 9 and 12 treating both as safety-relevant; split into `content_hash`/`metadata_hash`/`relationship_hash` (section 7), with relationship hashing normalized by (type, direction, key) so Jira's non-guaranteed link ordering can't manufacture false conflicts. (5) **Fixed a design gap** — the merge proposal artifact (section 8) had no persisted baseline fingerprint, leaving "changed since approval" undefined for direct `--into` invocation (no discovery report to compare against); added `approved_at`/`approved_source_state`/`approved_output`. Also corrected two claims that overstated what was actually established: the ADF-vs-wiki-markup inference (the probe never requested `description`, only confirmed Cloud deployment) and the PR #111 citation (verified via `gh pr view 111` and commit `6a3e25d`: both are about issue *creation*, not preserving a field during an in-place *update* — merge's actual policy is to omit reporter from the update payload entirely). Softened two overconfident claims: "workflow scheme changed" as the sole diagnosis for an unavailable transition (section 14), and "Probe complete" in the Status block (section 19 still lists sampling-size caveats). Fixed a literal typo in the direct-invocation CLI example (survivor was listed twice). Capped `allowed_values` output to a 20-item sample for non-identity fields (full ~90-item component lists aren't decision-relevant and needlessly expose internal taxonomy), dropped the raw server URL from probe output, added a note that `phase=closed` markers belong on the survivor (some workflows block comments on closed tickets), and flagged that provenance comments need a chunking policy if a description exceeds Jira's comment size limit. `probe_version` bumped to 4. |
| 2026-07-20 | Second adversarial review pass, again independently verified before fixing (code inspection, and for the fingerprint bug, tracing the actual comparison logic phase-by-phase): (1) **Fixed a real logic bug** — the just-added `approved_source_state` was a single pre-merge snapshot, but phase 3 intentionally changes relationship_hash (adds the Duplicate link) and phase 2 intentionally changes content/metadata_hash, so phase 4 comparing against that same snapshot would always "detect" phase 3's own link as an unexpected change. Renamed to `approved_baseline` and added `expected_after_survivor_update`/`expected_after_link`/`expected_after_close` (section 8); section 11 now states explicitly which block each phase checks against. Also added `proposal_hash` binding policy/risk/content/output together so a post-approval hand-edit can't go unnoticed. (2) **Fixed a real leak the "no server key" fix didn't actually close** — dropping `server` from top-level output didn't stop the raw `issueLinkType`/`resolution` responses from being written verbatim, and their `self` URLs embed the exact same instance hostname; replaced with purpose-built `summarize_link_types()`/`summarize_resolutions()`. Also replaced the previous "20-item sample" allowed-values fix (itself from the prior review round) with an enum-vs-opaque split: `priority`/`resolution` are small Jira-defined vocabularies safe to list in full, everything else (components, parent, labels, unrecognized custom fields) is now count-only, never names — the prior fix's flaw was that a 20-item sample of an arbitrary custom picklist can still contain a sensitive option name, just fewer of them. Also stopped surfacing `URLError`'s raw OS-level message (can embed a hostname via SSL errors) in favor of a static `network_error` classification plus the exception class name, and added a guard against valid-but-non-dict JSON (`null`, a bare list/string) reaching a summarizer's `.get()` call and crashing — verified all of the above with synthetic payloads/mocked responses before and after. `probe_version` bumped to 5. (3) **Corrected the provenance-content policy** (section 11, phase 1) — only the survivor's description is ever overwritten, so only its pre-merge state needs full preservation; absorbed issues keep their own descriptions unchanged and now get a concise pointer comment instead of a full duplicate, which also removes most of the previous round's comment-chunking concern (it now only applies to the one comment that can be description-sized). (4) **Fixed a scope gap** — pre-phase-2 fingerprint verification now explicitly covers the survivor, *every* absorbed issue, the local artifact, and the proposal manifest, not just whichever issue a naive per-issue loop is currently on; the failure matrix now defines **proposal** (one survivor + its absorbed issues, sharing one reconciled description — any source changing invalidates the whole thing) versus **batch** (independent proposals in one invocation — unaffected by each other) instead of using "pair" ambiguously for both. (5) **Filled a real policy gap** — section 6 had a non-terminal requirement for the survivor but said nothing about absorbed-issue status; added an explicit rule (already-Closed-as-Duplicate-with-correct-link is verified as already-done, not re-transitioned; any other terminal state forces `needs_human_review`). (6) Added `project`/`issuetype`/`resolution` to `metadata_hash` (section 7) — the first two are section 6's own eligibility requirements, so a post-approval change to either can invalidate eligibility itself, not just trigger a metadata-policy re-check. |
| 2026-07-20 | Third adversarial review pass, all four design issues confirmed by re-reading the actual current text (not assumed from memory) before fixing: (1) **Fixed a real gap of the same shape invariant 4 exists to prevent** — phase 1 posts Jira comments (a write), but proposal-wide validation was positioned only "before phase 2," so a source could change after approval, phase 1 could post provenance based on the now-stale proposal, and only the phase-2 check would notice — after the mutation, not before it. Split into three checks (section 11): at phase 1 start, immediately before each individual provenance comment, and before phase 2 (the existing one) — invariant 4 says "immediately before mutation," and there are two mutating moments in phase 1 (per-comment) and phase 2, not one. (2) **Fixed a real recovery gap for N>1 absorbed issues** — `expected_after_link`'s relationship_hash described only the fully-linked final state, so a crash after linking 1 of 3 absorbed issues would leave Jira's actual state matching neither `approved_baseline` (one link now exists) nor `expected_after_link` (two don't yet) — an undefined state a naive replay could get stuck on. Added `expected_merge_links` as a declarative work list separate from the final-state hash; phase 3 now reads actual `issuelinks`, checks each entry individually, creates only what's missing, and only checks the final hash once every entry is satisfied. (3) **Fixed a recurrence of the phase-2/3/4 fingerprint bug, one phase later** — `expected_after_close` supplied bare `status`/`resolution` values but no `metadata_hash`, even though `status` and `resolution` are both inside `metadata_hash`'s field list (section 7); per the document's own "absent field falls back to the prior expected block" rule, that meant closure recovery would compare against the *pre-close* metadata_hash forever — the exact bug class the `expected_after_*` split was created two rounds ago to fix, recurring in the one spot that split wasn't extended to. Added the corresponding post-close `metadata_hash` value. (4) **Defined `proposal_hash` deterministically** — the surrounding prose said "the entire file, every hash block" while the comment above the field said "metadata_policy, risk_gates, content, and approved_output," so the actual field list, key-sorting rule, list-ordering rule, and self-exclusion rule were all unstated; specified precisely in section 8. Probe hardening, verified with synthetic payloads before/after: transition-field summaries no longer include the field's display `name` (an admin-defined custom field can be named after a customer) or the full `schema` object (`schema.custom` is a plugin class name that can itself name a vendor) — narrowed to `schema_type`/`schema_items`/`schema_system`; link summaries no longer include the linked issue's `other_key` (a project prefix like a partner name can itself disclose a business relationship — this one is a closer call than the others, since issue keys aren't literally identity data, but it's cheap to drop and consistent with the doc's existing zero-tolerance posture). Softened the module docstring's "identity- and content-redacted" claim to something the code actually guarantees: identity-redaction is unconditional, but Jira/site-administered taxonomy (transition/status/resolution/link-type names) is intentionally retained as the whole point of a capability probe, and a final human scan is recommended before wider sharing. Also fixed a real traceability gap the second point above created: section 5 cited `probe_version 3` for the live run, but the script had since moved to `probe_version 6` across two redaction-hardening rounds — added `facts_collected_with_probe_version`/`current_probe_version` to make the gap explicit rather than silently stale, and replaced section 5's editability example (which still named three real component names) with a count-only description matching the v6 policy those same names would no longer be allowed to violate. `probe_version` bumped to 6. |
| 2026-07-20 | Fourth adversarial review pass. Two of the four points were the same fingerprint-inheritance bug recurring for a third and fourth time, now in the precheck logic itself rather than in an expected-state definition — confirming this shorthand ("check against block X, absent fields fall back to the prior block") is fundamentally fragile once more than one field is in flight, not just a one-off mistake to patch again: (1) Phase 3's opening precheck compared the survivor against `expected_after_survivor_update` and every absorbed issue against `approved_baseline` in full — but neither block's relationship_hash accounts for a resumed run where some links already exist, so the precheck itself would reject legitimate partial progress *before* the per-link recovery loop added last round ever ran, making that loop unreachable on the exact resume path it was built for. (2) Phase 4's survivor check compared against `expected_after_survivor_update` alone, which has no opinion on relationship_hash (falls back to `approved_baseline`, i.e. zero links) — but by phase 4 the survivor has every inbound Duplicate link phase 3 created, so this check would treat phase 3's own completed work as an unexplained conflict. Fixed both by introducing explicit composite checkpoints (section 8) instead of patching the fallback rule again: `phase_3_precheck` (with a `relationship_policy` — "baseline plus some subset of expected_merge_links" — since relationship state is a moving target mid-phase-3, not a fixed hash) and `survivor_before_close` (a fixed hash once phase 3 is fully done). Both are explicitly documented as pure compositions of already-hashed fields, not new approved data, so `proposal_hash` doesn't need to cover them. (3) **Fixed a real gap** — `approved_output.source_mapping_hash` had no corresponding path, so a resumed executor had a hash to verify against but no file to verify; added `content.source_mapping_path`. (4) **Fixed a real gap** — the proposal had no equivalent of `dupes-report.yaml`'s `canonicalization` field, so a canonicalization/normalization change between an interrupted run and its replay could make old and new hashes silently incomparable; added a `fingerprint_profile` block (versioned algorithm + content/metadata/relationship normalization identifiers) and included it in `proposal_hash`. Two smaller corrections, both verified against current text: invariant 9 said source identities are recorded "in the merge proposal," contradicting section 15's role-labels-only rule (the proposal was already correctly written that way — only the invariant's wording was wrong); reworded to match. Section 13's recovery markers had one `phase=provenance` marker per absorbed issue but nothing distinguishing a chunked survivor comment's parts from a complete one, so a crash after chunk 1 of a multi-part survivor provenance comment could read as done; split into `survivor-provenance` (with `part`/`total`, plus a `-complete` marker even for the unchunked case) and `absorbed-provenance` (unchanged, since that comment is never chunked). |
| 2026-07-20 | Fifth adversarial review pass, two implementation-safety gaps confirmed by re-reading current text, plus two corrections: (1) **Fixed a real gap in last round's own fix** — phase 3's per-link loop was preceded by a single upfront refetch, then created every missing link from that one stale read; with more than one absorbed issue, an edit landing between the first and second POST would go unnoticed, since only the very first check was "immediately before mutation." Restructured so each individual link creation gets its own refetch-revalidate-create-verify cycle (5 sub-steps), not one batch classification followed by unchecked writes. (2) **Fixed a real gap in provenance durability** — the multipart markers (added last round) proved a comment matching the marker pattern exists at each position, but never that its *content* was intact; a comment could be edited, corrupted, or have its body replaced while the marker line survived, defeating invariant 3's actual purpose (proving the pre-merge description recoverable) even though the marker-presence check would report success. Added `body_sha256` per part and `payload_sha256` on the completion marker; the state detector must now fetch and hash actual comment bodies, not just pattern-match marker text. Two corrections: the phase-1-start validation paragraph said the local merged artifact and proposal manifest were checked "against `approved_baseline`," which only holds Jira-issue fingerprints — separated into four explicit target→fingerprint pairs (proposal file → `proposal_hash`, merged description → `approved_output.merged_description_hash`, source mapping → `approved_output.source_mapping_hash`, Jira issues → `approved_baseline`). And last round's `phase_3_precheck`/`survivor_before_close` were called "illustrative" in prose but had no structural signal preventing a parser from reading them as real schema; nested both under a leading-underscore `_derived_examples` key with an explicit "MUST NOT be read from disk, MUST be recomputed at runtime, presence in a real file is itself suspicious" rule — a naming convention plus a stated MUST, not just a comment. |
| 2026-07-20 | Sixth adversarial review pass, two implementation-safety gaps confirmed against current text, plus a wording fix and a control-flow fix: (1) **Fixed a real integrity gap in last round's own fix** — `body_sha256`/`payload_sha256` as specified were self-referential (hashing "the whole fetched comment" would include the marker line stating the hash, which is circular) and had no anchor outside the mutable Jira comments themselves, so an edit to a comment's body and its marker's hash together would still pass. Specified the algorithm precisely: the marker line is excluded from the hash input, fragments are reassembled in numerical `part` order, and the authoritative comparison is against new `approved_output.survivor_provenance_payload_hash`/`absorbed_provenance_hashes` fields (section 8) — fixed at approval time, outside any Jira comment — not merely against a marker's self-reported hash. Extended the same hashing to the absorbed pointer comment, which last round left unhashed on the reasoning that it's short; it's still meaningful content (survivor key, proposal ID, retained-links disclosure) that deserves the same integrity check, just at smaller scale. Also fixed a wording bug the fix introduced: prose said the completion marker carries "its own `body_sha256`/`payload_sha256`," but the example only ever gave it `payload_sha256` (`body_sha256` belongs to `part` markers) — corrected. Added `provenance_canonicalization`/`provenance_chunking` to `fingerprint_profile` so this new algorithm is versioned like everything else. (2) **Fixed a real gap in the executor-entry sequence** — `proposal_hash` was only checked at phase 1 start and before phase 2, but a resume can land directly in phase 3 or 4, which derive issue keys, `expected_merge_links`, and closure expectations straight from the proposal file; nothing re-verified the file itself wasn't hand-edited before trusting those derivations on that path. Added a global rule at the top of section 11: every executor invocation, regardless of which phase it resumes into, strictly parses the proposal, rejects unknown reserved keys (including a literal `_derived_examples`), verifies `fingerprint_profile.version` is supported, and recomputes `proposal_hash` before anything else — separate from and prior to the Jira-state checks, which only make sense once the proposal itself is known-good. Smaller fix: Phase 3 sub-step 3 said to confirm a link "still absent" while sub-step 5 called a redundant create "harmless," which (a) left the control flow implicit rather than a clear if/else and (b) asserted `link_creation_idempotent` as settled when section 5/19 explicitly still lists it as pending the rehearsal. Made the branch explicit (link found → mark complete, skip POST; not found → create then verify) and reframed the redundant-creation case as "the refetch decides success, not the POST response" (extending invariant 5 symmetrically to error responses) rather than asserting a safety property this design doesn't yet know to be true. |
| 2026-07-20 | Seventh adversarial review pass. One real replay blocker, confirmed by re-reading Phase 4's actual prose order, plus a determinism gap: (1) **Fixed a real blocker of the same shape Phase 3 was fixed for two rounds ago** — Phase 4's opening sentence unconditionally rechecked every absorbed issue against the pre-close composite (`approved_baseline` + `expected_after_link`), with the already-terminal case mentioned only in the paragraph's last sentence, as an aside. Read left to right — which is how prose gets implemented — this rejects exactly the recovery scenario the failure matrix already promises: absorbed issue A closes successfully, absorbed issue B fails, the process crashes, and a replay must recognize A as already-done and only retry B. As written, A's now-Closed metadata would fail the unconditional "untouched" check before the already-terminal branch was ever reached. Restructured into the same classify-actual-state-first-then-branch pattern Phase 3 already uses for links: refetch, then branch three ways (matches `expected_after_close` → verify and backfill the marker if needed, don't transition again; matches the pre-close composite → verify and transition; matches neither → abort the proposal and surface for review). (2) **Determinism gap, not a state-machine blocker** — the provenance hashing algorithm said the payload and marker line could be "either order, as long as it's consistent," which isn't actually a specification; two conforming implementations of `rfe-merge-provenance-v1` could each pick a different order and produce different hashes for identical content. Pinned one canonical layout: marker last, exactly one `\n` separator, UTF-8, line-ending normalization before hashing, exact stripping behavior to recover the payload, and rejection (not best-guess resolution) of a comment containing more than one marker-pattern line. |
| 2026-07-20 | Eighth adversarial review pass, two narrow specification gaps confirmed against current text — no further state-machine issues found: (1) **Fixed a real self-contradiction from last round's own fix** — the canonicalization rules said "the payload fragment itself carries no trailing newline beyond that one [separator]" and, two bullets later, "nothing else is trimmed (no additional whitespace stripping that could silently absorb a real trailing blank line in the payload)" — both can't hold for a payload that genuinely ends in a blank line, and invariant 3 requires the original content back verbatim, blank lines included. Resolved by separating the two concerns cleanly: `canonical_payload` is line-ending-normalized but otherwise exactly the original text, whatever its own trailing newlines are; the posted comment is always `canonical_payload + "\n" + marker`, with the separator being one *mechanically appended* `\n` that's never conflated with anything already in `canonical_payload`; `body_sha256` hashes `canonical_payload` alone, before that separator exists. (2) **Fixed a real gap in applying Phase 4's own principle to itself** — the pre-close branch verified the survivor, provenance, and link, then transitioned the absorbed issue using the classification read from step 1, without a fresh check immediately before the transition itself; the already-closed branch's marker backfill (itself a Jira write) had the same gap. Both are the identical "immediately before mutation" principle Phase 3's per-link loop already applies strictly to its own writes — Phase 4 just hadn't been held to the same standard. Added an explicit refetch-and-reconfirm step right before each of those two writes. |
| 2026-07-20 | Non-blocking wording hardening, final review pass: both survivor-recheck spots added last round said "refetch the survivor" without naming what to compare the result against, which reads like a GET without a validation step even though a comparison was clearly intended. Both now explicitly say "refetch and reconfirm it still matches `survivor_before_close`" — the already-closed branch's marker backfill, and (added for consistency) the pre-close branch's own post-transition marker write, which shares the identical write and deserved the identical explicit check. No remaining state-machine or provenance-integrity issue identified; the design is implementation-ready pending the manual Jira rehearsal (section 16). |
| 2026-07-20 | The two JQL count queries (section 19) came back: `issueLinkType = "Polaris merge work item link"` → 0 issues, `issueLinkType = Duplicate` → 50+ issues. Exactly the predicted outcome — `Duplicate` is a real, actively-used convention; Polaris types are confirmed unused for this purpose on this instance. `merge_link_candidates` (section 5) updated from a recommendation to a confirmed decision; the corresponding section 19 open question moved to resolved. Separately: before recording this, replaced every real ticket key in this doc (three tickets actually probed) with placeholders (`sample-new`/`sample-mid-workflow`/`sample-closed`) — this is a public repository, and while what was recorded about those tickets was already minimal (status/link-presence only, no titles or content, per the redaction work in earlier rounds), the ticket identifiers themselves added no decision-relevant value and were removed rather than argued over. Only the identifiers changed; every finding, count, and structural fact in this document is unchanged. |
| 2026-07-21 | PR #133 opened against upstream; automated review (CodeRabbit) posted 6 actionable comments, each checked against the actual current text before acting, not taken on faith. Four were real and fixed: (1) **HTTPS enforcement in the probe** — `request_json()` had no scheme validation on `JIRA_SERVER`; a typo'd `http://` would send the Basic-auth token in the clear. Added `is_https_url()` and a startup check in `main()`, with unit test coverage. (2) **No path-traversal guard on proposal-controlled file paths** — `content.merged_description_path`/`source_mapping_path` (section 8) were bare strings with no canonicalization rule; added invariant 16 (section 4) requiring the executor to sandbox both against a fixed artifacts root before opening them, since a `proposal_hash`-verified proposal is still an untrusted path string until then. (3) **Missing issue-security-level eligibility check** — section 6's "same project and issue type" said nothing about Jira's separate Issue Security Level scheme; a more-restricted absorbed issue's content merging into a less-restricted survivor is a real visibility escalation, not just a metadata mismatch. Added as an explicit MVP eligibility requirement, forcing `needs_human_review` on a mismatch, and added `security` to `metadata_hash`'s field list (section 7) for the same reason `project`/`issuetype` are there. (4) **`expected_merge_links` didn't pin the actual probed Jira capability values** — `fingerprint_profile` versions the hashing algorithm, but nothing bound the proposal to the specific link-type ID or transition payload it was approved against, so an admin renaming `Duplicate` or changing the `Closed` transition between approval and execution would go undetected. Added `approved_write_profile` (section 8, included in `proposal_hash`), and updated phase 3/4 (section 11) to fail closed against it rather than re-deriving these values mid-execution. One finding was already handled — the "phase 1 needs full revalidation" comment describes exactly what checkpoints #1 and #2 in section 11 already do; likely a review-window limitation on CodeRabbit's side, not a real gap. One was scoped down rather than fully designed: concurrent-executor safety is real but out of scope for this MVP (single-operator, sequential execution assumed) — added as an explicit non-goal (section 2) rather than building a lock mechanism now. Separately, added `tests/test_rfe_merge_capability_probe.py` (37 tests, direct-import + subprocess-CLI style matching this repo's existing conventions) covering the probe's redaction guarantees, HTTPS enforcement, and error-handling paths — addressing the pre-merge check's fair point that the probe shipped with no repo-tracked tests, only ad-hoc verification during design review. |
| 2026-07-21 | Human review corrected the concurrent-executor non-goal (section 2) before it shipped: the previous wording suggested "a Jira-side or file-based lease" as a plausible future fix, which is wrong, not just unfinished — Jira has no atomic create-if-absent for comments or links, so a lease built from a Jira write needs its own check-then-set, which relocates the exact race it's meant to prevent rather than closing it. Rewrote the bullet to state the residual risk plainly (two genuinely concurrent executors can race the same writes; this document doesn't claim otherwise) and to explicitly distinguish what idempotency markers (section 13) actually buy — crash-and-resume safety for one executor, restarted, not concurrency control for two — rather than letting the two get conflated. Also fixed 4 markdownlint-flagged unlabeled code fences (the two CLI-invocation examples in section 6, the recovery-marker block in section 13, and the layout-compatibility block in section 17) by adding `text` language tags; the other 9 bare fences in the document are closing fences and don't need one. |
| 2026-07-21 | CodeRabbit re-reviewed the pushed commit and correctly marked 5 of 6 original findings resolved; one it re-raised is the single-flight guard, which stays deliberately unaddressed as designed (see the entry above — its suggested fix doesn't work). Two new findings, both confirmed against current text and real: (1) **`approved_write_profile` had no rule against staying a placeholder** — the block added last round to pin capability values for execution still contained literal `PENDING_REHEARSAL` and `"..."` with nothing stopping a proposal in that state from being approved, freezing the placeholder into `proposal_hash` instead of a real value. Added invariant 17 (section 4), a blocking condition on step 8 of the planning gate (section 10), and a matching check in the executor-entry sequence (section 11) — belt and suspenders, since invariant 17 says this should never have been approved, but the executor verifies rather than trusting that it wasn't. (2) **The probe's HTTPS-rejection message echoed the configured `JIRA_SERVER` value** (CWE-532/CWE-200 per the pre-merge check) — a URL can carry embedded userinfo or an internal-only hostname, and this message could land in a shared log or a pasted bug report. Removed the echoed value from the message entirely; added a regression test asserting a deliberately sensitive-looking value (embedded credentials + an internal hostname) never appears in stderr. 38 tests now pass. Separately, the "Contribution Quality" pre-merge check that previously warned the PR body read as template-like now passes outright, crediting the rewritten description and the added tests. |

As probe and rehearsal results come in, append rows here rather than
rewriting the rationale above them — the goal is that empirical
findings extend this document, they don't require re-litigating it.

## 1. Problem statement

`/rfe.create`'s duplicate detection (see PR #76/#93 history) only
guards the moment of creation — it never shipped, and even if it had,
it can't help with the RHAIRFE tickets that already exist. Nothing in
the current pipeline (`/rfe.split`, `/rfe.review`, `/rfe.auto-fix`)
addresses overlapping RFEs that already live in Jira.

`/rfe.merge` consolidates an approved set of duplicate/overlapping
RHAIRFE tickets into a single selected survivor: reconciled
description and acceptance criteria, credited reporters, and the
absorbed tickets closed and linked back to the survivor. It is the
inverse of `/rfe.split`, and reuses the same fetch/ADF/issue-link/
transition machinery that split already has proven out.

## 2. Goals and non-goals

**Goals**

- Lossless content reconciliation (no acceptance criterion silently dropped)
- Explicit survivor selection (human-approved, never inferred silently)
- Auditable reporter and assignee attribution
- Safe linking and source closure
- Conflict detection between discovery, approval, and execution
- Idempotent recovery after partial failure

**Non-goals for MVP**

- Global duplicate discovery (that's `/rfe.dupes`'s job, not merge's)
- Automatic watcher migration
- Comment or attachment migration
- Automatic migration of third-party issue links
- Cross-project merging
- Automatic survivor selection without approval
- Fully unattended ticket closure
- **Safe concurrent execution.** One executor per proposal at a time —
  enforced by the operator/process convention, not by anything in this
  design. Jira exposes no atomic create-if-absent for comments or
  links, so a "lease" implemented as a Jira write would need its own
  check-then-set, which is the exact race it's trying to prevent —
  it relocates the TOCTOU window, it doesn't close it. A file-based
  lock has the same problem across machines, and adds a new failure
  mode (a stale lock file after a crash) without removing the Jira-side
  one. The idempotency markers (section 13) are best-effort mitigation
  for **crash-and-resume** — one executor, interrupted, restarted later
  — which is the failure mode this design actually targets. They are
  not a concurrency-control mechanism, and the residual risk is real:
  two executors genuinely running at the same time against the same
  proposal can race the same writes, and this document does not claim
  otherwise. If concurrent execution becomes a real scenario, it needs
  its own design with its own primitive (most plausibly Jira's own
  optimistic-locking support, if any, or an external coordinator
  outside Jira entirely) — not a lease bolted onto phases that assume
  they're the only writer.

## 3. Terminology

- **Survivor** — the existing Jira issue enriched with the approved merged content.
- **Absorbed issue** — a duplicate issue closed after its content is incorporated into the survivor.
- **Discovery report** — the versioned `/rfe.dupes` output (`dupes-report.yaml`).
- **Merge proposal** — the reviewable local artifact containing reconciled content and policy decisions, produced by the planning gate.
- **Capability profile** — the sanitized result of the read-only Jira probe (section 5).
- **Merge fingerprint** — a canonical hash over the fields that affect merge safety (content and metadata), used to detect concurrent edits.

## 4. Design invariants

These invariants must hold and should guide implementation and future refactors:

1. **No Jira writes before approval.** Every write phase (11) is downstream of an explicit human sign-off on the merge proposal (8). There is no auto-merge tier in the MVP.

2. **No absorbed issue is closed before the survivor update is verified.** Phase ordering (11) is fixed: provenance, then survivor update + verify, then link + verify, then close. A later phase never starts speculatively.

3. **Original content is persisted before destructive lifecycle changes.** Provenance comments (phase 1) land before the survivor's description changes and before any closure — mirroring split's strongest pattern of persist-before-destroy.

4. **Every issue is refetched and fingerprint-checked immediately before mutation.** The discovery report's fingerprints are a proposal-time snapshot, not a live guarantee; each write phase re-verifies against current Jira state before acting.

5. **A successful HTTP response is not proof of final Jira state.** Every write is followed by a refetch and comparison — a 200 on link creation, for example, can mean "created" or "already existed," and only a refetch of `issuelinks` distinguishes them.

6. **Every merge is resumable and idempotent.** Durable markers (section 13) let a crashed or interrupted merge detect exactly which phases completed and continue without duplicating comments, links, or closures.

7. **The absorbed issue must point toward the survivor using the empirically verified link orientation.** Direction is not assumed; it's read from the probe's `issueLinkType` result and confirmed in the manual rehearsal.

8. **Active directional dependencies block automatic closure.** `Blocks` / `is blocked by` / dependency-shaped links on an absorbed issue move that merge to `needs_human_review` rather than proceeding — closing a ticket must never conceal live dependency information.

9. **Reporter and assignee identities are credited but not silently reassigned.** The survivor's reporter and assignee are preserved. Source identities are credited in Jira provenance (the comments, which are Jira-native and already access-controlled the same as the rest of the ticket); the merge proposal itself records only identity *presence*, *differences*, and the approved attribution policy, using non-identifying role labels — never a real identity — per section 15.

10. **Watchers, comments, attachments, and non-duplicate links are not migrated in the MVP.** These are recorded and disclosed, not carried over — see section 9 and 12.

11. **The `/rfe.dupes` → `/rfe.merge` boundary is a versioned YAML artifact, not a Python import.** No `from rfe_dupes... import ...`. This keeps each skill mechanically relocatable if PR #115's per-skill packaging restructure lands.

12. **No design or fixture artifact may contain real user identity data.** Extends this repo's existing `eval/dataset/` PII policy to probe output, rehearsal captures, and design examples alike.

13. **The implementation must not assume the permanent existence of the flat root `scripts/` layout.** Current repo state has no per-skill `scripts/` directories (PR #115 is open, unmerged, stale since 2026-06-12), but `/rfe.merge`'s scripts should resolve resources relative to their own location and avoid deep cross-skill imports regardless.

14. **The merged RFE must pass review and remain right-sized.** The planning gate runs `/rfe.review` on the reconciled draft before any Jira write; a merge that produces an oversized or low-quality RFE is not complete just because the Jira mechanics succeeded.

15. **Any unresolved content conflict prevents execution.** If reconciliation can't resolve a contradiction between source acceptance criteria (not just "different wording," but actual conflicting requirements), the merge proposal records it under `unresolved_conflicts` and cannot proceed to phase 1 until a human resolves it.

16. **Proposal-controlled file paths never resolve outside the artifacts root.** `content.merged_description_path` and `content.source_mapping_path` (section 8) are read from a proposal file — even a `proposal_hash`-verified one is still an untrusted path string until sandboxed. The executor must canonicalize each against a fixed artifacts root, reject absolute paths, `..` traversal, and symlink escapes, and refuse to read/write if the canonical result falls outside that root. `proposal_hash` proves the proposal wasn't tampered with after approval; it doesn't by itself prove a path inside it was ever safe to open.

17. **Approval requires concrete write-profile values, never placeholders.** `approved_write_profile` (section 8) is what phases 3–4 send to Jira verbatim — the exact link-type ID, orientation, transition ID, and resolution. A value still marked `PENDING_REHEARSAL` or `"..."` isn't a fact yet, it's a gap; approving a proposal in that state would freeze the gap into `proposal_hash` rather than a real value, and an executor that didn't specifically reject placeholders could send a nonsensical request or silently misinterpret one as real. The rehearsal (section 16) must resolve every placeholder before human approval — not after, and not left for the executor to catch at write time.

## 5. Jira capability profile

Filled in from `design-proposals/rfe-merge-capability-probe.py` output
and the manual rehearsal (section 16). This section is **descriptive**
— it records what the instance supports. The invariants in section 4
are **normative** and don't change based on what's discovered here;
the probe and rehearsal only tell us how to satisfy them mechanically.

The two sources are kept separate below because they have different
guarantees: everything under `probe` comes from read-only GETs and can
be re-run anytime with no side effects; everything under `rehearsal`
required actually executing a transition/link/close once, by hand, on
a real pair, because Jira's documented behavior isn't sufficient proof
of what a specific instance/workflow actually does.

**Naming note:** this design doc lives in a public repository. The
probe was run against three real RHAIRFE tickets, but their actual
keys are withheld here — referenced instead as `sample-new`,
`sample-mid-workflow`, and `sample-closed` (matching the status each
was in at probe time). Everything else about them — link types
observed, editability, redaction behavior — is unchanged from what was
actually measured; only the specific ticket identifiers are replaced.

```yaml
jira_capabilities:
  probe:
    validated_at: "2026-07-20"
    facts_collected_with_probe_version: 3   # the actual live run against redhat.atlassian.net
    current_probe_version: 6                # design-proposals/rfe-merge-capability-probe.py, as of this doc revision
    # The two differ because v3->v6 were redaction/robustness fixes to the
    # *script*, not re-runs against Jira. The structural facts below (link
    # types, closure semantics, editability booleans) aren't affected by
    # those fixes and don't need re-verifying. What v3's raw output did
    # capture with weaker redaction than v6 now applies (e.g. full custom
    # picklist option names) was hand-filtered when this section was
    # written, not carried over verbatim — but if exact reproducibility of
    # the raw JSON ever matters, re-run v6 rather than trusting v3's.
    validated_against:
      deployment: Cloud   # redhat.atlassian.net + successful /rest/api/3 calls confirm Cloud.
                          # This probe never requested the description or changelog fields,
                          # so it did NOT confirm ADF vs. wiki-markup representation directly —
                          # only that this is a Cloud instance. Merge should keep using the
                          # repository's existing canonicalization layer rather than assuming
                          # a representation from the deployment type alone; the PR #28 concern
                          # is Server/DC-specific and doesn't obviously apply to Cloud, but that
                          # inference hasn't been empirically checked against a real description.
      representative_statuses: [New, "Stakeholder review", Closed]   # sample-new, sample-mid-workflow, sample-closed (real ticket keys withheld — public repo)

    duplicate_link:
      available: true
      type_id: "10002"
      type_name: Duplicate
      inward_label: is duplicated by
      outward_label: duplicates
      # Cross-confirmed: identical values from the MCP partial fill and this
      # full script run.
      absorbed_to_survivor_request_orientation: PENDING_REHEARSAL
      # Still a hypothesis, not a conclusion (invariant 7 exists precisely
      # because inward/outward orientation is the thing everyone gets
      # backwards): inwardIssue=absorbed, outwardIssue=survivor, per the
      # earlier Blocks-convention reasoning. The rehearsal (section 16) must
      # confirm this against the actual resulting issue view, not just the
      # POST response — reading issueLinkType alone cannot settle it.

    # Full link-type inventory from the same issueLinkType call, feeding
    # section 12's dependency gate so it's driven by probed data rather
    # than a hardcoded name list. 16 link types exist on this instance.
    link_type_inventory:
      dependency_shaped: [{id: "10000", name: Blocks}, {id: "10076", name: Depend}]
      review_recommended: [{id: "10078", name: Causality}, {id: "10082", name: Triggers}]
      # New finding, not anticipated by the earlier design: this instance
      # already has a second link type shaped like a merge, distinct from
      # Duplicate. Leaning `Duplicate`, not treating this as a coin flip:
      # the Polaris-* link types are Jira Product Discovery's internal
      # machinery — JPD auto-creates "merged into" links when someone
      # merges *ideas* inside a Discovery project. They show up here only
      # because Jira link types are global across the site, not because
      # any RHAIRFE human chose this vocabulary for feature requests.
      # Creating one manually from a script against a non-JPD project
      # borrows another product's bookkeeping semantics, which JPD tooling
      # may act on or display in ways nobody here intended.
      #
      # RESOLVED 2026-07-20 via two JQL count queries against RHAIRFE:
      #   project = RHAIRFE AND issueLinkType = "Polaris merge work item link"  -> 0 issues
      #   project = RHAIRFE AND issueLinkType = Duplicate                       -> 50+ issues (paginated, not exhaustively counted)
      # Exactly the predicted outcome: Polaris is unused for this purpose on
      # this instance; Duplicate is an actively-used real convention with
      # real volume, not just a theoretical option. Default: Duplicate.
      # Polaris types remain reserved for JPD automation -- not a live
      # question anymore. This survey folds into /rfe.dupes's snapshot pass
      # as planned, not as throwaway one-off work. (Aggregate counts only --
      # no specific ticket keys needed or recorded for this check.)
      merge_link_candidates:
        - {id: "10002", name: Duplicate, inward: "is duplicated by", outward: duplicates, confirmed_default: true, observed_count: "50+"}
        - {id: "10188", name: "Polaris merge work item link", inward: "merged into", outward: "merged from", confirmed_default: false, observed_count: 0, reason: "JPD-internal automation, not a RHAIRFE human convention -- confirmed unused for this purpose on this instance"}
      other_notable:
        - {id: "10120", name: "Work item split", note: "split from / split to — /rfe.split doesn't use it today"}
        - {id: "10187", name: "Polaris work item link", note: "implements / is implemented by"}
        - {id: "10189", name: "Polaris datapoint work item link", note: "is idea for / added to idea"}
        - {id: "10080", name: Incorporates, note: "incorporates / is incorporated by — semantically merge-adjacent, unconfirmed relevance"}
        - {id: "10075", name: Account}
        - {id: "10001", name: Cloners}
        - {id: "10190", name: "Discovery - Connected"}
        - {id: "10079", name: Document}
        - {id: "10083", name: Informs}
      # Partial, real evidence — not exhaustive (n=2 tickets with any links;
      # sample-closed has none). Every observed link on both sample-new and
      # sample-mid-workflow was type "Related" (10077) — note the actual type name
      # is "Related", not "Relates" (fixed in section 12). Zero Blocks/
      # Depend/Duplicate/Polaris-merge links observed in this sample; too
      # small to conclude they're rare, but worth flagging as the section 19
      # frequency question to answer at scale before finalizing the gate
      # thresholds.
      appears_on_representative_tickets: "Related only (n=2 tickets with links)"

    closure:
      # All three sampled statuses (New, Stakeholder review, already-Closed)
      # exposed the *same* 14 transitions with the *same* IDs, including
      # Closed (id 21) — this workflow is not linear/gated across the
      # samples checked; nearly every status is reachable from any other.
      #
      # Scoped claim, not a general one: all 3 samples were RHAIRFE Feature
      # Request issues. Jira workflows attach per issue-type (via the
      # project's workflow scheme), so this is "status-independent within
      # the Feature Request workflow" — not a claim about every issue type
      # in the project. That scope is exactly what merge needs, since
      # section 6 already requires same-project *and* same-type, so merge
      # never crosses into a different workflow anyway.
      transitions_by_source_status: "global within the Feature Request workflow — identical transition set/IDs observed from all 3 sampled statuses"
      resolution_field_required: true    # on the Closed transition (id 21) specifically
      duplicate_resolution_available: true   # "Duplicate", id 10002, among 11 global resolutions

    editability:
      # All identical across the 3 sampled statuses.
      reporter: {required: true, editable: true, allowed_values_offered: false}
      assignee: {required: false, editable: true, allowed_values_offered: false}
      components: {required: false, editable: true, allowed_values: "count: ~90, names not retained (opaque field, per v6 policy — see design-proposals/rfe-merge-capability-probe.py)"}
      labels: {required: false, editable: true, allowed_values: "free-form, none enumerated"}
      priority: {required: false, editable: true, allowed_values: [Blocker, Critical, Major, Normal, Minor, Undefined]}
      parent: {required: false, editable: true, allowed_values: "none enumerated, picked by search"}
      # reporter.required: true describes the edit *screen*, not a demand
      # that every update resend it. Section 9's policy is preserve_survivor,
      # so merge's update payload should simply omit reporter rather than
      # resend the survivor's existing value. PR #111 (and its fallback
      # commit 6a3e25d) is about issue *creation* — the automation account
      # setting a child's reporter at create time and retrying without it if
      # Jira rejects that specific write. It doesn't establish that an
      # in-place update must resend every edit-screen-required field, and
      # citing it here as if it did was incorrect. PR #111's pattern only
      # becomes relevant again if a future design creates a new consolidated
      # ticket, or deliberately reassigns reporter — neither is this MVP.

  # Requires the manual rehearsal (section 16) -- a read-only probe cannot
  # confirm these, since they're only observable by actually writing once.
  rehearsal:
    verified_transition_payload: PENDING   # does POSTing the id-21 transition with a Duplicate
                                            # resolution actually succeed as editmeta/transitions promise?
    link_creation_idempotent: PENDING
    closed_ticket_visible_to_original_reporter: PENDING
    closed_ticket_comments_remain_visible: PENDING
```

Redaction check on this run: grepping the full 2691-line raw output for
`displayName`, `emailAddress`, `accountId`, `avatarUrls`, and any email
address returned zero matches. Every `reporter`/`assignee` field reads as
`{"present": true/false}` as designed, including the one genuinely
unassigned ticket in the sample (sample-mid-workflow). The redaction held
on its first run against real data, not just in review.

## 6. Input contract

Direct invocation — `--into` names the survivor, positional keys are the
absorbed issues only (an earlier draft repeated the survivor as both the
`--into` value and a positional argument, which was just a typo, not a
real repeat-the-survivor-twice syntax):

```text
/rfe.merge --into RHAIRFE-1234 RHAIRFE-1567
```

Discovery-driven invocation:

```text
/rfe.merge --report artifacts/rfe-dupes/dupes-report.yaml --pair pair-001
```

MVP requirements, regardless of invocation form:

- At least two existing Jira issues.
- An explicit survivor (never inferred without approval).
- Same Jira project and issue type.
- **Same issue security level.** Jira's Issue Security Level is a
  separate, orthogonal permission scheme from project-level access —
  an issue can be restricted to a smaller audience than the project it
  lives in. "Same project and issue type" says nothing about this.
  Merging pulls an absorbed issue's content (acceptance criteria,
  provenance) into the survivor; if the absorbed issue is more
  restricted than the survivor, that content becomes visible to
  whoever can already see the survivor — a real visibility escalation,
  not just a metadata mismatch. Differing security levels force
  `needs_human_review`, the same posture as an unexpected absorbed-issue
  terminal state below.
- Compatible parent relationship (see metadata policy, section 9).
- Non-terminal survivor (can't merge into an already-closed issue).
- Human approval before any write.
- **Absorbed-issue status policy** (unaddressed by an earlier draft,
  which only constrained the survivor): an absorbed issue may be
  non-terminal (the normal case — phase 4 transitions it) *or* already
  `Closed` with resolution `Duplicate` and a correct, verified link to
  the survivor (treated as already-completed — phase 4 verifies rather
  than transitions, which is what makes replay and historical cleanup
  safe without reopening a ticket someone already closed by hand). Any
  *other* terminal status or resolution on an absorbed issue (`Won't
  Do`, `Obsolete`, a `Duplicate`-of-something-else link, etc.) forces
  `needs_human_review` — the MVP does not guess what an unexpected
  terminal state means.

## 7. /rfe.dupes interface

`dupes-report.yaml` — the sole contract between `/rfe.dupes` and
`/rfe.merge`. Merge treats every field here as a hint to verify, not a
fact to trust; it always refetches and recomputes fingerprints before
acting (invariant 4).

```yaml
schema_version: 1
canonicalization: rfe-creator-adf-v1   # versioned separately from schema_version — see PR #28
generated_at: "2026-07-20T00:00:00Z"
scope_jql: "project = RHAIRFE AND status != Closed"

pairs:
  - issues:
      - key: RHAIRFE-1234
        updated_at: "2026-07-19T14:42:31Z"
        content_hash:
          algorithm: sha256
          canonicalization: rfe-creator-adf-v1
          value: "..."
        metadata_hash:
          algorithm: sha256
          fields: [project, issuetype, security, summary, priority, components, labels, parent, status, resolution, reporter, assignee]
          value: "..."
        relationship_hash:
          algorithm: sha256
          # Normalized by (type_id, direction, other_key), sorted before
          # hashing -- Jira's issuelinks ordering is not guaranteed stable,
          # so hashing the raw array would flag false conflicts on every
          # refetch even when nothing actually changed.
          value: "..."
      - key: RHAIRFE-1567
        updated_at: "..."
        content_hash: {algorithm: sha256, canonicalization: rfe-creator-adf-v1, value: "..."}
        metadata_hash:
          algorithm: sha256
          fields: [project, issuetype, security, summary, priority, components, labels, parent, status, resolution, reporter, assignee]
          value: "..."
        relationship_hash: {algorithm: sha256, value: "..."}

    confidence:
      tier: high            # high | medium | low
      score: 0.94

    proposed_survivor:
      key: RHAIRFE-1234      # or null — "these overlap" can be high-confidence
                              # while "which one should win" is undetermined
      rationale: [older_ticket, more_complete_acceptance_criteria]

    evidence:
      - type: shared_problem
        left_quote: "..."
        right_quote: "..."
      - type: overlapping_acceptance_criterion
        left_quote: "..."
        right_quote: "..."

    disposition: merge_candidate   # merge_candidate | related_only | needs_human_review | false_positive
```

`updated_at` is a diagnostic, not a guard — the three hashes are what
merge actually checks (invariant 4), since `updated_at` alone can't
distinguish "someone reworded a label" from "someone changed the
reporter." Three separate hashes rather than one combined
`merge_state_hash`, because they answer different questions and fail
differently: `content_hash` changing means the description itself
changed (reconciliation may be stale); `metadata_hash` changing means
something in section 9's policy table changed (a differing assignee
newly appeared, priority shifted); `relationship_hash` changing means
the link inventory changed (a new blocking link may have appeared,
which section 12 says must gate closure). An earlier draft of this
schema folded `assignee` and `issuelinks` out of the hash entirely,
which contradicted section 9's own differing-assignee risk gate and
section 12's link-change gate — both are safety-relevant, so both are
now hashed. `project` and `issuetype` are in `metadata_hash` because
they're section 6's own eligibility requirements — if either changed
after approval, the pair may no longer even qualify for merging, not
just need a metadata-policy re-check. `resolution` is there because
an absorbed issue can already be terminal (section 6's absorbed-issue
policy) or become terminal concurrently, and closure recovery (section
14) needs to distinguish "still open as approved" from "someone
resolved it a different way while the merge was in flight." `security`
is there for the same reason as `project`/`issuetype`: it's now a
section 6 eligibility requirement (same issue security level), so a
post-approval change to it can invalidate eligibility itself, not just
trigger a visibility re-check.

## 8. Merge proposal artifact

Produced by the planning gate (section 10), consumed by the write
phases (section 11). This is where policy decisions become concrete
and reviewable before anything touches Jira.

```yaml
schema_version: 1
proposal_id: merge-RHAIRFE-1234-20260720
survivor: RHAIRFE-1234
absorbed: [RHAIRFE-1567]

# Names which canonicalization/normalization produced every hash below.
# dupes-report.yaml already versions content canonicalization
# (section 7); this extends the same idea to metadata and relationship
# hashing, and to the proposal artifact itself. Without this, a
# canonicalization change between an interrupted run and its replay
# would make old and new hashes silently incomparable -- or, worse,
# coincidentally comparable in a way that's wrong.
fingerprint_profile:
  version: rfe-merge-fingerprint-v1
  algorithm: sha256
  content_canonicalization: rfe-creator-adf-v1
  metadata_normalization: rfe-merge-metadata-v1
  relationship_normalization: rfe-merge-links-v1
  provenance_canonicalization: rfe-merge-provenance-v1   # how a comment's payload fragment is normalized before hashing (section 13)
  provenance_chunking: rfe-merge-chunks-v1                # how the survivor's description is split into parts, and reassembled

# Pins the specific Jira configuration (section 5's jira_capabilities)
# this proposal was approved against. fingerprint_profile versions the
# *hashing* algorithm; this versions the *external Jira facts* the
# write phases depend on -- which link type, which transition, which
# resolution. Without it, an admin renaming the Duplicate link type or
# changing the Closed transition's required fields between approval
# and execution would go undetected until a write failed in a
# confusing way, or worse, silently succeeded against the wrong thing.
# Every write phase (11) that touches Jira validates against this
# block before acting, and fails closed on any mismatch -- it never
# falls back to re-deriving these values from a fresh capability probe
# mid-execution, since that would mean executing against a
# configuration nobody approved.
approved_write_profile:
  duplicate_link:
    type_id: "10002"
    type_name: Duplicate
    inward_label: is duplicated by
    outward_label: duplicates
    request_orientation: PENDING_REHEARSAL   # section 5/16 -- absorbed=inwardIssue, survivor=outwardIssue, unconfirmed
  closure_transition:
    id: "..."            # from section 5's jira_capabilities.rehearsal.verified_transition_payload
    name: Closed
    resolution_id: "10002"
    resolution_name: Duplicate

content:
  # Both paths below must be canonicalized against a fixed artifacts
  # root before the executor opens them -- reject absolute paths, ..
  # traversal, and symlink escapes. See invariant 16 (section 4);
  # proposal_hash proves the string wasn't tampered with, not that the
  # path it names is safe to open.
  merged_description_path: artifacts/rfe-merges/RHAIRFE-1234-merged.md
  # Phase 1's survivor provenance comment and step 6 of the planning
  # gate both need this file, not just its hash -- a resumed executor
  # can't reconstruct the mapping from source_mapping_hash alone.
  source_mapping_path: artifacts/rfe-merges/RHAIRFE-1234-source-map.yaml
  unresolved_conflicts: []

metadata_policy:
  reporter: preserve_survivor
  assignee: preserve_survivor
  priority: highest
  labels: union_non_automation
  components: union
  parent: require_equal
  watchers: unchanged
  non_duplicate_links: retain_on_original

risk_gates:
  active_directional_links: []
  differing_assignees:
    - issue: RHAIRFE-1567
      role_label: absorbed_issue_assignee_present   # never a real name — see section 15

# Required even for --into direct invocation, which has no
# dupes-report.yaml to serve as a baseline -- without this block,
# "changed since approval" has nothing to compare against for that
# invocation path.
approved_at: "2026-07-20T14:30:00Z"

# Pre-merge state -- what phase 2 checks before its *first* write.
approved_baseline:
  RHAIRFE-1234: {content_hash: "...", metadata_hash: "...", relationship_hash: "..."}
  RHAIRFE-1567: {content_hash: "...", metadata_hash: "...", relationship_hash: "..."}

# Each later phase intentionally changes some of the hashes above, so it
# must not recheck against approved_baseline once it's past phase 2 --
# phase 4 comparing the absorbed issue's post-link relationship_hash
# against its *pre-merge* baseline would always "detect" the Duplicate
# link phase 3 just added as an unexpected concurrent change. Each block
# below states only the fields that phase changes; an absent field means
# "still compare against approved_baseline" (or the previous phase's
# expected block, for a field changed more than once).
expected_after_survivor_update:
  RHAIRFE-1234: {content_hash: "merged", metadata_hash: "merged"}
  # relationship_hash absent: phase 2 never touches links.

expected_after_link:
  RHAIRFE-1234: {relationship_hash: "baseline-plus-all-inbound-duplicate-links"}
  RHAIRFE-1567: {relationship_hash: "baseline-plus-outbound-duplicate-link"}

# The work list phase 3 executes against, separate from the hash above.
# expected_after_link's relationship_hash is only meaningful once every
# entry below is done -- it says nothing about a state where 1 of 3
# links exists after a crash mid-phase. Phase 3 doesn't compare a
# partial state against a hash; it reads actual issuelinks, checks each
# entry here individually (does this specific correctly-oriented link
# already exist?), creates only the missing ones, and only then checks
# the final set against expected_after_link. One entry per absorbed
# issue, always -- this is what makes N-absorbed-issue proposals
# resumable after a crash between link 1 and link 2.
expected_merge_links:
  - source: RHAIRFE-1567
    survivor: RHAIRFE-1234

# NOT part of the artifact schema -- a leading underscore, by
# convention, marks this key as illustrative only. Every field inside
# is a pure composition of approved_baseline / expected_after_survivor_update
# / expected_merge_links, which are already in proposal_hash's input.
# Strict parser rule, not just a documentation note: an executor MUST
# NEVER read _derived_examples.* off disk and treat it as authoritative
# -- it MUST recompute phase_3_precheck and survivor_before_close at
# runtime from the real (hashed) fields every time they're needed. A
# conforming proposal file should not even contain this key; it's shown
# here only because "check X against expected_after_survivor_update"
# stopped being unambiguous prose once relationship_hash could ALSO
# have changed by the time of the check -- the same fallback shorthand
# that correctly meant "nothing has changed yet" for phase 2 silently
# meant the wrong thing once phase 3 started writing. If a parser ever
# encounters this key in a real file, that itself is suspicious (either
# a stale artifact from an older executor version, or tampering) and
# should be logged, not trusted.
_derived_examples:
  phase_3_precheck:
    survivor:
      content_hash: expected_after_survivor_update    # phase 2 already ran
      metadata_hash: expected_after_survivor_update
      # Not a fixed hash: 0 to N of the survivor's inbound links may
      # already exist (a resumed run partway through the loop). Valid iff
      # every relationship *delta* from approved_baseline is accounted for
      # by some entry in expected_merge_links -- anything else (e.g. an
      # unrelated Blocks link someone added mid-merge) is a real conflict.
      relationship_policy: baseline_plus_subset_of_expected_merge_links
    absorbed:
      content_hash: approved_baseline       # phase 2/3 never touch absorbed content
      metadata_hash: approved_baseline
      # Each absorbed issue has exactly one entry in expected_merge_links,
      # so its relationship state is 0 or 1 links added, never partial
      # within itself.
      relationship_policy: baseline_plus_zero_or_one_expected_merge_link

  survivor_before_close:
    content_hash: expected_after_survivor_update
    metadata_hash: expected_after_survivor_update
    relationship_hash: expected_after_link   # phase 3 already completed, checked as a fixed hash now (not a policy) -- entering phase 4 means every expected_merge_links entry is satisfied

expected_after_close:
  RHAIRFE-1567:
    # metadata_hash changes here too -- status/resolution are both in
    # metadata_hash's field list (section 7). Recomputing the *pre-close*
    # metadata_hash and finding it doesn't match post-close state isn't
    # a conflict, it's the closure working as intended; omitting this
    # field would repeat the exact bug the expected_after_* split (above)
    # exists to prevent, just one phase later.
    metadata_hash: "baseline-with-status-closed-and-resolution-duplicate"
    status: Closed        # plain-value companion to the hash, for the
    resolution: Duplicate # human-readable "confirm terminal status" check in phase 4

approved_output:
  merged_description_hash: "..."
  merged_metadata_hash: "..."
  source_mapping_hash: "..."   # binds section 10 step 6's source mapping to this proposal

  # The authoritative expected provenance content, fixed at approval time
  # -- not derived from, or compared only against, the Jira comments
  # themselves. Without this, the only "expected" value for a provenance
  # comment's content lives inside that same mutable comment (its own
  # self-reported body_sha256/payload_sha256), so an edit to a comment's
  # body and its marker's hash together would still look internally
  # consistent. These values close that: section 13's detector compares
  # against these, and only treats the completion marker's self-reported
  # payload_sha256 as a cheap first-pass duplicate check.
  survivor_provenance_payload_hash: "..."
  absorbed_provenance_hashes:
    RHAIRFE-1567: "..."

# proposal_hash = SHA-256 of the canonical JSON encoding of exactly these
# top-level keys: schema_version, proposal_id, survivor, absorbed,
# fingerprint_profile, approved_write_profile, content, metadata_policy,
# risk_gates, approved_at, approved_baseline, expected_after_survivor_update,
# expected_after_link, expected_merge_links, expected_after_close,
# approved_output.
# (_derived_examples is deliberately excluded and, per its own comment
# above, shouldn't exist in a real file at all -- it's a pure
# composition of the fields already listed, adding no new approved
# information to bind.)
#   - Object keys sorted lexicographically at every nesting level; UTF-8
#     encoding of the result.
#   - List order preserved where semantically meaningful (`absorbed`,
#     `expected_merge_links`); anything that's really a set in spirit
#     (e.g. risk_gates.active_directional_links) is sorted first, so
#     reordering it can't silently change the hash.
#   - proposal_hash itself is excluded from its own input -- the one
#     field allowed to differ between the hashed content and the file
#     that carries the hash.
#   - Any mismatch between a recomputed proposal_hash and the stored one
#     means the whole proposal is unapproved, full stop -- no field-by-
#     field partial trust.
proposal_hash: "..."
```

Human approval (section 10, step 8) means signing off on this entire
file, including every hash block — not just the policy fields above
them. Persisting `approved_baseline` at approval time, rather than
only at each phase's own refetch, is what makes "changed since
approval" a well-defined comparison instead of an implicit assumption.
The phase-specific `expected_after_*` blocks exist because a fixed
baseline stops being the right comparison the moment any phase writes
something — each write phase (section 11) states explicitly which
block it checks against, so "recheck the fingerprint" always means a
specific comparison, never an implicit one.

## 9. Metadata policies

| Field | MVP policy |
|---|---|
| Reporter | Preserve survivor; credit source reporters in provenance |
| Assignee | Preserve survivor; note differing source ownership as a suggestion, never an automatic write |
| Priority | Use highest, subject to approval |
| Components | Union |
| Labels | Union non-automation labels; apply merge labels |
| Parent | Must match |
| Status | Preserve survivor |
| Watchers | Do not migrate |
| Comments | Do not migrate; persist selected provenance instead |
| Attachments | Do not migrate |
| Non-duplicate links | Retain on absorbed issue and disclose (section 12) |
| Blocking/dependency links | Require human review before closure |

Reporter and assignee share one principle: identity is credited, never
silently reassigned. If the survivor is unassigned but an absorbed
ticket is assigned, that's surfaced as a suggestion in the merge
proposal — a human decides, the merge never writes it automatically.

## 10. Planning gate

Write-free. This is where the extra LLM step merge needs that split
doesn't — content reconciliation between conflicting acceptance
criteria — happens, producing a reviewable artifact before any Jira
write occurs (invariant 1).

1. Load candidate set (from `--into` or a `--report`/`--pair`).
2. Refetch Jira issues.
3. Verify fingerprints against the discovery report (abort/flag on mismatch).
4. Inventory links and metadata differences (section 12, section 9).
5. Reconcile description and acceptance criteria. The reconciled text
   must be phase-agnostic — describe the consolidation itself (e.g.
   "consolidates RHAIRFE-1567") rather than a completed outcome (e.g.
   "RHAIRFE-1567 has been closed as duplicate"). Phase 2 writes this
   text to the survivor before phases 3–4 run, so if either fails
   permanently, the survivor is left asserting something true and the
   absorbed ticket is left open and unmisrepresented — see the failure
   matrix (section 14).
6. Produce a source mapping: for every merged acceptance criterion, show whether it was copied, deduplicated, broadened, or reconciled from which source ticket.
7. Run `/rfe.review` on the reconciled draft.
8. Require human approval of the merge proposal artifact (section 8). Approval is also the moment step 3's fingerprints stop being a live re-checkable value and become the frozen `approved_baseline` — everything after this point compares against what was approved, not against a fresh re-read of the discovery report. **Approval is blocked while `approved_write_profile` contains a placeholder** (`PENDING_REHEARSAL`, `"..."`, or any other non-concrete value) — see invariant 17. A proposal in that state stays in `needs_human_review`; the manual rehearsal (section 16) must supply concrete values first, and `proposal_hash` is computed over those final values, not over the placeholders that preceded them.

## 11. Write phases

**Every executor invocation** — not just a fresh run starting at phase
1 — begins the same way, before state discovery decides which phase to
resume into and before any Jira write: strictly parse the proposal
file, reject any unknown reserved key (including a literal
`_derived_examples` key, which per section 8 should never appear in a
real file), reject any `approved_write_profile` value that's still a
placeholder (`PENDING_REHEARSAL`, `"..."`, or similar — invariant 17
says this should never have been approved, but the executor checks
again rather than trusting that it wasn't), verify
`fingerprint_profile.version` is a version this executor supports, and
recompute `proposal_hash`. A mismatch aborts immediately, regardless of
which phase the run is resuming into. This is deliberately separate
from, and prior to, the Jira-state checks
below: a crash can leave a resume starting directly in phase 3 or 4,
which derive issue keys, `expected_merge_links`, expected hashes, and
closure expectations straight from the proposal — trusting any of that
without first confirming the proposal file itself hasn't been
hand-edited since approval would make every downstream check
meaningless, no matter how carefully it compares Jira state.

Once the proposal itself is known-good, validation of *Jira* state
happens three more times, not once — invariant 4 says "immediately
before mutation," and phase 1 is already a mutation (it posts Jira
comments), so a single check positioned only before phase 2 would let
phase 1 write provenance based on Jira state that's already stale:

1. **At phase 1 start**: four different targets, four different
   fingerprints — `approved_baseline` covers Jira issue state only, not
   local files or the proposal itself:
   - Proposal file → `proposal_hash`
   - Merged description (`content.merged_description_path`) → `approved_output.merged_description_hash`
   - Source mapping (`content.source_mapping_path`) → `approved_output.source_mapping_hash`
   - Survivor and **every** absorbed issue in Jira → `approved_baseline`, not only whichever single issue a naive per-issue loop happens to be processing

   The reconciled description was built from *all* absorbed issues
   together; a change to any one of them can invalidate the whole
   proposal's reconciled output, not just that one issue's row. See the
   failure matrix (section 14) for how this differs from independent
   proposals in the same batch.
2. **Immediately before posting each individual provenance comment**:
   recheck that specific issue again. Time passes while phase 1 works
   through the survivor and every absorbed issue one at a time; a
   change landing between the phase-start check and this particular
   issue's write is exactly what "immediately before mutation" is
   there to catch, and the phase-start check alone can't.
3. **Before phase 2**: the check that already existed — same scope as
   #1, immediately before the first non-comment write.

**Phase 1 — Persist provenance**
Post idempotency-marked provenance comments (section 13), each
preceded by its own immediate recheck (#2 above). Deliberately *not* a
full copy of every absorbed description — only the survivor's own
content is being overwritten (phase 2), so only the survivor's
pre-merge state is at risk of being lost:

- **Survivor**: a provenance comment preserving its pre-merge
  description verbatim, plus the source keys, their fingerprints, and
  the section 10 step 6 source mapping (which merged criterion came
  from where).
- **Absorbed issues**: a concise comment naming the survivor and
  proposal ID, plus the retained-third-party-links disclosure section
  12 requires — a pointer, not a copy. The absorbed issue's own
  description is never modified, so it remains its own authoritative
  historical record; duplicating it into a comment would just be
  noise (and a needless comment-size problem — see below), and
  potentially exposes that ticket's content to whoever is watching
  the survivor. If the manual rehearsal (section 16) finds that closed
  tickets aren't visible enough for this to be sufficient, that's a
  reason to revisit copying more into the pointer comment, not a
  reason to default to full duplication now.

Only the survivor's provenance comment risks exceeding Jira's comment
size limit (since it can carry a full original description). If it
does, split across multiple sequentially-marked comments rather than
truncating. The exact limit is an implementation detail to confirm
against this instance, not a design decision to make speculatively
here.

**Phase 2 — Update and verify survivor**
Recheck the survivor's fingerprint against `approved_baseline`
immediately before writing — this is the last point where the
*pre-merge* state is the right comparison. Apply the merged
description and metadata policy. Refetch and hash-verify the result
against `expected_after_survivor_update` — a 200 on the PUT is not
sufficient proof (invariant 5).

**Phase 3 — Link and verify**
Before writing: recheck against `phase_3_precheck` — computed at
runtime from `approved_baseline`/`expected_after_survivor_update`/
`expected_merge_links` per section 8's `_derived_examples` note, never
read off disk as a stored value — not a plain
`expected_after_survivor_update`/`approved_baseline` check. Those
two blocks describe relationship_hash as "whatever it was before this
phase touched it," which is exactly wrong here: on a resumed run, the
survivor and one or more absorbed issues may already have some of
their intended links, and a check that expects zero links changed
would reject that legitimate partial progress before the per-link loop
below ever runs. `phase_3_precheck`'s `relationship_policy` is what
correctly allows "some subset of `expected_merge_links` already
applied, nothing else" instead of "nothing changed at all."

Iterate `expected_merge_links` (section 8), not a single "create all
links" step — with more than one absorbed issue, phase 3 is several
independent POSTs, and a crash after the first link but before the
second must be resumable without either re-creating the first link or
getting stuck because the overall state matches neither
`approved_baseline` (one link now exists) nor `expected_after_link`
(the rest don't yet):

1. Refetch actual `issuelinks` on the survivor and every absorbed issue
   once, to classify which `expected_merge_links` entries already
   exist. This read only decides what work remains — it doesn't
   authorize any write, since it's already stale by the time the loop
   below gets to entry 2 or 3.
2. For each entry still missing, immediately before creating *that*
   link (not once for the whole batch — with more than one absorbed
   issue, another edit can land between entry 1's POST and entry 2's,
   and "immediately before mutation" means immediately before *this*
   mutation):
   1. Refetch the survivor and that specific absorbed issue again.
   2. Revalidate content_hash/metadata_hash against `phase_3_precheck`.
   3. Revalidate relationship state is still baseline-plus-an-allowed-
      subset (`phase_3_precheck`'s `relationship_policy`), then check
      specifically whether *this* intended link now exists (it may
      have appeared since step 1's read — another process, a human,
      or a retry from an earlier crash):
      - **If it already exists** (correctly oriented): mark this
        entry complete. Do not POST.
      - **If it doesn't**: continue to step 4.
   4. Create the link using `approved_write_profile.duplicate_link`'s
      pinned `type_id` and `request_orientation` — not a value
      re-derived from a fresh capability probe. If the actual Jira
      instance rejects that exact type/orientation (the link type was
      renamed, removed, or its behavior changed since approval), that
      is a capability-profile mismatch: abort this proposal rather
      than falling back to guessing a different orientation.
   5. Refetch both issues and verify this specific link exists,
      correctly oriented — regardless of what step 4's response said.
      Invariant 5 says a 200 isn't proof; the symmetric case holds
      too, an error response isn't proof of failure either (the link
      may have been created by a racing process even though this
      call reported "already exists" or failed for some other
      reason). The refetch in this step is what decides success, not
      the create call's return value. Whether creating the same link
      twice in a race is itself safe (does it error, no-op, or
      duplicate?) is exactly `link_creation_idempotent` — still
      pending the manual rehearsal (section 5, section 19) — so this
      design doesn't assume an answer either way; it just says the
      final Jira state is authoritative, never the POST outcome.
3. Only after every entry is satisfied, refetch once more and confirm
   the *complete* set matches `expected_after_link`'s relationship
   hashes — that hash describes the finished state, not any
   intermediate one, so it's the last check, not a per-link check.

Jira's relationship state is authoritative throughout, exactly as
section 13 already establishes for comments: a durable marker says a
link phase started, but only a fresh `issuelinks` read says which
specific links actually exist.

**Phase 4 — Close absorbed issues and finalize**
For each absorbed issue, classify its actual state *before* deciding
what to do — the same principle Phase 3 now uses for links (check
which bucket you're in, then branch), not an unconditional pre-close
check applied to every issue regardless of where it actually is. An
unconditional "recheck against the pre-close composite first" would
reject the exact recovery scenario the failure matrix already
promises: if absorbed issue A closed successfully, absorbed issue B
failed, and the process then crashed, a replay must recognize A as
already-done and only retry B — not reject A for no longer matching
its pre-close fingerprint, which is precisely what closing it on
purpose was supposed to change.

1. Refetch the absorbed issue's actual state.
2. **If it matches `expected_after_close`**: closure is already
   complete. Verify the correct `Duplicate` link still exists, verify
   the survivor matches `survivor_before_close`, and verify both
   provenance comments. If the survivor-side `phase=closed` marker for
   this issue is missing — a prior crash closed the issue but didn't
   get as far as the marker — refetch the survivor once more and
   reconfirm it still matches `survivor_before_close` immediately
   before posting it: that comment is itself a Jira
   mutation, and everything else in this design rechecks immediately
   before a write rather than trusting a read from earlier in the
   same pass. Do not transition again.
3. **Else if it matches the pre-close composite** (content_hash and
   metadata_hash from `approved_baseline` — untouched — combined with
   relationship_hash from `expected_after_link` — phase 3's completed
   work): verify the survivor matches `survivor_before_close` — again
   computed at runtime per section 8's `_derived_examples` note, not
   stored — verify both provenance comments and the link exist, then
   **refetch the absorbed issue one more time, immediately before
   transitioning it**, and confirm it still matches the pre-close
   composite. The verifications above can take time; invariant 4
   means immediately before *this* mutation, not immediately before
   the classification read from step 1 — the same reasoning that
   already governs each individual link creation in phase 3. Then
   transition it using `approved_write_profile.closure_transition`'s
   pinned `id`/`resolution_id` — not a value re-derived mid-execution
   — refetch to confirm the result matches
   `expected_after_close`, and abort this proposal (not guess a
   different transition) if the actual Jira instance rejects that
   exact payload. Then — same principle, same write —
   refetch the survivor once more and reconfirm it still matches
   `survivor_before_close` immediately before posting the
   survivor-side `phase=closed` marker. Then update local artifacts
   and rebuild snapshots/index.
4. **Else**: abort this proposal and surface for human review. Neither
   block explains this issue's actual state — the same
   unexpected-concurrent-state posture the failure matrix already
   takes elsewhere, not something phase 4 should guess about.

`survivor_before_close` matters here specifically because
`expected_after_survivor_update` alone says nothing about
relationship_hash — checking against it instead would leave phase 3's
own completed links looking like an unexplained change. By phase 4,
`expected_merge_links` is fully satisfied, so the survivor's
relationship state is a fixed target again, not the moving one
`phase_3_precheck` had to allow for.

The central invariant across all four phases: **no absorbed issue is
closed unless the survivor contains the approved merged content,
provenance is durable, the relationship is visible in Jira, and
neither issue has changed since approval — checked against the
phase-appropriate expected state (section 8), never against a single
fixed baseline once any phase has written something.**

## 12. Non-duplicate-link behavior

Broken out from section 9 because it's a safety gate, not just a
metadata default.

The gate is driven by the probed `link_type_inventory` (section 5), not
a hardcoded name list — this instance has two link types that are
unambiguously dependency-shaped (`Blocks`, `Depend`) plus two more
that are dependency-*flavored* without being certain (`Causality`,
`Triggers`), so the policy has three tiers, not two:

- `dependency_shaped` links (`Blocks`, `Depend` on this instance): forces `needs_human_review` — a blocking or dependency relationship hidden behind a closed ticket is an active risk, not a cosmetic loss.
- `review_recommended` links (`Causality`, `Triggers` on this instance): does not force `needs_human_review` by itself, but is surfaced with `requires_acknowledgement: true` — plausibly dependency-flavored, not confirmed as blocking.
- Everything else (e.g. plain `Related` — confirmed the actual type name on this instance, not "Relates"): warning only, `requires_acknowledgement: false`, may proceed after approval.
- Parent/hierarchy relationships: require exact policy compatibility (section 6's "compatible parent relationship" requirement).
- Links are never silently migrated to the survivor in the MVP.
- Provenance comments on the absorbed issue list every retained third-party relationship explicitly, so it's discoverable even after closure.
- This instance also has a `Work item split` link type (10120) that `/rfe.split` doesn't use today — see section 5's `link_type_inventory.other_notable` for why that's a live question for merge's own link convention.

Example inventory entry from the planning gate:

```yaml
absorbed_links:
  - source: RHAIRFE-1567
    type: Blocks
    tier: dependency_shaped
    direction: outward
    target: RHAIRFE-1702
    policy: retained_on_absorbed
    requires_acknowledgement: true

  - source: RHAIRFE-1567
    type: Causality
    tier: review_recommended
    direction: outward
    target: RHAIRFE-1899
    policy: retained_on_absorbed
    requires_acknowledgement: true

  - source: RHAIRFE-1567
    type: Related
    tier: informational
    direction: inward
    target: RHAIRFE-1401
    policy: retained_on_absorbed
    requires_acknowledgement: false
```

## 13. Recovery model

Durable, idempotency-marked comments plus a state detector that infers
progress from actual Jira state, not just comment text:

```text
[RFE Creator Merge:v1] proposal=<proposal-id> phase=survivor-provenance part=1 total=3 body_sha256=<hash-of-chunk-1-payload>
[RFE Creator Merge:v1] proposal=<proposal-id> phase=survivor-provenance part=2 total=3 body_sha256=<hash-of-chunk-2-payload>
[RFE Creator Merge:v1] proposal=<proposal-id> phase=survivor-provenance part=3 total=3 body_sha256=<hash-of-chunk-3-payload>
[RFE Creator Merge:v1] proposal=<proposal-id> phase=survivor-provenance-complete payload_sha256=<hash-of-reassembled-payload>
[RFE Creator Merge:v1] proposal=<proposal-id> phase=absorbed-provenance source=RHAIRFE-1567 body_sha256=<hash-of-pointer-payload>
[RFE Creator Merge:v1] proposal=<proposal-id> phase=survivor-updated
[RFE Creator Merge:v1] proposal=<proposal-id> phase=linked source=RHAIRFE-1567
[RFE Creator Merge:v1] proposal=<proposal-id> phase=closed source=RHAIRFE-1567
```

`survivor-provenance` is split into `part`/`total` markers because
phase 1 may chunk the survivor's provenance comment across several
Jira comments (section 11) — a crash after posting chunk 1 of 3 must
not read as "provenance done." Marker presence alone only proves a
comment *matching the marker pattern* exists at each expected position
— it says nothing about whether that comment's actual content still
contains the historical description invariant 3 requires, since the
survivor's real Jira description is overwritten in phase 2 and can no
longer independently prove the copy is intact.

The hashing algorithm, precisely (per `fingerprint_profile.
provenance_canonicalization`/`provenance_chunking`, section 8):

1. `rfe-merge-provenance-v1`'s canonical comment layout, pinned so two
   conforming implementations produce identical hashes rather than
   merely "some consistent choice":
   - Text is UTF-8. Before anything else, normalize all line endings
     to `\n` (a `\r\n` or bare `\r` fetched from Jira is converted,
     not treated as a hash input difference) — call the result
     `canonical_payload`. Whatever trailing newlines `canonical_payload`
     genuinely has (including none, or several, from a description
     that itself ends in blank lines) are preserved exactly as part
     of it — invariant 3 requires the original content back verbatim,
     blank lines included, not a lossy round-trip.
   - The posted comment body is always `canonical_payload + "\n" +
     marker line` — marker **last**, always, and the separator is
     *exactly one mechanically appended* `\n`, never conflated with
     any newline that was already part of `canonical_payload`.
   - `body_sha256` is `SHA-256(UTF-8(canonical_payload))` — hashed
     *before* the separator and marker are appended, so the marker
     that states the hash is never part of what it hashes (hashing
     the posted comment as a whole would be self-referential and
     impossible to construct correctly).
   - Recovering `canonical_payload` from a fetched comment means:
     remove the final line matching the `[RFE Creator Merge:v1] ...`
     pattern, then remove exactly the one separator `\n` that
     precedes it — nothing else is trimmed. Any newlines before that
     point are part of `canonical_payload` and stay untouched.
   - A comment body containing more than one line matching the marker
     pattern is rejected as corrupted or tampered, not resolved by
     guessing which one is authoritative.
2. On recovery, fetch every comment's actual current body, apply the
   same normalization and stripping rules to recover the payload
   fragment, and recompute each part's hash from it. Compare against
   that part's `body_sha256`.
3. Reassemble the payload fragments in numerical `part` order (not
   Jira comment creation order, which the API doesn't guarantee) and
   recompute the full payload hash.
4. Compare the result against **`approved_output.
   survivor_provenance_payload_hash`** (section 8) — not merely
   against the completion marker's own self-reported `payload_sha256`.
   The marker's value is a cheap first-pass duplicate for a quick
   check; the proposal-stored hash is authoritative, because it's the
   one value in this whole chain that isn't sitting inside a mutable
   Jira comment next to the content it's supposed to verify. Without
   an external anchor, an edit to a comment's body and its marker's
   hash together would still look internally consistent — comparing
   only within the comment can't catch that.

Provenance counts as complete only when every `part` (1..`total`)
*and* the `-complete` marker are present, every `body_sha256` matches
its comment's recomputed payload hash, and the reassembled result
matches `approved_output.survivor_provenance_payload_hash`. An
unchunked proposal still posts `total=1` plus the `-complete` marker,
so the detector never special-cases the common case.

`absorbed-provenance` doesn't chunk — it's a short pointer comment
(section 11), not a copy of anything description-sized — but it still
carries meaningful content (the survivor key, the proposal ID, and the
retained-third-party-links disclosure from section 12), so it gets the
same treatment at smaller scale: one `body_sha256`, checked against
`approved_output.absorbed_provenance_hashes[<issue key>]`. Marker
presence alone doesn't prove that content is still correct any more
than it did for the survivor's.

The `phase=closed` marker is posted on the **survivor**, not on the
absorbed issue it names — some Jira workflows prohibit adding comments
to an already-closed ticket, so the marker needs a location that's
guaranteed writable regardless of the absorbed issue's post-close
comment policy. The survivor accumulates one `phase=closed` marker per
absorbed issue, each naming its `source`.

The state detector reconciles both signals: the presence of these
markers *and* the issue's actual fields (description content hash,
`issuelinks`, status/resolution) — and, for the provenance markers
specifically, each comment's recomputed payload hash against
`approved_output`'s authoritative values (never merely against the
marker's own self-reported hash, per the algorithm above). Comments
alone are insufficient — if a comment claims `phase=survivor-updated`
but the description hash doesn't match the proposal's merged content,
the phase is treated as incomplete and re-run, matching split's
`discover_state()` pattern.

## 14. Failure matrix

A **proposal** is one survivor plus all of its absorbed issues — they
share one reconciled description (section 10), so a change to any one
of them can invalidate the whole proposal's output, not just that
issue's row. A **batch** is multiple independent proposals processed
in one command invocation (e.g. several `--pair` values from the same
discovery report); proposals in a batch don't share reconciled output,
so one proposal's invalidation doesn't touch another's.

| Scenario | Expected behavior |
|---|---|
| Survivor changed after approval | Abort before any write; require re-approval |
| Any absorbed issue changed after approval | Abort the **entire proposal** before any write — not just that issue's row, since the reconciled description was built from every absorbed issue together and may now be stale. Other **proposals** in the same batch are unaffected. |
| Provenance comment succeeds, update fails | Resumable — rerun re-posts nothing (idempotency marker present), proceeds to update |
| Survivor update succeeds, verification differs | Treat as failed; do not proceed to linking; surface for human review |
| Duplicate link already exists | Treat as success (verified by refetch, not by POST response) |
| Link succeeds, closure fails | Resumable — rerun skips link creation, proceeds to closure |
| One of multiple absorbed tickets closes and another fails | Per-issue phase tracking; first ticket's closure is not undone, second is retried independently |
| Required resolution unavailable | Abort the proposal; surface as a capability-profile gap, not a per-run error |
| Transition unavailable from current status | Abort the proposal and refresh the capability profile (section 5) before retrying; given the closure transition was observed as status-independent within the Feature Request workflow across 3 samples, an unavailable transition now points at *some* kind of drift — a workflow-scheme edit, a changed permission, an issue-specific condition/validator, or a different workflow mapping than sampled — not routine status variance. Don't assume which one; refreshing the profile is cheaper than guessing. |
| Blocking link appears after approval | Treat as newly discovered risk gate; downgrade to `needs_human_review`, do not close |
| Jira accepts a request but resulting state differs | Never trust the response code alone (invariant 5); refetch, and treat mismatch as failure requiring investigation |

## 15. PII and fixture policy

Extends this repository's existing `eval/dataset/` PII rule to every
merge-related artifact:

- Probe output is identity-redacted by default — no flag disables it (`design-proposals/rfe-merge-capability-probe.py`).
- No account IDs, email addresses, display names, avatars, customer names, or other personal identifiers in committed fixtures, design examples, or the probe output.
- Use role labels instead — e.g. `survivor_reporter_present`, `absorbed_issue_assignee_present`.
- Manual-rehearsal captures (section 16) must be sanitized before entering the repository.
- Raw rehearsal captures remain local and should be deleted once fixtures are derived from them.

## 16. Manual rehearsal fixture

Before writing the executor, one real duplicate pair should be merged
by hand using the exact transition/resolution/link orientation the
probe discovers — this is the merge analog of the wiki-vs-ADF
discovery PR #28's author made on the read path. Capture it as:

```yaml
sample:
  survivor: RHAIRFE-XXXX
  absorbed: RHAIRFE-YYYY

operations:
  - operation: comment_provenance
    result: success
  - operation: update_survivor
    fields_accepted: [summary, description, labels, components]
  - operation: create_link
    link_type: Duplicate
    direction: absorbed_duplicates_survivor
  - operation: close_absorbed
    transition_id: "..."
    transition_name: Closed
    resolution: {id: "...", name: Duplicate}

observations:
  comments_visible_after_close: true
  original_reporter_can_access_ticket: true
  watchers_preserved_on_absorbed_ticket: true
```

Record request payloads, sanitized responses, transition IDs/names,
required transition fields, resulting issue appearance, whether a
rerun duplicates the link or comment, and whether the closed ticket
remains searchable/accessible.

## 17. Repository-layout compatibility

Implementation paths are provisional pending PR #115:

```text
Current layout:
  scripts/...

Compatible packaged layout (if #115 lands):
  .claude/skills/rfe.merge/scripts/...
```

Rules that make either layout work without rework:

- Skill scripts resolve resources relative to their own directory, never a hardcoded `scripts/` root.
- No imports from sibling skill directories.
- `/rfe.dupes` and `/rfe.merge` communicate only through `dupes-report.yaml` (invariant 11) — never a Python import.
- Any shared utility copies expose a narrow, stable interface, so duplicating them (per #115's pattern) doesn't create drift.
- A layout migration must never change artifact schemas or Jira write semantics.

## 18. Test strategy

- Pure unit tests for reconciliation and fingerprinting.
- Sanitizer tests specifically covering nested user objects (reporter/assignee inside issue payloads, editmeta, and transition `allowedValues`).
- Emulator integration tests (jira-emulator, per PR #93's precedent) for the full write path.
- Replay/idempotency tests — rerun a partially-completed merge and assert no duplicate comments/links/closures.
- Link-direction tests against the probed `issueLinkType` orientation.
- Transition-field and resolution tests against the probed capability profile.
- Concurrent-edit tests (fingerprint mismatch mid-merge).
- Partial-failure recovery tests, one per row of the failure matrix (section 14).
- PII scanning of committed fixtures.
- Manual real-instance acceptance checklist (section 16), run once per capability-profile change.

## 19. Open questions

Restricted to facts the probe/rehearsal must supply — everything else
above is a decision, not an open question. Split by which artifact
resolves them, since the probe is read-only and re-runnable while the
rehearsal is a one-time real write.

**Resolved by the 2026-07-20 probe run** (section 5 has full detail):
duplicate link name/labels, closure transition name/ID and resolution
requirement, whether `Duplicate` is an allowed resolution, whether
transition IDs are status-dependent (they're not, across the 3 sampled
statuses), and reporter/assignee/components/labels/priority/parent
editability. Removed from the open list below — see section 5 rather
than re-deriving them.

**Still open, from the probe** (`design-proposals/rfe-merge-capability-probe.py`, more read-only calls, no rehearsal needed):

- Which link types and how much assignee/reporter variance actually appear on representative RHAIRFE tickets at a larger sample size than n=2 (informs whether the risk-gate thresholds in section 12 need tightening — the current sample showed `Related` only).
- Whether editability and the global transition set hold across statuses beyond the 3 sampled (New, Stakeholder review, Closed) — e.g. Draft, Rejection Pending — or whether those 3 happened to be representative by chance.

**Resolved 2026-07-20** (moved out of the open list, into section 5's `merge_link_candidates`): whether already-closed RHAIRFE duplicates use `Duplicate` or `Polaris merge work item link` — two JQL count queries returned 0 vs. 50+, confirming `Duplicate` as the real, actively-used convention.

**From the manual rehearsal** (section 16) — not answerable read-only:

- Whether the discovered transition payload actually succeeds as documented (`verified_transition_payload`).
- Whether link creation is observably idempotent (does a second POST of the same link error, no-op, or duplicate?).
- Closed-ticket visibility and notification behavior — does the original reporter retain access, are comments still visible, are watchers notified on close?
