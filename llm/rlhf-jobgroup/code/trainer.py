#!/usr/bin/env python3
"""GRPO Trainer for RLHF math training.

This trainer orchestrates the RLHF pipeline by:
1. Fetching prompts from data-server
2. Generating responses via rollout-server (vLLM)
3. Computing rewards via reward-server
4. Updating the policy using GRPO (Group Relative Policy Optimization)

GRPO is a simplified variant of PPO that doesn't require a critic model,
making it popular for math/code tasks with verifiable rewards.

Usage:
    python trainer.py \
        --data-server localhost:8000 \
        --rollout-server localhost:8001 \
        --reward-server localhost:8002 \
        --num-epochs 3
"""

import argparse
import os
import time
from dataclasses import dataclass
from typing import List, Optional

import httpx
import torch
from torch.utils.data import DataLoader
from transformers import AutoModelForCausalLM, AutoTokenizer
from accelerate import Accelerator


@dataclass
class TrainingConfig:
    """Training configuration."""
    data_server: str
    rollout_server: str
    reward_server: str
    model_name: str = "Qwen/Qwen2.5-0.5B-Instruct"
    batch_size: int = 4
    num_epochs: int = 3
    learning_rate: float = 1e-6
    max_new_tokens: int = 512
    temperature: float = 0.7
    num_samples_per_prompt: int = 4  # For GRPO, generate multiple samples
    kl_coef: float = 0.01
    clip_range: float = 0.2


