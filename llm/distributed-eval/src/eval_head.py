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
import time
from typing import Set
import os

import numpy as np
import torch
import torch.nn as nn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, FileResponse
import uvicorn

# Try importing sky for cluster discovery
try:
    import sky
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
        self.net = nn.Sequential(
            nn.Linear(state_dim, 256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, action_dim)
        )
    
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
    
    def __init__(self, model_path: str = None, batch_size: int = 32, 
                 game_server_prefix: str = "game-server"):
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
        self.discovered_servers = {}  # cluster_name -> info
        
        # Statistics
        self.stats = {
            "total_requests": 0,
            "total_batches": 0,
            "connected_servers": set(),
            "requests_per_second": 0.0,
            "avg_batch_size": 0.0,
            "avg_latency_ms": 0.0,
            "start_time": datetime.now()
        }
        
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
        """Discover game servers from SkyPilot clusters."""
        if not SKY_AVAILABLE:
            return
        
        try:
            # Get all clusters using correct SDK
            clusters = sky.stream_and_get(sky.status())
            
            # Filter for game servers
            game_servers = []
            for cluster in clusters:
                if cluster['name'].startswith(self.game_server_prefix):
                    server_info = {
                        "name": cluster['name'],
                        "status": cluster['status'].value if hasattr(cluster['status'], 'value') else str(cluster['status']),
                        "cloud": cluster.get('cloud', 'unknown'),
                        "region": cluster.get('region', 'unknown'),
                        "resources": cluster.get('resources_str', 'unknown')
                    }
                    
                    # Get IP if cluster is UP
                    if SKY_AVAILABLE and status_lib:
                        if cluster.get('status') == status_lib.ClusterStatus.UP:
                            # Try to get IP from handle if available
                            handle = cluster.get('handle')
                            if handle and hasattr(handle, 'head_ip'):
                                server_info['ip'] = handle.head_ip
                            else:
                                server_info['ip'] = None
                        else:
                            server_info['ip'] = None
                    else:
                        server_info['ip'] = None
                    
                    game_servers.append(server_info)
            
            # Update discovered servers
            self.discovered_servers = {s["name"]: s for s in game_servers}
            
            print(f"✓ Discovered {len(game_servers)} game servers with prefix '{self.game_server_prefix}'")
            for server in game_servers:
                ip_str = server['ip'] if server['ip'] else 'IP not available'
                print(f"  - {server['name']}: {ip_str} ({server['status']})")
                
        except Exception as e:
            print(f"Error discovering game servers: {e}")
    
    async def discovery_loop(self):
        """Periodically discover new game servers."""
        while True:
            await self.discover_game_servers()
            await asyncio.sleep(30)  # Check every 30 seconds
    
    async def process_batch(self):
        """Process a batch of game states through the model."""
        if not self.pending_requests:
            return
        
        # Collect batch (up to batch_size)
        batch_size = min(len(self.pending_requests), self.batch_size)
        batch = [self.pending_requests.popleft() for _ in range(batch_size)]
        
        start_time = time.time()
        
        # Extract game states and convert to tensor
        states = torch.tensor(
            np.array([req["state"] for req in batch]),
            dtype=torch.float32
        )
        
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
        
        print(f"Processed batch of {batch_size} requests in {inference_time:.2f}ms")
    
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
                self.stats["requests_per_second"] = self.request_count_since_update / time_delta
            
            # Store historical data point
            self.metrics_history.append({
                "timestamp": datetime.now().isoformat(),
                "requests_per_second": self.stats["requests_per_second"],
                "queue_size": len(self.pending_requests),
                "connected_servers": len(self.stats["connected_servers"]),
                "discovered_servers": len(self.discovered_servers),
                "avg_batch_size": self.stats["avg_batch_size"],
                "avg_latency_ms": self.stats["avg_latency_ms"]
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
                    "queue_size": len(self.pending_requests),
                    "connected_servers": len(self.stats["connected_servers"]),
                    "discovered_servers": len(self.discovered_servers),
                    "requests_per_second": round(self.stats["requests_per_second"], 2),
                    "avg_batch_size": round(self.stats["avg_batch_size"], 2),
                    "avg_latency_ms": round(self.stats["avg_latency_ms"], 2)
                },
                "discovered": list(self.discovered_servers.values()),
                "active": [
                    {
                        "id": sid,
                        "requests": metrics["requests"],
                        "last_seen": metrics["last_seen"].isoformat(),
                        "status": "active" if (datetime.now() - metrics["last_seen"]).seconds < 10 else "inactive"
                    }
                    for sid, metrics in self.server_metrics.items()
                ],
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
    checkpoint_bucket = os.environ.get("CHECKPOINT_BUCKET", None)
    
    eval_head = EvaluationHead(
        model_path=None,  # Could load from checkpoint_bucket
        game_server_prefix=game_server_prefix
    )
    
    # Start background tasks
    asyncio.create_task(eval_head.batch_processing_loop())
    asyncio.create_task(eval_head.metrics_collection_loop())
    
    if SKY_AVAILABLE:
        asyncio.create_task(eval_head.discovery_loop())
    
    print(f"✓ Evaluation head started")
    print(f"  Game server prefix: {game_server_prefix}")
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
    await eval_head.discover_game_servers()
    return {"discovered": len(eval_head.discovered_servers)}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time stats updates."""
    await websocket.accept()
    eval_head.websockets.add(websocket)
    
    try:
        # Send initial stats
        await eval_head.broadcast_stats()
        
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
            status_code=404
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--model-path", help="Path to model file")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--game-server-prefix", default="game-server",
                        help="Prefix for game server cluster names")
    parser.add_argument("--checkpoint-bucket", help="S3/GCS bucket for checkpoints")
    args = parser.parse_args()
    
    # Set environment variables for startup handler
    if args.game_server_prefix:
        os.environ["GAME_SERVER_PREFIX"] = args.game_server_prefix
    if args.checkpoint_bucket:
        os.environ["CHECKPOINT_BUCKET"] = args.checkpoint_bucket
    
    print(f"""
    ╔════════════════════════════════════════╗
    ║     EVALUATION HEAD SERVER             ║
    ╠════════════════════════════════════════╣
    ║  Port: {args.port:<32}║
    ║  Batch Size: {args.batch_size:<26}║
    ║  Server Prefix: {args.game_server_prefix:<23}║
    ║  Auto-Discovery: {'Enabled' if SKY_AVAILABLE else 'Disabled':<22}║
    ╚════════════════════════════════════════╝
    """)
    
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
