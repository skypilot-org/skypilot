#!/usr/bin/env python3
"""Data server for RLHF training - serves math prompts from GSM8K dataset.

This server provides batches of math problems with their ground truth answers
for training LLMs on mathematical reasoning tasks.

Usage:
    python data_server.py --port 8000
"""

import argparse
import random
from typing import List, Optional

from fastapi import FastAPI
from fastapi import Query
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="RLHF Data Server",
              description="Serves math prompts for training")

# Global state
prompts_data: List[dict] = []
current_index: int = 0


class Prompt(BaseModel):
    """A single prompt with its ground truth answer."""
    id: int
    prompt: str
    ground_truth: str


class PromptBatch(BaseModel):
    """A batch of prompts."""
    prompts: List[Prompt]
    total_available: int


def load_dataset():
    """Load GSM8K dataset from HuggingFace."""
    global prompts_data

    try:
        from datasets import load_dataset
        print("Loading GSM8K dataset...")
        dataset = load_dataset("openai/gsm8k", "main", split="train")

        prompts_data = []
        for i, item in enumerate(dataset):
            # Extract the numerical answer from the solution
            # GSM8K format: solution ends with "#### <answer>"
            solution = item["answer"]
            answer_marker = "####"
            if answer_marker in solution:
                ground_truth = solution.split(answer_marker)[-1].strip()
            else:
                ground_truth = solution.strip()

            # Format prompt for instruction-following model
            prompt = f"""Solve the following math problem step by step. End your solution with the final numerical answer.

Problem: {item["question"]}

Solution:"""

            prompts_data.append({
                "id": i,
                "prompt": prompt,
                "ground_truth": ground_truth
            })

        # Shuffle for training
        random.shuffle(prompts_data)
        print(f"Loaded {len(prompts_data)} prompts from GSM8K")

    except Exception as e:
        print(f"Error loading dataset: {e}")
        # Fallback to simple math problems for testing
        prompts_data = [
            {
                "id": 0,
                "prompt": "What is 2 + 2?",
                "ground_truth": "4"
            },
            {
                "id": 1,
                "prompt": "What is 10 * 5?",
                "ground_truth": "50"
            },
            {
                "id": 2,
                "prompt": "What is 100 / 4?",
                "ground_truth": "25"
            },
            {
                "id": 3,
                "prompt": "What is 7 + 8?",
                "ground_truth": "15"
            },
            {
                "id": 4,
                "prompt": "What is 9 * 9?",
                "ground_truth": "81"
            },
        ]
        print(f"Using {len(prompts_data)} fallback prompts")


@app.on_event("startup")
async def startup_event():
    """Load dataset on startup."""
    load_dataset()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "prompts_loaded": len(prompts_data)}


@app.get("/prompts", response_model=PromptBatch)
async def get_prompts(
        batch_size: int = Query(default=8,
                                ge=1,
                                le=256,
                                description="Number of prompts to return"),
        shuffle: bool = Query(default=True,
                              description="Whether to shuffle prompts")):
    """Get a batch of prompts for training."""
    global current_index

    if not prompts_data:
        return PromptBatch(prompts=[], total_available=0)

    # Get batch of prompts
    if shuffle:
        batch = random.sample(prompts_data, min(batch_size, len(prompts_data)))
    else:
        # Sequential access with wraparound
        batch = []
        for _ in range(batch_size):
            batch.append(prompts_data[current_index])
            current_index = (current_index + 1) % len(prompts_data)

    prompts = [Prompt(**p) for p in batch]
    return PromptBatch(prompts=prompts, total_available=len(prompts_data))


@app.get("/prompt/{prompt_id}", response_model=Optional[Prompt])
async def get_prompt_by_id(prompt_id: int):
    """Get a specific prompt by ID."""
    for p in prompts_data:
        if p["id"] == prompt_id:
            return Prompt(**p)
    return None


@app.post("/reset")
async def reset_index():
    """Reset the sequential index to the beginning."""
    global current_index
    current_index = 0
    return {"status": "reset", "index": current_index}


def main():
    parser = argparse.ArgumentParser(description="RLHF Data Server")
    parser.add_argument("--port",
                        type=int,
                        default=8000,
                        help="Port to run server on")
    parser.add_argument("--host",
                        type=str,
                        default="0.0.0.0",
                        help="Host to bind to")
    args = parser.parse_args()

    print(f"Starting data server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
