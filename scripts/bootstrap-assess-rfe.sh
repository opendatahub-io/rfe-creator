#!/bin/bash
# Ensures the assess-rfe plugin is available locally, checked out at the commit
# the type descriptor pins (pipeline.rubric.ref, design §7.3 / Q9) — or at
# ASSESS_RFE_REF when that is set (a branch, tag or commit for an ad-hoc run).
# Safe to run multiple times — clones on first run, fetches and re-pins after.
#
# Usage: bootstrap-assess-rfe.sh [--type rfe|initiative]
#
# The caller declares which pipeline's assets it needs. Validating only the
# RFE rubric lets a checkout that lacks the initiative rubric or the
# initiative-scorer agent exit 0, after which the ASSESS phase can never
# complete and wait-for-wave spins on exit 3 with nothing to diagnose.

PIPELINE_TYPE="rfe"
while [ $# -gt 0 ]; do
  case "$1" in
    --type)
      PIPELINE_TYPE="$2"
      shift 2
      ;;
    --type=*)
      PIPELINE_TYPE="${1#--type=}"
      shift
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      echo "Usage: bootstrap-assess-rfe.sh [--type rfe|initiative]" >&2
      exit 2
      ;;
  esac
done

# The registered type names come from the registry (scripts/type_registry.py list,
# one name per line): an unknown --type fails with the registered list (design §5
# rung 1) and a type added under types/ is accepted without editing this script.
# Validation stays ahead of the RFE_SKIP_BOOTSTRAP short-circuit — a mistyped
# pipeline must fail even in an offline run. This script IS the dependency
# bootstrap, so it must not need a bootstrapped Python itself: when the registry
# cannot be read (no PyYAML yet, no python3 on PATH) the names fall back to the
# shipped root, <scripts>/../types/<name>/type.yaml — the set `list` prints when
# RFE_CREATOR_EXTRA_TYPES is unset (a drop-in root needs the registry) — and the
# registry's own stderr is not shown, so a valid --type behaves exactly as before
# the registry existed. Only when that root holds no descriptor either is the
# failure fatal (exit 2).
SCRIPT_DIR="$(dirname "$0")"
TYPES_ROOT="$SCRIPT_DIR/../types"

