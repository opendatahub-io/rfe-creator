# Working with work item types

This page is for the person filing the work item: a product manager writing an RFE, or a team
lead writing an Initiative. It explains what a work item type is, how the skills decide which
type you are working with, what changes from one type to the other, and how to file an
Initiative end to end.

## What a work item type is

A work item type is the kind of thing you are filing. It decides which Jira project and issue
type the ticket lands in, which template the skill writes to, which clarifying questions it
asks, how the review scores the item, which labels show up in Jira, and where the files go in
your working directory. Two types are registered:

| Type | Jira project | Jira issue type | Purpose | Jira key | Id before submit |
|---|---|---|---|---|---|
| RFE | RHAIRFE | Feature Request | A business need from the user's perspective: the WHAT and the WHY, never the HOW. | `RHAIRFE-1234` | `RFE-001` |
| Initiative | RHOAIENG | Initiative | A team commitment: a scoped body of work with a clear objective and the evidence for why it matters. It sits between a strategic Outcome and the Epics. | `RHOAIENG-12345` | `INIT-001` |

A new item carries a local id (`RFE-001`, `INIT-001`) until it is submitted; submit renames it
to the Jira key the ticket was created under.

## The skills

Six skills run the pipeline, and every one of them works for every type:

| Skill | What it does |
|---|---|
| `/rfe-create` | Writes a new item from a problem statement (RFE) or an objective (Initiative), after a few clarifying questions. |
| `/rfe-review` | Scores an item, runs the type's review checks and auto-revises what it finds. Takes Jira keys to fetch existing tickets, or reviews the local files written by `/rfe-create`. |
| `/rfe-split` | Splits an oversized item into right-sized ones and reviews the pieces. |
| `/rfe-submit` | Creates the Jira ticket for a new item, or updates the ticket an existing item came from. |
| `/rfe-speedrun` | Create, review, auto-fix and submit in one go, from an idea, a Jira key or a batch file. |
| `/rfe-auto-fix` | Reviews, revises and splits a batch of existing tickets, from explicit keys or a JQL query, without asking questions. |

The dotted names (`/rfe.create`, `/rfe.review`, `/rfe.split`, `/rfe.submit`, `/rfe.speedrun`,
`/rfe.auto-fix`) are compatibility aliases kept so existing invocations keep working. Each one
runs the same skill as its dashed twin with the same arguments, `--type` included. Prefer the
dashed names in anything new.

## How the type is chosen

Every run works on exactly one type. The skill decides which one from your arguments, in this
order; the first signal present wins:

1. **`--type rfe` or `--type initiative`** on the command line. Always wins. An unknown name is
   an error that lists the registered types.
2. **The batch file's `type:`** (`/rfe-speedrun --input <file>`), when the file is written as
   `type: initiative` followed by `items:`. A `--type` that disagrees with it is an error.
3. **What you pass.** A Jira key or local id names its type on its own: `RHAIRFE-` and `RFE-`
   ids are RFEs, `RHOAIENG-` and `INIT-` ids are Initiatives. An item file names its type
   through its `type:` field, or failing that through the folder it sits in
   (`artifacts/rfe-tasks/` is RFE, `artifacts/initiatives/` is Initiative).
4. **Nothing at all**: the run is an RFE run. That is the historical default, so `/rfe-create`
   with just a sentence writes an RFE and a new Initiative needs `--type initiative`.

### The `TYPE RESOLVED` line

The first thing every skill prints, before it fetches or writes anything, is one line saying
which type it settled on and which rule decided it:

```
TYPE RESOLVED: initiative (--type)
TYPE RESOLVED: initiative (id grammar)
TYPE RESOLVED: rfe (legacy default)
```

The words in parentheses name the rule: `--type`, `batch type`, `frontmatter type` (the file's
`type:` field), `artifact dir` (the folder), `id grammar` (the id you passed) or
`legacy default` (nothing decided it, so RFE). Read this line first when a run surprises you.

### Conflicts are errors, not questions

Two signals that name different types stop the run at once; the skill neither picks one nor
asks. This is what the error looks like:

```
ERROR: conflicting type signals: --type initiative vs RHAIRFE-123 -> rfe; a run is single-typed — split the input by type
ERROR: conflicting type signals: RHAIRFE-123 -> rfe, RHOAIENG-456 -> initiative; a run is single-typed — split the input by type or pass --type
```

The fix is what the message says: run the RFEs and the Initiatives as separate invocations.
Three more shapes you may meet:

