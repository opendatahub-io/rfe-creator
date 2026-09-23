---
name: rfe-create
description: Write a new work item of any registered type — an RFE from a problem statement, idea, or need (business needs, WHAT/WHY), or an Initiative from an objective or strategic goal (/rfe-create --type initiative ...). Asks clarifying questions, then produces well-formed items. Use when starting from scratch.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
---

You are a work-item creation assistant. The type's own guidance (read in Step 2, in every mode) says who you are helping and what a good item of that type looks like; this body is the procedure.

## Step 0: Resolve the Type and Parse Arguments

Parse `$ARGUMENTS` for:
- `--type <t>`: an explicit type — always wins
- `--headless`: Ask no clarifying questions — generate items directly from the input (the guidance in Step 2 is still read)
- `--priority <value>`: Override default priority (Blocker, Critical, Major, Normal, Minor)
- `--labels <comma-separated>`: Labels to apply to created items
- `--id <ID>`: Pre-assigned ID. When provided, use this ID instead of calling `next_rfe_id.py` in Step 4. The placeholder file already exists. (`--rfe-id <ID>` and `--initiative-id <ID>` are accepted as aliases.)
- `--parent <KEY>`: parent key to set on the created item — only for a type whose batch entries carry `parent_key` (`PARENT_FLAG={PARENT_FLAG}` in the launch block; the type's `batch.extra_fields` and `conventions.parent_key_patterns` say what an item of this type links to)
- Remaining arguments: the problem statement / idea / objective text

Resolve the work-item type — forward only `--type <t>` (when given) and `--headless` (when given); never the text, the priority, the labels, the id or the parent:

```bash
python3 scripts/type_registry.py resolve [--type <t>] [--headless]
```

It prints `TYPE RESOLVED: <type> (<how>)`. Interactive and unresolved, it asks you to pick a type — offer the registered types (`python3 scripts/type_registry.py list`) to the user with AskUserQuestion and re-run with `--type`. Then print the stage's launch block and keep it: every `{VAR}` in this skill is the value of that `VAR=` line:

```bash
python3 scripts/type_registry.py launch-vars <type> create
```

If `--headless` is present, Step 2 still reads the guidance but asks nothing: proceed from that read straight to Step 3 using the provided input.

## Step 1: Load Rubric

Only when this type exports a rubric (`RUBRIC_EXPORT={RUBRIC_EXPORT}` is a path, not `none`): if `{RUBRIC_EXPORT}` does not exist, bootstrap and export it:

1. Run `{BOOTSTRAP}` to fetch the assess-rfe skills
2. When any assess-rfe skill resolves its `{PLUGIN_ROOT}`, it should use the absolute path of `{CONTEXT_DIR}/` in the project working directory.
3. Invoke `/export-rubric` to export the rubric to `{RUBRIC_EXPORT}`

If either step fails (network issue, script missing), proceed without the rubric.

If `{RUBRIC_EXPORT}` exists (either already present or just exported), read it. Use the rubric criteria to shape your clarifying questions and guide {ENTITY} generation. The rubric tells you what a good {ENTITY} looks like — use it to ensure the {ENTITY_PLURAL} you produce will pass validation.

If the rubric is still not available after the bootstrap attempt, proceed with the built-in question flow below (the guidance's question list).

## Step 2: Read the Guidance, then Ask Clarifying Questions

Read the type's creation guidance at `{CREATE_GUIDANCE_PATH}` — always, headless too. It holds the clarifying questions to ask, how to adapt them to the rubric, the writing rules and the don'ts for this type; Step 3 and "What NOT to Do" apply them. Unless headless, ask the questions it lists (2-5 maximum — only what you cannot reasonably infer from the input), then continue.

## Step 3: Generate Items

Read the template from `{TEMPLATE_PATH}`. If this type sizes its items (`SIZE_FIELD={SIZE_FIELD}` is true), internalize the **Size Guide** — you will use it to determine each {ENTITY}'s t-shirt size (allowed values: `{SIZE_ENUM}`).

After receiving answers, generate {ENTITY_PLURAL} using that template — in headless mode, generate the {ENTITY} using that template directly from the input. Apply the guidance's writing rules: one item per distinct need, priority from the Jira values (default Normal unless the input clearly indicates urgency).

## Step 4: Write Artifacts

For each item, determine its ID, then write the markdown body and set frontmatter.

If `--id` was provided, use that ID (the placeholder file already exists). Otherwise, allocate IDs atomically:

```bash
python3 scripts/next_rfe_id.py {NEXT_ID_FLAGS} <count>
```

This prints one `{LOCAL_PREFIX}NNN` per line. Use these IDs for filenames: `{TASKS_DIR}/{LOCAL_PREFIX}NNN.md`.

Read the schema to know exact field names and allowed values:

```bash
python3 scripts/frontmatter.py schema {TASK_SCHEMA}
```

Then set frontmatter on each item file, using the actual values for this item (`{SIZE_SET}` is empty for a type without sizes):

```bash
python3 scripts/frontmatter.py set {TASKS_DIR}/<filename>.md \
    {ID_FIELD}=<id> \
    title="<title>" \
    priority=<priority>{SIZE_SET} \
    status=Draft \
    type={TYPE}
```

If `--parent` was provided:

```bash
python3 scripts/frontmatter.py set {TASKS_DIR}/<filename>.md parent_key=<parent_key>
```

If `INDEX_ENABLED={INDEX_ENABLED}` is true, rebuild the index after all files are written:

```bash
python3 scripts/frontmatter.py rebuild-index
```

Create the `artifacts/`, `{TASKS_DIR}/`, and `{REVIEWS_DIR}/` directories if they don't exist.

Tell the user they can:
- Edit any artifact file directly before proceeding
- Run `/rfe-review --type {TYPE}` to validate the {ENTITY_PLURAL}
- Re-run `/rfe-create --type {TYPE}` to start over from scratch

## What NOT to Do

Follow the guidance's own "What NOT to do" list for this type. In every type: do NOT use High/Medium/Low for priority (use the Jira values: Blocker, Critical, Major, Normal, Minor), and do NOT generate an intermediate document — go directly from the input to the items.

$ARGUMENTS
