# RFE Creator Fullsend Agents

**Note**: Fullsend agents for `rfe-creator` are under development and in a very alpha stage.
They only allow for local execution or a heavily customized workflow. They are not yet
in a state to run them within Fullsend on GitHub or GitLab default Fullsend workflows. You
can find instructions on how to run Fullsend agents locally in the
[fullsend documentation](https://fullsend.sh) and in each agent README's here.

This folder contains multiple agents implemented for [Fullsend](https://fullsend.sh) that make
use of the RFE Creator skills.

To use these agents run add them to your configuration file with:

```bash
fullsend agent add --fullsend-dir .fullsend https://github.com/opendatahub-io/rfe-creator/blob/main/fullsend-agents/rfe-creator/rfe-creator.yaml
```

For this change on your `.fullsend/config.yaml` file:

```yaml
agents:
- source: https://raw.githubusercontent.com/opendatahub-io/rfe-creator/<SHA>/fullsend-agents/rfe-creator/rfe-creator.yaml#sha256=<SHA256>
  ref: main
```

Each agent has its own README for more installation instructions:

* [rfe-creator](./rfe-creator/README.md)