class RLHFTrainer:
    """GRPO trainer that coordinates with external services."""

    def __init__(self, config: TrainingConfig):
        self.config = config
        self.accelerator = Accelerator()

        # HTTP clients for services
        self.http_client = httpx.Client(timeout=120.0)

        # Load model and tokenizer
        if self.accelerator.is_main_process:
            print(f"Loading model: {config.model_name}")

        self.tokenizer = AutoTokenizer.from_pretrained(config.model_name)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.model = AutoModelForCausalLM.from_pretrained(
            config.model_name,
            torch_dtype=torch.bfloat16,
            device_map="auto"
        )

        # Optimizer
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=config.learning_rate
        )

        # Prepare with accelerator
        self.model, self.optimizer = self.accelerator.prepare(
            self.model, self.optimizer
        )

        # Statistics
        self.total_steps = 0
        self.total_rewards = 0.0

    def wait_for_services(self, max_retries: int = 30, retry_interval: int = 10):
        """Wait for all services to be available."""
        services = [
            ("data-server", f"http://{self.config.data_server}/health"),
            ("rollout-server", f"http://{self.config.rollout_server}/health"),
            ("reward-server", f"http://{self.config.reward_server}/health"),
        ]

        for name, url in services:
            if self.accelerator.is_main_process:
                print(f"Waiting for {name} at {url}...")

            for attempt in range(max_retries):
                try:
                    response = self.http_client.get(url)
                    if response.status_code == 200:
                        if self.accelerator.is_main_process:
                            print(f"  {name} is ready!")
                        break
                except Exception as e:
                    pass

                if attempt < max_retries - 1:
                    time.sleep(retry_interval)
            else:
                raise RuntimeError(f"Service {name} not available after {max_retries} retries")

    def fetch_prompts(self, batch_size: int) -> List[dict]:
        """Fetch a batch of prompts from data server."""
        url = f"http://{self.config.data_server}/prompts"
        response = self.http_client.get(url, params={"batch_size": batch_size})
        response.raise_for_status()
        data = response.json()
        return data["prompts"]

    def generate_responses(self, prompts: List[str]) -> List[str]:
        """Generate responses using the rollout server (vLLM)."""
        url = f"http://{self.config.rollout_server}/v1/completions"

        responses = []
        for prompt in prompts:
            payload = {
                "model": self.config.model_name,
                "prompt": prompt,
                "max_tokens": self.config.max_new_tokens,
                "temperature": self.config.temperature,
                "n": 1,
            }
            try:
                response = self.http_client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                text = data["choices"][0]["text"]
                responses.append(text)
            except Exception as e:
                print(f"Error generating response: {e}")
                responses.append("")

        return responses

    def compute_rewards(self, prompts: List[str], responses: List[str],
                       ground_truths: List[str]) -> List[float]:
        """Compute rewards using the reward server."""
        url = f"http://{self.config.reward_server}/batch_reward"

        items = [
            {"prompt": p, "response": r, "ground_truth": gt}
            for p, r, gt in zip(prompts, responses, ground_truths)
        ]

        response = self.http_client.post(url, json={"items": items})
        response.raise_for_status()
        data = response.json()

        return [r["reward"] for r in data["rewards"]]

    def compute_grpo_loss(self, prompts: List[str], responses: List[str],
                         rewards: List[float]) -> torch.Tensor:
        """Compute GRPO loss for policy update.

        GRPO uses group-relative advantages: for each prompt, we compare
        the reward of each response to the mean reward of all responses
        for that prompt.
        """
        # Tokenize prompts and responses together
        full_texts = [p + r for p, r in zip(prompts, responses)]
        encodings = self.tokenizer(
            full_texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=1024
        ).to(self.accelerator.device)

        # Get prompt lengths for masking
        prompt_encodings = self.tokenizer(
            prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512
        )
        prompt_lengths = prompt_encodings.attention_mask.sum(dim=1)

        # Forward pass
        outputs = self.model(**encodings, labels=encodings.input_ids)

        # Compute per-token log probabilities
        logits = outputs.logits[:, :-1, :]
        labels = encodings.input_ids[:, 1:]
        log_probs = torch.nn.functional.log_softmax(logits, dim=-1)
        token_log_probs = torch.gather(log_probs, 2, labels.unsqueeze(-1)).squeeze(-1)

        # Mask out prompt tokens (only count response tokens)
        response_mask = torch.zeros_like(token_log_probs)
        for i, plen in enumerate(prompt_lengths):
            response_mask[i, plen-1:] = 1.0
        response_mask = response_mask * encodings.attention_mask[:, 1:]

        # Sum log probs for each response
        response_log_probs = (token_log_probs * response_mask).sum(dim=1)

        # Convert rewards to tensor and compute advantages
        rewards_tensor = torch.tensor(rewards, device=self.accelerator.device, dtype=torch.float32)

        # GRPO: normalize rewards within batch (group-relative)
        mean_reward = rewards_tensor.mean()
        std_reward = rewards_tensor.std() + 1e-8
        advantages = (rewards_tensor - mean_reward) / std_reward

        # Policy gradient loss (negative because we maximize reward)
        loss = -(response_log_probs * advantages).mean()

        return loss

    def train_step(self) -> dict:
        """Execute one training step."""
        self.model.train()

        # 1. Fetch prompts
        prompt_data = self.fetch_prompts(self.config.batch_size)
        prompts = [p["prompt"] for p in prompt_data]
        ground_truths = [p["ground_truth"] for p in prompt_data]

        # 2. Generate responses
        responses = self.generate_responses(prompts)

        # 3. Compute rewards
        rewards = self.compute_rewards(prompts, responses, ground_truths)

        # 4. Compute loss and update
        loss = self.compute_grpo_loss(prompts, responses, rewards)

        self.optimizer.zero_grad()
        self.accelerator.backward(loss)
        self.optimizer.step()

        # Update statistics
        self.total_steps += 1
        mean_reward = sum(rewards) / len(rewards)
        self.total_rewards += mean_reward

        return {
            "loss": loss.item(),
            "mean_reward": mean_reward,
            "accuracy": sum(1 for r in rewards if r > 0) / len(rewards),
            "num_samples": len(prompts)
        }

    def train(self):
        """Run the full training loop."""
        if self.accelerator.is_main_process:
            print("=" * 60)
            print("GRPO Training for Math")
            print("=" * 60)
            print(f"Model: {self.config.model_name}")
            print(f"Batch size: {self.config.batch_size}")
            print(f"Epochs: {self.config.num_epochs}")
            print(f"Learning rate: {self.config.learning_rate}")
            print("=" * 60)

        # Wait for services
        self.wait_for_services()

        # Training loop
        steps_per_epoch = 100  # Configurable
        for epoch in range(self.config.num_epochs):
            epoch_rewards = []
            epoch_losses = []

            for step in range(steps_per_epoch):
                metrics = self.train_step()
                epoch_rewards.append(metrics["mean_reward"])
                epoch_losses.append(metrics["loss"])

                if self.accelerator.is_main_process and step % 10 == 0:
                    print(f"Epoch {epoch+1}/{self.config.num_epochs} | "
                          f"Step {step+1}/{steps_per_epoch} | "
                          f"Loss: {metrics['loss']:.4f} | "
                          f"Reward: {metrics['mean_reward']:.4f} | "
                          f"Accuracy: {metrics['accuracy']:.2%}")

            # Epoch summary
            if self.accelerator.is_main_process:
                mean_epoch_reward = sum(epoch_rewards) / len(epoch_rewards)
                mean_epoch_loss = sum(epoch_losses) / len(epoch_losses)
                print(f"\n=== Epoch {epoch+1} Complete ===")
                print(f"Mean Reward: {mean_epoch_reward:.4f}")
                print(f"Mean Loss: {mean_epoch_loss:.4f}\n")

        if self.accelerator.is_main_process:
            print("=" * 60)
            print("Training Complete!")
            print(f"Total steps: {self.total_steps}")
            print(f"Average reward: {self.total_rewards / self.total_steps:.4f}")
            print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="GRPO Trainer for RLHF")
    parser.add_argument("--data-server", type=str, required=True,
                       help="Data server address (host:port)")
    parser.add_argument("--rollout-server", type=str, required=True,
                       help="Rollout server address (host:port)")
    parser.add_argument("--reward-server", type=str, required=True,
                       help="Reward server address (host:port)")
    parser.add_argument("--model", type=str, default="Qwen/Qwen2.5-0.5B-Instruct",
                       help="Model name or path")
    parser.add_argument("--batch-size", type=int, default=4,
                       help="Training batch size")
    parser.add_argument("--num-epochs", type=int, default=3,
                       help="Number of training epochs")
    parser.add_argument("--learning-rate", type=float, default=1e-6,
                       help="Learning rate")
    args = parser.parse_args()

    config = TrainingConfig(
        data_server=args.data_server,
        rollout_server=args.rollout_server,
        reward_server=args.reward_server,
        model_name=args.model,
        batch_size=args.batch_size,
        num_epochs=args.num_epochs,
        learning_rate=args.learning_rate,
    )

    trainer = RLHFTrainer(config)
    trainer.train()


if __name__ == "__main__":
    main()
