#!/usr/bin/env python3
"""Data server for RL code-execution training - serves code prompts + tests.

This server provides batches of MBPP-style code-generation problems. Each
problem is a natural-language task description plus a set of *hidden* test
cases (assert statements). The model is asked to write a Python function; the
sandbox reward server later runs the function against the hidden tests to
decide the reward.

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

app = FastAPI(title="RL Code Data Server",
              description="Serves code-generation prompts for training")

# Global state
prompts_data: List[dict] = []
current_index: int = 0


class Prompt(BaseModel):
    """A single code-generation prompt with its hidden tests."""
    id: int
    prompt: str
    # Hidden test cases (assert statements) used by the reward server. These
    # are NOT shown to the model in full; only one example test is included in
    # the prompt as a signature hint (standard MBPP practice).
    tests: List[str]
    # Optional setup code (imports / helper definitions) the tests rely on.
    setup_code: str = ""


class PromptBatch(BaseModel):
    """A batch of prompts."""
    prompts: List[Prompt]
    total_available: int


def make_prompt(text: str, example_test: str) -> str:
    """Format a code-generation instruction for a completion-style model.

    The prompt is left ending with an open ```python fence so that a
    completion model immediately starts emitting the function body. The reward
    server is tolerant of either a fully fenced block or raw code.
    """
    return (
        "You are an expert Python programmer. Write a single, self-contained "
        "Python function that solves the problem below. Respond with exactly "
        "one Python code block and nothing else.\n\n"
        f"Problem: {text}\n\n"
        f"Your function must pass tests such as:\n{example_test}\n\n"
        "```python\n")


# A small, deterministic fallback set so the pipeline runs fully offline (e.g.
# when HuggingFace is unreachable). These mirror the shape of MBPP problems.
FALLBACK_PROBLEMS = [
    {
        "text": "Write a function to find the shared elements from the given "
                "two lists.",
        "tests": [
            "assert set(similar_elements((3, 4, 5, 6), (5, 7, 4, 10))) == "
            "set((4, 5))",
            "assert set(similar_elements((1, 2, 3, 4), (5, 4, 3, 7))) == "
            "set((3, 4))",
            "assert set(similar_elements((11, 12, 14, 13), (17, 15, 14, 13))) "
            "== set((13, 14))",
        ],
    },
    {
        "text": "Write a python function to identify non-prime numbers. It "
                "should return True if the number is not prime.",
        "tests": [
            "assert is_not_prime(2) == False",
            "assert is_not_prime(10) == True",
            "assert is_not_prime(35) == True",
        ],
    },
    {
        "text": "Write a function to find squares of individual elements in a "
                "list.",
        "tests": [
            "assert square_nums([1, 2, 3, 4, 5]) == [1, 4, 9, 16, 25]",
            "assert square_nums([10, 20, 30]) == [100, 400, 900]",
            "assert square_nums([12, 15]) == [144, 225]",
        ],
    },
    {
        "text": "Write a function to reverse the order of words in a given "
                "string.",
        "tests": [
            "assert reverse_words('python program') == 'program python'",
            "assert reverse_words('java language') == 'language java'",
            "assert reverse_words('indian man') == 'man indian'",
        ],
    },
    {
        "text": "Write a python function to check whether the given number is "
                "even or not.",
        "tests": [
            "assert is_even(1) == False",
            "assert is_even(2) == True",
            "assert is_even(2018) == True",
        ],
    },
    {
        "text": "Write a python function to find the sum of all items in a "
                "list.",
        "tests": [
            "assert sum_list([1, 2, 3]) == 6",
            "assert sum_list([15, 12, 13, 10]) == 50",
            "assert sum_list([0, 1, 2]) == 3",
        ],
    },
]


def load_fallback():
    """Populate prompts_data from the embedded fallback problem set."""
    global prompts_data
    prompts_data = []
    for i, problem in enumerate(FALLBACK_PROBLEMS):
        prompts_data.append({
            "id": i,
            "prompt": make_prompt(problem["text"], problem["tests"][0]),
            "tests": problem["tests"],
            "setup_code": problem.get("setup_code", ""),
        })
    print(f"Using {len(prompts_data)} fallback code problems")


def load_dataset(max_problems: int):
    """Load a small subset of the MBPP dataset from HuggingFace."""
    global prompts_data

    try:
        from datasets import load_dataset as hf_load_dataset
        print("Loading MBPP dataset...")
        dataset = hf_load_dataset("google-research-datasets/mbpp",
                                  "full",
                                  split="train")

        prompts_data = []
        for i, item in enumerate(dataset):
            tests = list(item["test_list"])
            if not tests:
                continue
            setup_code = item.get("test_setup_code", "") or ""
            prompts_data.append({
                "id": i,
                "prompt": make_prompt(item["text"], tests[0]),
                "tests": tests,
                "setup_code": setup_code,
            })

        # Shuffle and keep a small subset so this stays runnable on a small
        # model and modest sandbox concurrency.
        random.shuffle(prompts_data)
        if max_problems > 0:
            prompts_data = prompts_data[:max_problems]
        print(f"Loaded {len(prompts_data)} problems from MBPP")

    except Exception as e:  # pylint: disable=broad-except
        print(f"Error loading dataset: {e}")
        load_fallback()


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
    parser = argparse.ArgumentParser(description="RL Code Data Server")
    parser.add_argument("--port",
                        type=int,
                        default=8000,
                        help="Port to run server on")
    parser.add_argument("--host",
                        type=str,
                        default="0.0.0.0",
                        help="Host to bind to")
    parser.add_argument("--max-problems",
                        type=int,
                        default=100,
                        help="Max number of MBPP problems to serve (0 = all)")
    args = parser.parse_args()

    load_dataset(args.max_problems)

    print(f"Starting data server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
