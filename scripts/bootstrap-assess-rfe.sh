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
ASSESS_REPO="${ASSESS_RFE_REPO:-https://github.com/opendatahub-io/assess-rfe}"
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
# fallback: the `ref:` line under `rubric:` of the shipped descriptor. Every
# descriptor sharing the repo pins the same commit (validate_types rule 6), so
# the requested type's pin is the checkout's pin.
rubric_ref_from_root() {
  awk '/^  rubric:/ { f = 1; next }
       f && /^    ref:/ { gsub(/"/, "", $2); print $2; exit }
       f && /^  [a-z]/ { exit }' "$TYPES_ROOT/$PIPELINE_TYPE/type.yaml" 2>/dev/null
}
if [ -n "${ASSESS_RFE_REF:-}" ]; then
  ASSESS_REF="$ASSESS_RFE_REF"
  REF_SOURCE="ASSESS_RFE_REF"
else
  ASSESS_REF="$(python3 "$SCRIPT_DIR/type_registry.py" get "$PIPELINE_TYPE" pipeline.rubric.ref 2>/dev/null)" || ASSESS_REF=""
  if [ -z "$ASSESS_REF" ]; then
    ASSESS_REF="$(rubric_ref_from_root)"
  fi
  if [ -z "$ASSESS_REF" ]; then
    echo "ERROR: could not read pipeline.rubric.ref for type '$PIPELINE_TYPE' (python3 $SCRIPT_DIR/type_registry.py get failed and $TYPES_ROOT/$PIPELINE_TYPE/type.yaml holds no rubric ref)" >&2
    exit 2
  fi
  REF_SOURCE="types/$PIPELINE_TYPE/type.yaml pipeline.rubric.ref"
fi

if [ ! -d "$CONTEXT_DIR" ]; then
  git clone "$ASSESS_REPO" "$CONTEXT_DIR" 2>&1
fi

if [ -d "$CONTEXT_DIR/.git" ]; then
  # Refresh the objects (a detached checkout cannot `pull`); offline, the
  # cached objects may already hold the pin.
  git -C "$CONTEXT_DIR" fetch --quiet origin 2>&1 || echo "WARN: assess-rfe fetch failed, using cached objects" >&2
  # A full SHA (GitHub serves reachable commits by name) or a branch/tag
  # override lands in FETCH_HEAD; an abbreviated SHA only resolves locally.
  if git -C "$CONTEXT_DIR" fetch --quiet origin "$ASSESS_REF" 2>/dev/null; then
    CHECKOUT_TARGET="FETCH_HEAD"
  else
    CHECKOUT_TARGET="$ASSESS_REF"
  fi
  if ! git -C "$CONTEXT_DIR" checkout --quiet --detach "$CHECKOUT_TARGET" 2>/dev/null; then
    echo "ERROR: could not check out assess-rfe at $ASSESS_REF ($REF_SOURCE) in $CONTEXT_DIR" >&2
    exit 1
  fi
  HEAD_SHA="$(git -C "$CONTEXT_DIR" rev-parse HEAD 2>/dev/null)"
  # Verify a commit pin resolved to itself (a branch or tag name has nothing to
  # compare against).
  case "$ASSESS_REF" in
    *[!0-9a-f]*) ;;
    *)
      case "$HEAD_SHA" in
        "$ASSESS_REF"*) ;;
        *)
          echo "ERROR: assess-rfe checkout is at $HEAD_SHA, not the pinned $ASSESS_REF ($REF_SOURCE)" >&2
          exit 1
          ;;
      esac
      ;;
  esac
  echo "assess-rfe at ${HEAD_SHA:0:12} ($REF_SOURCE)"
else
  echo "WARN: $CONTEXT_DIR is not a git checkout; the rubric pin $ASSESS_REF ($REF_SOURCE) is not enforced" >&2
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