registered_types_from_root() {
  # rfe first, then the rest in glob order — the order `list` prints (names()).
  # Directories starting with "_" (types/_schema) are never types.
  local descriptor name rest="" have_rfe=0
  for descriptor in "$TYPES_ROOT"/*/type.yaml; do
    [ -f "$descriptor" ] || continue
    name="$(basename "$(dirname "$descriptor")")"
    case "$name" in _*) continue ;; esac
    if [ "$name" = "rfe" ]; then
      have_rfe=1
    else
      rest+="$name"$'\n'
    fi
  done
  if [ "$have_rfe" -eq 1 ]; then
    echo "rfe"
  fi
  printf '%s' "$rest"
}

REGISTERED="$(python3 "$SCRIPT_DIR/type_registry.py" list 2>/dev/null)" || REGISTERED=""
if [ -z "$REGISTERED" ]; then
  REGISTERED="$(registered_types_from_root)"
  if [ -z "$REGISTERED" ]; then
    echo "ERROR: could not read the type registry (python3 $SCRIPT_DIR/type_registry.py list failed and $TYPES_ROOT holds no <name>/type.yaml)" >&2
    exit 2
  fi
fi
KNOWN=0
REGISTERED_LIST=""
while IFS= read -r registered_type; do
  [ -n "$registered_type" ] || continue
  if [ "$registered_type" = "$PIPELINE_TYPE" ]; then
    KNOWN=1
  fi
  REGISTERED_LIST="${REGISTERED_LIST:+$REGISTERED_LIST, }$registered_type"
done <<EOF
$REGISTERED
EOF
if [ "$KNOWN" -ne 1 ]; then
  echo "ERROR: unknown --type '$PIPELINE_TYPE' (registered types: $REGISTERED_LIST)" >&2
  exit 2
fi

if [ -n "${RFE_SKIP_BOOTSTRAP:-}" ]; then
  echo "RFE_SKIP_BOOTSTRAP set - skipping dependency bootstrapping step"
  exit 0
fi

CONTEXT_DIR=".context/assess-rfe"

# One field of the descriptor's `rubric:` block, without the registry (no PyYAML
# yet, no python3): the `<key>:` line under `  rubric:`, quotes stripped.
rubric_field_from_root() {
  awk -v key="$1" '/^  rubric:/ { f = 1; next }
       f && $1 == key ":" { gsub(/"/, "", $2); print $2; exit }
       f && /^  [a-z]/ { exit }' "$TYPES_ROOT/$PIPELINE_TYPE/type.yaml" 2>/dev/null
}

# The repository this checkout is cloned from. The descriptor owns it
# (pipeline.rubric.repo; validate_types rule 6 keeps every external rubric on
# ONE repo, since there is one checkout); ASSESS_RFE_REPO overrides it for an
# ad-hoc run. Read through the registry, then the descriptor line, and only
# when neither can be read the historical default. A bare `owner/repo` slug
# (the schema's other spelling) is a GitHub repository.
if [ -n "${ASSESS_RFE_REPO:-}" ]; then
  ASSESS_REPO="$ASSESS_RFE_REPO"
  REPO_SOURCE="ASSESS_RFE_REPO"
else
  ASSESS_REPO="$(python3 "$SCRIPT_DIR/type_registry.py" get "$PIPELINE_TYPE" pipeline.rubric.repo 2>/dev/null)" || ASSESS_REPO=""
  if [ -z "$ASSESS_REPO" ]; then
    ASSESS_REPO="$(rubric_field_from_root repo)"
  fi
  if [ -n "$ASSESS_REPO" ]; then
    REPO_SOURCE="types/$PIPELINE_TYPE/type.yaml pipeline.rubric.repo"
  else
    ASSESS_REPO="https://github.com/opendatahub-io/assess-rfe"
    REPO_SOURCE="built-in default"
  fi
fi
case "$ASSESS_REPO" in
  *://*|*@*:*) ;;
  *) ASSESS_REPO="https://github.com/$ASSESS_REPO" ;;
esac
# Scripts now live under each skill dir (assess-rfe moved them out of the repo
# root in opendatahub-io/assess-rfe#5 "move-scripts-to-skill-dirs").
RUBRIC_FILE="$CONTEXT_DIR/skills/assess-rfe/scripts/agent_prompt.md"
# Mirrors PIPELINE_TYPES["initiative"] rubric_path/scorer_type in
# scripts/pipeline_state.py; tests/test_bootstrap_assess.py pins them together.
INITIATIVE_RUBRIC="$CONTEXT_DIR/skills/assess-initiative/scripts/agent_prompt.md"
INITIATIVE_AGENT="initiative-scorer.md"

# The commit this checkout must sit at. The descriptor owns the pin
# (pipeline.rubric.ref); ASSESS_RFE_REF overrides it for an ad-hoc run. Read
# through the registry like the type list above, with the same no-Python
# fallback. Every descriptor pins the same commit (validate_types rule 6), so
# the requested type's pin is the checkout's pin.
if [ -n "${ASSESS_RFE_REF:-}" ]; then
  ASSESS_REF="$ASSESS_RFE_REF"
  REF_SOURCE="ASSESS_RFE_REF"
else
  ASSESS_REF="$(python3 "$SCRIPT_DIR/type_registry.py" get "$PIPELINE_TYPE" pipeline.rubric.ref 2>/dev/null)" || ASSESS_REF=""
  if [ -z "$ASSESS_REF" ]; then
    ASSESS_REF="$(rubric_field_from_root ref)"
  fi
  if [ -z "$ASSESS_REF" ]; then
    echo "ERROR: could not read pipeline.rubric.ref for type '$PIPELINE_TYPE' (python3 $SCRIPT_DIR/type_registry.py get failed and $TYPES_ROOT/$PIPELINE_TYPE/type.yaml holds no rubric ref)" >&2
    exit 2
  fi
  REF_SOURCE="types/$PIPELINE_TYPE/type.yaml pipeline.rubric.ref"
fi

if [ ! -d "$CONTEXT_DIR" ]; then
  echo "cloning assess-rfe from $ASSESS_REPO ($REPO_SOURCE)"
  git clone "$ASSESS_REPO" "$CONTEXT_DIR" 2>&1
fi

# A git checkout is whatever `rev-parse --git-dir` accepts: a clone (.git is a
# directory), a worktree or a submodule (.git is a file). A checkout owned by
# another user is refused by git ("dubious ownership") and that refusal is
# respected — never overridden with safe.directory: a foreign-owned repository
# can carry hooks a checkout would run with this account's privileges. Such a
# checkout is one git cannot operate on, and its vendored files are used as
# found (the WARN branch below).
gitc() {
  git -C "$CONTEXT_DIR" "$@"
}
pin_is_commit() {
  # A commit pin is all-hex, at least 7 characters, and resolves to a commit
  # whose id starts with it. Anything else — a branch, a tag, even a hex-named
  # one like `cafe` — is a ref name with nothing to compare HEAD against.
  case "$1" in *[!0-9a-f]*) return 1 ;; esac
  [ "${#1}" -ge 7 ] || return 1
  local id
  id="$(gitc rev-parse --verify -q "$1^{commit}" 2>/dev/null)" || return 1
  case "$id" in "$1"*) return 0 ;; *) return 1 ;; esac
}

GIT_ERR="$(gitc rev-parse --git-dir 2>&1 >/dev/null)"
if [ -e "$CONTEXT_DIR/.git" ] && ! gitc rev-parse --git-dir >/dev/null 2>&1; then
  # Present but unusable (another UID, an unreadable .git): the vendored files
  # are still there, so keep the pre-pin behaviour — use them, say why.
  echo "WARN: git cannot operate on $CONTEXT_DIR (${GIT_ERR:-unknown error}); the rubric pin $ASSESS_REF ($REF_SOURCE) is not enforced, using the checkout as is" >&2
elif ! gitc rev-parse --git-dir >/dev/null 2>&1; then
  echo "WARN: $CONTEXT_DIR is not a git checkout; the rubric pin $ASSESS_REF ($REF_SOURCE) is not enforced" >&2
else
  HEAD_SHA="$(gitc rev-parse HEAD 2>/dev/null)" || HEAD_SHA=""
  AT_PIN=0
  if [ -n "$HEAD_SHA" ] && pin_is_commit "$ASSESS_REF"; then
    case "$HEAD_SHA" in "$ASSESS_REF"*) AT_PIN=1 ;; esac
  fi
  if [ "$AT_PIN" -eq 1 ]; then
    # Already where the pin says: no network, nothing to move.
    echo "assess-rfe at ${HEAD_SHA:0:12} ($REF_SOURCE, already checked out)"
  else
    # Refresh the objects (a detached checkout cannot `pull`); offline, the
    # cached objects may already hold the pin.
    gitc fetch --quiet origin 2>&1 || echo "WARN: assess-rfe fetch failed, using cached objects" >&2
    # A full SHA (GitHub serves reachable commits by name) or a branch/tag
    # override lands in FETCH_HEAD; an abbreviated SHA only resolves locally.
    if gitc fetch --quiet origin "$ASSESS_REF" 2>/dev/null; then
      CHECKOUT_TARGET="FETCH_HEAD"
    else
      CHECKOUT_TARGET="$ASSESS_REF"
    fi
    if CHECKOUT_ERR="$(gitc checkout --quiet --detach "$CHECKOUT_TARGET" 2>&1 >/dev/null)"; then
      HEAD_SHA="$(gitc rev-parse HEAD 2>/dev/null)"
      if pin_is_commit "$ASSESS_REF"; then
        case "$HEAD_SHA" in
          "$ASSESS_REF"*) ;;
          *)
            echo "ERROR: assess-rfe checkout is at $HEAD_SHA, not the pinned $ASSESS_REF ($REF_SOURCE)" >&2
            exit 1
            ;;
        esac
      fi
      echo "assess-rfe at ${HEAD_SHA:0:12} ($REF_SOURCE)"
    elif [ -z "$HEAD_SHA" ]; then
      # No readable HEAD and no way to move: git cannot operate here either.
      echo "WARN: git cannot check out $CONTEXT_DIR (${CHECKOUT_ERR:-unknown error}); the rubric pin $ASSESS_REF ($REF_SOURCE) is not enforced, using the checkout as is" >&2
    else
      # A positive mismatch: HEAD is readable, is not the pin, and the pin
      # cannot be reached. git's own message tells a commit the remote lacks
      # from local modifications or a stale index.lock.
      echo "ERROR: could not check out assess-rfe at $ASSESS_REF ($REF_SOURCE) in $CONTEXT_DIR (HEAD is ${HEAD_SHA:0:12}): ${CHECKOUT_ERR:-no error text}" >&2
      exit 1
    fi
  fi
fi

# Validate that the rubric file exists after cloning
if [ ! -f "$RUBRIC_FILE" ]; then
  echo "ERROR: Rubric file not found at $RUBRIC_FILE after bootstrap" >&2
  exit 1
fi

if [ "$PIPELINE_TYPE" = "initiative" ] && [ ! -f "$INITIATIVE_RUBRIC" ]; then
  echo "ERROR: Initiative rubric not found at $INITIATIVE_RUBRIC after bootstrap" >&2
  echo "       $ASSESS_REPO${ASSESS_RFE_REF:+ @ $ASSESS_RFE_REF} provides no assess-initiative skill." >&2
  echo "       Set ASSESS_RFE_REPO/ASSESS_RFE_REF to a checkout that has it." >&2
  exit 1
fi

# Copy all skills from the plugin, including their bundled scripts/ so the
# copied SKILL.md's ${CLAUDE_SKILL_DIR}/scripts/... references resolve at
# runtime (scripts are co-located with each SKILL.md as of assess-rfe#5).
for skill_dir in "$CONTEXT_DIR"/skills/*/; do
  skill_name=$(basename "$skill_dir")
  target=".claude/skills/$skill_name"
  mkdir -p "$target"
  cp -r "$skill_dir". "$target/"
done

# Install agent definitions
if [ -d "$CONTEXT_DIR/agents" ]; then
  mkdir -p .claude/agents
  cp "$CONTEXT_DIR"/agents/*.md .claude/agents/
fi

if [ "$PIPELINE_TYPE" = "initiative" ] && [ ! -f ".claude/agents/$INITIATIVE_AGENT" ]; then
  echo "ERROR: Agent definition .claude/agents/$INITIATIVE_AGENT not installed by bootstrap" >&2
  echo "       The initiative assess agent launches with subagent_type: initiative-scorer;" >&2
  echo "       without it the ASSESS phase never completes." >&2
  exit 1
fi

# Export rubric to artifacts
python3 "$CONTEXT_DIR/skills/export-rubric/scripts/export_rubric.py" 2>/dev/null || true
