# Fullsend Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fullsend harness so `FULLSEND_WORK_ITEM_URL=... fullsend run rfe-creator` processes a Jira issue end-to-end with no skill code changes.

**Architecture:** Pre-script fetches the issue from Jira into `artifacts/`. The agent runs `/rfe.speedrun --headless --dry-run` on local files in a sandbox with no Jira network access. A validation script checks artifact integrity. The post-script reads review recommendations and calls `submit.py` to write results to Jira.

**Tech Stack:** Bash (pre/post/validation scripts), Python (existing `scripts/`), fullsend harness YAML

**Spec:** `docs/superpowers/specs/2026-07-13-fullsend-harness-design.md`

---

### Task 1: Add `--from-reviews` flag to `collect_recommendations.py`

**Files:**
- Modify: `scripts/collect_recommendations.py:75-100`
- Test: `tests/test_collect_recommendations.py`

This is the only change to existing code. The validation and post scripts
need `collect_recommendations.py` to discover IDs by globbing review files
rather than requiring explicit IDs.

- [ ] **Step 1: Write the failing test for `--from-reviews`**

Add to `tests/test_collect_recommendations.py`:

```python
class TestFromReviews:
    def test_discovers_ids_from_review_files(self, art_dir):
        _write(
            f"{art_dir}/artifacts/rfe-reviews/DRAFT-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="DRAFT-001",
                score=9,
                pass_val="true",
                recommendation="submit",
                auto_revised="false",
            ),
        )
        _write(
            f"{art_dir}/artifacts/rfe-reviews/DRAFT-002-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="DRAFT-002",
                score=3,
                pass_val="false",
                recommendation="reject",
                auto_revised="false",
            ),
        )
        out, _, rc = _run(["--from-reviews"])
        assert rc == 0
        groups = _parse_output(out)
        assert "DRAFT-001" in groups["SUBMIT"]
        assert "DRAFT-002" in groups["REJECT"]

    def test_no_review_files_exits_nonzero(self, art_dir):
        _, _, rc = _run(["--from-reviews"])
        assert rc != 0

    def test_from_reviews_with_reassess(self, art_dir):
        _write(
            f"{art_dir}/artifacts/rfe-reviews/DRAFT-001-review.md",
            REVIEW_TEMPLATE.format(
                rfe_id="DRAFT-001",
                score=5,
                pass_val="false",
                recommendation="revise",
                auto_revised="true",
            ),
        )
        out, _, rc = _run(["--from-reviews", "--reassess"])
        assert rc == 0
        groups = _parse_output(out)
        assert "DRAFT-001" in groups["REASSESS"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_collect_recommendations.py::TestFromReviews -v`
Expected: FAIL — `--from-reviews` is not a recognized argument.

- [ ] **Step 3: Implement `--from-reviews`**

In `scripts/collect_recommendations.py`, add a function to discover IDs
from review files and wire it into the argument parser:

```python
def discover_ids_from_reviews():
    """Glob review files and extract IDs from filenames."""
    import glob

    review_dir = os.path.join(ARTIFACTS_DIR, "rfe-reviews")
    pattern = os.path.join(review_dir, "*-review.md")
    ids = []
    for path in sorted(glob.glob(pattern)):
        basename = os.path.basename(path)
        # DRAFT-001-review.md -> DRAFT-001
        # PROJ-1234-review.md -> PROJ-1234
        rfe_id = basename.rsplit("-review.md", 1)[0]
        ids.append(rfe_id)
    return ids
```

Update `main()` — add the `--from-reviews` argument and adjust the ID
resolution logic:

```python
def main():
    parser = argparse.ArgumentParser(description="Group RFE IDs by review recommendation.")
    parser.add_argument("ids", nargs="*", help="RFE IDs to check")
    parser.add_argument(
        "--ids-file", help="Read RFE IDs from a file (one per line) instead of positional args"
    )
    parser.add_argument(
        "--from-reviews",
        action="store_true",
        help="Discover IDs by globbing artifacts/rfe-reviews/*-review.md",
    )
    parser.add_argument(
        "--reassess", action="store_true", help="Collect re-assess candidates instead"
    )
    parser.add_argument("--errors", action="store_true", help="Collect IDs with error field set")
    args = parser.parse_args()

    if args.from_reviews:
        ids = discover_ids_from_reviews()
        if not ids:
            print("ERROR: no review files found in artifacts/rfe-reviews/", file=sys.stderr)
            sys.exit(1)
    else:
        ids = resolve_ids(args.ids, args.ids_file)
        if not ids:
            parser.error("no RFE IDs provided (pass positionally or via --ids-file)")

    if args.errors:
        collect_errors(ids)
    elif args.reassess:
        collect_reassess(ids)
    else:
        collect_default(ids)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_collect_recommendations.py -v`
