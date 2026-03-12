#!/usr/bin/env python3
"""Reward server for RLHF training - verifies math answers.

This server computes rewards by comparing generated answers against ground truth.
Uses simple string/numeric matching for math problems.

Usage:
    python reward_server.py --port 8002
"""

import argparse
import re
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="RLHF Reward Server",
              description="Computes rewards for math responses")


class RewardRequest(BaseModel):
    """Request for computing reward for a single response."""
    prompt: str
    response: str
    ground_truth: str


class RewardResponse(BaseModel):
    """Reward computation result."""
    reward: float
    extracted_answer: Optional[str]
    ground_truth: str
    correct: bool


class BatchRewardRequest(BaseModel):
    """Request for computing rewards for multiple responses."""
    items: List[RewardRequest]


class BatchRewardResponse(BaseModel):
    """Batch reward computation results."""
    rewards: List[RewardResponse]
    mean_reward: float
    accuracy: float


def extract_answer(response: str) -> Optional[str]:
    """Extract the final numerical answer from a response.

    Tries multiple patterns commonly used in math solutions:
    1. "#### <answer>" (GSM8K format)
    2. "The answer is <answer>"
    3. "= <answer>" at the end
    4. Last number in the response
    """
    response = response.strip()

    # Pattern 1: GSM8K format "#### <answer>"
    match = re.search(r'####\s*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)', response)
    if match:
        return match.group(1).replace(',', '')

    # Pattern 2: "The answer is <answer>"
    match = re.search(
        r'[Tt]he\s+(?:final\s+)?answer\s+is[:\s]*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)',
        response)
    if match:
        return match.group(1).replace(',', '')

    # Pattern 3: "= <answer>" at the end of a line
    match = re.search(r'=\s*([+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*$', response,
                      re.MULTILINE)
    if match:
        return match.group(1).replace(',', '')

    # Pattern 4: Last number in the response
    numbers = re.findall(r'([+-]?\d+(?:,\d{3})*(?:\.\d+)?)', response)
    if numbers:
        return numbers[-1].replace(',', '')

    return None


def normalize_answer(answer: str) -> str:
    """Normalize an answer for comparison."""
    if answer is None:
        return ""
    # Remove commas, whitespace, and convert to lowercase
    answer = answer.replace(',', '').strip().lower()
    # Try to parse as number and format consistently
    try:
        num = float(answer)
        # If it's a whole number, return as int
        if num == int(num):
            return str(int(num))
        return str(num)
    except ValueError:
        return answer


def compute_reward(prompt: str, response: str,
                   ground_truth: str) -> RewardResponse:
    """Compute reward by comparing extracted answer to ground truth."""
    extracted = extract_answer(response)
    normalized_extracted = normalize_answer(extracted)
    normalized_truth = normalize_answer(ground_truth)

    # Check if answers match
    correct = normalized_extracted == normalized_truth

    # Binary reward: 1.0 for correct, 0.0 for incorrect
    reward = 1.0 if correct else 0.0

    return RewardResponse(reward=reward,
                          extracted_answer=extracted,
                          ground_truth=ground_truth,
                          correct=correct)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/reward", response_model=RewardResponse)
async def get_reward(request: RewardRequest):
    """Compute reward for a single response."""
    return compute_reward(request.prompt, request.response,
                          request.ground_truth)


@app.post("/batch_reward", response_model=BatchRewardResponse)
async def get_batch_reward(request: BatchRewardRequest):
    """Compute rewards for a batch of responses."""
    rewards = [
        compute_reward(item.prompt, item.response, item.ground_truth)
        for item in request.items
    ]

    total_reward = sum(r.reward for r in rewards)
    correct_count = sum(1 for r in rewards if r.correct)

    return BatchRewardResponse(
        rewards=rewards,
        mean_reward=total_reward / len(rewards) if rewards else 0.0,
        accuracy=correct_count / len(rewards) if rewards else 0.0)


def main():
    parser = argparse.ArgumentParser(description="RLHF Reward Server")
    parser.add_argument("--port",
                        type=int,
                        default=8002,
                        help="Port to run server on")
    parser.add_argument("--host",
                        type=str,
                        default="0.0.0.0",
                        help="Host to bind to")
    args = parser.parse_args()

    print(f"Starting reward server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
