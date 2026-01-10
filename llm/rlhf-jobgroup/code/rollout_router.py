"""
Rollout Router - Load balancer for multiple vLLM rollout servers.

This router provides a single endpoint that load balances requests across
multiple vLLM instances using round-robin distribution.

Features:
- Round-robin load balancing across vLLM backends
- Health checking to skip unhealthy backends
- OpenAI-compatible API passthrough
- Request statistics

Usage:
    python rollout_router.py --port 8010 --backends "server1:8001,server2:8001"

The router exposes the same OpenAI-compatible API as vLLM, so clients can
use it as a drop-in replacement for a single vLLM instance.
"""

import argparse
import asyncio
import time
from typing import Optional
from collections import deque
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

app = FastAPI(title="Rollout Router", description="Load balancer for vLLM backends")


@dataclass
class BackendStats:
    """Statistics for a single backend."""
    requests: int = 0
    errors: int = 0
    last_error: Optional[str] = None
    last_healthy_check: float = 0
    is_healthy: bool = True


@dataclass
class RouterState:
    """Global router state."""
    backends: list[str] = field(default_factory=list)
    backend_stats: dict[str, BackendStats] = field(default_factory=dict)
    current_index: int = 0
    total_requests: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


router_state = RouterState()


def parse_backends(backends_str: str) -> list[str]:
    """Parse comma-separated backend list."""
    backends = []
    for backend in backends_str.split(","):
        backend = backend.strip()
        if backend:
            # Ensure http:// prefix
            if not backend.startswith("http"):
                backend = f"http://{backend}"
            backends.append(backend)
    return backends


async def check_backend_health(backend: str) -> bool:
    """Check if a backend is healthy."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Try the health endpoint first, fall back to models endpoint
            for endpoint in ["/health", "/v1/models"]:
                try:
                    response = await client.get(f"{backend}{endpoint}")
                    if response.status_code == 200:
                        return True
                except Exception:
                    continue
        return False
    except Exception:
        return False


async def get_next_healthy_backend() -> Optional[str]:
    """Get the next healthy backend using round-robin."""
    async with router_state.lock:
        if not router_state.backends:
            return None

        # Try each backend starting from current index
        num_backends = len(router_state.backends)
        for _ in range(num_backends):
            idx = router_state.current_index % num_backends
            router_state.current_index += 1

            backend = router_state.backends[idx]
            stats = router_state.backend_stats[backend]

            # Check health if not recently checked
            current_time = time.time()
            if current_time - stats.last_healthy_check > 10:  # Re-check every 10s
                stats.is_healthy = await check_backend_health(backend)
                stats.last_healthy_check = current_time

            if stats.is_healthy:
                return backend

        # If no healthy backends, return first one anyway (let it fail explicitly)
        return router_state.backends[0]


async def proxy_request(request: Request, path: str):
    """Proxy a request to a backend."""
    backend = await get_next_healthy_backend()
    if not backend:
        raise HTTPException(status_code=503, detail="No backends available")

    router_state.total_requests += 1
    stats = router_state.backend_stats[backend]
    stats.requests += 1

    # Build the target URL
    target_url = f"{backend}{path}"
    if request.query_params:
        target_url += f"?{request.query_params}"

    # Get request body
    body = await request.body()

    # Forward headers (exclude host)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ["host", "content-length"]}

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Check if this is a streaming request
            is_streaming = b'"stream": true' in body or b'"stream":true' in body

            if is_streaming:
                # Handle streaming response
                async def stream_response():
                    async with client.stream(
                        request.method,
                        target_url,
                        headers=headers,
                        content=body
                    ) as response:
                        async for chunk in response.aiter_bytes():
                            yield chunk

                return StreamingResponse(
                    stream_response(),
                    media_type="text/event-stream"
                )
            else:
                # Handle non-streaming response
                response = await client.request(
                    request.method,
                    target_url,
                    headers=headers,
                    content=body
                )
                return JSONResponse(
                    content=response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
                    status_code=response.status_code
                )
    except Exception as e:
        stats.errors += 1
        stats.last_error = str(e)
        stats.is_healthy = False
        raise HTTPException(status_code=502, detail=f"Backend error: {str(e)}")


# OpenAI-compatible API endpoints
@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def openai_api(request: Request, path: str):
    """Proxy OpenAI-compatible API requests."""
    return await proxy_request(request, f"/v1/{path}")


@app.get("/health")
async def health():
    """Health check endpoint."""
    healthy_backends = sum(1 for stats in router_state.backend_stats.values() if stats.is_healthy)
    return {
        "status": "healthy" if healthy_backends > 0 else "degraded",
        "healthy_backends": healthy_backends,
        "total_backends": len(router_state.backends)
    }


@app.get("/stats")
async def stats():
    """Get router statistics."""
    return {
        "total_requests": router_state.total_requests,
        "backends": {
            backend: {
                "requests": stats.requests,
                "errors": stats.errors,
                "is_healthy": stats.is_healthy,
                "last_error": stats.last_error
            }
            for backend, stats in router_state.backend_stats.items()
        }
    }


@app.get("/v1/models")
async def list_models():
    """List models from first healthy backend."""
    backend = await get_next_healthy_backend()
    if not backend:
        raise HTTPException(status_code=503, detail="No backends available")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{backend}/v1/models")
        return response.json()


def main():
    parser = argparse.ArgumentParser(description="Rollout Router - Load balancer for vLLM")
    parser.add_argument("--port", type=int, default=8010, help="Port to listen on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--backends", type=str, required=True,
                        help="Comma-separated list of backend URLs (e.g., 'server1:8001,server2:8001')")
    args = parser.parse_args()

    # Initialize backends
    router_state.backends = parse_backends(args.backends)
    router_state.backend_stats = {backend: BackendStats() for backend in router_state.backends}

    print(f"Starting Rollout Router on port {args.port}")
    print(f"Backends: {router_state.backends}")

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
