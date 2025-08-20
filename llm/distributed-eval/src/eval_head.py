#!/usr/bin/env python3
"""
Evaluation Head Server with Auto-Discovery
==============================================
Receives game states from game servers and returns AI model actions.
Auto-discovers game servers based on cluster naming prefix.
"""

import argparse
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
import json
import os
import time
from typing import Set

from fastapi import FastAPI
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
import numpy as np
import torch
import torch.nn as nn
import uvicorn

# Try importing sky for cluster discovery
try:
    import sky
    from sky.client import sdk_async
    from sky.utils import status_lib
    SKY_AVAILABLE = True
except ImportError:
    SKY_AVAILABLE = False
    status_lib = None
    print("Note: SkyPilot not available. Cluster discovery disabled.")


# Simple AI model for demonstration
class DemoModel(nn.Module):
    """A simple neural network for demonstration."""

    def __init__(self, state_dim=100, action_dim=10):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(state_dim, 256), nn.ReLU(),
                                 nn.Linear(256, 128), nn.ReLU(),
                                 nn.Linear(128, action_dim))

    def forward(self, x):
        return self.net(x)


class EvaluationHead:
    """
    Main evaluation server that:
    1. Auto-discovers game servers by cluster name prefix
    2. Collects game states from multiple servers
    3. Batches them for efficient inference
    4. Returns actions to each game server
    """

    def __init__(self,
                 model_path: str = None,
                 batch_size: int = 32,
                 game_server_prefix: str = "game-server",
                 game_server_port: int = 8081):
        # Load or create model
        self.model = DemoModel()
        if model_path:
            try:
                self.model.load_state_dict(torch.load(model_path))
                print(f"✓ Loaded model from {model_path}")
            except:
                print(f"✗ Could not load model, using random weights")

        self.model.eval()  # Set to evaluation mode

        # Request queue for batching
        self.pending_requests = deque()
        self.batch_size = batch_size

        # Game server discovery
        self.game_server_prefix = game_server_prefix
        self.game_server_port = game_server_port
        self.discovered_servers = {}  # cluster_name -> info

        # Statistics
        self.stats = {
            "total_requests": 0,
            "total_batches": 0,
            "connected_servers": set(),
            "requests_per_second": 0.0,
            "avg_batch_size": 0.0,
            "avg_latency_ms": 0.0,
            "start_time": datetime.now(),
            "active_episodes": 0  # Track number of active episodes
        }

        # Track active episodes per server to prevent negative counts
        self.active_episodes_per_server = {}

        # Track which servers are being controlled to prevent duplicates
        self.controlled_servers = set()

        # Response storage
        self.responses = {}

        # Historical metrics (for graphs)
        self.metrics_history = deque(maxlen=300)  # Keep last 5 minutes
        self.server_metrics = {}  # Per-server metrics

        # WebSocket connections for real-time updates
        self.websockets: Set[WebSocket] = set()

        # Timing tracking
        self.last_stats_update = time.time()
        self.request_count_since_update = 0
        self.batch_sizes = deque(maxlen=100)
        self.latencies = deque(maxlen=100)

    async def discover_game_servers(self):
        """Discover game servers from SkyPilot clusters and SkyServe services."""
        if not SKY_AVAILABLE:
            return

        try:
            game_servers = []

            # Get all clusters using async SDK
            clusters = await sdk_async.status()

            # Filter for game servers from clusters
            for cluster in clusters:
                if cluster['name'].startswith(self.game_server_prefix):
                    server_info = {
                        "name": cluster['name'],
                        "type": "cluster",
                        "status": cluster['status'].value if hasattr(
                            cluster['status'], 'value') else str(
                                cluster['status']),
                        "cloud": cluster.get('cloud', 'unknown'),
                        "region": cluster.get('region', 'unknown'),
                        "resources": cluster.get('resources_str', 'unknown'),
                        "endpoint": None
                    }

                    # Get endpoint if cluster is UP
                    if SKY_AVAILABLE and status_lib:
                        if cluster.get('status') == status_lib.ClusterStatus.UP:
                            try:
                                # Get the endpoint for game server port using async SDK
                                print(
                                    f"  Getting endpoint for {cluster['name']} port {self.game_server_port}..."
                                )
                                endpoints = await sdk_async.endpoints(
                                    cluster['name'], port=self.game_server_port)
                                print(f"  Endpoints returned: {endpoints}")
                                port_str = str(self.game_server_port)
                                if endpoints and port_str in endpoints:
                                    server_info['endpoint'] = endpoints[
                                        port_str]
                                    print(
                                        f"  ✓ Found endpoint for {cluster['name']}: {server_info['endpoint']}"
                                    )

                                    # Add to discovered servers immediately
                                    self.discovered_servers[
                                        cluster['name']] = server_info

                                    # Start controlling this game server immediately if not already controlled
                                    if cluster[
                                            'name'] not in self.controlled_servers:
                                        self.controlled_servers.add(
                                            cluster['name'])
                                        asyncio.create_task(
                                            self.control_game_server(
                                                server_info))
                                        print(
                                            f"  ▶ Started controlling {cluster['name']}"
                                        )
                                else:
                                    print(
                                        f"  ✗ No endpoint found for port {port_str} in {endpoints}"
                                    )
                            except Exception as e:
                                print(
                                    f"  ✗ Could not get endpoint for {cluster['name']}: {e}"
                                )
                                server_info['endpoint'] = None
                        else:
                            server_info['endpoint'] = None
                    else:
                        server_info['endpoint'] = None

                    # Add to discovered servers even if no endpoint (for visibility)
                    if cluster['name'] not in self.discovered_servers:
                        self.discovered_servers[cluster['name']] = server_info

                    game_servers.append(server_info)

            # Also check SkyServe services
            try:
                from sky.serve.client import sdk_async as serve_async

                # Get all services by passing None for service_names using async SDK
                services = await serve_async.status(service_names=None)

                # services is a list of service records
                if services:
                    for service in services:
                        if service.get('name',
                                       '').startswith(self.game_server_prefix):
                            server_info = {
                                "name": service['name'],
                                "type": "service",
                                "status": str(service.get('status', 'unknown')),
                                "replicas": len(service.get('replica_info',
                                                            [])),
                                "endpoint": service.get('endpoint', None)
                            }

                            # For services, we get the endpoint URL
                            if server_info['endpoint']:
                                # Add to discovered servers immediately
                                self.discovered_servers[
                                    service['name']] = server_info

                                # Start controlling this game server immediately if not already controlled
                                if service[
                                        'name'] not in self.controlled_servers:
                                    self.controlled_servers.add(service['name'])
                                    asyncio.create_task(
                                        self.control_game_server(server_info))
                                    print(
                                        f"  ▶ Started controlling service {service['name']}"
                                    )
                            else:
                                # Add to discovered servers even without endpoint
                                if service[
                                        'name'] not in self.discovered_servers:
                                    self.discovered_servers[
                                        service['name']] = server_info

                            game_servers.append(server_info)

            except ImportError:
                print("Note: SkyServe not available for service discovery")
            except Exception as e:
                # Only print if it's a real error, not just no services
                if "No service" not in str(e):
                    print(f"Note: Could not check SkyServe services: {e}")

            # Update discovered servers (merge, don't replace since we added them incrementally)
            # This ensures we keep all discovered servers including those added during discovery
            for server in game_servers:
                if server["name"] not in self.discovered_servers:
                    self.discovered_servers[server["name"]] = server

            # Summarize discovery results
            active_count = sum(1 for s in game_servers if s.get('endpoint'))
            print(
                f"✓ Discovery complete: Found {len(game_servers)} game servers with prefix '{self.game_server_prefix}'"
            )
            print(f"  • {active_count} servers are being controlled")
            print(
                f"  • {len(game_servers) - active_count} servers have no endpoint"
            )

            # List servers with their status
            for server in game_servers:
                if server.get('type') == 'service':
                    status = "🎮 ACTIVE" if server.get(
                        'endpoint') else "⏸ NO ENDPOINT"
                    print(f"  - {server['name']} (service): {status}")
                else:
                    status = "🎮 ACTIVE" if server.get(
                        'endpoint') else "⏸ NO ENDPOINT"
                    print(f"  - {server['name']} (cluster): {status}")

        except Exception as e:
            print(f"Error discovering game servers: {e}")

    async def control_game_server(self, server_info):
        """Control a game server by sending actions to drive the simulation."""
        import aiohttp

        server_name = server_info['name']
        endpoint = server_info.get('endpoint')

        if not endpoint:
            print(f"No endpoint for {server_name}, skipping control")
            return

        # Ensure endpoint has http:// prefix
        if not endpoint.startswith('http'):
            endpoint = f'http://{endpoint}'

        print(f"Starting to control game server: {server_name} at {endpoint}")

        # Initialize episode tracking for this server
        self.active_episodes_per_server[server_name] = False

        # Control the game server continuously
        while server_name in self.discovered_servers:
            try:
                async with aiohttp.ClientSession() as session:
                    # Reset the game environment
                    print(f"  Resetting {server_name}...")
                    async with session.post(
                            f'{endpoint}/reset',
                            timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status == 200:
                            reset_data = await response.json()
                            state = np.array(reset_data['state'])
                            print(
                                f"  Reset successful for {server_name}, starting episode"
                            )

                            # Track active episode for this server
                            if not self.active_episodes_per_server.get(
                                    server_name, False):
                                self.active_episodes_per_server[
                                    server_name] = True
                                self.stats["active_episodes"] += 1

                            # Run one episode
                            done = False
                            steps = 0
                            while not done and steps < 1000:  # Safety limit
                                # Get action from model
                                with torch.no_grad():
                                    state_tensor = torch.tensor(
                                        state, dtype=torch.float32).unsqueeze(0)
                                    action = self.model(
                                        state_tensor).squeeze().numpy()

                                # Send action to game server
                                try:
                                    async with session.post(
                                            f'{endpoint}/step',
                                            json={"action": action.tolist()},
                                            timeout=aiohttp.ClientTimeout(
                                                total=60)) as step_response:
                                        if step_response.status == 200:
                                            step_data = await step_response.json(
                                            )
                                            state = np.array(step_data['state'])
                                            done = step_data['done']

                                            # Update metrics
                                            if server_name not in self.server_metrics:
                                                self.server_metrics[
                                                    server_name] = {
                                                        "requests": 0,
                                                        "last_seen":
                                                            datetime.now(),
                                                        "avg_latency": 0,
                                                        "episodes": 0,
                                                        "total_steps": 0
                                                    }

                                            self.server_metrics[
                                                server_name].update({
                                                    "episodes": step_data.get(
                                                        'episode', 0),
                                                    "total_steps":
                                                        step_data.get(
                                                            'total_steps', 0),
                                                    "last_seen": datetime.now(),
                                                    "requests":
                                                        self.server_metrics[
                                                            server_name]
                                                        ["requests"] + 1
                                                })

                                            # Register the server as connected
                                            self.stats["connected_servers"].add(
                                                server_name)
                                            self.stats["total_requests"] += 1
                                            self.request_count_since_update += 1

                                            steps += 1

                                            # Small delay to not overwhelm the server
                                            await asyncio.sleep(
                                                0.01
                                            )  # Reduced from 0.1 to 0.01 for faster evaluation
                                        else:
                                            print(
                                                f"Error in step for {server_name}: HTTP {step_response.status} (episode interrupted at step {steps})"
                                            )
                                            done = True  # Force episode to end
                                            break
                                except asyncio.TimeoutError:
                                    print(
                                        f"Timeout in step for {server_name} (episode interrupted at step {steps})"
                                    )
                                    done = True  # Force episode to end
                                    break
                                except Exception as e:
                                    print(
                                        f"Exception in step for {server_name}: {e} (episode interrupted at step {steps})"
                                    )
                                    done = True  # Force episode to end
                                    break

                            # Episode complete - decrement active episodes for this server
                            if self.active_episodes_per_server.get(
                                    server_name, False):
                                self.active_episodes_per_server[
                                    server_name] = False
                                self.stats["active_episodes"] = max(
                                    0, self.stats["active_episodes"] - 1)

                            # Get episode number from server metrics
                            episode_num = self.server_metrics.get(
                                server_name, {}).get("episodes", 0)
                            print(
                                f"Completed episode #{episode_num} for {server_name}: {steps} steps"
                            )
                        else:
                            print(
                                f"  Failed to reset {server_name}: HTTP {response.status}"
                            )
                            await asyncio.sleep(5)

            except aiohttp.ClientConnectorError as e:
                print(f"Cannot connect to {server_name} at {endpoint}: {e}")
                # Only decrement if this server had an active episode
                if self.active_episodes_per_server.get(server_name, False):
                    self.active_episodes_per_server[server_name] = False
                    self.stats["active_episodes"] = max(
                        0, self.stats["active_episodes"] - 1)
                await asyncio.sleep(10
                                   )  # Wait longer before retrying connection
            except Exception as e:
                print(f"Error controlling {server_name}: {e}")
                # Only decrement if this server had an active episode
                if self.active_episodes_per_server.get(server_name, False):
                    self.active_episodes_per_server[server_name] = False
                    self.stats["active_episodes"] = max(
                        0, self.stats["active_episodes"] - 1)
                await asyncio.sleep(5)  # Wait before retrying

        # Clean up when server is no longer discovered
        if server_name in self.active_episodes_per_server:
            if self.active_episodes_per_server[server_name]:
                self.stats["active_episodes"] = max(
                    0, self.stats["active_episodes"] - 1)
            del self.active_episodes_per_server[server_name]

        # Remove from controlled servers set
        self.controlled_servers.discard(server_name)
        print(f"Stopped controlling {server_name}")

    async def discovery_loop(self):
        """Periodically discover new game servers."""
        while True:
            # Wait before next discovery (first discovery runs immediately on startup)
            await asyncio.sleep(15
                               )  # Check every 15 seconds for faster response
            await self.discover_game_servers()

    async def process_batch(self):
        """Process a batch of game states through the model."""
        if not self.pending_requests:
            return

        # Collect batch (up to batch_size)
        batch_size = min(len(self.pending_requests), self.batch_size)
        batch = [self.pending_requests.popleft() for _ in range(batch_size)]

        start_time = time.time()

        # Extract game states and convert to tensor
        states = torch.tensor(np.array([req["state"] for req in batch]),
                              dtype=torch.float32)

        # Run model inference
        with torch.no_grad():
            actions = self.model(states).numpy()

        # Calculate latency
        inference_time = (time.time() - start_time) * 1000  # ms

        # Store responses
        for req, action in zip(batch, actions):
            self.responses[req["request_id"]] = {
                "action": action.tolist(),
                "server_id": req["server_id"],
                "inference_time_ms": inference_time / batch_size
            }

            # Update per-server metrics
            server_id = req["server_id"]
            if server_id not in self.server_metrics:
                self.server_metrics[server_id] = {
                    "requests": 0,
                    "last_seen": datetime.now(),
                    "avg_latency": 0
                }
            self.server_metrics[server_id]["requests"] += 1
            self.server_metrics[server_id]["last_seen"] = datetime.now()

        # Update statistics
        self.stats["total_batches"] += 1
        self.batch_sizes.append(batch_size)
        self.latencies.append(inference_time)

        if self.batch_sizes:
            self.stats["avg_batch_size"] = np.mean(self.batch_sizes)
        if self.latencies:
            self.stats["avg_latency_ms"] = np.mean(self.latencies)

        print(
            f"Processed batch of {batch_size} requests in {inference_time:.2f}ms"
        )

    async def batch_processing_loop(self):
        """Continuously process batches of requests."""
        while True:
            await self.process_batch()
            await asyncio.sleep(0.1)  # Process every 100ms

    async def metrics_collection_loop(self):
        """Collect metrics every second for historical graphs."""
        while True:
            # Calculate requests per second
            current_time = time.time()
            time_delta = current_time - self.last_stats_update
            if time_delta > 0:
                self.stats[
                    "requests_per_second"] = self.request_count_since_update / time_delta

            # Store historical data point
            self.metrics_history.append({
                "timestamp": datetime.now().isoformat(),
                "requests_per_second": self.stats["requests_per_second"],
                "queue_size":
                    self.stats["active_episodes"],  # Now tracks active episodes
                "connected_servers": len(self.stats["connected_servers"]),
                "discovered_servers": len(self.discovered_servers),
                "avg_batch_size": self.stats["avg_batch_size"],
                "avg_latency_ms": self.stats["avg_latency_ms"],
                "active_episodes": self.stats["active_episodes"]
            })

            # Reset counters
            self.last_stats_update = current_time
            self.request_count_since_update = 0

            # Broadcast updates to WebSocket clients
            await self.broadcast_stats()

            await asyncio.sleep(1)

    async def broadcast_stats(self):
        """Send stats update to all connected WebSocket clients."""
        if not self.websockets:
            return

        stats_message = json.dumps({
            "type": "stats_update",
            "data": {
                "current": {
                    "total_requests": self.stats["total_requests"],
                    "total_batches": self.stats["total_batches"],
                    "queue_size":
                        self.
                        stats["active_episodes"],  # Now shows active episodes
                    "connected_servers": len(self.stats["connected_servers"]),
                    "discovered_servers": len(self.discovered_servers),
                    "requests_per_second": round(
                        self.stats["requests_per_second"], 2),
                    "avg_batch_size": round(self.stats["avg_batch_size"], 2),
                    "avg_latency_ms": round(self.stats["avg_latency_ms"], 2),
                    "active_episodes": self.stats["active_episodes"]
                },
                "discovered": list(self.discovered_servers.values()),
                "active": [{
                    "id": sid,
                    "requests": metrics["requests"],
                    "last_seen": metrics["last_seen"].isoformat(),
                    "status":
                        "active" if
                        (datetime.now() - metrics["last_seen"]).seconds < 10
                        else "inactive"
                } for sid, metrics in self.server_metrics.items()],
                "history": list(self.metrics_history)
            }
        })

        disconnected = set()
        for ws in self.websockets:
            try:
                await ws.send_text(stats_message)
            except:
                disconnected.add(ws)

        # Remove disconnected clients
        self.websockets -= disconnected


# Global eval_head instance
eval_head = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    # Startup
    global eval_head

    # Get configuration from environment or defaults
    game_server_prefix = os.environ.get("GAME_SERVER_PREFIX", "game-server")
    game_server_port = int(os.environ.get("GAME_SERVER_PORT", "8081"))
    checkpoint_bucket = os.environ.get("CHECKPOINT_BUCKET", None)

    eval_head = EvaluationHead(
        model_path=None,  # Could load from checkpoint_bucket
        game_server_prefix=game_server_prefix,
        game_server_port=game_server_port)

    # Start background tasks
    asyncio.create_task(eval_head.batch_processing_loop())
    asyncio.create_task(eval_head.metrics_collection_loop())

    if SKY_AVAILABLE:
        # Run discovery immediately on startup (don't wait for first interval)
        print("🔍 Starting initial game server discovery...")
        asyncio.create_task(eval_head.discover_game_servers())

        # Also start the periodic discovery loop
        asyncio.create_task(eval_head.discovery_loop())

    print(f"✓ Evaluation head started")
    print(f"  Game server prefix: {game_server_prefix}")
    print(f"  Game server port: {game_server_port}")
    if checkpoint_bucket:
        print(f"  Checkpoint bucket: {checkpoint_bucket}")

    yield

    # Shutdown
    print("✓ Evaluation head shutting down")


# Create FastAPI app with lifespan
app = FastAPI(title="Evaluation Head", lifespan=lifespan)


@app.post("/evaluate")
async def evaluate(request: dict):
    """
    Receive game state from a game server and return action.
    
    Expected request format:
    {
        "server_id": "game-server-0",
        "state": [0.1, 0.2, ...],  # 100-dim array
        "request_id": "uuid-123"
    }
    """
    # Add to processing queue
    eval_head.pending_requests.append(request)
    eval_head.stats["total_requests"] += 1
    eval_head.request_count_since_update += 1
    eval_head.stats["connected_servers"].add(request["server_id"])

    # Wait for response (with timeout)
    request_id = request["request_id"]
    timeout = 5.0
    start_time = time.time()

    while request_id not in eval_head.responses:
        if time.time() - start_time > timeout:
            return JSONResponse({"error": "Request timeout"}, status_code=408)
        await asyncio.sleep(0.01)

    # Return response
    response = eval_head.responses.pop(request_id)
    return response


@app.get("/stats")
async def get_stats():
    """Get server statistics."""
    stats = dict(eval_head.stats)
    stats["connected_servers"] = list(stats["connected_servers"])
    stats["discovered_servers"] = eval_head.discovered_servers
    stats["queue_size"] = len(eval_head.pending_requests)
    return stats


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/discovered")
async def get_discovered_servers():
    """Get list of discovered game servers."""
    return {
        "servers": list(eval_head.discovered_servers.values()),
        "prefix": eval_head.game_server_prefix,
        "sky_available": SKY_AVAILABLE
    }


@app.post("/discover")
async def trigger_discovery():
    """Manually trigger game server discovery."""
    # Run discovery in background to avoid blocking
    asyncio.create_task(eval_head.discover_game_servers())
    return {
        "discovered": len(eval_head.discovered_servers),
        "status": "discovery started"
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time stats updates."""
    await websocket.accept()
    eval_head.websockets.add(websocket)

    try:
        # Send initial stats immediately to new client
        stats_message = json.dumps({
            "type": "stats_update",
            "data": {
                "current": {
                    "total_requests": eval_head.stats["total_requests"],
                    "total_batches": eval_head.stats["total_batches"],
                    "queue_size": eval_head.stats["active_episodes"],
                    "connected_servers": len(
                        eval_head.stats["connected_servers"]),
                    "discovered_servers": len(eval_head.discovered_servers),
                    "requests_per_second": round(
                        eval_head.stats["requests_per_second"], 2),
                    "avg_batch_size": round(eval_head.stats["avg_batch_size"],
                                            2),
                    "avg_latency_ms": round(eval_head.stats["avg_latency_ms"],
                                            2),
                    "active_episodes": eval_head.stats["active_episodes"]
                },
                "discovered": list(eval_head.discovered_servers.values()),
                "active": [{
                    "id": sid,
                    "requests": metrics["requests"],
                    "last_seen": metrics["last_seen"].isoformat(),
                    "status":
                        "active" if
                        (datetime.now() - metrics["last_seen"]).seconds < 10
                        else "inactive"
                } for sid, metrics in eval_head.server_metrics.items()],
                "history": list(eval_head.metrics_history)
            }
        })
        await websocket.send_text(stats_message)

        # Keep connection alive
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        eval_head.websockets.remove(websocket)


@app.get("/dashboard")
async def dashboard():
    """Serve the monitoring dashboard."""
    # Get the path to the static HTML file
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, "..", "static", "dashboard.html")

    if os.path.exists(html_path):
        return FileResponse(html_path)
    else:
        # Fallback if file not found
        return JSONResponse(
            {"error": "Dashboard file not found at " + html_path},
            status_code=404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model-path", help="Path to model file")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--game-server-prefix",
                        default="game-server",
                        help="Prefix for game server cluster names")
    parser.add_argument("--game-server-port",
                        type=int,
                        default=8081,
                        help="Port where game servers are listening")
    parser.add_argument("--checkpoint-bucket",
                        help="S3/GCS bucket for checkpoints")
    args = parser.parse_args()

    # Set environment variables for startup handler
    if args.game_server_prefix:
        os.environ["GAME_SERVER_PREFIX"] = args.game_server_prefix
    if args.game_server_port:
        os.environ["GAME_SERVER_PORT"] = str(args.game_server_port)
    if args.checkpoint_bucket:
        os.environ["CHECKPOINT_BUCKET"] = args.checkpoint_bucket

    print(f"""
    ╔════════════════════════════════════════╗
    ║     EVALUATION HEAD SERVER             ║
    ╠════════════════════════════════════════╣
    ║  Port: {args.port:<32}║
    ║  Batch Size: {args.batch_size:<26}║
    ║  Server Prefix: {args.game_server_prefix:<23}║
    ║  Server Port: {args.game_server_port:<25}║
    ║  Auto-Discovery: {'Enabled' if SKY_AVAILABLE else 'Disabled':<22}║
    ╚════════════════════════════════════════╝
    """)

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
