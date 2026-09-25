# `rfe-creator` Fullsend Agent

When installing this agent, you need environment variables set up. You can
introduce them to `fullsend` by using an dotenv file:

```env
# .env
ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-name>
GOOGLE_CLOUD_PROJECT=<your-gcp-project-name>
CLOUD_ML_REGION=global
GOOGLE_APPLICATION_CREDENTIALS=<your-local-json-key-file-for-gcp>

# In the format of `/rfe.review RHAIRFE-1234 --headless`, `/rfe.auto-fix RHAIRFE-1234 RHAIRFE-5678 --headless`
# `--headless` is always required
FULLSEND_TASK=""
JIRA_SERVER=https://<your-jira-instance>
JIRA_USER="<user-of-the-token@example.com>"
JIRA_TOKEN="<token>"
```

Create a `.fullsend/rfe-creator/jira-policy.yaml` to allow connectivity from within the sandbox to the instance:

```yaml
---
id: jira
display_name: Jira
description: Jira
category: data
endpoints:
  - host: <your-jira-host>
    port: 443
    protocol: rest
    enforcement: enforce
    access: read-write
binaries:
  - path: "**/python"
  - path: "**/python*"
  - path: "**/curl"
```


Then run it with:

```bash
git clone https://github.com/opendatahub-io/rfe-creator.git --branch main /tmp/rfe-creator
fullsend run rfe-creator --fullsend-dir .fullsend --target-repo /tmp/rfe-creator --env-file .env
```

The results of `rfe-creator` are placed inside `/tmp/rfe-creator/artifacts/` and `/tmp/rfe-creator/tmp`.