Expected: All tests pass, including existing ones.

- [ ] **Step 5: Run linter**

Run: `ruff check scripts/collect_recommendations.py && ruff format --check scripts/collect_recommendations.py`
Expected: No issues.

- [ ] **Step 6: Commit**

```bash
git add scripts/collect_recommendations.py tests/test_collect_recommendations.py
git commit -S -s -m "$(cat <<'EOF'
Add --from-reviews flag to collect_recommendations.py

Discovers RFE IDs by globbing artifacts/rfe-reviews/*-review.md
instead of requiring explicit IDs. Needed by fullsend harness
validation and post-submit scripts.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Create the harness config

**Files:**
- Create: `.fullsend/harnesses/rfe-creator.yaml`

- [ ] **Step 1: Create the harness file**

```yaml
agent: agents/rfe-creator.md
model: opus
policy: fullsend-ai/agents:policies/base.yaml
timeout_minutes: 30

skills:
  - .claude/skills/rfe.speedrun
  - .claude/skills/rfe.create
  - .claude/skills/rfe.review
  - .claude/skills/rfe.auto-fix
  - .claude/skills/rfe.split
  - .claude/skills/rfe.submit

pre_script: .fullsend/scripts/pre-fetch.sh
post_script: .fullsend/scripts/post-submit.sh

validation_loop:
  script: .fullsend/scripts/validate-artifacts.sh
  max_iterations: 1

env:
  runner:
    FULLSEND_WORK_ITEM_URL: "${FULLSEND_WORK_ITEM_URL}"
    JIRA_SERVER: "${JIRA_SERVER}"
    JIRA_USER: "${JIRA_USER}"
    JIRA_TOKEN: "${JIRA_TOKEN}"
    JIRA_PROJECT: "${JIRA_PROJECT}"
  sandbox:
    FULLSEND_WORK_ITEM_URL: "${FULLSEND_WORK_ITEM_URL}"
```

- [ ] **Step 2: Commit**

```bash
git add .fullsend/harnesses/rfe-creator.yaml
git commit -S -s -m "$(cat <<'EOF'
Add fullsend harness config for rfe-creator

Wires agent definition, pre/post scripts, validation, env vars,
and timeout. Jira credentials stay on the runner; the sandbox
gets only FULLSEND_WORK_ITEM_URL.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Create the agent definition

**Files:**
- Create: `.fullsend/agents/rfe-creator.md`

- [ ] **Step 1: Create the agent definition**

```markdown
# RFE Creator Agent

You are an RFE review and improvement agent. Your workspace contains
a pre-fetched RFE in `artifacts/rfe-tasks/`. Your job is to review,
score, and improve it.

## Instructions

1. Identify the RFE file in `artifacts/rfe-tasks/`
2. Run `/rfe.speedrun --headless --dry-run <ISSUE_KEY>` where ISSUE_KEY
   is the `rfe_id` from the file's frontmatter
3. Do not contact Jira. All artifacts are local. Jira submission is
   handled externally after you complete.
4. When the skill completes, your work is done. The artifacts you
   produced will be validated and submitted by external tooling.
```

- [ ] **Step 2: Commit**

```bash
git add .fullsend/agents/rfe-creator.md
git commit -S -s -m "$(cat <<'EOF'
Add fullsend agent definition for rfe-creator

Adapter layer between fullsend and the rfe.speedrun skill.
Instructs the agent to work on pre-fetched local artifacts
with --headless --dry-run; no Jira contact from the sandbox.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Create the pre-fetch script

**Files:**
- Create: `.fullsend/scripts/pre-fetch.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Extract issue key from URL
# e.g., https://issues.redhat.com/browse/PROJ-1234 -> PROJ-1234
ISSUE_KEY="${FULLSEND_WORK_ITEM_URL##*/}"

