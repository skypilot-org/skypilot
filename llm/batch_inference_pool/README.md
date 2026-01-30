# Batch Inference Pool

This is a pool of jobs that compute text vectors using vLLM.

## Usage

1. Start a pool of workers to compute text vectors:
```bash
sky jobs pool apply -p vllm-pool pool.yaml
```
2. Launch a job to compute text vectors:
```bash
./batch_compute_vectors.sh
```


