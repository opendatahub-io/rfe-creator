"""Golden fidelity of the PR-5b collapse (design §4.2 tiers, plan PR-5b).

Every sentence of today's per-type prompt files survives in the generic surface: the prompt
skeleton rendered with the type's launch block (``type_registry.py launch-vars``) plus the
typed files under ``types/<t>/``. Sentences are compared after a mechanical normalisation
(code fences, headings, table rows, backtick spans, ``{PLACEHOLDERS}`` and path-like tokens
are dropped; emphasis and case are folded) so that the comparison is about judgement prose,
not about the literal paths the tokens now carry. The few sentences that were deliberately
rewritten by the collapse are listed in ALLOWED with the reason — the list is the reviewable
record of every prose change.
"""

import os
import re
import sys

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import type_registry  # noqa: E402

REG = type_registry.load(extra_roots=[], env={})
SKELETONS = ".claude/skills/rfe-review/prompts"
LEGACY_DIR = {"rfe": "rfe.", "initiative": "initiative-"}
LEGACY_DIM = {
    ("rfe", "feasibility"): ".claude/skills/rfe-feasibility-review/SKILL.md",
    ("initiative", "feasibility"): ".claude/skills/initiative-feasibility-review/SKILL.md",
    ("initiative", "alignment"): ".claude/skills/strategic-alignment-review/SKILL.md",
}
MIN_SENTENCE = 12


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def render(text, pairs):
    """Substitute the launch block into a skeleton (runtime placeholders stay)."""
    for key, value in pairs:
        text = text.replace("{" + key + "}", value)
    return text


def body(text):
    """A skill file without its frontmatter block."""
    parts = text.split("---", 2)
    return parts[2] if text.startswith("---") and len(parts) == 3 else text


