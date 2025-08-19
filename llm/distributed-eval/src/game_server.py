#!/usr/bin/env python3
"""
Game Server
===========
Simulates a game environment and gets AI actions from the evaluation head.
"""

import argparse
import asyncio
from typing import Optional
import uuid

from fastapi import FastAPI
import numpy as np
import requests
import uvicorn


class GameEnvironment:
    """
    Simple game environment for demonstration.
    """

    def __init__(self):
        self.state_dim = 100
        self.action_dim = 10
        self.max_steps = 20  # Reduced from 100 to make episodes quicker

    def reset(self):
        """Reset environment and return initial state."""
        self.steps = 0
        return np.random.randn(self.state_dim)

    def step(self, action):
        """Execute action and return next state, done flag."""
        self.steps += 1
        next_state = np.random.randn(self.state_dim)
        done = self.steps >= self.max_steps
        return next_state, done


class GameServer:
    """
    Game server that:
    1. Runs game simulation
    2. Sends states to evaluation head
    3. Executes returned actions
    """

    def __init__(self, server_id: str, eval_head_url: Optional[str] = None):
        self.server_id = server_id
        self.eval_head_url = eval_head_url
        self.env = GameEnvironment()

        # Statistics
        self.stats = {
            "episodes": 0,
            "total_steps": 0,
            "current_episode_steps": 0
        }

        self.running = False

    def get_action(self, state):
        """Get action from evaluation head or use random action."""
        if not self.eval_head_url:
            # No eval head - use random actions
            return np.random.randn(self.env.action_dim) * 0.1

        try:
            # Request action from evaluation head
            url = f"{self.eval_head_url}/evaluate"
            request_data = {
                "server_id": self.server_id,
                "state": state.tolist(),
                "request_id": str(uuid.uuid4())
            }
            response = requests.post(url, json=request_data, timeout=5.0)

            if response.status_code == 200:
                # Successfully got action from eval head
                return np.array(response.json()["action"])
            else:
                print(f"Error from eval head: {response.status_code} - "
                      f"{response.text}")
                return np.random.randn(self.env.action_dim) * 0.1

        except Exception as e:
            print(f"Failed to get action: {e}")
            return np.random.randn(self.env.action_dim) * 0.1

    async def run_episode(self):
        """Run one episode of the game."""
        state = self.env.reset()
        self.stats["current_episode_steps"] = 0

        while True:
            # Get action from evaluation head
            action = self.get_action(state)

            # Execute action in environment
            state, done = self.env.step(action)

            self.stats["total_steps"] += 1
            self.stats["current_episode_steps"] += 1

            if done:
                self.stats["episodes"] += 1
                print(f"Episode {self.stats['episodes']} completed "
                      f"({self.stats['current_episode_steps']} steps)")
                break

            # Delay to simulate real game computation time
            await asyncio.sleep(0.5)  # 500ms per step for more realistic simulation

    async def game_loop(self):
        """Main game loop - runs episodes continuously."""
        self.running = True
        print(f"🎮 Starting game loop for {self.server_id}")
        print(f"📡 Eval head URL: {self.eval_head_url}")

        while self.running:
            await self.run_episode()

            # Every 10 episodes, log connection status
            if self.stats["episodes"] % 10 == 0:
                if self.eval_head_url:
                    print(f"📊 Status: {self.stats['episodes']} episodes, "
                          f"{self.stats['total_steps']} total steps sent to "
                          f"{self.eval_head_url}")
                else:
                    print(f"📊 Status: {self.stats['episodes']} episodes, "
                          f"{self.stats['total_steps']} total steps (no eval head)")
            await asyncio.sleep(2.0)  # 2 second pause between episodes


# Create FastAPI app
app = FastAPI(title="Game Server")
game_server = None


@app.post("/start")
async def start_game():
    """Start the game loop."""
    if not game_server.running:
        asyncio.create_task(game_server.game_loop())
        return {"status": "started"}
    return {"status": "already running"}


@app.post("/stop")
async def stop_game():
    """Stop the game loop."""
    game_server.running = False
    return {"status": "stopped"}


@app.post("/set_eval_head")
async def set_eval_head(request: dict):
    """Set the evaluation head URL."""
    game_server.eval_head_url = request["url"]
    return {"status": "updated", "url": game_server.eval_head_url}


@app.get("/stats")
async def get_stats():
    """Get game server statistics."""
    return game_server.stats


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy", "server_id": game_server.server_id}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--server-id",
                        default=f"game-server-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--eval-head-url", help="URL of evaluation head")
    parser.add_argument("--auto-start",
                        action="store_true",
                        help="Start game loop automatically")
    args = parser.parse_args()

    # Initialize global game server with command line args
    global game_server
    game_server = GameServer(args.server_id, args.eval_head_url)

    print(f"""
    ╔═════════════════════════════════════╗
    ║         GAME SERVER                 ║
    ╠═════════════════════════════════════╣
    ║  Server ID: {args.server_id:<24}║
    ║  Port: {args.port:<29}║
    ║  Eval Head: {args.eval_head_url or 'Not connected':<25}║
    ╚═════════════════════════════════════╝
    """)

    if args.auto_start:
        # Start game loop automatically
        async def auto_start():
            await asyncio.sleep(2)  # Wait for server to be ready
            print("✓ Auto-starting game loop")
            await game_server.game_loop()

        # Run server and game loop together
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        config = uvicorn.Config(app,
                                host="0.0.0.0",
                                port=args.port,
                                log_level="warning")
        server = uvicorn.Server(config)

        # Run both server and game loop
        loop.create_task(server.serve())
        loop.create_task(auto_start())
        loop.run_forever()
    else:
        # Just run the server
        uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()