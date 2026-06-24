# OpenCode Coding Agent on SkyPilot

Run [OpenCode's web UI](https://opencode.ai/docs/web/) on a cloud instance via SkyPilot. It clones your GitHub repo and starts the OpenCode web interface so you can interact with the coding agent from your browser.

## Prerequisites

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # https://console.anthropic.com/
export GITHUB_TOKEN=ghp_...           # Needs repo read/write access
```

## Quick start

```bash
sky launch -c opencode-test agent-task.yaml --infra k8s \
  --secret GITHUB_TOKEN \
  --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot -y
```

Once the cluster is up, forward the port and open the UI in your browser:

```bash
ssh -L 8080:localhost:8080 opencode-test
# Then open http://localhost:8080
```

![OpenCode Web UI](https://i.imgur.com/P4jByQc.png)

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GITHUB_REPO` | yes | GitHub repo in `owner/repo` format |
| `LLM_MODEL_ID` | no | Model ID (default: `anthropic/claude-sonnet-4-6`) |

## References

- [SkyPilot Docs](https://docs.skypilot.co/) / [OpenCode Web UI](https://opencode.ai/docs/web/) / [SkyPilot Secrets](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html#secrets)
