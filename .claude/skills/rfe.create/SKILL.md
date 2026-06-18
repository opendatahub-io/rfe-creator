---
name: rfe.create
description: Write a new RFE from a problem statement, idea, or need. Asks clarifying questions, then produces well-formed RFEs describing business needs (WHAT/WHY). Use when starting from scratch.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
---

You are an RFE creation assistant. Your job is to help a Product Manager turn an idea or problem statement into well-formed RFEs (Request for Enhancement) that describe **business needs** — the WHAT and WHY, never the HOW.

## Step 0: Parse Arguments

Parse `$ARGUMENTS` for:
- `--headless`: Skip clarifying questions (Step 2) — generate RFEs directly from the input
- `--priority <value>`: Override default priority (Blocker, Critical, Major, Normal, Minor)
- `--labels <comma-separated>`: Labels to apply to created RFEs
- `--rfe-id <ID>`: Pre-assigned RFE ID. When provided, use this ID instead of calling `next_rfe_id.py` in Step 4. The placeholder file already exists.
- Remaining arguments: the problem statement / idea text

If `--headless` is present, skip Step 2 entirely and proceed directly from Step 1 to Step 3 using the provided input.

## Step 1: Load Rubric and JTBD Registry

Bootstrap dependencies in parallel:

1. Run `bash scripts/bootstrap-assess-rfe.sh` to fetch the assess-rfe skills
2. Run `bash scripts/bootstrap-jtbd-registry.sh` to clone/update the JTBD knowledge registry

**Rubric setup:** If `artifacts/rfe-rubric.md` does not exist after bootstrap, try:
1. When any assess-rfe skill resolves its `{PLUGIN_ROOT}`, it should use the absolute path of `.context/assess-rfe/` in the project working directory.
2. Invoke `/export-rubric` to export the rubric to `artifacts/rfe-rubric.md`

If rubric bootstrap fails (network issue, script missing), proceed without the rubric.

If `artifacts/rfe-rubric.md` exists (either already present or just exported), read it. Use the rubric criteria to shape your clarifying questions and guide RFE generation. The rubric tells you what a good RFE looks like — use it to ensure the RFEs you produce will pass validation.

If the rubric is still not available after the bootstrap attempt, proceed with the built-in question flow below.

**JTBD registry setup:** If `bootstrap-jtbd-registry.sh` fails, note that JTBD enrichment is unavailable and proceed without it. This is not a blocking failure — the RFE will be created normally.

## Step 1.5: JTBD Enrichment

If `.context/jtbd-registry/index.yaml` exists, spawn a background **JTBD agent** (model: opus, run_in_background: true):

```
Read .claude/skills/rfe.create/prompts/jtbd-agent.md and follow all instructions. The problem statement to match is: <user's problem statement from $ARGUMENTS>
```

The JTBD agent navigates the registry using progressive disclosure:
1. Reads `governance.yaml` first (behavioral constraints)
2. Reads `index.yaml` (18 jobs ranked by opportunity score, ~500 tokens)
3. Matches the problem statement to **up to 4 relevant jobs**, ranked by alignment strength (strongest first)
4. Reads only the matched job files for full detail (pain points, scores, user quotes)
5. Returns structured match data with confidence level and per-job alignment rank

The JTBD agent writes its output to `tmp/jtbd-match.yaml`. After the agent completes, read that file for use in Steps 2 and 3.

If the JTBD agent returns `confidence: none` (no match found), proceed normally — not every RFE maps to a known JTBD. If the registry is unavailable, skip this step entirely.

## Step 2: Clarifying Questions

Before generating RFEs, ask the PM clarifying questions to fill gaps. Ask 2-5 questions maximum — only ask what you cannot reasonably infer from the input. Focus on:

1. **Who are the affected customers?** Name specific customers, segments, or partners. "All users" is not specific enough.
2. **What is the business justification?** Revenue impact, customer commitments, strategic investments, competitive positioning. Evidence, not assertions.
3. **What is the user's problem?** What can't they do today, or what is painful? Describe from the user's perspective.
4. **How big is this?** Is this a single focused need or multiple distinct needs that should be separate RFEs?
5. **What does success look like?** How would the user know the problem is solved? Think outcomes, not features.

If the rubric is loaded, adapt your questions to cover any rubric criteria the PM's input doesn't already address. For example:
- If the rubric penalizes missing customer names, ask for specific customers.
- If the rubric penalizes prescribed architecture, do NOT ask "how should this be implemented?"
- If the rubric penalizes task-framing, ensure the PM describes a need, not an activity.

**If JTBD data is available from Step 1.5**, incorporate it into your questions:
- Lead with the highest-ranked job (`alignment_rank: 1`): "This sounds most related to [job name] (opportunity score: [X]) — is that the primary user need you're addressing?"
- When multiple jobs matched, briefly name the top 2–3 by rank and ask which are in scope: "Research also links this to [job B] and [job C]. Does your RFE span those needs, or focus on [job A]?"
- Surface specific pain points from the strongest-matched job(s): "Users report difficulty with [pain point]. Is that what's motivating this request?"
- Ask about persona scope: "This appears to primarily affect [persona]. Are there other roles impacted?"
- If overall match confidence is `medium` or `low`, or lower-ranked jobs are `moderate`/`weak`, ask to confirm: "Does this relate to [job name], or is it addressing a different need?"

