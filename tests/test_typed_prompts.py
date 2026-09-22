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
MIN_SENTENCE = 30


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


def test_allowed_entries_are_still_needed():
    """An allowlist entry whose sentence is present again is stale — drop it."""
    stale = []
    for t, rel, legacy_text, corpus, mode in (p.values for p in _cases()):
        have = normalise(corpus)
        wanted = sentences(body(legacy_text)) if mode == "full" else bold_rules(legacy_text)
        for a in ALLOWED.get((t, rel), []):
            if any(a in s and s in have for s in wanted):
                stale.append((t, rel, a))
    assert not stale, stale
