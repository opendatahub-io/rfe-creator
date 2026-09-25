#!/bin/bash
# Compatibility name. The workspace bootstrap is scripts/bootstrap.sh (PR-5d); this
# file forwards to it so callers that still name it — the autofixer's job setup, older
# allow rules, the skills-registry entry — keep working until they move.
exec bash "$(dirname "$0")/bootstrap.sh" "$@"
