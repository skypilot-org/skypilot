#!/usr/bin/env python3
"""Sandbox reward server for RL code-execution training.

This server exposes the same ``/batch_reward`` interface the trainer already
calls, but instead of string-matching a math answer it *runs the model's code*
to decide the reward:

  1. Extract the Python code block from the model's response.
  2. Run ``candidate_code + setup_code + hidden_tests`` inside a SkyPilot
     Sandbox, an isolated per-pod environment on your own Kubernetes cluster.
  3. Reward = 1.0 if every test passes (process exit_code == 0), else 0.0.
     A crash, timeout, or non-zero exit is reward 0.0. A bad rollout must
     never raise out of here.

The whole batch is scored concurrently using the async ``.aio`` siblings of the
sandbox SDK (``sky.sandbox.create.aio`` / ``sb.exec.aio``) driven by
``asyncio.gather``. Sandboxes are claimed from a pre-warmed pool so each launch
is sub-second.

Usage:
    python sandbox_reward_server.py --port 8002 --pool rl-reward
"""

import argparse
import asyncio
import re
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

try:
    import sky.sandbox as sandbox  # public SDK: ``import sky; sky.sandbox...``
except ImportError:
    # The bundled Sandbox SDK also ships a top-level ``sky_sandbox`` shim that
    # works even when the host ``sky`` predates the client-SDK entry-point
    # loader that exposes ``sky.sandbox``.
    import sky_sandbox as sandbox

app = FastAPI(title="Sandbox Reward Server",
              description="Runs model-generated code in sandboxes for rewards")

# Filled in by main() from CLI args; read by the request handlers.
POOL_NAME: str = "rl-reward"
POOL_IMAGE: str = "python:3.11-slim"
POOL_REPLICAS: int = 8
EXEC_TIMEOUT_SECONDS: float = 30.0
# Outputs captured from the sandbox are truncated before being returned, so a
# pathological program can't bloat the reward response.
MAX_OUTPUT_CHARS: int = 2000


class RewardRequest(BaseModel):
    """Request for computing reward for a single response."""
    prompt: str
    response: str
    # Hidden test cases (assert statements) the generated code must pass.
    tests: List[str] = []
    # Optional setup code (imports / helpers) the tests rely on.
    setup_code: str = ""


class RewardResponse(BaseModel):
    """Reward computation result for a single response."""
    reward: float
    passed: bool
    exit_code: Optional[int] = None
    extracted_code: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None


class BatchRewardRequest(BaseModel):
    """Request for computing rewards for multiple responses."""
    items: List[RewardRequest]


class BatchRewardResponse(BaseModel):
    """Batch reward computation results."""
    rewards: List[RewardResponse]
    mean_reward: float
    pass_rate: float


def extract_code(response: str) -> str:
    """Extract a Python code block from the model's response.

    Handles three cases:
    1. A fully fenced ```python ... ``` (or bare ``` ... ```) block.
    2. A completion that began inside an open fence from the prompt and then
       emitted code until a closing fence.
    3. Raw code with no fences at all.
    """
    fenced = re.findall(r"```(?:python)?\s*\n(.*?)```", response, re.DOTALL)
    if fenced:
        return fenced[0].strip()

    code = response
    # The prompt ends with an open ```python fence, so the model often emits
    # code and then a single closing fence. Cut at the first closing fence.
    if "```" in code:
        code = code.split("```", 1)[0]
    return code.strip()


def build_test_script(code: str, setup_code: str, tests: List[str]) -> str:
    """Assemble the script run inside the sandbox: code + setup + asserts."""
    parts = [code]
    if setup_code:
        parts.append(setup_code)
    parts.extend(tests)
    return "\n\n".join(parts) + "\n"


def _truncate(text: Optional[str]) -> str:
    if not text:
        return ""
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "... [truncated]"
    return text


