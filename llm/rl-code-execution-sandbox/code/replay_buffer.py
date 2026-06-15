"""Replay Buffer Server for RLHF Training.

This server provides a centralized experience replay buffer that stores
(prompt, response, reward) tuples and allows sampling for training.

Features:
- Thread-safe storage with configurable capacity
- Uniform random sampling
- Priority-based sampling (optional)
- Statistics tracking

API Endpoints:
- POST /add: Add experiences to the buffer
- POST /sample: Sample a batch of experiences
- GET /stats: Get buffer statistics
- POST /clear: Clear the buffer
- GET /health: Health check
"""

import argparse
from collections import deque
from dataclasses import dataclass
from dataclasses import field
import random
import threading
import time
from typing import List, Optional

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
import uvicorn


@dataclass
class Experience:
    """A single experience tuple."""
    prompt: str
    response: str
    reward: float
    ground_truth: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    priority: float = 1.0


class AddExperienceRequest(BaseModel):
    """Request to add experiences to the buffer."""
    experiences: List[dict]


class SampleRequest(BaseModel):
    """Request to sample from the buffer."""
    batch_size: int = 4
    prioritized: bool = False


class ReplayBuffer:
    """Thread-safe replay buffer with priority sampling support."""

    def __init__(self, capacity: int = 10000):
        self.capacity = capacity
        self.buffer: deque = deque(maxlen=capacity)
        self.lock = threading.Lock()
        self.total_added = 0
        self.total_sampled = 0

    def add(self, experiences: List[Experience]) -> int:
        """Add experiences to the buffer."""
        with self.lock:
            for exp in experiences:
                self.buffer.append(exp)
                self.total_added += 1
        return len(experiences)

    def sample(self,
               batch_size: int,
               prioritized: bool = False) -> List[Experience]:
        """Sample a batch of experiences."""
        with self.lock:
            if len(self.buffer) == 0:
                return []

            actual_size = min(batch_size, len(self.buffer))

            if prioritized:
                # Priority-based sampling (higher reward = higher priority)
                priorities = [exp.priority for exp in self.buffer]
                total_priority = sum(priorities)
                if total_priority > 0:
                    probs = [p / total_priority for p in priorities]
                    indices = random.choices(range(len(self.buffer)),
                                             weights=probs,
                                             k=actual_size)
                else:
                    indices = random.sample(range(len(self.buffer)),
                                            actual_size)
            else:
                # Uniform random sampling
                indices = random.sample(range(len(self.buffer)), actual_size)

            samples = [self.buffer[i] for i in indices]
            self.total_sampled += len(samples)
            return samples

    def clear(self):
        """Clear the buffer."""
        with self.lock:
            self.buffer.clear()

    def stats(self) -> dict:
        """Get buffer statistics."""
        with self.lock:
            rewards = [exp.reward for exp in self.buffer]
            return {
                "size": len(self.buffer),
                "capacity": self.capacity,
                "total_added": self.total_added,
                "total_sampled": self.total_sampled,
                "avg_reward": sum(rewards) / len(rewards) if rewards else 0,
                "min_reward": min(rewards) if rewards else 0,
                "max_reward": max(rewards) if rewards else 0,
                "positive_ratio": sum(1 for r in rewards if r > 0) /
                                  len(rewards) if rewards else 0,
            }


# Initialize FastAPI app
app = FastAPI(title="Replay Buffer Server",
              description="Experience replay buffer for RLHF training")

# Global buffer instance
buffer: Optional[ReplayBuffer] = None


@app.on_event("startup")
async def startup():
    """Initialize the replay buffer on startup."""
    global buffer
    buffer = ReplayBuffer(capacity=10000)
    print("Replay buffer initialized with capacity 10000")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "buffer_size": len(buffer.buffer) if buffer else 0
    }


@app.post("/add")
async def add_experiences(request: AddExperienceRequest):
    """Add experiences to the replay buffer.

    Each experience should have:
    - prompt: The input prompt
    - response: The model's response
    - reward: The reward score
    - ground_truth (optional): The correct answer
    """
    if buffer is None:
        raise HTTPException(status_code=503, detail="Buffer not initialized")

    experiences = []
    for exp_dict in request.experiences:
        exp = Experience(
            prompt=exp_dict.get("prompt", ""),
            response=exp_dict.get("response", ""),
            reward=exp_dict.get("reward", 0.0),
            ground_truth=exp_dict.get("ground_truth"),
            priority=abs(exp_dict.get("reward", 0.0)) +
            0.1  # Higher reward = higher priority
        )
        experiences.append(exp)

    added = buffer.add(experiences)

    return {"added": added, "buffer_size": len(buffer.buffer)}


@app.post("/sample")
async def sample_experiences(request: SampleRequest):
    """Sample a batch of experiences from the buffer.

    Args:
        batch_size: Number of experiences to sample
        prioritized: If True, use priority-based sampling
    """
    if buffer is None:
        raise HTTPException(status_code=503, detail="Buffer not initialized")

    if len(buffer.buffer) == 0:
        return {"experiences": [], "message": "Buffer is empty"}

    samples = buffer.sample(request.batch_size, request.prioritized)

    return {
        "experiences": [{
            "prompt": exp.prompt,
            "response": exp.response,
            "reward": exp.reward,
            "ground_truth": exp.ground_truth,
            "timestamp": exp.timestamp
        } for exp in samples],
        "sampled": len(samples),
        "buffer_size": len(buffer.buffer)
    }


@app.get("/stats")
async def get_stats():
    """Get buffer statistics."""
    if buffer is None:
        raise HTTPException(status_code=503, detail="Buffer not initialized")

    return buffer.stats()


@app.post("/clear")
async def clear_buffer():
    """Clear the replay buffer."""
    if buffer is None:
        raise HTTPException(status_code=503, detail="Buffer not initialized")

    buffer.clear()
    return {"status": "cleared"}


def main():
    parser = argparse.ArgumentParser(description="Replay Buffer Server")
    parser.add_argument("--port", type=int, default=8003, help="Port to run on")
    parser.add_argument("--host",
                        type=str,
                        default="0.0.0.0",
                        help="Host to bind to")
    parser.add_argument("--capacity",
                        type=int,
                        default=10000,
                        help="Buffer capacity")
    args = parser.parse_args()

    # Update global capacity
    global buffer
    buffer = ReplayBuffer(capacity=args.capacity)

    print(f"Starting Replay Buffer Server on {args.host}:{args.port}")
    print(f"Buffer capacity: {args.capacity}")

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
