# Search tooling for VERL

This folder contains SkyPilot YAMLs for training and inference with tool-augmented “search” workflows (Search-R1 style), using either:
- a **Google Search** backend, or
- a **Wikipedia retrieval service** (FAISS index).

See this [blog](https://blog.skypilot.co/verl-tool-calling/) for how the YAMLs are used for training a RL agent that can use Google search.

## Inference (Google Search backend)

```bash
sky launch -c verl-infer-google llm/verl/search-tooling/verl-search-interaction-google-search.yaml \
  --env MODEL_PATH=/checkpoints/hf_model \
  --env GOOGLE_API_KEY=your_key_here \
  --env GOOGLE_CSE_ID=your_cse_id_here \
  -y
```

## Inference (local Wikipedia retrieval on the same node)

```bash
sky launch -c verl-infer llm/verl/search-tooling/verl-search-interaction-infer.yaml \
  --env MODEL_PATH=/checkpoints/hf_model \
  -y
```

## Retrieval service (CPU-only, for reuse across jobs)

```bash
sky serve up -n retrieval llm/verl/search-tooling/verl-search-interaction-retrieval.yaml --cpus 32+ --memory 256+ -y
sky serve status retrieval --endpoint 8000
```

## Training

- Single-node training with retrieval running on the same node: `llm/verl/search-tooling/verl-search-interaction.yaml`
- Training that points to an external retrieval service: `llm/verl/search-tooling/verl-search-interaction-rl-trainer.yaml`
