# Verl: State-of-the-art RL Training for LLMs

```bash
sky launch -c verl-ppo llm/verl/verl-ppo.yaml --secret WANDB_API_KEY --num-nodes 1 -y
sky launch -c verl-ppo llm/verl/verl-ppo.yaml --secret WANDB_API_KEY --secret HF_TOKEN --num-nodes 1 -y

sky launch -c verl-grpo llm/verl/verl-grpo.yaml --secret WANDB_API_KEY --num-nodes 1 -y
sky launch -c verl-grpo llm/verl/verl-grpo.yaml --secret WANDB_API_KEY --secret HF_TOKEN --num-nodes 1 -y
```


