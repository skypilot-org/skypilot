# Multi-Instance Local Development

Run multiple isolated copies of SkyPilot simultaneously for local development.
Each instance runs in its own Docker container with isolated state.

## Requirements

- Docker

## Quick Start

```bash
# Per-worktree: run setup
cd /path/to/skypilot-worktree
./dev/multi-instance/setup.sh

# Activate the wrapper (aliases sky, modifies prompt)
source ./dev/multi-instance/activate

# Use sky commands (auto-detects instance from current directory)
start-container
sky api start
sky status
sky api stop
stop-container

# Deactivate when done
deactivate-sky
```

## How It Works

1. `setup.sh` creates `.sky-dev/` in your worktree with instance config
2. Scripts auto-detect the instance by finding `.sky-dev/.instance` in parent directories
3. All `sky` commands run inside a Docker container via `docker exec`
4. Each container has isolated state, unique port, but shares repo code

## Scripts

| Script | Description |
|--------|-------------|
| `setup.sh` | Creates `.sky-dev/` directory with instance config |
| `activate` | Source this to alias `sky` and modify prompt |
| `start-container` | Creates/starts the Docker container |
| `stop-container` | Stops the container |
| `sky` | Runs sky commands in the container |

All scripts (except `setup.sh`) auto-detect the instance by walking up from the current directory looking for `.sky-dev/.instance`.

## Activation

Source `activate` to enable the wrapper:
```bash
source /path/to/skypilot/dev/multi-instance/activate
```

This will:
- Alias `sky`, `start-container`, `stop-container` to the wrappers
- Prefix your prompt with `(sky-dev)` so you know it's active
- Define `deactivate-sky` function to undo everything

Alternatively, add to PATH permanently:
```bash
export PATH="/path/to/skypilot/dev/multi-instance:$PATH"
```

## Directory Structure

```
<worktree>/
├── .sky-dev/
│   ├── .instance    # Instance config (name, port, repo path)
│   ├── .port        # Allocated port number
│   ├── state/       # Container's ~/.sky/ (persisted)
│   └── sky_logs/    # Container's ~/sky_logs/ (persisted)
```

## Port Mapping

Each instance gets a unique host port (46501-46599) mapped to the container's 46580.
The API server inside uses the default port; isolation comes from the container.

## Environment Variables

The `sky` wrapper passes through these env vars to the container:
- `SKYPILOT_DEV`, `SKYPILOT_DEBUG`
- `AWS_*` credentials
- `GOOGLE_APPLICATION_CREDENTIALS`
- `AZURE_SUBSCRIPTION_ID`
- `KUBECONFIG`

## Credentials

Cloud credentials are mounted read-only from your home directory:
`~/.aws`, `~/.kube`, `~/.config/gcloud`, `~/.azure`, `~/.ssh`

## Multiple Worktrees

Each worktree has its own container. The scripts detect which instance to use
based on the current directory, so the same PATH works everywhere:

```bash
cd ~/worktree-1 && sky status  # Uses worktree-1's container
cd ~/worktree-2 && sky status  # Uses worktree-2's container
```

## Cleanup

```bash
stop-container
docker rm skypilot-dev-<instance-name>
rm -rf .sky-dev
```
