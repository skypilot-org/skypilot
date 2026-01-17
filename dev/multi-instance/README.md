# Multi-Instance Local Development

Run multiple isolated copies of SkyPilot simultaneously for local development.
Each instance runs in its own Docker container with isolated state, while sharing
the same repo code and cloud credentials.

## Requirements

- Docker

## Quick Start

```bash
# 1. Navigate to your SkyPilot worktree
cd /path/to/skypilot-worktree

# 2. Run setup (one-time per worktree)
./dev/multi-instance/setup.sh

# 3. Activate the instance
source .sky-dev/bin/activate

# 4. Start the container
start-container

# 5. Use sky commands as normal - they run in the container
sky --help
sky api start
sky status
sky api stop
```

## How It Works

Each worktree gets its own Docker container with:

- **Isolated state**: Container's `/root/.sky/` maps to `.sky-dev/state/`
- **Isolated port**: Each container binds a unique port (46501-46599) to its internal 46580
- **Shared code**: Your repo is mounted at `/app` (changes reflected immediately)
- **Shared credentials**: `~/.aws`, `~/.kube`, etc. mounted read-only

The `sky` wrapper simply runs `docker exec` to execute commands in the container,
so all real code paths are exercised.

## Directory Structure

```
<worktree>/
├── .sky-dev/
│   ├── .instance            # Instance metadata
│   ├── .port                # Allocated port number
│   ├── state/               # Container's ~/.sky/ (isolated)
│   ├── sky_logs/            # Container's ~/sky_logs/
│   ├── venv/                # Python venv (created in container)
│   └── bin/
│       ├── activate         # Source this to set up PATH
│       ├── sky              # Wrapper that docker execs
│       ├── start-container  # Start/create the container
│       └── stop-container   # Stop the container
```

## Commands

### Setup (one-time)

```bash
./dev/multi-instance/setup.sh [instance-name]
```

Instance name defaults to the worktree directory name.

### Activate

```bash
source .sky-dev/bin/activate
```

This adds `.sky-dev/bin/` to your PATH and sets env vars.

### Start the container

```bash
start-container
```

Creates the container if needed, starts it, and sets up the Python venv.
The container runs in the background and persists across terminal sessions.

### Run sky commands

```bash
sky --help
sky api start
sky status
sky launch cluster.yaml
sky api stop
```

All commands run inside the container via `docker exec`.

### Stop the container

```bash
stop-container
```

### Access the container directly

```bash
# Get a shell
docker exec -it skypilot-dev-<instance-name> bash

# Run arbitrary commands
docker exec -it skypilot-dev-<instance-name> /app/.sky-dev/venv/bin/python -c "import sky; print(sky.__version__)"
```

## Multiple Terminals

The container persists, so you can access it from multiple terminals:

```bash
# Terminal 1
source .sky-dev/bin/activate
sky api start

# Terminal 2
source .sky-dev/bin/activate
sky status
```

## Port Mapping

Each instance gets a unique host port mapped to the container's 46580:

- Instance 1: `localhost:46501` → container:46580
- Instance 2: `localhost:46502` → container:46580
- etc.

The API server inside the container always listens on 46580 (the default),
but from the host you'd access it via the mapped port.

## Credentials

Cloud credentials are mounted read-only from your home directory:

- `~/.aws` → `/root/.aws`
- `~/.kube` → `/root/.kube`
- `~/.config/gcloud` → `/root/.config/gcloud`
- `~/.azure` → `/root/.azure`
- `~/.ssh` → `/root/.ssh`

## Cleanup

```bash
# Stop and remove the container
stop-container
docker rm skypilot-dev-<instance-name>

# Remove instance directory
rm -rf .sky-dev
```

## Troubleshooting

### Container won't start

Check Docker is running:
```bash
docker info
```

### Python package issues

Rebuild the venv:
```bash
rm -rf .sky-dev/venv
start-container
```

### Port conflict

Delete `.sky-dev/.port` and re-run setup:
```bash
rm .sky-dev/.port
./dev/multi-instance/setup.sh
```

### See container logs

```bash
docker logs skypilot-dev-<instance-name>
```
