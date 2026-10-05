# `rfe-creator` Fullsend Agent

## Prerequisites

### Fullsend installed

See [the Fullsend docs](https://fullsend.sh/docs/guides/user/running-agents-locally) about
how to setup a local Fullsend environment.

### jsonschema and pyyaml

Fullsend requires the Python package `jsonschema` on the host to run schema validation. `pyyaml`
is also required for the pre script of the agent. You can provide them within a Python Virtual
Environment:

```bash
python -m venv venv
source venv/bin/activate
python -m pip install jsonschema pyyaml
fullsend run ...
```

### General CLIs

This agent needs `git` and `curl` installed to clone some repositories and fetch some
resources.

### Environment variables variables

When installing this agent, you need environment variables set up. You can
introduce them to `fullsend` by using an dotenv file:

```env
# Required inference variables
ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-name>
GOOGLE_CLOUD_PROJECT=<your-gcp-project-name>
CLOUD_ML_REGION=global
GOOGLE_APPLICATION_CREDENTIALS=<your-local-json-key-file-for-gcp>

# Required variables
# In the format of `/rfe-review RHAIRFE-1234`, `/rfe-auto-fix RHAIRFE-1234 RHAIRFE-5678`, etc.
FULLSEND_TASK="/rfe-review RHAIRFE-1234"
JIRA_SERVER=https://redhat.atlassian.net
JIRA_USER="<user-of-the-token@example.com>"
JIRA_TOKEN="<token>"
```

Then run it with:

```bash
git clone https://github.com/opendatahub-io/rfe-creator.git --branch main 
cd rfe-creator
fullsend run rfe-creator --fullsend-dir .fullsend --target-repo . --env-file .env
```

The results of `rfe-creator` are placed inside the target repository `artifacts/` and `tmp/`.
Output from the Fullsend run is stored at `/tmp/fullsend/<sandbox>` and it contains metrics, transcripts...
inspect it to debug runs. Running `fullsend run` with `--keep-sandbox` does not delete the OpenShell
sandbox, which is useful to debug as well.

*Note*: `/rfe-review`, `/rfe-auto-fix` and `/rfe-split` are read-only from the
sandbox; `/rfe-speedrun` and `/rfe-submit` fail inside it without `--dry-run`.
The dotted names (`/rfe.review`, ...) are compatibility aliases of the same skills,
and every skill takes `--type <name>` for any type in `python3 scripts/type_registry.py list`.

## Expected log lines

Two warnings appear at agent start inside the sandbox and are inert:

```
Ignoring 40 permissions.allow entries from .claude/settings.json: this workspace has not been trusted.
Ignoring 2 permissions.additionalDirectories entries from .claude/settings.json: this workspace has not been trusted.
```

fullsend keeps the Claude config directory (`/sandbox/claude-config`) outside the
repository on purpose, so a checkout cannot mark itself trusted, and it runs Claude Code
with permissions bypassed, so the allow list is not consulted anyway. The project hooks
in `.claude/settings.json` (compaction recovery, Stop guard) still load.
