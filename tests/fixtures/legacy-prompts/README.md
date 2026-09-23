# Legacy prompt corpus (frozen)

The per-type skill bodies and prompts as they were on `main` at 0670c57 (the PR-5b merge),
immediately before PR-5c deleted them. `tests/test_typed_prompts.py` proves that every
judgement sentence and every script invocation of this corpus survives in the generic
surface (`.claude/skills/rfe-*/SKILL.md`, the skeletons under
`.claude/skills/rfe-review/prompts/` and the typed files under `types/<type>/`).

This corpus is a historical record: it is never edited. A prose change to the generic
surface that drops a sentence of this corpus is recorded in the test's `ALLOWED` list with
its reason, not by rewriting the fixture.

Layout, per type (`rfe/`, `initiative/`):

| Fixture | Was |
|---|---|
| `<stage>.md` | `.claude/skills/rfe.<stage>/SKILL.md` / `.claude/skills/initiative-<stage>/SKILL.md` |
| `prompts/{review,revise,fetch,assess}-agent.md` | `.claude/skills/<legacy review skill>/prompts/` |
| `prompts/split-agent.md` | `.claude/skills/<legacy split skill>/prompts/split-agent.md` |
| `template.md` | `.claude/skills/rfe.create/rfe-template.md` / `.claude/skills/initiative-create/initiative-template.md` |
| `dimensions/feasibility.md` | `.claude/skills/rfe-feasibility-review/SKILL.md` / `.claude/skills/initiative-feasibility-review/SKILL.md` |
| `dimensions/alignment.md` | `.claude/skills/strategic-alignment-review/SKILL.md` |
