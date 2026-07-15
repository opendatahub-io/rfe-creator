# RFE Creator Agent

Reviews, scores, and improves RFEs (Requests for Enhancement) in Jira,
then submits the results. Runs the `rfe.speedrun` skill in a sandboxed
environment with no direct Jira access.

## How the agent works

The rfe-creator agent processes a single Jira issue through the full RFE
pipeline: assess, review, revise, and (if it passes) submit.

1. **Pre-script** extracts the issue key from `FULLSEND_WORK_ITEM_URL`,
   fetches the issue from Jira via `scripts/fetch_issue.py`, and writes
   the task, original, and comment artifacts into the workspace.
2. **Sandbox** — the agent runs `/rfe.speedrun --headless --dry-run` on
   the pre-fetched artifacts. The skill scores the RFE against a rubric,
   checks technical feasibility against architecture context, and
   auto-revises to improve quality. If the RFE is oversized, it splits
   it into smaller pieces. The agent has no Jira credentials and cannot
   contact Jira — it works entirely on local files.
3. **Validation** — `validate-artifacts.sh` checks that all task and
   review files have valid frontmatter, that every `rfe_id` belongs to
   the expected `JIRA_PROJECT` (or is a `DRAFT-NNN` split child), and
   that recommendations are parseable.
4. **Post-script** reads review recommendations via
   `collect_recommendations.py --from-reviews` and calls `submit.py`
   to create or update issues in Jira for RFEs recommended for
   submission. RFEs recommended for rejection produce no Jira writes.

Jira credentials never enter the sandbox. The LLM sees the issue content
but cannot make API calls. All Jira reads happen in the pre-script and
all writes happen in the post-script, both running on the host.

## Running locally

To test the harness end-to-end on a real issue:

```bash
export JIRA_SERVER=https://issues.redhat.com
export JIRA_USER=you@example.com
export JIRA_TOKEN=your-api-token
export JIRA_PROJECT=MYPROJECT

FULLSEND_WORK_ITEM_URL=https://issues.redhat.com/browse/MYPROJECT-1234 \
  fullsend run rfe-creator
```

To do a dry run (no Jira writes from the post-script), omit the Jira
credentials. The pre-script will fail, so you would need to pre-stage
the artifacts manually and run only the sandbox + validation steps.

## How it helps

- RFEs get a thorough quality review and revision without manual effort.
- Oversized RFEs are automatically split into right-sized pieces.
- Jira writes are deterministic (handled by `submit.py`, not the LLM).
- The sandbox boundary prevents accidental or malicious Jira mutations.

## Triggers

The rfe-creator agent does not use GitHub labels or slash commands. It is
triggered by providing a Jira issue URL.

| Trigger | Mechanism | Status |
|---------|-----------|--------|
| Manual | `FULLSEND_WORK_ITEM_URL=... fullsend run rfe-creator` | Available now |
| Jira poll | `fullsend poll` with a `jira-poll` input driver | Coming — [ADR 63](https://github.com/fullsend-ai/fullsend/blob/main/docs/ADRs/0063-polling-based-work-discovery.md) |
| Jira webhook | Jira webhook → fullsend dispatch | Coming — [fullsend#3812](https://github.com/fullsend-ai/fullsend/pull/3812) |

Once polling and webhook triggers land, this harness will add a `trigger`
key ([ADR 61](https://github.com/fullsend-ai/fullsend/blob/main/docs/ADRs/0061-harness-cel-dispatch.md))
with a CEL expression to match Jira work-item events for the target project.

## Variables

| Variable | Required | Where | Purpose |
|----------|----------|-------|---------|
| `FULLSEND_WORK_ITEM_URL` | yes | runner + sandbox | Jira issue URL (e.g., `https://issues.redhat.com/browse/PROJ-1234`) |
| `JIRA_SERVER` | yes | runner only | Jira server base URL |
| `JIRA_USER` | yes | runner only | Jira username / email |
| `JIRA_TOKEN` | yes | runner only | Jira API token |
| `JIRA_PROJECT` | yes | runner only | Expected Jira project key (e.g., `MYPROJECT`). Validation rejects artifacts with IDs outside this project. |

## File layout

```
.fullsend/
  agents/
    rfe-creator.md              # Agent definition (adapter to rfe.speedrun)
  harness/
    rfe-creator.yaml            # Harness config (entry point)
  scripts/
    pre-fetch.sh                # Fetches issue from Jira into artifacts/
    post-submit.sh              # Submits passing RFEs via submit.py
    validate-artifacts.sh       # Validates frontmatter, project IDs, recommendations
```

The harness file (`.fullsend/harness/rfe-creator.yaml`) is the main entry
point. It wires together the agent definition, pre/post scripts, validation,
sandbox policy, and environment variables.

## Adding to your fullsend installation

Register the agent in your `.fullsend/config.yaml`:

```yaml
agents:
  - name: rfe-creator
    harness: https://github.com/opendatahub-io/rfe-creator/blob/main/.fullsend/harness/rfe-creator.yaml
```

For Jira polling, add a `jira-poll` input driver to your poll configuration
that queries the target project. The poll driver emits
`FULLSEND_WORK_ITEM_URL` automatically per ADR 63.

## Source

[`.fullsend/harness/rfe-creator.yaml`](harness/rfe-creator.yaml)
