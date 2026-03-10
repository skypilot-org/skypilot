# OpenCode Coding Agent on SkyPilot

Run [OpenCode](https://opencode.ai) as a headless coding agent via SkyPilot. Give it a repo and a task — it clones, runs the agent, commits, and pushes a branch.

## Prerequisites

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # https://console.anthropic.com/
export GITHUB_TOKEN=ghp_...           # Needs repo read/write access
```

## Quick start: single task with `sky launch`

```bash
sky launch -c opencode-test agent-task.yaml --infra k8s \
  --secret GITHUB_TOKEN \
  --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot \
  --env AGENT_TASK="Make the README more concise" \
  --env BRANCH_NAME=update-readme -y
```

The agent clones the repo, creates branch `opencode/update-readme`, completes the task, commits, and pushes. See [example PR](https://github.com/alex000kim/skypilot/pull/2).

<details>
<summary>Example output</summary>

```
(opencode-agent, pid=1030) Cloning into '/tmp/opencode-workspace'...
(opencode-agent, pid=1030) Switched to a new branch 'opencode/update-readme'
(opencode-agent, pid=1030) ==> Task: Make the README more concise
(opencode-agent, pid=1030) > build · claude-sonnet-4-6
(opencode-agent, pid=1030) ← Edit README.md (4 edits applied)
(opencode-agent, pid=1030) $ git commit -m "[Docs] Condense README overview and news sections for clarity"
(opencode-agent, pid=1030) Branch pushed: opencode/update-readme
(opencode-agent, pid=1030) Open a PR: https://github.com/alex000kim/skypilot/compare/master...opencode/update-readme
✓ Job finished (status: SUCCEEDED).
```
</details>

Run a follow-up task on the same cluster (reuses existing environment, skips setup):

```bash
sky exec opencode-test agent-task.yaml \
  --secret GITHUB_TOKEN --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot \
  --env AGENT_TASK="Add a table of contents to the README" \
  --env USE_BRANCH=update-readme
```

## Scaling with worker pools

`sky launch` works for one-off tasks but re-provisions and re-installs everything each time. **Worker pools** keep warm workers with Node.js + OpenCode pre-installed — jobs start in seconds.

### 1. Create the pool

```bash
sky jobs pool apply -p opencode-pool pool.yaml
```

Creates 3 warm workers. Check status with `sky jobs pool status opencode-pool`.

### 2. Submit tasks

```bash
# Single task
sky jobs launch -p opencode-pool pool-job.yaml \
  --secret GITHUB_TOKEN --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot \
  --env AGENT_TASK="Make the README more concise" \
  --env BRANCH_NAME=update-readme

# Multiple tasks in parallel (one per worker, no setup delay)
sky jobs launch -p opencode-pool pool-job.yaml \
  --secret GITHUB_TOKEN --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot \
  --env AGENT_TASK="Add docstrings to public functions in sky/core.py" \
  --env BRANCH_NAME=add-docstrings

sky jobs launch -p opencode-pool pool-job.yaml \
  --secret GITHUB_TOKEN --secret ANTHROPIC_API_KEY \
  --env GITHUB_REPO=alex000kim/skypilot \
  --env AGENT_TASK="Fix the typo in sky/utils/log_utils.py" \
  --env BRANCH_NAME=fix-typo
```

### 3. Monitor and clean up

```bash
sky jobs queue                    # View job status
sky jobs pool down opencode-pool  # Tear down when done
```

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GITHUB_REPO` | yes | GitHub repo in `owner/repo` format |
| `AGENT_TASK` | yes | Natural-language task for the agent |
| `BRANCH_NAME` | no | Branch suffix; creates `opencode/<BRANCH_NAME>` (default: `opencode/agent`) |
| `USE_BRANCH` | no | Check out existing `opencode/<USE_BRANCH>` instead of creating a new one |
| `LLM_MODEL_ID` | no | Anthropic model ID (default: `anthropic/claude-sonnet-4-6`) |

## Files

| File | Purpose |
|---|---|
| `agent-task.yaml` | Single-task YAML for `sky launch` (includes setup) |
| `pool.yaml` | Pool config — number of workers and shared setup |
| `pool-job.yaml` | Job config for pool — same as `agent-task.yaml` minus setup |
| `run_agent.sh` | Entrypoint: clone, branch, run agent, commit, push |

## References

- [SkyPilot Docs](https://docs.skypilot.co/) / [OpenCode](https://opencode.ai) / [SkyPilot Secrets](https://docs.skypilot.co/en/latest/running-jobs/environment-variables.html#secrets)
