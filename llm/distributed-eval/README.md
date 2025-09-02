# Distributed RL Evaluation System

A scalable system for distributed AI model evaluation using SkyPilot. The system automatically discovers and connects game servers for efficient batch processing.

## Architecture

![](https://i.imgur.com/EOEeiBb.png)


## Features

- **Auto-Discovery**: Evaluation head automatically discovers game servers by cluster name prefix from centralized SkyPilot API server
- **Real-time Dashboard**: Web-based monitoring at `http://<eval-head-ip>:8080/dashboard`
- **Efficient Batching**: Processes multiple game states in batches for optimal throughput
- **Cost-Optimized**: Game servers run on spot instances for 70-90% cost savings
- **Scalable**: Add/remove game servers dynamically

## Quick Start

If you have a centralized SkyPilot API server, the eval head can auto-discover game servers:

```bash
# Launch the evaluation head with API server credentials
export SKYPILOT_API_SERVER_ENDPOINT=https://your-api-server.com
export SKYPILOT_API_SERVER_SERVICE_ACCOUNT_TOKEN=your-service-account-token
sky launch -c eval-head configs/eval_head.yaml \
  --env GAME_SERVER_PREFIX=game \
  --secret SKYPILOT_API_SERVER_ENDPOINT \
  --secret SKYPILOT_API_SERVER_SERVICE_ACCOUNT_TOKEN

# Launch game servers (eval head will auto-discover them)
sky launch -c game-1 configs/game_server.yaml
sky launch -c game-2 configs/game_server.yaml
```

The eval head will automatically discover all clusters and services with the matching prefix through the API server.


You can also deploy game servers as SkyServe services for automatic failover and scaling:

```bash
# Deploy game servers as SkyServe services (with autoscaling and failover)
# Note: Uses the same game_server.yaml - the service field is used by sky serve
sky serve up -n game-svc configs/game_server_service.yaml
```

SkyServe provides:
- Automatic failover if a replica fails
- Autoscaling based on load (1-5 replicas)
- Health checks and automatic restarts
- Load balancing across replicas

### 3. Monitor the System

1. Check cluster status:
```bash
sky status
```

2. Access the dashboard:
```bash
# Get the eval head endpoint
sky status --endpoint 8080 eval-head

# Open in browser (will show the full URL)
```

## Configuration

### Evaluation Head (`configs/eval_head.yaml`)
- **Resources**: 8+ CPUs, 16+ GB memory (no GPU needed)
- **Ports**: 8080 (dashboard and API)
- **Environment Variables**:
  - `GAME_SERVER_PREFIX`: Prefix for auto-discovering game servers (required)
  - `CHECKPOINT_BUCKET`: S3/GCS bucket for model checkpoints (optional)
- **Secrets** (for API server):
  - `SKYPILOT_API_SERVER_ENDPOINT`: API server URL for cluster discovery
  - `SKYPILOT_API_SERVER_SERVICE_ACCOUNT_TOKEN`: Authentication token

### Game Server (`configs/game_server.yaml`)
- **Resources**: 4+ CPUs, 8+ GB memory
- **Spot Instances**: Enabled by default for cost savings
- **Environment Variables**:
  - `EVAL_HEAD_ENDPOINT`: URL of the evaluation head (required)
- **Auto-connects** to evaluation head using the provided endpoint

## Scaling

### Add More Game Servers
```bash
# Get eval head endpoint
EVAL_HEAD_ENDPOINT=$(sky status --endpoint 8080 eval-head)

# Launch additional servers
sky launch -c game-N configs/game_server.yaml
```

### Remove Game Servers
```bash
sky down game-N
```

### Stop All Clusters
```bash
sky down game-* eval-head
```

## Updating and Restarting

### Restart the Evaluation Head

When you need to update the eval head code or configuration:

```bash
# Cancel the current job without terminating the cluster
sky cancel -ay eval-head

# Execute the new configuration (with API server)
sky exec eval-head configs/eval_head.yaml \
  --env GAME_SERVER_PREFIX=game \
  --secret SKYPILOT_API_SERVER_ENDPOINT \
  --secret SKYPILOT_API_SERVER_SERVICE_ACCOUNT_TOKEN

# Or without API server
sky exec eval-head configs/eval_head.yaml \
  --env GAME_SERVER_PREFIX=game
```

This approach:
- Keeps the cluster running (saves time)
- Updates the code from your local workdir
- Restarts the service with new configuration
- Preserves the cluster IP (game servers stay connected)

### Restart Game Servers

Similarly, to restart game servers:

```bash
# Cancel and restart a specific game server
sky cancel -ay game-1
sky exec game-1 configs/game_server.yaml \
  --env EVAL_HEAD_ENDPOINT=$EVAL_HEAD_ENDPOINT
```

The evaluation head exposes the following endpoints:

- `GET /dashboard` - Web monitoring dashboard
- `GET /health` - Health check
- `GET /stats` - Current statistics
- `GET /discovered` - List discovered game servers (requires API server)
- `POST /discover` - Manually trigger discovery
- `POST /evaluate` - Submit game state for evaluation (used by game servers)
- `WebSocket /ws` - Real-time stats updates

## Development

### Running Locally

For local development without SkyPilot:

```bash
# Terminal 1: Start eval head
cd src
python eval_head.py --port 8080

# Terminal 2: Start game server
python game_server.py --port 8081 --eval-head-url http://localhost:8080
```

### Project Structure

```
distributed-eval/
├── configs/
│   ├── eval_head.yaml      # Eval head SkyPilot config
│   └── game_server.yaml    # Game server config (works with both sky launch and sky serve)
├── src/
│   ├── eval_head.py        # Evaluation head with auto-discovery
│   └── game_server.py      # Game simulation server
├── static/
│   └── dashboard.html       # Web dashboard
└── README.md                # This file
```

## Environment Setup

The configs use `uv` for fast Python package management:
- Automatic virtual environment creation
- Fast dependency installation
- CPU-only PyTorch for the eval head (smaller and faster)

## Cost Optimization

- **Eval Head**: Runs on regular instances for reliability
- **Game Servers**: Run on spot instances (70-90% cheaper)
- **Auto-stop**: Configure with `--down` or set idle timeouts

## Troubleshooting

1. **Game servers not discovered** (when using API server):
   - Verify API server credentials are correct
   - Check that clusters have the correct name prefix
   - Ensure eval head has `.sky.yaml` configured properly
   - Manually trigger discovery in dashboard

2. **Connection issues**:
   - Ensure eval head is running first
   - Verify `EVAL_HEAD_ENDPOINT` is correctly set
   - Check firewall rules allow port 8080
   - Use `sky status --endpoint` to get the correct URL

3. **Performance issues**:
   - Increase batch size in eval head
   - Add more game servers for parallel processing
   - Check CPU/memory usage in dashboard

## Advanced Usage

See the [advanced/](advanced/) directory for:
- Custom model integration
- Production deployment guides
- Performance tuning
- Monitoring and logging setup