- `ERROR: unknown type 'epic' (--type); registered types: rfe, initiative`: a `--type` that is
  not registered.
- An id no type owns, say `FOO-1`: in a terminal the skill prints
  `TYPE AMBIGUOUS: rfe, initiative - pass --type` and asks you to pick; in a headless or CI run
  it stops with `ERROR: ambiguous type for FOO-1: candidates rfe, initiative — pass --type`,
  because a headless run never guesses.
- A batch file with a `type` on an individual entry is rejected: a batch is single-typed, so
  split it into one file per type.

## What changes with the type

| | RFE | Initiative |
|---|---|---|
| Jira ticket | project RHAIRFE, issue type Feature Request | project RHOAIENG, issue type Initiative |
| Template | Summary, Problem Statement, Affected Customers, Business Justification, Acceptance Criteria, Success Criteria; large items add User Scenarios, Scope and Open Questions. Sized S, M, L or XL by the number of acceptance criteria. | Objective, Problem Statement, Scope. Prose is fine; there is no size. |
| Clarifying questions (two to five) | Who are the affected customers? What is the business justification? What is the user's problem? How big is this? What does success look like? | What is the objective? What problem does it solve, with what evidence? What is in and out of scope? Is there a parent RHAISTRAT Outcome? |
| Parent | none | `--parent RHAISTRAT-NNNN` on create, `parent_key` in a batch entry; links the ticket to the Outcome |
| Review scores | WHAT, WHY, HOW (open to how), Not-a-task, Right-sized | WHAT, WHY, Scope, HOW (open to how), Right-sized |
| Review checks | Technical feasibility | Technical feasibility, plus strategic alignment with the parent Outcome when the item has a RHAISTRAT parent (`not_assessed` otherwise; weak alignment flags the item for human attention) |
| Item files | `artifacts/rfe-tasks/` | `artifacts/initiatives/` |
| Review files | `artifacts/rfe-reviews/` | `artifacts/initiative-reviews/` |
| Jira description as fetched | `artifacts/rfe-originals/` | `artifacts/initiative-originals/` |
| Companion files | `<id>-comments.md` (stakeholder comments) and `<id>-removed-context.yaml` (implementation detail moved out during review) | `<id>-removed-context.yaml` only |
| Index | `artifacts/rfes.md`, rebuilt after every write | none |
| Jira comment the pipeline posts | starts with `[RFE Creator]` | starts with `[Initiative Creator]` |
| Batch query (`/rfe-auto-fix --jql`) | a query on project RHAIRFE | a query on project RHOAIENG; a query on another project is refused |
| Run report of a batch run | `artifacts/auto-fix-runs/<run-id>.yaml`, items listed under `per_rfe` | `artifacts/auto-fix-runs/initiative-run-<run-id>.yaml`, items listed under `per_initiative`, each with its `alignment` and `feasibility` |

### Labels in Jira

The pipeline sets these labels on the tickets it creates or updates. They are exact strings;
the first two rows at the bottom are the ones a human sets or clears.

| Meaning | RFE | Initiative |
|---|---|---|
| Created by the pipeline | `rfe-creator-auto-created` | `initiative-auto-created` |
| Description revised by the pipeline | `rfe-creator-auto-revised` | `initiative-auto-revised` |
| Passed review; left out of later batch runs | `rfe-creator-autofix-rubric-pass` | `initiative-autofix-rubric-pass` |
| Needs a human | `rfe-creator-needs-attention` | `initiative-needs-attention` |
| Parent that was split | `rfe-creator-split-original` | `initiative-split-original` |
| Child produced by a split | `rfe-creator-split-result` | `initiative-split-result` |
| Split bookkeeping on a child | `rfe-creator-split-child-<parent>-<child>-<fingerprint>` | `initiative-split-child-<parent>-<child>-<fingerprint>` |
| Feasibility verdict | `rfe-creator-feasibility-pass`, `rfe-creator-feasibility-fail`, `rfe-creator-feasibility-unknown` | `initiative-feasibility-pass`, `initiative-feasibility-fail`, `initiative-feasibility-unknown` |
| Alignment verdict | none | `initiative-alignment-strong`, `initiative-alignment-partial`, `initiative-alignment-weak` |
| Keep this ticket out of batch runs for good (set by a human) | `rfe-creator-ignore` | `initiative-ignore` |
| Parked after a split failed partway (a human removes it) | `rfe-creator-split-quarantine` | `initiative-split-quarantine` |

