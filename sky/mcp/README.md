# SkyPilot MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that exposes SkyPilot functionality to AI assistants and other MCP clients.

## Overview

This MCP server allows AI assistants (like Claude, GPT, etc.) to interact with SkyPilot to:

- **Manage clusters**: Launch, stop, start, and tear down cloud compute clusters
- **Run jobs**: Execute tasks on clusters and monitor their status
- **Query resources**: List available GPUs, check costs, view endpoints
- **Manage storage**: List and delete managed storage buckets

## Installation

Install SkyPilot with MCP support:

```bash
pip install "skypilot[mcp]"
```

Or if you have SkyPilot installed, add MCP support:

```bash
pip install mcp
```

## Usage

### Running the MCP Server

Start the server directly:

```bash
skypilot-mcp
```

Or run as a Python module:

```bash
python -m sky.mcp.server
```

### Configuring with Claude Desktop

Add the following to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "skypilot": {
      "command": "skypilot-mcp"
    }
  }
}
```

If you're using a virtual environment:

```json
{
  "mcpServers": {
    "skypilot": {
      "command": "/path/to/your/venv/bin/skypilot-mcp"
    }
  }
}
```

### Configuring with Other MCP Clients

The server uses stdio transport by default. Configure your MCP client to run `skypilot-mcp` as a subprocess.

## Available Tools

### Cluster Management

| Tool | Description |
|------|-------------|
| `skypilot_status` | Get cluster status (INIT, UP, STOPPED) |
| `skypilot_launch` | Launch a new cluster or run a task |
| `skypilot_exec` | Execute a command on an existing cluster |
| `skypilot_stop` | Stop a cluster (preserves storage) |
| `skypilot_start` | Restart a stopped cluster |
| `skypilot_down` | Tear down a cluster completely |
| `skypilot_autostop` | Configure auto-stop settings |

### Job Management

| Tool | Description |
|------|-------------|
| `skypilot_queue` | View the job queue for a cluster |
| `skypilot_cancel` | Cancel running or pending jobs |
| `skypilot_logs` | Get logs from a job |

### Resource Queries

| Tool | Description |
|------|-------------|
| `skypilot_show_gpus` | List available GPUs across clouds |
| `skypilot_check` | Check which clouds are enabled |
| `skypilot_cost_report` | Get cost report for clusters |
| `skypilot_endpoints` | Get service endpoints for a cluster |

### Storage

| Tool | Description |
|------|-------------|
| `skypilot_storage_ls` | List managed storage |
| `skypilot_storage_delete` | Delete managed storage |

### API Server

| Tool | Description |
|------|-------------|
| `skypilot_api_info` | Get API server information |

## Examples

Here are some example prompts you can use with an AI assistant connected to this MCP server:

**Check cluster status:**
> "What clusters do I have running?"

**Launch a GPU cluster:**
> "Launch a cluster with 4 A100 GPUs on AWS"

**Run a training job:**
> "Execute this training script on my cluster: python train.py --epochs 100"

**Check costs:**
> "Show me my cloud costs for the last 7 days"

**Find GPUs:**
> "What H100 GPUs are available and how much do they cost?"

**Stop idle clusters:**
> "Set up auto-stop for my-cluster to stop after 30 minutes of idle time"

## Prerequisites

Before using the MCP server, ensure:

1. SkyPilot is properly configured with cloud credentials
2. Run `sky check` to verify cloud access
3. The SkyPilot API server is running if using remote mode

## Troubleshooting

### Server won't start

Ensure MCP is installed:
```bash
pip install mcp
```

### Cloud operations fail

Check your cloud credentials:
```bash
sky check
```

### API server errors

Start the SkyPilot API server:
```bash
sky api start
```

## Development

To develop or modify the MCP server:

```bash
# Clone the repository
git clone https://github.com/skypilot-org/skypilot.git
cd skypilot

# Install in development mode with MCP support
pip install -e ".[mcp]"

# Run the server
python -m sky.mcp.server
```

## Architecture

The MCP server is implemented in `sky/mcp/server.py` and:

- Uses the standard MCP Python SDK
- Exposes SkyPilot SDK functions as MCP tools
- Communicates via stdio transport
- Handles async operations by waiting for SkyPilot requests to complete

## License

Apache 2.0 - See the main SkyPilot repository for details.