# Fetch issue and write all artifact files (task, original, comments)
python3 scripts/fetch_issue.py "$ISSUE_KEY" --fetch-all artifacts
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x .fullsend/scripts/pre-fetch.sh`

- [ ] **Step 3: Run shellcheck**

Run: `shellcheck .fullsend/scripts/pre-fetch.sh`
Expected: No issues.

- [ ] **Step 4: Commit**

```bash
git add .fullsend/scripts/pre-fetch.sh
git commit -S -s -m "$(cat <<'EOF'
Add fullsend pre-fetch script

Extracts issue key from FULLSEND_WORK_ITEM_URL and runs
fetch_issue.py --fetch-all to stage artifacts for the agent.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Create the validation script

**Files:**
- Create: `.fullsend/scripts/validate-artifacts.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
set -euo pipefail

errors=0

# Validate all task files
for f in artifacts/rfe-tasks/*.md; do
    [[ "$(basename "$f")" == *-comments.md ]] && continue
    python3 scripts/frontmatter.py read "$f" > /dev/null || errors=$((errors + 1))
done

# Validate all review files
for f in artifacts/rfe-reviews/*-review.md; do
    python3 scripts/frontmatter.py read "$f" > /dev/null || errors=$((errors + 1))
done

# Verify all rfe_ids belong to expected project or are DRAFT-NNN (split children)
for f in artifacts/rfe-tasks/*.md; do
    [[ "$(basename "$f")" == *-comments.md ]] && continue
    rfe_id=$(python3 scripts/frontmatter.py read "$f" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['rfe_id'])")
    case "$rfe_id" in
        DRAFT-*) ;;
        "${JIRA_PROJECT}"-*) ;;
        *) echo "ERROR: unexpected project in rfe_id: $rfe_id"
           errors=$((errors + 1)) ;;
    esac
done

# Collect recommendations (exits non-zero if review files missing/broken)
python3 scripts/collect_recommendations.py --from-reviews || errors=$((errors + 1))

exit "$errors"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x .fullsend/scripts/validate-artifacts.sh`

- [ ] **Step 3: Run shellcheck**

Run: `shellcheck .fullsend/scripts/validate-artifacts.sh`
Expected: No issues. If shellcheck flags the `${JIRA_PROJECT}` in the case
pattern, add a `# shellcheck disable=SC2254` above that case statement (variable
in case pattern is intentional here).

- [ ] **Step 4: Commit**

```bash
git add .fullsend/scripts/validate-artifacts.sh
git commit -S -s -m "$(cat <<'EOF'
Add fullsend artifact validation script

Validates frontmatter on all task and review files, checks that
rfe_ids belong to the expected JIRA_PROJECT or are DRAFT-NNN
split children, and verifies recommendations are parseable.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Create the post-submit script

**Files:**
- Create: `.fullsend/scripts/post-submit.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
set -euo pipefail

# Get recommendations — outputs lines like SUBMIT=PROJ-1234,DRAFT-001
eval "$(python3 scripts/collect_recommendations.py --from-reviews)"

if [[ -z "${SUBMIT:-}" ]]; then
    echo "No RFEs recommended for submission. Done."
    exit 0
fi

# Submit all passing RFEs
python3 scripts/submit.py --ids "$SUBMIT" --artifacts-dir artifacts
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x .fullsend/scripts/post-submit.sh`

- [ ] **Step 3: Run shellcheck**

Run: `shellcheck .fullsend/scripts/post-submit.sh`
Expected: No issues. If shellcheck flags the `eval`, add
`# shellcheck disable=SC2046` — the eval of collect_recommendations output
is intentional and controlled (we own the script producing the output).

- [ ] **Step 4: Commit**

```bash
git add .fullsend/scripts/post-submit.sh
git commit -S -s -m "$(cat <<'EOF'
Add fullsend post-submit script

Reads review recommendations via collect_recommendations.py
--from-reviews and calls submit.py for RFEs recommended for
submission. Exits cleanly when nothing is recommended.

Assisted-by: Claude claude-opus-4-6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Run full lint and verify

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full lint suite**

Run: `make lint`
Expected: All checks pass — skillsaw, ruff, shellcheck, pytest.

- [ ] **Step 2: Verify the file layout**

Run: `find .fullsend -type f | sort`
Expected:
```
.fullsend/agents/rfe-creator.md
.fullsend/harnesses/rfe-creator.yaml
.fullsend/scripts/post-submit.sh
.fullsend/scripts/pre-fetch.sh
.fullsend/scripts/validate-artifacts.sh
```