def normalise(text):
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    kept = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "|"))]
    text = " ".join(kept)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"\{[A-Za-z_<>]+\}", " ", text)
    text = re.sub(r"\S*/\S*", " ", text)  # paths, flags with slashes, skill names
    text = re.sub(r"\$ARGUMENTS", " ", text)
    text = re.sub(r"[*_>]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def sentences(text):
    """Normalised sentences, segmented per line first so list items stay separate."""
    out = []
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    for line in text.splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", normalise(line)):
            sentence = sentence.strip(" .;:—-")
            if len(sentence) >= MIN_SENTENCE:
                out.append(sentence)
    return out


def commands(text, dims=()):
    """Every ``python3 scripts/…`` / ``bash scripts/…`` invocation — a code-block line
    (backslash continuations joined) or an inline backtick span — normalised: trailing comments
    dropped; ``--type <t>`` and ``type=<t>`` folded (the generic surface adds the type
    everywhere); ``{PLACEHOLDERS}`` and ``<slots>`` folded; the declared dimension names folded
    (the generic body templates them as ``<name>``)."""
    found, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        ln = lines[i].strip()
        if re.match(r"(python3|bash) scripts/", ln):
            parts = [ln]
            while parts[-1].endswith("\\") and i + 1 < len(lines):
                parts[-1] = parts[-1][:-1].strip()
                i += 1
                parts.append(lines[i].strip())
            found.append(" ".join(parts))
        i += 1
    found.extend(re.findall(r"`((?:python3|bash) scripts/[^`]*)`", text))
    out = []
    for cmd in found:
        cmd = re.sub(r"\s+#.*$", "", cmd)
        cmd = re.sub(r"\s--type\s+\S+", "", cmd)
        cmd = re.sub(r"\stype=\S+", "", cmd)
        cmd = re.sub(r"\{[A-Za-z_<>]+\}", "{}", cmd)
        cmd = re.sub(r"<[^>]*>", "<>", cmd)
        for name in dims:
            cmd = re.sub(rf"\b{re.escape(name)}\b", "<>", cmd)
        out.append(re.sub(r"\s+", " ", cmd).strip())
    return out


def bold_rules(text):
    """The judgement-bearing lines of an orchestrator body: those opening with a bold lead."""
    return [
        s
        for line in body(text).splitlines()
        if re.match(r"\s*(?:[-*\d.]+\s+)?\*\*", line)
        for s in sentences(line)
    ]


# (type, legacy file) -> substrings of normalised sentences the collapse rewrote, with why.
ALLOWED = {}
# The revise skeleton keeps the rfe wording for the two shared rules; the initiative header
# sentence and its guarded content-preservation phrasing are the ones that moved.
_REWRITTEN_REVISE_RFE = ("then read and classify each block's",)
_REWRITTEN_REVISE_INIT = ("auto-revise initiative to address review findings",)
# Generic names: the headless completion marker names the generic skill (design §4.4).
_STEP_COMPLETED = ("step completed.",)
# The review skeleton reads "every dimension file listed above" (DIMENSION_FILES, one <NAME>_PATH
# each) instead of naming the feasibility file; the typed review rules name the type's inputs.
_DIMENSION_GENERIC_REVIEW = (
    "write a review file with assessor feedback, feasibility analysis, and frontmatter scores",
)
_DIMENSION_GENERIC_REVIEW_RFE = ("read the assessment result file and the feasibility file",)
# The review body launches every declared dimension by one rule (DIMENSIONS, DIMENSION_<NAME>_*
# and the condition grammar) — the per-dimension launch headings are that rule's renderings.
_DIMENSION_GENERIC_BODY = ("launch feasibility agent",)
_DIMENSION_GENERIC_BODY_INIT = ("launch alignment agent",)
# D6: one re-split trigger (the descriptor's resplit threshold) — the initiative body's
# recommendation-based wording is replaced, not carried.
_RESPLIT_INIT = (
    "check again: read review results for new children",
    "do not re-split for non-scope criteria",
    "this loop only corrects scope issues caught by the review agent's recommendation",
)


def _allow(t, rel, *entries):
    ALLOWED.setdefault((t, rel), []).extend(entries)


_allow("rfe", ".claude/skills/rfe.review/prompts/revise-agent.md", *_REWRITTEN_REVISE_RFE)
_allow(
    "initiative",
    ".claude/skills/initiative-review/prompts/revise-agent.md",
    *_REWRITTEN_REVISE_INIT,
)
for _t, _prefix in (("rfe", "rfe."), ("initiative", "initiative-")):
    _allow(_t, f".claude/skills/{_prefix}review/SKILL.md", *_STEP_COMPLETED)
    _allow(_t, f".claude/skills/{_prefix}split/SKILL.md", *_STEP_COMPLETED)
    _allow(
        _t, f".claude/skills/{_prefix}review/prompts/review-agent.md", *_DIMENSION_GENERIC_REVIEW
    )
    _allow(_t, f".claude/skills/{_prefix}review/SKILL.md", *_DIMENSION_GENERIC_BODY)
_allow("initiative", ".claude/skills/initiative-review/SKILL.md", *_DIMENSION_GENERIC_BODY_INIT)
_allow("rfe", ".claude/skills/rfe.review/prompts/review-agent.md", *_DIMENSION_GENERIC_REVIEW_RFE)
# The revise skeleton's step list keeps the rfe wording ("Read the task file to see what needs
# changing"); the initiative's "Read the initiative: <path>" is the same read, the path now the
# skeleton's `Task file:` header line.
_allow(
    "initiative", ".claude/skills/initiative-review/prompts/revise-agent.md", "read the initiative"
)

# (type, legacy file) -> substrings of normalised script invocations the collapse rewrote, why.
ALLOWED_COMMANDS = {}


def _allow_command(t, rel, *entries):
    ALLOWED_COMMANDS.setdefault((t, rel), []).extend(entries)


# NEXT_ID_FLAGS renders `--prefix <local prefix> --dir <tasks dir>` for every type; the rfe
# bodies relied on next_rfe_id.py's defaults, which are those same values.
for _rel in (
    ".claude/skills/rfe.split/prompts/split-agent.md",
    ".claude/skills/rfe.create/SKILL.md",
    ".claude/skills/rfe.speedrun/SKILL.md",
):
    _allow_command("rfe", _rel, "python3 scripts/next_rfe_id.py")
# PROMPT_PATH is a launch-block line: the PR-5a lookup of pipeline.rubric.path is subsumed.
_allow_command(
    "rfe",
    ".claude/skills/rfe.review/SKILL.md",
    "python3 scripts/type_registry.py get rfe pipeline.rubric.path",
)
_allow("initiative", ".claude/skills/initiative-split/SKILL.md", *_RESPLIT_INIT)


def sections(text, headings):
    """The text of the given ``## `` sections (by heading prefix) of a SKILL body."""
    out = []
    for heading in headings:
        m = re.search(rf"^## {re.escape(heading)}.*?(?=^## |\Z)", body(text), re.S | re.M)
        if m:
            out.append(m.group(0))
    return "\n".join(out)


CREATE_GUIDANCE_SECTIONS = (
    "Step 1: Load Rubric",
    "Step 2: Clarifying Questions",
    "Step 1: Clarifying Questions",
    "Step 3: Generate",
    "Step 2: Generate",
    "What NOT to Do",
)


def _cases():
    """(type, legacy file, legacy text to cover, corpus text, mode) — mode ``full`` covers every
    sentence (prompts, dimension bodies, the create guidance), ``rules`` only the bold-lead
    rule lines (orchestrator bodies, whose mechanics the collapse restructures on purpose)."""
    for t in REG.names():
        desc = REG.get(t)
        pairs = type_registry.launch_vars(desc, "review")
        prompts = desc.get("pipeline.prompts")
        dims = {d["name"]: d["prompt"] for d in desc.get("pipeline.dimensions")}
        legacy = f".claude/skills/{LEGACY_DIR[t]}"
        typed = {k: read(v) for k, v in prompts.items()}
        generic = {
            stage: render(read(f".claude/skills/rfe-{stage}/SKILL.md"), pairs)
            for stage in ("create", "review", "split", "submit", "auto-fix", "speedrun")
        }
        skeleton = {
            name: render(read(f"{SKELETONS}/{name}-agent.md"), pairs)
            for name in ("review", "revise", "fetch", "assess")
        }
        full = [
            (
                f"{legacy}review/prompts/review-agent.md",
                skeleton["review"] + typed["review_rules"] + typed["review_sections"],
            ),
            (f"{legacy}review/prompts/revise-agent.md", skeleton["revise"] + typed["revise_rules"]),
            (f"{legacy}review/prompts/fetch-agent.md", skeleton["fetch"]),
            (f"{legacy}review/prompts/assess-agent.md", skeleton["assess"]),
            (f"{legacy}split/prompts/split-agent.md", render(typed["split_rules"], pairs)),
        ]
        for name, prompt in dims.items():
            full.append((LEGACY_DIM[(t, name)], read(prompt)))
        for rel, corpus in full:
            yield pytest.param(t, rel, read(rel), corpus, "full", id=f"{t}:{rel.split('/')[-1]}")
        create_rel = f"{legacy}create/SKILL.md"
        yield pytest.param(
            t,
            create_rel,
            sections(read(create_rel), CREATE_GUIDANCE_SECTIONS),
            generic["create"] + typed["create_guidance"] + typed["template"],
            "full",
            id=f"{t}:create-guidance",
        )
        for stage in ("create", "review", "split", "submit", "auto-fix", "speedrun"):
            rel = f"{legacy}{stage}/SKILL.md"
            corpus = generic[stage]
            if stage == "create":
                corpus += typed["create_guidance"] + typed["template"]
            yield pytest.param(t, rel, read(rel), corpus, "rules", id=f"{t}:{stage}-body")


@pytest.mark.parametrize("t,rel,legacy_text,corpus,mode", list(_cases()))
def test_every_legacy_sentence_survives(t, rel, legacy_text, corpus, mode):
    have = normalise(corpus)
    allowed = ALLOWED.get((t, rel), [])
    wanted = sentences(body(legacy_text)) if mode == "full" else bold_rules(legacy_text)
    missing = [s for s in wanted if s not in have and not any(a in s for a in allowed)]
    assert not missing, f"{t} {rel} ({mode}): {len(missing)} sentence(s) lost:\n- " + "\n- ".join(
        missing
    )


@pytest.mark.parametrize("t,rel,legacy_text,corpus,mode", list(_cases()))
def test_every_legacy_script_invocation_survives(t, rel, legacy_text, corpus, mode):
    """The mechanical lines too: every script invocation of a legacy file is one of the
    generic surface's, after placeholder normalisation."""
    dims = [d["name"] for d in REG.get(t).get("pipeline.dimensions")]
    have = set(commands(corpus, dims))
    allowed = ALLOWED_COMMANDS.get((t, rel), [])
    missing = [
        c
        for c in commands(body(legacy_text), dims)
        if c not in have and not any(a in c for a in allowed)
    ]
    assert not missing, f"{t} {rel}: {len(missing)} invocation(s) lost:\n- " + "\n- ".join(missing)


def test_allowed_entries_are_still_needed():
    """An allowlist entry is stale when the sentence (or invocation) it excuses is present
    again, or when no legacy sentence matches it at all — either way, drop it. A legacy file
    may back several cases (the create body backs the guidance case too), so the verdict is
    per (type, file) across its cases."""
    matched, present = set(), set()
    for t, rel, legacy_text, corpus, mode in (p.values for p in _cases()):
        dims = [d["name"] for d in REG.get(t).get("pipeline.dimensions")]
        surfaces = [
            (
                ALLOWED,
                sentences(body(legacy_text)) if mode == "full" else bold_rules(legacy_text),
                lambda s, have=normalise(corpus): s in have,
            ),
            (
                ALLOWED_COMMANDS,
                commands(body(legacy_text), dims),
                lambda c, have=set(commands(corpus, dims)): c in have,
            ),
        ]
        for allowed, wanted, survives in surfaces:
            for a in allowed.get((t, rel), []):
                for item in wanted:
                    if a in item:
                        matched.add((t, rel, a))
                        if survives(item):
                            present.add((t, rel, a))
    entries = {
        (t, rel, a)
        for table in (ALLOWED, ALLOWED_COMMANDS)
        for (t, rel), v in table.items()
        for a in v
    }
    stale = sorted((entries - matched) | present)
    assert not stale, stale
