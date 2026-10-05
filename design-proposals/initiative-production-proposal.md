<!-- Draft 2026-10-05 for AISDLC-183/184/205/206: facts checked against rfe-creator main 502f6dc, the pinned assess-rfe rubric and read-only Jira queries on 2026-10-05; placeholders in angle brackets. -->

# Initiative Creator in RHOAIENG: proposal and decisions for the Initiative owners

**To:** <owner names>, RHOAIENG Initiative owners  **From:** <our names>  **Answer by:** <date>
**Tracking:** AISDLC-184 (production, opt-in by label) and AISDLC-183 (review of the questions, template and rubric with you). Decisions go on AISDLC-205 (label), AISDLC-206 (policy), AISDLC-207 (first live results).

## 1. Context

Since 31 March 2026, RFE Creator has reviewed every new or changed RFE in RHAIRFE twice a day (03:00 and 15:00 UTC; 376 runs so far). It scores each description against five quality criteria, checks technical feasibility against the RHOAI architecture documentation, rewrites the description when the review asks for it, splits an RFE that bundles unrelated requests, and records the result on the ticket: labels, a comment signed "[RFE Creator]" and, when both checks pass, a move to Approved. On 4 October (03:10 run): 8 RFEs, 7 passed, 1 split into 3.

The same workflow now supports RHOAIENG Initiatives. It has run offline and, twice, by hand: RHOAIENG-83253 (12 August, still New, carrying the tool's labels; tell us if they should come off) and RHOAIENG-90167 (1 September, since closed as a duplicate) were created from local drafts with it. No scheduled job has touched RHOAIENG and no existing Initiative has been edited. On a test set an independent judge rated the Initiatives it produces at about 4.3/5 and its revisions at about 4.8/5. Its review content (questions, template, rubric, split rules) has never been checked by the people who own Initiatives. Nothing further in RHOAIENG is touched until you opt an Initiative in by label, and the first period is a dry run: it reads, it reports, it writes nothing.

## 2. What an opt-in Initiative would experience

**You opt in** by adding the agreed label (D1). Remove it, or add `initiative-ignore`, and the tool leaves the Initiative alone from the next run.

**Dry-run period (D2).** Each run reads the Initiative's summary, description, priority, labels and status; scores it; writes a feasibility assessment; drafts a revised description when the review asks for one and re-scores it. All of this stays in a review report shared with you (<link to report>). Nothing is written to Jira: no label, no comment, no edit, no status change, no new issue.

**Live, auto-approve off (our recommendation, D3).** The tool may:

- **Add labels:** `initiative-autofix-rubric-pass` on a pass; `initiative-feasibility-pass`, `-fail` or `-unknown` (a stale one is removed when the verdict changes); `initiative-needs-attention`; `initiative-auto-revised`; `initiative-alignment-strong`, `-partial` or `-weak` when that check ran; `initiative-split-quarantine` if a split fails part-way (a person removes it). On a reject, the rubric-pass and feasibility labels it set earlier come off. Whatever its labels, a reviewed Initiative is not picked up again until its description changes (the tool compares a content hash).
- **Post a comment** starting with `[Initiative Creator]`: either "This Initiative has been flagged for human review:" plus the reason, or, after a revision, the implementation details removed from the description, kept for reference.
- **Rewrite the description**, only when the review fails it (below 7/10 or any zero) and only with re-scored text; the previous text stays in Jira's history, and the tool never invents evidence (a gap is marked `[NEEDS: ...]` and flagged).

It would **not** change the status (RHOAIENG's Initiative workflow has no Approved status, see D4) and would **not** post the approval comment RFEs receive. Two things to close before going live: the hold that keeps back a rewrite whose re-scoring did not finish is tied to the auto-approve switch today, and a split is not held back by that switch at all (when Right-sized scores 0/2, the RFE workflow creates child issues, copies their text as comments on the parent and closes the parent as Obsolete). We will untie the first and add a second so that a live Initiative run only reports "would split" until you have agreed (D3).

With auto-approve on (what RFEs get; not proposed now) a passing, feasible Initiative is moved to the agreed status with a comment saying so and noting that approval is not a customer commitment.

## 3. Review packet for AISDLC-183: please confirm or change

### 3a. The five criteria (0-2 each; pass = total >= 7/10 and no zero)

| Criterion | One-line definition | Scores 0 when |
|---|---|---|
| **WHAT** | A reader knows exactly what will be delivered and what changes. | Vague or absent ("improve performance"). |
| **WHY** | What is broken or missing today, with evidence: pain statements, metrics, incidents, competitive gaps, cost, or a mandate with a causal chain. Stated evidence is taken at face value. | No evidence, or circular ("we need it because we lack it"). |
| **Scope** | What is in and what is out; phasing language counts; formal In/Out sections optional. | No boundaries. |
| **Open to HOW** | Leaves implementation choices to engineering. Naming established RHOAI technologies (KServe, vLLM, Kueue, ...) or the thing being integrated is fine; mandating a choice where alternatives exist is not. | Mandates architecture or technology. |
| **Right-sized** | One coherent effort: one goal, one team or related group. Breadth is not a reason to split; when uncertain the score is 1, not 0. | Bundles separate initiatives with different goals, ownable by unrelated teams. |

Outcome of a review: **pass** (>= 7, no zero), **revise** (fails but fixable), **split** (Right-sized 0 and no other zero), **reject** (3+ zeros or fundamentally infeasible).

> **Please confirm / change:** the five criteria and the "7/10 and no zero" bar. In particular: is WHY's evidence bar right for internal or process Initiatives, and should Right-sized be applied to container Initiatives (release activities, "tech debt" buckets)?

### 3b. Clarifying questions (only when the tool drafts a new Initiative from a one-line objective)

1. **What is the objective?** A specific outcome ("reduce model serving latency by 50% for batch inference"), not "improve performance".
2. **What problem does this solve?** What is broken or missing today, with evidence if available.
3. **What is the scope boundary?** What is explicitly in and out.
4. **Is there a parent Outcome?** Which RHAISTRAT Outcome it rolls up to, if any.

Rules: at most 2-5 questions, only for what cannot be inferred; never about epics or tasks; priority uses the Jira values (Blocker, Critical, Major, Normal, Minor).

> **Please confirm / change:** the four questions. Missing anything you always want asked (owning team, target release, success measures)?

### 3c. Template

- **Objective**: what is delivered and why it matters.
- **Problem Statement**: what is broken, missing or insufficient, with concrete evidence.
- **Scope**: what is included and, optionally, what is explicitly not.

Guidance: describe outcomes, not architecture; keep to one coherent effort; prose is fine, headings are optional and scoring does not depend on formatting.

> **Please confirm / change:** the three sections. Note: the split and feasibility rules refer to "success criteria", "timeline" and "release targets", which the template does not ask for. Should those become template sections?

### 3d. Strategic alignment check (runs only when the Initiative's Parent is a RHAISTRAT Outcome)

- Compares the Initiative with its parent Outcome on three points: objective advancement, scope consistency, success-criteria contribution.
- Verdicts: **strong** / **partial** (default when uncertain) / **weak**. Weak flags the Initiative for human review; the verdict never blocks anything.
- Reality check: 2 of the 197 open Initiatives have a RHAISTRAT Outcome as Parent, and both are In Progress (out of scope under D6); 15 more have an Outcome that lives in RHOAIENG as Parent, which this check ignores; and the tool does not yet read the Parent field when it fetches an Initiative from Jira. In production this check would currently run for none of them.

> **Please confirm / change:** do you want this check at all? If yes, would owners set Parent on their Initiatives, and we wire the Parent field in before the first run.

### 3e. Technical feasibility check

- Five questions: technically feasible on this platform? any architectural incompatibility? realistic as one planning unit (one strategy feature)? dependencies realistic and on track? hidden complexities (migration, multi-tenancy, cross-team coordination)?
- Grounded in the current RHOAI architecture documentation; a capability that does not exist yet is **not** a blocker; a named component that does not exist is a note for execution, not a blocker.
- Verdicts: **feasible** / **infeasible** (the platform's design conflicts with the approach) / **indeterminate** (too ambiguous to assess). Also: scope **appropriate / needs splitting / unclear**; dependencies **realistic / at risk / unclear**; a list of execution considerations for epic planning.
- Infeasible or indeterminate flags the Initiative for human review; only "feasible" could ever qualify for an automatic approval.

> **Please confirm / change:** is "rubric pass + feasible" the right bar? Are there Initiative kinds (outreach, release activities, organisational work) where an architecture-based check is meaningless and should be skipped?

### 3f. Split rules

- Considered only when Right-sized scores 0/2 and no other criterion is 0; a 1/2 ("arguably separable but coupled") does not split unless the parts serve genuinely different objectives.
- Never split delivery-coupled work (must ship together, shared critical path) or breadth under one mission.
- Work already delivered is acknowledged as context, not re-planned; only gaps become children.
- Each child must stand alone (own objective, problem statement, scope and success criteria); a coverage check ensures nothing from the parent is lost.
- On a live split (RFE behaviour): each child's text is first copied as a comment on the parent; children are created with the parent's priority by default, its Parent, components, reporter and non-automation labels, plus `initiative-auto-created`, `initiative-split-result`, a provenance label `initiative-split-child-<parent>-<child>-<hash>` and the verdict labels from the child's own review; each creation is confirmed by a comment on the parent; the parent is labelled `initiative-split-original`, linked to the children ("Work item split"), closed as Obsolete and given a summary comment.

> **Please confirm / change:** the 0/2 trigger, and whether a live split should create issues at all, or only post the proposed children as a comment for you to act on.

### 3g. Revision rules

- WHAT: make the objective concrete. WHY: add evidence or mark `[NEEDS: specific metrics/evidence]`, never invent it. Scope: make boundaries explicit. Open to HOW: reframe mandates as suggestions ("such as", "one approach would be") rather than deleting them. Right-sized: do not narrow in place; flag for split instead.
- Reframe, do not remove; removed implementation details are preserved in a comment on the ticket.

> **Please confirm / change:** may the tool rewrite your description at all (versus posting its suggestions as a comment)?

## 4. Decisions

**D1. Opt-in label.** Every label the tool puts on an Initiative starts with `initiative-`; the pass label is `initiative-autofix-rubric-pass`. RFEs have no opt-in label (all are in scope; `rfe-creator-ignore` opts out). Options: (a) `initiative-autofix-opt-in`; (b) `initiative-autofix`; (c) `initiative-creator-review`. **Recommendation: (a)**, it says what it does and matches the labels the tool adds.

**D2. Dry-run length and sharing.** Options: (a) one week; (b) two weeks or ten reviewed opt-in Initiatives, whichever is later; (c) open-ended until the content review is signed off. **Recommendation: (b)**, one run per day; after each run a report link and a five-line summary on AISDLC-206 (or <Slack channel>); each opt-in owner receives their Initiative's review.

**D3. What the tool may write live.** Options: (a) labels only; (b) labels and comment; (c) plus the description revision when the review asks; (d) plus a status transition; (e) plus splits. **Recommendation: (c)**; no transition and no splits until the first live results are reviewed (D5). Please also confirm the comment signature `[Initiative Creator]` (AISDLC-206 records it).

**D4. Transition target.** Fact: a RHOAIENG Initiative can move to New, Backlog, In Progress, Review, Testing, Resolved or Closed; there is no Approved, and no RHOAIENG issue is in one. The tool is configured for "Approved" today and would log a warning and skip; your answer replaces that before the first live run. Options: (a) none, the rubric-pass label is the signal; (b) Backlog, meaning "reviewed, ready for planning", from New only; (c) a new status (Jira admin change). **Recommendation: (a)** now; revisit (b) after the live review.

**D5. Who reviews the first live results, where.** Options: (a) each opt-in owner on their ticket; (b) two named reviewers, <owner names>, with us for 30 minutes after live runs 1 and 2, notes on AISDLC-207; (c) asynchronous only. **Recommendation: (b) plus (a).**

**D6. Statuses in scope besides the label.** Of 197 open Initiatives: 117 New, 16 Backlog, 63 In Progress, 1 Testing. Options: (a) any open status; (b) New and Backlog only. **Recommendation: (b)**: an Initiative being executed should not have its description rewritten by a tool.

## 5. Proposed timeline

| When | What |
|---|---|
| Week of <date 1> | Your answers to D1-D6 on AISDLC-206; reviewers named. |
| Week of <date 2> | 60-minute walk-through: three to five of your Initiatives scored in front of you; label created; first Initiatives opted in. |
| Weeks <date 2> to <date 4> | Daily dry run; report after each run; wording changes land. |
| Week of <date 4> | Dry-run review; sign-off on questions, template and rubric; we re-baseline our offline evaluation on the agreed content; go or no-go. |
| Week of <date 5> | First live run under D3; reviews after runs 1 and 2. |
| Weeks <date 6-7> | Keep, lift or adjust the opt-in label (AISDLC-207). |

## 6. What we need from you this week

1. Two names: who decides, and who reviews the first live results.
2. Answers to D1-D6 (one line each on AISDLC-206) by <date>.
3. Three to five Initiatives to opt in. Candidates we saw: RHOAIENG-97020, RHOAIENG-97456, RHOAIENG-98536 (recent, status New, substantive descriptions); confirm or replace.
4. A 60-minute slot in the week of <date 2> for the walk-through.
5. Whether you can open <link to results repository>, or prefer the report attached to the Jira issue.

## Appendix: where the content lives

All in the `opendatahub-io/rfe-creator` repository unless noted.

- Clarifying questions and writing rules: `types/initiative/prompts/create-guidance.md`
- Template: `types/initiative/template.md`
- Review, revision and split rules: `types/initiative/prompts/review-rules.md`, `revise-rules.md`, `split-rules.md`
- Alignment and feasibility checks: `types/initiative/dimensions/alignment.md`, `feasibility.md`
- Labels, comment marker, status names: `types/initiative/type.yaml`
- Scoring rubric: `opendatahub-io/assess-rfe`, `skills/assess-initiative/scripts/agent_prompt.md`
- What a live run writes to Jira: `scripts/submit.py`, `scripts/split_submit.py`, `scripts/jira_utils.py`
- Offline evaluation: `eval-initiative.yaml`, `types/initiative/eval/fragment.yaml`
- The scheduled RFE job this would mirror: `rfe-autofixer/.gitlab-ci.yml` (GitLab, `redhat/rhel-ai/agentic-ci/rfe-autofixer`)


---

## Short message (Slack or email)

Hi <owner names>,

We would like to run the tool that reviews RFEs in RHAIRFE (twice a day since March, 376 runs) on RHOAIENG Initiatives too, opt-in by label and dry run first. Apart from two Initiatives created by hand with it in August and September (RHOAIENG-83253, RHOAIENG-90167), nothing in RHOAIENG is touched until you opt an Initiative in; during the dry run nothing is written to Jira at all.

Proposal: <link to document>

We need six decisions from you, ideally by <date> (one line each on AISDLC-206 is enough):

- **D1** the opt-in label name (we suggest `initiative-autofix-opt-in`)
- **D2** dry-run length and how results are shared (we suggest two weeks, report after each run)
- **D3** what may be written live (we suggest labels + comment + revision when the review asks; no status change, no splits yet) and the comment signature `[Initiative Creator]`
- **D4** status transition target (we suggest none; RHOAIENG has no Approved status)
- **D5** who reviews the first live results, and where
- **D6** which statuses are in scope (we suggest New and Backlog only)

Also: two names, three to five Initiatives to opt in (candidates: RHOAIENG-97020, -97456, -98536), and a 60-minute slot in the week of <date> to score a few of your Initiatives together. Section 3 of the document lists the rubric, questions, template and split rules with a confirm/change prompt each.

Thanks, <our names>