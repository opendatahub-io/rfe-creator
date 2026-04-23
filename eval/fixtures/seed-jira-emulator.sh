#!/bin/bash
# Seed the jira-emulator with RFE issues from a previous eval run.
#
# Reads RFE task files from $RUNS_DIR/<run-id>/cases/*/artifacts/rfe-tasks/
# and creates them as Feature Request issues in the RHAIRFE project.
# Uses the frontmatter title as summary and the markdown body as description.
#
# Usage:
#   bash eval/fixtures/seed-jira-emulator.sh [server_url] [run_id]
#
# Defaults:
#   server_url: http://localhost:8080
#   run_id:     2026-04-19-opus-46-v2
#
# Prerequisites:
#   - jira-emulator running
#   - python3 available

set -euo pipefail

SERVER="${1:-http://localhost:8080}"
RUN_ID="${2:-2026-04-19-opus-46-v2}"
RUNS_DIR="${AGENT_EVAL_RUNS_DIR:-eval/runs}"

echo "Seeding jira-emulator at $SERVER from run $RUN_ID..."

# Wait for server to be ready
for i in $(seq 1 30); do
    if curl -sf "$SERVER/rest/api/2/priority" > /dev/null 2>&1; then
        break
    fi
    echo "  Waiting for server... ($i/30)"
    sleep 1
done

python3 - "$SERVER" "$RUNS_DIR/$RUN_ID" << 'SEED_EOF'
import json, urllib.request, base64, sys, os, re
from pathlib import Path

server = sys.argv[1]
run_dir = Path(sys.argv[2])

creds = base64.b64encode(b"admin:admin").decode()
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Basic {creds}",
}

cases_dir = run_dir / "cases"
if not cases_dir.is_dir():
    print(f"ERROR: {cases_dir} not found", file=sys.stderr)
    sys.exit(1)

created = 0
total = 0

for case_dir in sorted(cases_dir.iterdir()):
    if not case_dir.is_dir():
        continue
    rfe_dir = case_dir / "artifacts" / "rfe-tasks"
    if not rfe_dir.is_dir():
        continue
    for rfe_file in sorted(rfe_dir.glob("*.md")):
        total += 1
        content = rfe_file.read_text()

        # Parse frontmatter
        parts = content.split("---", 2)
        if len(parts) < 3:
            print(f"  SKIP: {rfe_file.name} — no frontmatter", file=sys.stderr)
            continue

        # Extract title from frontmatter
        title = ""
        for line in parts[1].strip().splitlines():
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip("'\"")
                break

        if not title:
            print(f"  SKIP: {rfe_file.name} — no title", file=sys.stderr)
            continue

        # Use markdown body as description (strip frontmatter)
        body = parts[2].strip()

        payload = json.dumps({"fields": {
            "project": {"key": "RHAIRFE"},
            "issuetype": {"name": "Feature Request"},
            "summary": title,
            "description": body,
            "priority": {"name": "Major"},
        }}).encode()

        req = urllib.request.Request(f"{server}/rest/api/2/issue",
                                    data=payload, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req)
            key = json.loads(resp.read())["key"]
            print(f"  {key}: {title}")
            created += 1
        except Exception as e:
            print(f"  FAILED: {title} — {e}", file=sys.stderr)

print(f"Done. Seeded {created}/{total} issues from {run_dir.name}.")
SEED_EOF
