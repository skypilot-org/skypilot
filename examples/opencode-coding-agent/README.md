# OpenCode Coding Agent on SkyPilot

This example runs [OpenCode](https://opencode.ai) as a headless coding agent on Kubernetes via SkyPilot. Give it a GitHub repo and a task — it clones the repo, runs the agent, commits the changes, and pushes a branch.

## Prerequisites

1. An [Anthropic API key](https://console.anthropic.com/)
2. A [GitHub personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) with repo read/write access

Set them as environment variables so SkyPilot can pass them as secrets:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_TOKEN=ghp_...
```

## Quick start

Launch a single coding agent:

```bash
# Replace the below values as needed
sky launch -c opencode-test agent-task.yaml --infra k8s \
  --secret GITHUB_TOKEN \
  --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot \
  --env AGENT_TASK="Make the README more concise" \
  --env BRANCH_NAME=update-readme -y
```

The agent will:
1. Clone the repo
2. Create branch `opencode/update-readme`
3. Run OpenCode to complete the task
4. Commit and push the branch
5. Print a link to open a PR

See [example PR](https://github.com/alex000kim/skypilot/pull/2) created by the agent.

<details>
<summary>Example output (click to expand)</summary>

```
YAML to run: agent-task.yaml
Running on cluster: opencode-test
⚙︎ Launching on Kubernetes.
└── Pod is up.
✓ Cluster launched: opencode-test.
⚙︎ Syncing files.
✓ Synced workdir.
✓ Setup detached.
⚙︎ Job submitted, ID: 2
├── Waiting for task resources on 1 node.
└── Job started. Streaming logs... (Ctrl-C to exit log streaming; job will not be killed)
(opencode-agent, pid=1030) Cloning into '/tmp/opencode-workspace'...
(opencode-agent, pid=1030) Switched to a new branch 'opencode/update-readme'
(opencode-agent, pid=1030) ==> Task: Make the README more concise
(opencode-agent, pid=1030)
(opencode-agent, pid=1030) > build · claude-sonnet-4-6
(opencode-agent, pid=1030)
(opencode-agent, pid=1030) → Read README.md
(opencode-agent, pid=1030) Here's my plan for making the README more concise:
(opencode-agent, pid=1030)
(opencode-agent, pid=1030) # Todos
(opencode-agent, pid=1030) [ ] Trim verbose sections: news items, overview bullets, task YAML comments,
(opencode-agent, pid=1030)     step-by-step list, more information links
(opencode-agent, pid=1030)
(opencode-agent, pid=1030) Key cuts I'll make:
(opencode-agent, pid=1030) - **News**: Keep only the 3 most recent items
(opencode-agent, pid=1030) - **Overview**: Collapse 4 sections into a tighter bullet list
(opencode-agent, pid=1030) - **Task YAML**: Remove inline comments
(opencode-agent, pid=1030) - **`sky launch` steps**: Remove the numbered explanation list
(opencode-agent, pid=1030) - **More information**: Collapse redundant links
(opencode-agent, pid=1030) ← Edit README.md (4 edits applied)
(opencode-agent, pid=1030)
(opencode-agent, pid=1030) Done. Changes made:
(opencode-agent, pid=1030) - **News**: Trimmed from 11 items to 3 (most recent)
(opencode-agent, pid=1030) - **Overview**: Collapsed 4 verbose subsections into 5 tight bullets
(opencode-agent, pid=1030) - **Task YAML**: Removed all inline comments
(opencode-agent, pid=1030) - **`sky launch` output**: Replaced a 5-item numbered list with a single sentence
(opencode-agent, pid=1030) - **More information**: Collapsed ~15 scattered links into 3 compact lines
(opencode-agent, pid=1030)
(opencode-agent, pid=1030) $ git commit -m "[Docs] Condense README overview and news sections for clarity"
(opencode-agent, pid=1030) [opencode/update-readme 3712020] [Docs] Condense README overview and news sections
(opencode-agent, pid=1030)  1 file changed, 12 insertions(+), 64 deletions(-)
(opencode-agent, pid=1030)
(opencode-agent, pid=1030) Branch pushed: opencode/update-readme
(opencode-agent, pid=1030) Open a PR: https://github.com/alex000kim/skypilot/compare/master...opencode/update-readme
✓ Job finished (status: SUCCEEDED).
```
</details>

## Follow-up tasks

Run another task on the same cluster and branch — this reuses the existing environment and skips setup:

```bash
sky exec opencode-test agent-task.yaml \
  --secret GITHUB_TOKEN \
  --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot \
  --env AGENT_TASK="Add a table of contents to the README" \
  --env USE_BRANCH=update-readme
```

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GITHUB_REPO` | yes | GitHub repo in `owner/repo` format |
| `AGENT_TASK` | yes | Natural-language task for the agent |
| `BRANCH_NAME` | no | Branch suffix; creates `opencode/<BRANCH_NAME>` (default: `opencode/agent`) |
| `USE_BRANCH` | no | Check out existing `opencode/<USE_BRANCH>` instead of creating a new one |
| `LLM_MODEL_ID` | no | Anthropic model ID (default: `anthropic/claude-sonnet-4-6`) |

## How it works

### Task YAML (`agent-task.yaml`)

The SkyPilot YAML defines the environment:
- **Resources**: CPU-only (no GPU needed for coding agents)
- **Secrets**: `ANTHROPIC_API_KEY` and `GITHUB_TOKEN` are passed via SkyPilot secrets, which keeps them out of logs and the dashboard
- **Setup**: Installs Node.js and OpenCode once; subsequent `sky exec` calls skip this step

### Entrypoint (`run_agent.sh`)

The script handles the full workflow:
1. **Clone** the repo using the GitHub token for authentication
2. **Branch** - creates a new branch or checks out an existing one
3. **Run** the OpenCode agent with the provided task
4. **Commit** - uses the agent to generate an informative commit message
5. **Push** the branch and print a PR link

Git identity is automatically set from the GitHub token owner - no manual `git config` needed.

## References

- [SkyPilot Documentation](https://docs.skypilot.co/)
- [OpenCode](https://opencode.ai)
- [SkyPilot Secrets](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html#secrets)
