# Upstream Project Boundaries

Some Red Hat AI components are backed by more than one upstream repository with similar names. An RFE that names the wrong one sends engineering to the wrong codebase, and the STRAT inherits the error verbatim. This file lists the pairs that get confused and the rule for naming them.

The rule is short: **name the repository that owns the code, not the product family.** "vLLM" is a product family in RHAIIS. It is also one specific upstream repo. When the need lives in a sibling repo, say so.

A mirror of this file lives in `strat-creator` (`.claude/skills/strategy-refine/upstream-project-boundaries.md`). Keep the two in sync.

## Pair 1: vLLM vs. vLLM-Omni

| | vLLM | vLLM-Omni |
|---|---|---|
| Repo | `vllm-project/vllm` | `vllm-project/vllm-omni` |
| What it is | Text and multimodal-input LLM engine and OpenAI-compatible server | Separate project for multimodal **output** (speech, image, video, full-duplex realtime). Imports vLLM's entrypoints and adds its own router, so a running Omni server exposes both route sets. |
| Maintainers, PR numbers, release cadence | Own | Own. A PR number means nothing without the repo name. |
| RHAIIS status | GA | Dev Preview per architecture-context overlay `0014` (re-check the overlay; this goes stale) |
| Architecture-context component doc | `vllm.md` exists in `rhoai.next` only | **None** in any version as of 2026-09-19. Only overlays `0014` and `0021` mention it. Do not fold vLLM-Omni into the nearest vLLM component because the inventory has no entry for it. |

### Which repo owns a capability

Verified at vLLM-Omni `573ec4c` and vLLM `674b6d9` (both 2026-09-19). Re-verify before writing; the commands are below.

vLLM-Omni owns: `/v1/audio/speech` (plus `/batch` and the `/stream` WebSocket), `/v1/audio/generate`, `/v1/audio/voices`, `/v1/images/generations`, `/v1/images/edits`, `/v1/videos*`, `/v1/video/chat/stream`, `/v1/duplex`, `/v1/realtime/video`, `/v1/realtime/sessions*`, `/v1/omni/sleep`, `/v1/omni/wakeup`, its own override of `/v1/chat/completions` and `/v1/chat/completions/batch`, endpoint rejection policy (`vllm_omni/config/endpoint_policy.py`), stage-per-process serving, and platform support for cuda, rocm, npu, xpu, musa (no cpu).

vLLM owns: `/v1/audio/transcriptions`, `/v1/audio/translations`, `/v1/completions`, and every OpenAI route not listed above.

**Both register `WS /v1/realtime`.** vLLM's (`vllm/entrypoints/speech_to_text/realtime/`) is streaming transcription: audio in, text out. vLLM-Omni's (`vllm_omni/entrypoints/duplex/`) is full-duplex speech. An RFE that says "realtime" must say which one. If it cannot, it is not ready.

Words that must be pinned to a repo before use: batch, streaming, realtime, any-to-any, headless, CPU.

### Verify from code, not memory

```bash
git clone --depth 1 https://github.com/vllm-project/vllm-omni.git
git clone --depth 1 --filter=blob:none --sparse https://github.com/vllm-project/vllm.git && (cd vllm && git sparse-checkout set vllm/entrypoints)
(cd vllm-omni && git log -1 --format='%h %cd' --date=short)
(cd vllm && git log -1 --format='%h %cd' --date=short)
# Routes vLLM-Omni registers itself
grep -nE '^\s*@router\.(get|post|put|delete|websocket)\(' vllm-omni/vllm_omni/entrypoints/openai/api_server.py
# Routes that come from vLLM
grep -rn '"/v1/' vllm/vllm/entrypoints --include=*.py | grep -E 'router|route'
# Does the capability exist anywhere in Omni?
grep -rni '<feature keyword>' vllm-omni/vllm_omni vllm-omni/docs
```

If the clone fails, do not write from recall. Put `NEEDS VERIFICATION` in the RFE text next to every claim you could not check.

## Writing rules for any RFE that touches a paired project

1. **Name the repo in the Summary.** "vLLM-Omni (`vllm-project/vllm-omni`)", not "upstream vLLM". Naming the repo is WHAT, not HOW. It does not violate the open-to-HOW rubric and must not be generalized away during revision.
2. **Open with a dated Current State block** when the capability may already exist. This is a statement of what exists today, which is WHAT. Keep it if the rubric reviewer flags it, and answer with this paragraph.

   ```
   ## Current State (verified <repo> @ <sha>, <date>)
   - Repo: vllm-project/vllm-omni  (<file path>)
   - Exists at HEAD: yes | no | partial (say what part)
   - Classification: NEW | PRODUCTIZE | ORCHESTRATE | INTERNAL | DONE
   - Upstream in flight: <repo>#NNNN (state, author, last commit date) | none
   - Coordination risk: <one line, or "none">
   ```

   NEW: not in the codebase; upstream-first work. PRODUCTIZE: exists at HEAD; work is testing, packaging, KServe passthrough, docs. ORCHESTRATE: the runtime has it, OpenShift/llm-d/KServe does not; name which. INTERNAL: CI, docs, process. DONE: already shipped; close it.
3. **Never write a bare PR or issue number.** Format: `vllm-project/vllm-omni PR #NNNN (open, <author>, last commit <date>)`. A number without a repo is ambiguous between the two projects by construction. Merged bugfixes are not dependencies. PR references belong in Current State, never in Business Justification.
4. **Acceptance criteria follow the real contract.** For PRODUCTIZE and DONE, copy verb, request fields, sync vs. async, and response shape from `vllm-omni/docs/serving/*.md` and the handler. For NEW, describe user outcomes and do not invent API shape.
5. **One RFE may carry two classifications** when it spans models (PRODUCTIZE for one, NEW for another). State it per model. Do not average.

## Checklist before submit

- Repo named for every capability; none attributed to "upstream vLLM" that lives in vLLM-Omni, or the reverse.
- Current State present and dated when the capability may already exist.
- Every PR or issue reference carries repo, state, author, date.
- "realtime", "batch", "streaming" each pinned to a route and a repo.
- Every unverified claim marked `NEEDS VERIFICATION` in the text.
