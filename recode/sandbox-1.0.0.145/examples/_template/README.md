# Your Example Name

Brief description of what this example demonstrates.

## Quick Start

1. Start the sandbox

```bash
docker run --security-opt seccomp=unconfined --rm -it -p 8080:8080 ghcr.io/agent-infra/sandbox:latest
```

2. Configure the environment

```bash
# 1. Copy .env.example to .env
cp .env.example .env

# 2. Run the example
uv run main.py
```

## What This Does

Explain what happens when you run this example.

## Customize

Modify `main.py` to add your own logic.
