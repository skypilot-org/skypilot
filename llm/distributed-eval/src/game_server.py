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
import uvicorn


class GameEnvironment:
    """
    Simple game environment for demonstration.
    """

    def __init__(self, max_steps=100):
        self.state_dim = 100
        self.action_dim = 10
        self.base_max_steps = max_steps  # Base episode length
        self.max_steps = max_steps  # Actual max steps for current episode

    def reset(self):
        """Reset environment and return initial state."""
        self.steps = 0
        # Add some randomness to episode length (±30% variation)
        variation = np.random.uniform(0.7, 1.3)
        self.max_steps = int(self.base_max_steps * variation)
        return np.random.randn(self.state_dim)

    def step(self, action):
        """Execute action and return next state, done flag."""
        self.steps += 1
        next_state = np.random.randn(self.state_dim)
        
        # Episode ends when reaching max steps
        done = self.steps >= self.max_steps
        
        # Add 1% chance of early termination (simulating task completion or failure)
        if not done and np.random.random() < 0.01:
            done = True
            
        return next_state, done


class GameServer:
    """
    Game server that:
    1. Runs game simulation
    2. Sends states to evaluation head
    3. Executes returned actions
    """

    def __init__(self, server_id: str, max_steps: int = 100):
        self.server_id = server_id
        self.env = GameEnvironment(max_steps=max_steps)

        # Statistics
        self.stats = {
            "episodes": 0,
            "total_steps": 0,
            "current_episode_steps": 0
        }

        self.running = False

    def reset(self):
        """Reset the environment and return initial state."""
        self.current_state = self.env.reset()
        self.stats["current_episode_steps"] = 0
        return self.current_state
    
    def step(self, action):
        """Execute an action and return the new state."""
        # Execute action in environment
        self.current_state, done = self.env.step(action)
        
        self.stats["total_steps"] += 1
        self.stats["current_episode_steps"] += 1
        
        if done:
            self.stats["episodes"] += 1
            print(f"Episode {self.stats['episodes']} completed "
                  f"({self.stats['current_episode_steps']} steps)")
            # Auto-reset for next episode
            self.current_state = self.env.reset()
            self.stats["current_episode_steps"] = 0
        
        return {
            "state": self.current_state.tolist(),
            "done": done,
            "episode": self.stats["episodes"],
            "total_steps": self.stats["total_steps"]
        }


# Create FastAPI app
app = FastAPI(title="Game Server")
game_server = None


@app.post("/reset")
async def reset_game():
    """Reset the game environment and return initial state."""
    state = game_server.reset()
    return {
        "state": state.tolist(),
        "server_id": game_server.server_id,
        "episode": game_server.stats["episodes"]
    }


@app.post("/step")
async def step_game(request: dict):
    """Execute an action and return the new state."""
    action = np.array(request["action"])
    result = game_server.step(action)
    result["server_id"] = game_server.server_id
    return result


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
    parser.add_argument("--max-steps", type=int, default=100,
                        help="Maximum steps per episode (default: 100)")
    args = parser.parse_args()

    # Initialize global game server with command line args
    global game_server
    game_server = GameServer(args.server_id, max_steps=args.max_steps)

    print(f"""
    ╔═════════════════════════════════════╗
    ║         GAME SERVER                 ║
    ╠═════════════════════════════════════╣
    ║  Server ID: {args.server_id:<24}║
    ║  Port: {args.port:<29}║
    ║  Max Steps: {args.max_steps:<24}║
    ║  Mode: {'Eval Head Controlled':<29}║
    ╚═════════════════════════════════════════╝
    
    Waiting for eval head to send actions...
    """)

    # Just run the server - eval head will control it
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