## When the wrong type was picked

How to tell:

- The `TYPE RESOLVED` line at the top of the run names the type and the rule that chose it.
- The folder: an RFE lands in `artifacts/rfe-tasks/RFE-NNN.md`, an Initiative in
  `artifacts/initiatives/INIT-NNN.md`.
- The ticket: an RFE is created in RHAIRFE, an Initiative in RHOAIENG.

What to do:

- Nothing reaches Jira until you run `/rfe-submit` (or the submit phase of a speedrun). Before
  that point, re-run with the type spelled out, `/rfe-create --type initiative <the same text>`,
  and delete the file that landed in the wrong folder (and its review file, if any) so a later
  run of that type does not pick it up.
- For the review, split or submit of an existing ticket, pass the key together with `--type`,
  or just the key: `RHOAIENG-12345` resolves to Initiative on its own, and the conflict error
  above tells you when a `--type` and a key disagree.
- Once a ticket exists in Jira under the wrong project, no skill moves it: close it in Jira and
  file it again with the right `--type`.

## Worked example: file an Initiative

### 1. Create

```
/rfe-create --type initiative Cut the cold-start time of model serving in half for the next release, so that autoscaled deployments stop timing out under burst load
```

Add `--parent RHAISTRAT-1234` when you know the Outcome it rolls up to, and `--priority Critical`
(Blocker, Critical, Major, Normal or Minor; the default is Normal) when the default is wrong.

The run prints `TYPE RESOLVED: initiative (--type)`, asks two to five questions (the objective,
the problem and its evidence, the scope boundary, the parent Outcome) and writes
`artifacts/initiatives/INIT-001.md`: a header with `initiative_id: INIT-001`, `title`,
`priority`, `status: Draft` and `type: initiative` (plus `parent_key` when given), then the
Objective, Problem Statement and Scope sections. Edit the file by hand if you like.

### 2. Review

```
/rfe-review --type initiative INIT-001
```

The review scores WHAT, WHY, Scope, HOW and Right-sized, checks technical feasibility, checks
strategic alignment when a RHAISTRAT parent is set, and revises the file itself for the problems
it can fix (up to two passes). It writes `artifacts/initiative-reviews/INIT-001-review.md` with
the score, a `recommendation` (`submit`, `revise`, `split` or `reject`), `feasibility`,
`alignment` and `needs_attention`, and ends by telling you one of three things: the item is
ready for `/rfe-submit --type initiative`; edit the file and re-run
`/rfe-review --type initiative`; or run `/rfe-split --type initiative INIT-001` because the
item is too big.

### 3. Submit

Submission needs Jira credentials in the environment:

```
export JIRA_SERVER=https://your-site.atlassian.net
export JIRA_USER=your-email@example.com
export JIRA_TOKEN=your-api-token
```

Then:

```
/rfe-submit --type initiative --dry-run    # optional: validate without touching Jira
/rfe-submit --type initiative
```

The skill does not ask for confirmation; invoking it is the confirmation. For each item it
prints what happened, for example:

```
  INIT-001: Created RHOAIENG-12345
           Labels: initiative-auto-created, initiative-feasibility-pass
```

What you get:

- In Jira: an **Initiative** in project **RHOAIENG**, status New, with the labels above (plus an
  `initiative-alignment-*` label when alignment was assessed, and `initiative-auto-revised` when
  the review changed the text), and its parent set to the RHAISTRAT Outcome when you gave one.
- Locally: the files renamed to the Jira key, `artifacts/initiatives/RHOAIENG-12345.md` and
  `artifacts/initiative-reviews/RHOAIENG-12345-review.md`, with `initiative_id: RHOAIENG-12345`,
  `status: Submitted` and `local_id: INIT-001` in the header.
- An item the review did not clear is skipped, with the reason printed next to its id.

### The same in one command

```
/rfe-speedrun --type initiative <the objective text>
/rfe-speedrun --type initiative RHOAIENG-12345        # review, revise and update an existing Initiative
/rfe-speedrun --input initiatives.yaml --headless     # a batch; the file carries the type
```

A batch file for Initiatives:

```yaml
type: initiative
items:
  - prompt: "Cut the cold-start time of model serving in half for the next release"
    priority: Major
    parent_key: RHAISTRAT-1234
    clarifying_context: |
      Three customer escalations last quarter trace back to autoscaling timeouts.
  - prompt: "Retire the legacy model registry endpoints"
```
