# Multi-Instance Local Development

This directory contains tooling for running multiple isolated copies of SkyPilot
simultaneously for local development. This is useful when you have multiple git
worktrees and want to run different versions/branches of SkyPilot without conflicts.

## The Problem

SkyPilot stores state in several locations that cause conflicts when running multiple instances:

- `~/.sky/` - State databases, config, locks
- Port 46580 - Hardcoded API server port
- `/tmp/skypilot_graceful_shutdown.lock` - Shared lock file
- `~/sky_logs/` - Log files

## The Solution

Each worktree gets its own isolated environment by:

1. **Overriding `$HOME`** - All `~/.sky/` paths become isolated
2. **Overriding `$TMPDIR`** - The `/tmp/` lock file becomes isolated
3. **Unique port per instance** - Each API server runs on a different port (46501-46599)
4. **Symlinking credentials** - Cloud credentials (`.aws`, `.kube`, etc.) are shared via symlinks

## Quick Start

```bash
# 1. Navigate to your SkyPilot worktree
cd /path/to/skypilot-worktree

# 2. Run setup (one-time per worktree)
./dev/multi-instance/setup.sh

# 3. Activate the isolated environment
source .sky-dev/bin/activate

# 4. Use sky commands as normal!
sky api start
sky status
sky launch ...
sky api stop
```

## Directory Structure

After setup, each worktree will have:

```
<worktree>/
├── .sky-dev/                    # Instance-specific directory
│   ├── .instance                # Instance metadata
│   ├── .port                    # Allocated port number
│   ├── server.pid               # Running server PID
│   ├── home/                    # Isolated $HOME
│   │   ├── .sky/                # SkyPilot state (isolated)
│   │   ├── sky_logs/            # Logs (isolated)
│   │   ├── .aws -> ~/.aws       # Symlink to real credentials
│   │   ├── .kube -> ~/.kube     # Symlink to real credentials
│   │   └── .config/
│   │       └── gcloud -> ~/.config/gcloud
│   ├── tmp/                     # Isolated $TMPDIR
│   └── bin/
│       ├── sky                  # Wrapper script
│       └── activate             # Activation script
```

## How It Works

### The `sky` Wrapper

The wrapper script (`.sky-dev/bin/sky`) does several things:

1. **Sets environment variables:**
   - `HOME` → `.sky-dev/home/`
   - `TMPDIR` → `.sky-dev/tmp/`
   - `SKY_RUNTIME_DIR` → `.sky-dev/home/`
   - `SKYPILOT_API_SERVER_ENDPOINT` → `http://127.0.0.1:<port>`

2. **Intercepts `sky api start`:**
   - Starts the server directly with `python -m sky.server.server --port=<port>`
   - Manages PID file for later stopping
   - Waits for server to be ready

3. **Intercepts `sky api stop`:**
   - Kills the server by PID
   - Handles graceful shutdown with fallback to force kill

4. **Passes through other commands:**
   - All other `sky` commands work normally with the isolated environment

### Port Allocation

Ports are allocated from the range 46501-46599. The setup script:
1. Scans for ports not in use (via `ss` or `netstat`)
2. Checks sibling worktrees to avoid conflicts
3. Stores the allocated port in `.sky-dev/.port`

### Credential Sharing

Cloud credentials are symlinked from your real home directory:
- `~/.aws` - AWS credentials
- `~/.kube` - Kubernetes config
- `~/.config/gcloud` - GCP credentials
- `~/.azure` - Azure credentials
- `~/.ssh` - SSH keys

This means credential changes in your real home are automatically reflected.

## Commands

### Setup a new instance

```bash
./dev/multi-instance/setup.sh [instance-name]
```

The instance name defaults to the worktree directory name.

### Activate the instance

```bash
source .sky-dev/bin/activate
```

Or add to your shell permanently:
```bash
export PATH="/path/to/worktree/.sky-dev/bin:$PATH"
```

### Check instance status

```bash
sky api status
```

This shows instance info plus the normal API server status.

### Clean up

To remove an instance:
```bash
sky api stop
rm -rf .sky-dev
```

## Limitations

1. **Remote clusters**: Clusters launched from one instance aren't visible to others
   (each has its own `~/.sky/state.db`)

2. **No shared state**: Instances don't share cluster state, job history, etc.

3. **Port conflicts**: If you have >99 instances, you'll run out of ports

4. **Credential updates**: If you update credentials in `.sky-dev/home/.aws/`
   (not the real `~/.aws/`), changes won't persist

## Troubleshooting

### Server won't start

Check logs at `.sky-dev/home/.sky/api_server/server.log`

### Port already in use

Delete `.sky-dev/.port` and run setup again to reallocate:
```bash
rm .sky-dev/.port
./dev/multi-instance/setup.sh
```

### Wrong sky binary being used

Check your PATH:
```bash
which sky
# Should show: /path/to/worktree/.sky-dev/bin/sky
```

### Server appears to hang

The server may take a few seconds to initialize. Check if it's running:
```bash
curl http://127.0.0.1:$(cat .sky-dev/.port)/api/health
```