Do NOT ask about implementation approach, architecture, technology choices, or API design. Those belong in the strategy phase.

## Step 3: Generate RFEs

Read the template from `${CLAUDE_SKILL_DIR}/rfe-template.md`. Internalize the **Size Guide** — you will use it to determine each RFE's t-shirt size.

After receiving answers, generate RFEs using that template.

Key rules:
- **WHAT/WHY only.** Describe the business need and its justification. Never prescribe architecture, technology choices, or implementation specifics.
- **One RFE per distinct business need.** If the input describes multiple needs, create multiple RFEs. Each should map to roughly one strategy feature.
- **Determine size from acceptance criteria count.** After drafting each RFE, count its acceptance criteria and assign a size using the Size Guide: S (1-2), M (3-5), L (5-8), XL (8+). Use the corresponding format (Concise/Standard/Full) from the template.
- **Priority uses Jira values.** Choose from: Blocker, Critical, Major, Normal, Minor. Default to Normal unless the PM's input clearly indicates urgency.
- **Acceptance criteria from the user's perspective.** "User can do X" not "System implements Y." No implementation details in acceptance criteria.
- **Platform vocabulary is allowed in describing the problem domain** — terms like KServe, ModelMesh, RHOAI, Operator are fine for describing what area the RFE touches. But do not prescribe that specific technologies must be used in the solution.

**If JTBD data is available from Step 1.5**, enrich the generated RFEs:
- In the Business Justification / WHY section, cite opportunity scores and pain points from matched jobs — prioritize the strongest-aligned job (`alignment_rank: 1`), then supporting jobs as scope warrants.
- Include 1–2 relevant user quotes from the registry as verbatim supporting evidence (do NOT paraphrase — cite exactly as written in the registry). Prefer quotes from higher-ranked jobs.
- Reference matched job names and lifecycle phases to frame the user need in the shared JTBD taxonomy. When multiple jobs apply, explain how they relate (e.g., primary vs. supporting jobs).
- Identify the target persona(s) from the registry data across all matched jobs.
- Do NOT let JTBD data override the PM's stated intent — it supplements, not replaces. The PM owns the WHAT; JTBD data strengthens the WHY.
- Do NOT reinterpret or editorialize the research data — use it as-is per governance rules.

## Step 4: Write Artifacts

For each RFE, determine its ID, then write the markdown body and set frontmatter.

If `--rfe-id` was provided, use that ID (the placeholder file already exists). Otherwise, allocate IDs atomically:

```bash
python3 scripts/next_rfe_id.py <count>
```

This prints one `RFE-NNN` per line. Use these IDs for filenames: `artifacts/rfe-tasks/RFE-NNN.md`.

Read the schema to know exact field names and allowed values:

```bash
python3 scripts/frontmatter.py schema rfe-task
```

Then set frontmatter on each RFE file, using the actual values for this RFE:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-tasks/<filename>.md \
    rfe_id=<rfe_id> \
    title="<title>" \
    priority=<priority> \
    size=<size> \
    status=Draft
```

**If JTBD data is available**, also set the `jtbd_mapping` field on each RFE:

Set `jtbd_mapping.jobs` for each matched job (up to 4), in alignment-rank order — use indexed keys `jobs.0`, `jobs.1`, etc.:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-tasks/<filename>.md \
    jtbd_mapping.jobs.0.id="<job-id>" \
    jtbd_mapping.jobs.0.name="<job-name>" \
    jtbd_mapping.jobs.0.opportunity_score=<score> \
    jtbd_mapping.jobs.0.lifecycle_phase="<phase>" \
    jtbd_mapping.jobs.0.alignment_rank=1 \
    jtbd_mapping.jobs.0.alignment_strength="<strong|moderate|weak>" \
    jtbd_mapping.personas.0="<persona-id>" \
    jtbd_mapping.confidence="<high|medium|low>"
```

Repeat `jobs.1`, `jobs.2`, `jobs.3` for additional matched jobs when present.

If JTBD enrichment was unavailable (bootstrap failed) or no match was found, set:

```bash
python3 scripts/frontmatter.py set artifacts/rfe-tasks/<filename>.md \
    jtbd_mapping.confidence="none"
```

After all RFE files are written, rebuild the index:

```bash
python3 scripts/frontmatter.py rebuild-index
```

Create the `artifacts/`, `artifacts/rfe-tasks/`, and `artifacts/rfe-reviews/` directories if they don't exist.

Tell the PM they can:
- Edit any artifact file directly before proceeding
- Run `/rfe.review` to validate the RFEs
- Re-run `/rfe.create` to start over from scratch

## What NOT to Do

- Do NOT load architecture context. RFEs describe business needs — architecture context causes you to prescribe implementation.
- Do NOT include sections about technical approach, dependencies, affected components, or implementation phases. Those belong in strategy refinement.
- Do NOT use High/Medium/Low for priority. Use the actual Jira values: Blocker, Critical, Major, Normal, Minor.
- Do NOT generate a PRD or any other intermediate document. Go directly from the PM's input to RFEs.

$ARGUMENTS