async def score_one(sb, item: RewardRequest) -> RewardResponse:
    """Score a single rollout in its own sandbox. Never raises."""
    code = extract_code(item.response)

    # No code or no tests means we can't verify anything: reward 0.0.
    if not code:
        return RewardResponse(reward=0.0,
                              passed=False,
                              extracted_code=code,
                              error="no code block found in response")
    if not item.tests:
        return RewardResponse(reward=0.0,
                              passed=False,
                              extracted_code=_truncate(code),
                              error="no tests provided for this problem")

    script = build_test_script(code, item.setup_code, item.tests)

    try:
        # argv tokens, no implicit shell. Bound the run so an infinite loop in
        # generated code can't stall the batch.
        result = await asyncio.wait_for(sb.exec.aio("python", "-c", script),
                                        timeout=EXEC_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return RewardResponse(reward=0.0,
                              passed=False,
                              extracted_code=_truncate(code),
                              error=f"timed out after {EXEC_TIMEOUT_SECONDS}s")
    except Exception as e:  # pylint: disable=broad-except
        # Anything else (sandbox error, transport error, ...) is a 0.0 reward,
        # never an exception that bubbles up and kills the batch.
        return RewardResponse(reward=0.0,
                              passed=False,
                              extracted_code=_truncate(code),
                              error=f"sandbox exec failed: {e}")

    exit_code = result.get("exit_code")
    passed = exit_code == 0
    return RewardResponse(reward=1.0 if passed else 0.0,
                          passed=passed,
                          exit_code=exit_code,
                          extracted_code=_truncate(code),
                          stdout=_truncate(result.get("stdout")),
                          stderr=_truncate(result.get("stderr")))


async def score_batch(items: List[RewardRequest]) -> List[RewardResponse]:
    """Score a whole batch concurrently, one sandbox per rollout.

    This is the headline pattern: create a batch of sandboxes from the warm
    pool in a single call, fan out the per-rollout ``exec`` calls with
    ``asyncio.gather``, and ALWAYS tear the sandboxes down in a ``finally``
    block, even if an exec raises.
    """
    if not items:
        return []

    sandboxes = await sandbox.create.aio(name="reward",
                                         num_sandboxes=len(items),
                                         pool=POOL_NAME)
    try:
        rewards = await asyncio.gather(
            *(score_one(sb, item) for sb, item in zip(sandboxes, items)))
    finally:
        # Tear every sandbox down regardless of what happened above. Each
        # sandbox is reward-0 isolated, so one bad rollout never leaks.
        await asyncio.gather(*(sb.terminate.aio() for sb in sandboxes),
                             return_exceptions=True)
    return list(rewards)


@app.on_event("startup")
def ensure_pool():
    """Create (or resize) the warm pool the reward sandboxes are claimed from.

    A pool keeps pre-provisioned pods idle and ready, so ``create`` claims a
    running pod instead of waiting on Kubernetes scheduling + image pull,
    cutting launch time by more than 50%.
    """
    try:
        sandbox.create_pool(name=POOL_NAME,
                            image=POOL_IMAGE,
                            cpus=1,
                            memory_gb=2,
                            replicas=POOL_REPLICAS)
        print(f"Created warm pool '{POOL_NAME}' "
              f"(image={POOL_IMAGE}, replicas={POOL_REPLICAS})")
    except Exception as e:  # pylint: disable=broad-except
        # Pool may already exist from a previous run; make sure it is at least
        # the requested size and carry on.
        print(f"create_pool('{POOL_NAME}') did not create a new pool ({e}); "
              f"ensuring size {POOL_REPLICAS}")
        try:
            sandbox.set_pool_size(POOL_NAME, replicas=POOL_REPLICAS)
        except Exception as e2:  # pylint: disable=broad-except
            print(f"Warning: could not set pool size: {e2}")


@app.on_event("shutdown")
async def close_session():
    """Release the shared sandbox session once, when the server stops."""
    try:
        await sandbox.aclose()
    except Exception as e:  # pylint: disable=broad-except
        print(f"Warning: sky.sandbox.aclose() failed: {e}")


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "pool": POOL_NAME}


@app.post("/reward", response_model=RewardResponse)
async def get_reward(request: RewardRequest):
    """Compute reward for a single response (scored in one sandbox)."""
    rewards = await score_batch([request])
    return rewards[0]


@app.post("/batch_reward", response_model=BatchRewardResponse)
async def get_batch_reward(request: BatchRewardRequest):
    """Compute rewards for a batch of responses, one sandbox each."""
    rewards = await score_batch(request.items)

    if not rewards:
        return BatchRewardResponse(rewards=[], mean_reward=0.0, pass_rate=0.0)

    total_reward = sum(r.reward for r in rewards)
    passed_count = sum(1 for r in rewards if r.passed)
    return BatchRewardResponse(rewards=rewards,
                               mean_reward=total_reward / len(rewards),
                               pass_rate=passed_count / len(rewards))


def main():
    parser = argparse.ArgumentParser(description="Sandbox Reward Server")
    parser.add_argument("--port",
                        type=int,
                        default=8002,
                        help="Port to run server on")
    parser.add_argument("--host",
                        type=str,
                        default="0.0.0.0",
                        help="Host to bind to")
    parser.add_argument("--pool",
                        type=str,
                        default="rl-reward",
                        help="Warm sandbox pool to claim reward sandboxes from")
    parser.add_argument("--pool-image",
                        type=str,
                        default="python:3.11-slim",
                        help="Container image for the warm pool")
    parser.add_argument("--pool-replicas",
                        type=int,
                        default=8,
                        help="Number of warm pods to keep ready")
    parser.add_argument("--exec-timeout",
                        type=float,
                        default=30.0,
                        help="Per-rollout execution timeout in seconds")
    args = parser.parse_args()

    global POOL_NAME, POOL_IMAGE, POOL_REPLICAS, EXEC_TIMEOUT_SECONDS
    POOL_NAME = args.pool
    POOL_IMAGE = args.pool_image
    POOL_REPLICAS = args.pool_replicas
    EXEC_TIMEOUT_SECONDS = args.exec_timeout

    print(f"Starting sandbox reward server on {args.host}:{args.port}")
    print(f"Warm pool: {POOL_NAME} (image={POOL_IMAGE}, "
          f"replicas={POOL_REPLICAS})")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
