#!/usr/bin/env python3
"""Reward server for VeRL job group.

Computes rewards for math problem solutions by verifying answers
against ground truth. Supports both exact matching and symbolic
equivalence checking.

Usage:
    python reward_server.py --port 8001
"""

import argparse
import re
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="VeRL Reward Server", description="Computes rewards for RL training")


class RewardItem(BaseModel):
    """A single item for reward computation."""
    prompt: str
    response: str
    ground_truth: str


class RewardRequest(BaseModel):
    """Batch reward request."""
    items: List[RewardItem]


class RewardResult(BaseModel):
    """Reward result for a single item."""
    reward: float
    correct: bool
    extracted_answer: Optional[str] = None
    details: Optional[str] = None


class RewardResponse(BaseModel):
    """Batch reward response."""
    rewards: List[RewardResult]
    accuracy: float


def extract_number(text: str) -> Optional[str]:
    """Extract the final numerical answer from text.

    Handles various formats:
    - "The answer is 42"
    - "#### 42"
    - "= 42"
    - Just "42" at the end
    """
    if not text:
        return None

    text = text.strip()

    # Try common answer patterns
    patterns = [
        r"####\s*([-+]?\d*\.?\d+)",  # GSM8K format
        r"[Tt]he\s+(?:final\s+)?answer\s+is[:\s]*([-+]?\d*\.?\d+)",
        r"[Aa]nswer[:\s]*([-+]?\d*\.?\d+)",
        r"=\s*([-+]?\d*\.?\d+)\s*$",
        r"([-+]?\d*\.?\d+)\s*$",  # Number at end
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()

    return None


def normalize_number(num_str: str) -> Optional[float]:
    """Normalize a number string to float for comparison."""
    if not num_str:
        return None
    try:
        # Remove commas and spaces
        num_str = num_str.replace(",", "").replace(" ", "")
        return float(num_str)
    except ValueError:
        return None


def check_symbolic_equivalence(answer1: str, answer2: str) -> bool:
    """Check if two mathematical expressions are equivalent using SymPy."""
    try:
        from sympy import simplify, sympify
        from sympy.parsing.sympy_parser import parse_expr

        expr1 = parse_expr(answer1)
        expr2 = parse_expr(answer2)

        return simplify(expr1 - expr2) == 0
    except Exception:
        return False


def compute_reward(response: str, ground_truth: str) -> tuple[float, bool, Optional[str], str]:
    """Compute reward for a single response.

    Returns:
        tuple: (reward, correct, extracted_answer, details)
    """
    extracted = extract_number(response)
    expected = extract_number(ground_truth) or ground_truth.strip()

    if extracted is None:
        return 0.0, False, None, "Could not extract answer from response"

    # Try exact string match first
    if extracted == expected:
        return 1.0, True, extracted, "Exact match"

    # Try numerical comparison
    extracted_num = normalize_number(extracted)
    expected_num = normalize_number(expected)

    if extracted_num is not None and expected_num is not None:
        # Allow small floating point tolerance
        if abs(extracted_num - expected_num) < 1e-6:
            return 1.0, True, extracted, "Numerical match"

        # Check if within 1% for large numbers
        if expected_num != 0 and abs((extracted_num - expected_num) / expected_num) < 0.01:
            return 0.8, True, extracted, "Approximate match (within 1%)"

    # Try symbolic equivalence as last resort
    if check_symbolic_equivalence(extracted, expected):
        return 1.0, True, extracted, "Symbolic equivalence"

    return 0.0, False, extracted, f"Incorrect: expected {expected}, got {extracted}"


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "reward-server"}


@app.post("/reward")
async def single_reward(item: RewardItem) -> RewardResult:
    """Compute reward for a single item."""
    reward, correct, extracted, details = compute_reward(item.response, item.ground_truth)
    return RewardResult(
        reward=reward,
        correct=correct,
        extracted_answer=extracted,
        details=details
    )


@app.post("/batch_reward", response_model=RewardResponse)
async def batch_reward(request: RewardRequest) -> RewardResponse:
    """Compute rewards for a batch of items."""
    results = []
    correct_count = 0

    for item in request.items:
        reward, correct, extracted, details = compute_reward(item.response, item.ground_truth)
        results.append(RewardResult(
            reward=reward,
            correct=correct,
            extracted_answer=extracted,
            details=details
        ))
        if correct:
            correct_count += 1

    accuracy = correct_count / len(request.items) if request.items else 0.0

    return RewardResponse(
        rewards=results,
        accuracy=accuracy
    )


@app.get("/stats")
async def get_stats():
    """Get server statistics."""
    return {
        "service": "reward-server",
        "type": "rule-based",
        "supported_tasks": ["math-verification", "gsm8k"]
    }


def main():
    parser = argparse.ArgumentParser(description="VeRL Reward Server")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8001, help="Port to listen on")
    args = parser.parse_args()

    print(f"Starting reward server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
