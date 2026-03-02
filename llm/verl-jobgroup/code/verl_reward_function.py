#!/usr/bin/env python3
"""Custom VeRL reward function that calls external reward server.

This module provides a reward function that can be used with VeRL's
RewardManager to compute rewards via an external HTTP service.

The reward server URL is configured via the REWARD_SERVER_URL environment variable.

Usage in VeRL config:
    reward_model:
        custom_cls: examples.verl_reward_function.ExternalRewardFunction
"""

import os
from typing import Any, Dict, List, Optional

import httpx
import torch


class ExternalRewardFunction:
    """Reward function that calls an external reward server via HTTP.

    This allows separating the reward computation from the main training
    process, enabling:
    - Independent scaling of reward computation
    - Using different hardware (CPU vs GPU)
    - Easy swapping between rule-based and model-based rewards
    """

    def __init__(
        self,
        reward_server_url: Optional[str] = None,
        timeout: float = 30.0,
        batch_size: int = 32,
    ):
        """Initialize the external reward function.

        Args:
            reward_server_url: URL of the reward server. If not provided,
                              uses REWARD_SERVER_URL environment variable.
            timeout: HTTP request timeout in seconds.
            batch_size: Maximum batch size for reward requests.
        """
        self.reward_server_url = reward_server_url or os.environ.get(
            "REWARD_SERVER_URL", "http://reward-server-0:8001"
        )
        self.timeout = timeout
        self.batch_size = batch_size
        self.client = httpx.Client(timeout=timeout)

        print(f"ExternalRewardFunction initialized with server: {self.reward_server_url}")

    def __call__(
        self,
        data_batch: Dict[str, Any],
        tokenizer: Any = None,
    ) -> torch.Tensor:
        """Compute rewards for a batch of responses.

        This method is called by VeRL's RewardManager during training.

        Args:
            data_batch: Dictionary containing:
                - 'prompts': List of prompt strings
                - 'responses': List of response strings
                - 'ground_truths': List of ground truth answers (if available)
            tokenizer: Tokenizer (unused for external reward)

        Returns:
            torch.Tensor: Rewards for each response
        """
        prompts = data_batch.get("prompts", [])
        responses = data_batch.get("responses", [])
        ground_truths = data_batch.get("ground_truths", data_batch.get("extra_info", {}).get("ground_truth", []))

        if not prompts or not responses:
            return torch.zeros(0)

        # Ensure ground_truths has same length as responses
        if len(ground_truths) < len(responses):
            ground_truths = ground_truths + [""] * (len(responses) - len(ground_truths))

        rewards = self._compute_rewards_batch(prompts, responses, ground_truths)
        return torch.tensor(rewards, dtype=torch.float32)

    def _compute_rewards_batch(
        self,
        prompts: List[str],
        responses: List[str],
        ground_truths: List[str],
    ) -> List[float]:
        """Compute rewards by calling the external server.

        Args:
            prompts: List of prompts
            responses: List of model responses
            ground_truths: List of ground truth answers

        Returns:
            List of reward values
        """
        all_rewards = []

        # Process in batches
        for i in range(0, len(responses), self.batch_size):
            batch_prompts = prompts[i:i + self.batch_size]
            batch_responses = responses[i:i + self.batch_size]
            batch_gt = ground_truths[i:i + self.batch_size]

            items = [
                {
                    "prompt": p,
                    "response": r,
                    "ground_truth": gt
                }
                for p, r, gt in zip(batch_prompts, batch_responses, batch_gt)
            ]

            try:
                response = self.client.post(
                    f"{self.reward_server_url}/batch_reward",
                    json={"items": items}
                )
                response.raise_for_status()
                data = response.json()
                batch_rewards = [r["reward"] for r in data["rewards"]]
                all_rewards.extend(batch_rewards)

            except Exception as e:
                print(f"Error calling reward server: {e}")
                # Fall back to zero rewards on error
                all_rewards.extend([0.0] * len(items))

        return all_rewards

    def close(self):
        """Close the HTTP client."""
        self.client.close()


class MathVerificationReward:
    """Local math verification reward function (no external server needed).

    This is a fallback that can be used if the external reward server
    is not available. It implements the same logic locally.
    """

    def __init__(self):
        import re
        self.re = re

    def extract_answer(self, text: str) -> Optional[str]:
        """Extract numerical answer from text."""
        if not text:
            return None

        patterns = [
            r"####\s*([-+]?\d*\.?\d+)",
            r"[Tt]he\s+(?:final\s+)?answer\s+is[:\s]*([-+]?\d*\.?\d+)",
            r"=\s*([-+]?\d*\.?\d+)\s*$",
            r"([-+]?\d*\.?\d+)\s*$",
        ]

        for pattern in patterns:
            match = self.re.search(pattern, text.strip())
            if match:
                return match.group(1).strip()
        return None

    def __call__(
        self,
        data_batch: Dict[str, Any],
        tokenizer: Any = None,
    ) -> torch.Tensor:
        """Compute rewards locally."""
        responses = data_batch.get("responses", [])
        ground_truths = data_batch.get("ground_truths", [])

        rewards = []
        for response, gt in zip(responses, ground_truths):
            extracted = self.extract_answer(response)
            expected = self.extract_answer(gt) or gt.strip()

            if extracted and extracted == expected:
                rewards.append(1.0)
            else:
                try:
                    if extracted and float(extracted) == float(expected):
                        rewards.append(1.0)
                    else:
                        rewards.append(0.0)
                except (ValueError, TypeError):
                    rewards.append(0.0)

        return torch.tensor(rewards, dtype=torch.float32)


# Factory function for VeRL
def create_reward_function(config: Optional[Dict] = None) -> ExternalRewardFunction:
    """Create a reward function instance.

    This function is called by VeRL's RewardManager.

    Args:
        config: Optional configuration dictionary

    Returns:
        ExternalRewardFunction instance
    """
    config = config or {}
    return ExternalRewardFunction(
        reward_server_url=config.get("reward_server_url"),
        timeout=config.get("timeout", 30.0),
        batch_size=config.get("batch_size", 32),
    )
