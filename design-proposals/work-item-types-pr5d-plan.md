# PR-5d — marketplace-compatible layout: plan and decisions

Companion to `work-item-types-pr5-plan.md` ("Known gap outside the series: marketplace installs") and `work-item-types-unified.md` §3.5.1 (packaging stance, settles PR #115). Status: **plan; decisions D1–D8 open**. Facts measured 2026-09-25 on rfe-creator main 281c837.

## 1. The problem, measured

**Where the plugin lives after a marketplace install.** `~/.claude/plugins/cache/opendatahub-skills/rfe-creator/0.1.0/` on this machine is a full clone: `scripts/`, `types/`, `tests/`, `docs/`, `eval/`, both READMEs. The skills run with **cwd = the user's project**, not the plugin.

**What breaks there** (verified 2026-09-23 with a real `/plugin install` from an empty project; session transcript):

1. Every skill body invokes helpers as `python3 scripts/<x>.py` / `bash scripts/<x>.sh`, cwd-relative. Counted on main: 111 sites in the six generic bodies (review 43, speedrun 19, auto-fix 19, split 18, create 8, submit 4), 9 in the four review-agent skeletons, 8 in typed files (`split-rules.md` ×2 types, `dimensions/alignment.md`), 3 in `rfe-creator.update-deps`; 26 distinct scripts. `scripts/pipeline_state.py` emits 49 more such command strings in its `next-action` YAML. In a project cwd none of them resolves; the first two commands fail and the model recovers by locating the plugin root and re-running by absolute path.
2. The six `rfe.*` compat shims read `.claude/skills/rfe-<stage>/SKILL.md` cwd-relative — the same failure, one hop earlier.
3. The 36 literal allow rules (`Bash(python3 scripts/<x>.py *)`) live in the checkout's `.claude/settings.json`. A project has none of them, so every command prompts (interactive) or is denied (headless); the recovered absolute-path commands would not match the literal rules even if the rules were present.
4. `scripts/bootstrap-assess-rfe.sh` writes into the project: `.context/assess-rfe/` (the rubric plugin checkout), `.claude/agents/*.md` (the scorer agents), `.claude/skills/<vendored assess skills>/`, and since #203 a `types` symlink into the plugin. Nothing tells git to ignore any of it.

**What already works.** Subagent prompts: `launch_path` (#203) renders typed files relative when the cwd carries them (the bootstrap's `types` link) and absolute otherwise, and the skeleton prompt paths under `.claude/skills/rfe-review/prompts/` are handed to agents by the launcher. Production, CI and the evals run with the checkout as cwd (or the harness links `scripts/`, `.claude/`, `.context/`, `skills/` into the run dir) and are unaffected by everything above.

**The design's stance (§3.5.1).** Convention: the working directory is the plugin root; bodies invoke `python3 scripts/<x>.py` by that relative path (the literal-match allowlist rule in AGENTS.md/CLAUDE.md); Python never assumes cwd. PR #115 (per-skill script copies) was closed as superseded because the production installer is a full clone and duplication is the drift class the design removes. The named fallback if a copy-only installer ever mattered: strat-creator's zero-duplication symlink convention (`<skill>/scripts → ../<common>/scripts → ../../../scripts`, `${CLAUDE_SKILL_DIR}/scripts/<x>.py` in bodies, matching per-skill allowed-tools, the launcher pre-rendering absolute prompt paths). The maintainer ruled the marketplace gap "not a must-have" for PR-5 (2026-09-23) and scheduled it here.

**Precedent.** strat-creator commit 5371520 (2026-04-21): three skills renamed to kebab-case, `strategy-common/scripts → ../../../scripts` plus one `scripts` symlink per skill dir, every body path rewritten to `${CLAUDE_SKILL_DIR}/scripts/<x>.py`, `settings.json` gained wildcard rules `Bash(python3 *skills/*/scripts/<x>.py *)` beside the literal ones (11 files, 58 insertions, 47 deletions). Its `assess-strat` invocations stayed cwd-relative (`python3 scripts/assess-strat/<x>.py`), so strat is compatible for its own scripts and still cwd-bound for the assess ones.

## 2. Acceptance — what "compatible" has to mean

A1. From an empty project with the plugin installed from the marketplace, `/rfe-creator:rfe-create <idea>` (and review/split/submit/auto-fix/speedrun) runs with **no failing command and no model-side search for the plugin root**.
A2. Production, CI and the evals stay **byte-stable**: same command strings, same allowlist matches, same launch blocks (the §3.5.1 invariant).
A3. No script duplication (the drift class).
A4. Subagents keep one path frame (the #203 rule): everything a subagent reads resolves from the cwd.
A5. The project the user runs in is left tidy: nothing the plugin writes there is left un-ignored by git, and nothing of theirs is overwritten.

## 3. Options

### Option A — strat's shape (the §3.5.1 named fallback)

Per-skill `scripts` symlink (`.claude/skills/rfe-<stage>/scripts → ../rfe-common/scripts → ../../../scripts`), every body site rewritten to `python3 ${CLAUDE_SKILL_DIR}/scripts/<x>.py`, the skeletons and typed files (17 sites) rewritten to a launcher token (e.g. `{SCRIPTS}` resolved by `launch-vars`, absolute off the checkout) because variables are not substituted in files subagents Read, `pipeline_state.py`'s 49 emitted commands rendered with an absolute `PLUGIN_ROOT` prefix, the allowlist doubled with wildcard variants, AGENTS.md/CLAUDE.md's literal-path rule rewritten, the compat shims reading `${CLAUDE_SKILL_DIR}/../rfe-<stage>/SKILL.md`.

Pros: no filesystem side effects in the project beyond what the bootstrap already writes; identical to strat. Cons: ~180 sites across bodies, prompts, typed files, Python and settings; the production command strings change shape (allowlist wildcards must land first); it depends on `${CLAUDE_SKILL_DIR}` being substituted for plugin skills in headless runs (**verify — D2**); Codex substitution unknown; git symlinks are broken on Windows checkouts.

### Option B — the bootstrap materializes the layout (what the eval harness does, and what the `types` link already started)

The **only** absolute reference is the first command of Step 0, the bootstrap call. It runs from the plugin (`bash ${CLAUDE_SKILL_DIR}/scripts/bootstrap-assess-rfe.sh --type <t>` through a per-skill `scripts` symlink as in A, or `${CLAUDE_PLUGIN_ROOT}/scripts/...` if that is substituted in skills — D2), and the bootstrap links `scripts → <plugin>/scripts` and `types → <plugin>/types` into the cwd when the cwd is not the plugin and has no such entries (the `types` half exists since #203). After that every cwd-relative literal resolves unchanged: the 111 body sites, the 17 prompt/typed sites, the 49 emitted commands, the literal allow rules, the CLAUDE.md rule. The compat shims still need their one path made plugin-relative (D2's variable), or they read `scripts/../.claude/skills/rfe-<stage>/SKILL.md` through the link.

Pros: A2 by construction (production and eval see no change at all); 6 Step-0 lines plus the bootstrap plus tests; the marketplace project becomes the same shape as the eval's run directory. Cons: two symlinks written into the user's project on top of the bootstrap's existing writes (A5 — D3, D4); a collision policy is needed when the project has its own `scripts/` or `types/`; the first command still depends on a variable or a locator instruction (D2); Windows still unsupported (symlinks), Codex still one recovery if it substitutes nothing.

### Option C — document "run from the checkout"

Keep the status quo (launch_path's absolute fallback is already the only in-code mitigation) and state the requirement in README and the registry description. Cheapest; does not meet A1.

## 4. Recommendation

**B**, with A's per-skill `scripts` symlink used only as the bootstrap locator. It is the one shape that keeps §3.5.1's convention literally intact everywhere the plugin runs today — the same reason #203 chose relative paths with a bootstrap link over absolute paths — and it reuses a mechanism already in production. A stays the fallback if D2's verification shows the variables are not substituted for plugin skills either; in that case both shapes need a locator instruction, and B still changes less.

## 5. Decisions requested (recommendation first)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Shape: B (bootstrap links `scripts` + `types` into the cwd) vs A (strat's body rewrite) vs C (docs only) | **B** |
| D2 | Locator for the first command: `${CLAUDE_SKILL_DIR}/scripts/...` via a per-skill symlink, `${CLAUDE_PLUGIN_ROOT}/scripts/...`, or a deterministic instruction to glob `~/.claude/plugins/cache/*/rfe-creator/*/` | whichever the docs verification confirms is substituted for plugin skills in headless runs (pending); the glob instruction as the documented fallback line in Step 0 |
| D3 | Collision policy when the cwd already carries a different `scripts/` or `types/` | **refuse** with a one-line message naming the entry and the two supported layouts (checkout cwd, empty project); never silently fall back to absolute paths, which reintroduces the #203 misrouting |
| D4 | Project hygiene | the bootstrap appends `scripts`, `types`, `.context/`, `.claude/agents/<vendored>.md`, `.claude/skills/<vendored>/` to `.git/info/exclude` when the cwd is inside a git repo (local, uncommitted, idempotent); document the list |
| D5 | Permission rules in a marketplace project (a plugin cannot ship them — verify) | ship a documented settings snippet in README and the registry page; **no** silent writes of permission rules into the user's settings; an opt-in `--write-permissions` flag on the bootstrap is a possible follow-up |
| D6 | Where the assess context is vendored | keep `.context/assess-rfe`, `.claude/agents`, the vendored assess skills in the project cwd: it is the workspace, `additionalDirectories` already names `.context/assess-rfe`, and `subagent_type` resolution needs the agents where the session looks (verify whether plugin `agents/` would do — if yes, a later PR can ship the scorer agents from the plugin) |
| D7 | Windows and Codex | best effort, documented: symlinks unsupported on Windows checkouts (same as strat); Codex gets one recovery hop if it substitutes no variable |
| D8 | Proof | unit tests for the bootstrap link and refusal paths; a real marketplace install from an empty project with the transcript attached to the PR (A1); the production/eval byte-stability by the usual stage dry run and one eval pair (A2) |

## 6. Out of scope

`rfe-creator.update-deps` beyond making its four commands go through the same layout; the RESULT/green-run guard MRs and the other PR-10 items; shipping the scorer agents from the plugin (D6 follow-up); a Windows-native layout.

## 7. Verification log

**Probe 1 — project skill, headless slash command (2026-09-25).** A throwaway `.claude/skills/probe-vars/SKILL.md` whose body is one line, `DIR=${CLAUDE_SKILL_DIR} ROOT=${CLAUDE_PLUGIN_ROOT} ARGS=$ARGUMENTS`, invoked as `claude -p "/probe-vars alpha beta"` from that project, replied `DIR=/private/tmp/skillvar-probe.FySm/.claude/skills/probe-vars ROOT=${CLAUDE_PLUGIN_ROOT} ARGS=alpha beta`. So `${CLAUDE_SKILL_DIR}` **is** substituted in a project skill body under headless slash invocation (the case that matters for D2), `$ARGUMENTS` is substituted, and `${CLAUDE_PLUGIN_ROOT}` is left verbatim for a project skill (expected: no plugin). Pending: the same probe for a marketplace-installed skill (slash and Skill-tool paths) — the session's permission classifier blocked writing the probe into the plugin cache; a ready-to-run script for the maintainer is at `/tmp/pr4/plugin-var-probe.sh`.

## 8. Progress

2026-09-25 — plan written; docs verification of the variable and permission semantics in flight; probe 1 done (above).
