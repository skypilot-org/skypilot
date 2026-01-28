#!/usr/bin/env python3
"""Data server for VeRL job group.

Serves math prompts from the GSM8K dataset via FastAPI.
This server provides training data that can be used by the VeRL trainer
or for generating rollouts.

Usage:
    python data_server.py --port 8000
"""

import argparse
import random
from typing import List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="VeRL Data Server", description="Serves math prompts for RL training")


class Prompt(BaseModel):
    """A single training prompt with ground truth."""
    prompt: str
    ground_truth: str
    question_id: Optional[str] = None


class PromptBatch(BaseModel):
    """A batch of prompts."""
    prompts: List[Prompt]
    total_available: int


class DataServer:
    """Serves math prompts from GSM8K dataset."""

    def __init__(self):
        self.prompts = []
        self.load_dataset()

    def load_dataset(self):
        """Load GSM8K dataset from HuggingFace."""
        print("Loading GSM8K dataset...")
        try:
            from datasets import load_dataset
            dataset = load_dataset("openai/gsm8k", "main", split="train")

            for i, item in enumerate(dataset):
                question = item["question"]
                answer = item["answer"]

                # Extract the final numerical answer from GSM8K format
                # GSM8K answers end with "#### <number>"
                final_answer = answer.split("####")[-1].strip() if "####" in answer else answer

                # Format as chat prompt
                prompt = f"Solve this math problem step by step.\n\nProblem: {question}\n\nSolution:"

                self.prompts.append({
                    "prompt": prompt,
                    "ground_truth": final_answer,
                    "full_solution": answer,
                    "question_id": f"gsm8k_{i}"
                })

            print(f"Loaded {len(self.prompts)} prompts from GSM8K")

        except Exception as e:
            print(f"Error loading dataset: {e}")
            # Fallback to synthetic data for testing
            self._load_synthetic_data()

    def _load_synthetic_data(self):
        """Load synthetic math problems for testing."""
        print("Loading synthetic math data...")
        synthetic_problems = [
            ("If John has 5 apples and gives 2 to Mary, how many apples does John have left?", "3"),
            ("A train travels at 60 mph. How far does it travel in 3 hours?", "180"),
            ("If a book costs $15 and you have $50, how many books can you buy?", "3"),
            ("What is 25% of 80?", "20"),
            ("If you divide 144 by 12, what do you get?", "12"),
            ("A rectangle has length 8 and width 5. What is its area?", "40"),
            ("If x + 7 = 15, what is x?", "8"),
            ("How many minutes are in 2.5 hours?", "150"),
            ("If 3 workers can build a wall in 6 days, how many days for 6 workers?", "3"),
            ("What is the sum of 123 and 456?", "579"),
        ]

        for i, (question, answer) in enumerate(synthetic_problems):
            prompt = f"Solve this math problem step by step.\n\nProblem: {question}\n\nSolution:"
            self.prompts.append({
                "prompt": prompt,
                "ground_truth": answer,
                "full_solution": f"The answer is {answer}",
                "question_id": f"synthetic_{i}"
            })

        print(f"Loaded {len(self.prompts)} synthetic prompts")

    def get_batch(self, batch_size: int, shuffle: bool = True) -> List[dict]:
        """Get a batch of prompts."""
        if shuffle:
            return random.sample(self.prompts, min(batch_size, len(self.prompts)))
        return self.prompts[:batch_size]


# Global data server instance
data_server = DataServer()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "prompts_loaded": len(data_server.prompts)}


@app.get("/prompts", response_model=PromptBatch)
async def get_prompts(
    batch_size: int = Query(default=4, ge=1, le=1000),
    shuffle: bool = Query(default=True)
):
    """Get a batch of training prompts."""
    batch = data_server.get_batch(batch_size, shuffle)
    return PromptBatch(
        prompts=[
            Prompt(
                prompt=p["prompt"],
                ground_truth=p["ground_truth"],
                question_id=p["question_id"]
            )
            for p in batch
        ],
        total_available=len(data_server.prompts)
    )


@app.get("/stats")
async def get_stats():
    """Get dataset statistics."""
    return {
        "total_prompts": len(data_server.prompts),
        "dataset": "gsm8k" if data_server.prompts and "gsm8k" in data_server.prompts[0].get("question_id", "") else "synthetic"
    }


def main():
    parser = argparse.ArgumentParser(description="VeRL Data Server")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on")
    args = parser.parse_args()

    print(f"Starting data server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
