"""Trainer component of the disaggregated RL Job Group.

This is the driver of a small but *real* RL loop -- GRPO (Group Relative Policy
Optimization, the algorithm behind DeepSeek-R1 and modern RL-for-LLM stacks).
Unlike data-parallel training where the same program runs on every node, here
each Job Group task is a *different* component:

  - trainer        (this file): owns the prompt distribution, drives the loop,
                    holds the trainable policy + optimizer, and pushes updated
                    weights to the actor over NCCL.
  - actor          (rollout_server.py): GPU inference server that generates
                    completions and receives weight updates over NCCL.
  - reward         (reward_server.py): CPU verifier that scores completions.

Two communication planes, each on the channel that fits it:
  - Data plane (prompts -> completions -> rewards): plain HTTP over Job Group
    service discovery (`actor-0.${SKYPILOT_JOBGROUP_NAME}`, `reward-0...`).
  - Weight sync (trainer -> actor): a torch.distributed (NCCL) broadcast. The
    trainer is rank 0 / rendezvous master, the actor is rank 1. This collective
    is what `network_tier: best` accelerates.

Each step: sample prompts -> ask the actor to generate a group of completions
per prompt -> ask the reward server to score them -> standardize rewards within
each group (GRPO advantages) -> policy-gradient update on the local policy ->
broadcast the new weights to the actor so its next rollouts are on-policy.

Env knobs (all optional): MODEL, STEPS, GROUP_SIZE, PROMPTS_PER_STEP,
MAX_NEW_TOKENS, MAX_INT, LR.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

import torch
import torch.distributed as dist
import torch.nn.functional as F
from transformers import AutoModelForCausalLM
from transformers import AutoTokenizer

MODEL = os.environ.get("MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
STEPS = int(os.environ.get("STEPS", "40"))
GROUP_SIZE = int(os.environ.get("GROUP_SIZE", "8"))
PROMPTS_PER_STEP = int(os.environ.get("PROMPTS_PER_STEP", "4"))
MAX_NEW_TOKENS = int(os.environ.get("MAX_NEW_TOKENS", "32"))
MAX_INT = int(os.environ.get("MAX_INT", "9"))
LR = float(os.environ.get("LR", "1e-5"))

JOBGROUP = os.environ["SKYPILOT_JOBGROUP_NAME"]
ACTOR = f"http://actor-0.{JOBGROUP}:{os.environ.get('ACTOR_PORT', '8000')}"
REWARD = f"http://reward-0.{JOBGROUP}:{os.environ.get('REWARD_PORT', '8001')}"
MASTER_PORT = os.environ.get("NCCL_RENDEZVOUS_PORT", "29500")


def post_json(url, payload, retries=120, backoff=2.0, timeout=600):
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last = exc
            time.sleep(backoff)  # service may still be starting up
    raise RuntimeError(f"POST {url} failed after {retries} attempts: {last}")


def build_prompt(tokenizer, a, b):
    messages = [{
        "role": "user",
        "content": (f"What is {a} + {b}? Think briefly, then give the final "
                    f"integer answer on its own line after '####'."),
    }]
    return tokenizer.apply_chat_template(messages,
                                         tokenize=False,
                                         add_generation_prompt=True)


def sync_weights_to_actor(model):
    """Broadcast every parameter to the actor (rank 1) over NCCL.

    The actor's /sync handler enters a matching receive loop. We fire the HTTP
    trigger from a background thread and broadcast from this thread so the two
    ranks rendezvous instead of deadlocking on each other.
    """
    kicker = threading.Thread(target=post_json, args=(f"{ACTOR}/sync", {}))
    kicker.start()
    for param in model.parameters():
        dist.broadcast(param.data, src=0)
    torch.cuda.synchronize()
    kicker.join()


def main():
    torch.manual_seed(0)
    torch.cuda.set_device(0)
    device = torch.device("cuda", 0)

    # Rank 0 / rendezvous master of the trainer<->actor NCCL group.
    dist.init_process_group(
        backend="nccl",
        init_method=f"tcp://0.0.0.0:{MASTER_PORT}",
        world_size=2,
        rank=0,
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    pad_id = tokenizer.pad_token_id
    model = AutoModelForCausalLM.from_pretrained(MODEL, torch_dtype=torch.bfloat16)
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR)

    post_json(f"{REWARD}/ping", {})
    print(f"[trainer] components up; model={MODEL} group={GROUP_SIZE} "
          f"prompts/step={PROMPTS_PER_STEP} steps={STEPS}", flush=True)

    for step in range(STEPS):
        groups = []  # (sequences[list[list[int]]], prompt_len, advantages)
        step_reward = 0.0
        num_completions = 0

        for _ in range(PROMPTS_PER_STEP):
            a = int(torch.randint(0, MAX_INT + 1, (1,)).item())
            b = int(torch.randint(0, MAX_INT + 1, (1,)).item())
            prompt = build_prompt(tokenizer, a, b)

            # Rollout: actor generates the completion group (different component).
            gen = post_json(f"{ACTOR}/generate", {
                "prompt": prompt, "n": GROUP_SIZE,
                "max_new_tokens": MAX_NEW_TOKENS, "temperature": 1.0})
            prompt_ids = gen["prompt_ids"]
            comps = gen["completions"]

            # Reward: verifier scores the completions (different component).
            scored = post_json(f"{REWARD}/score", {
                "items": [{"text": c["text"], "a": a, "b": b} for c in comps]})
            rewards = torch.tensor(scored["rewards"], device=device,
                                   dtype=torch.float32)
            step_reward += float(rewards.sum().item())
            num_completions += rewards.numel()

            advantages = (rewards - rewards.mean()) / (rewards.std() + 1e-4)
            seqs = [prompt_ids + c["ids"] for c in comps]
            groups.append((seqs, len(prompt_ids), advantages))

        # Policy update: recompute completion log-probs on the local policy
        # (on-policy: the actor generated with the same weights), one forward +
        # one backward.
        max_len = max(len(s) for seqs, _, _ in groups for s in seqs)
        rows, adv_rows, prompt_lens = [], [], []
        for seqs, prompt_len, advantages in groups:
            for s in seqs:
                rows.append(s + [pad_id] * (max_len - len(s)))
            adv_rows.append(advantages)
            prompt_lens.extend([prompt_len] * len(seqs))
        batch = torch.tensor(rows, device=device)             # [N, max_len]
        adv = torch.cat(adv_rows, dim=0)                      # [N]
        plens = torch.tensor(prompt_lens, device=device)      # [N]

        attn = (batch != pad_id).long()
        logits = model(input_ids=batch, attention_mask=attn).logits[:, :-1, :]
        labels = batch[:, 1:]
        logp = F.log_softmax(logits.float(), dim=-1).gather(
            -1, labels.unsqueeze(-1)).squeeze(-1)             # [N, max_len-1]

        pos = torch.arange(batch.shape[1] - 1, device=device).unsqueeze(0)
        comp_mask = (pos >= (plens.unsqueeze(1) - 1)).float()
        comp_mask = comp_mask * (labels != pad_id).float()
        seq_logp = (logp * comp_mask).sum(dim=-1)             # [N]

        loss = -(adv * seq_logp).mean()
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()

        # Push fresh weights to the actor over NCCL so its next rollouts are
        # on-policy.
        sync_weights_to_actor(model)

        mean_reward = step_reward / num_completions
        print(f"[step {step + 1:>3}/{STEPS}] mean_reward={mean_reward:.3f} "
              f"loss={loss.item():+.4f}", flush=True)

    print("[done] training complete", flush=True)
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
