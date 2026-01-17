# Multi-Instance Local Development

Run multiple isolated copies of SkyPilot simultaneously for local development.
Each instance runs in its own Docker container with isolated state.

## Requirements

- Docker

## Quick Start

```bash
cd /path/to/skypilot-worktree
source ./dev/multi-instance/activate   # Auto-creates .sky-dev/ if needed

sky api start
sky status
sky api stop

deactivate-sky   # When done
```

## How It Works

1. `activate` auto-runs `setup.sh` to create `.sky-dev/` if needed
2. Scripts find the instance by looking for `.git` in parent directories
3. All `sky` commands run inside a Docker container via `docker exec`
4. Each container has isolated state, unique port, but shares repo code

## Scripts

| Script | Description |
|--------|-------------|
| `activate` | Source this to set up aliases and prompt |
| `setup.sh` | Creates `.sky-dev/` (called automatically by activate) |
| `start-container` | Creates/starts the Docker container |
| `stop-container` | Stops the container |
| `sky` | Runs sky commands in the container |

## Directory Structure

```
<worktree>/
└── .sky-dev/
    ├── .instance    # Instance config (name, port)
    ├── state/       # Container's ~/.sky/ (persisted)
    └── sky_logs/    # Container's ~/sky_logs/ (persisted)
```

## Port Allocation

Each worktree gets a deterministic port (46501-46599) based on a hash of the repo path.

## Environment Variables

The `sky` wrapper passes through: `SKYPILOT_DEV`, `SKYPILOT_DEBUG`, `AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS`, `AZURE_SUBSCRIPTION_ID`, `KUBECONFIG`

## Cleanup

```bash
stop-container
docker rm skypilot-dev-<name>
rm -rf .sky-dev
```
