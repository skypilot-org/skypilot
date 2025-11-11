# Batch Text Classification with SkyPilot Managed Jobs Pool

This example demonstrates how to use SkyPilot's **Managed Jobs Pool** feature for efficient offline batch inference. We'll classify sentiment from movie reviews using a pre-loaded LLM (Llama-3.1-8B-Instruct) running on vLLM.

## What is a Managed Jobs Pool?

A **pool** is a collection of pre-configured workers that can process multiple jobs without cold starts. Key benefits:

- **Fast job execution**: Workers are pre-provisioned with models loaded
- **No setup overhead**: Each job starts immediately without reinstalling dependencies or downloading models
- **Cost-effective**: Reuse expensive GPU resources across many small jobs
- **Simple parallelism**: Submit multiple jobs with a single command using `--num-jobs`

## Example Overview

This example:
1. Creates a pool of 3 workers with Llama-3.1-8B-Instruct pre-downloaded
2. Submits 10 classification jobs that process 5,000 IMDB movie reviews in parallel
3. Each job uses vLLM's Python SDK to classify ~500 reviews in batches
4. Results are saved to a cloud storage bucket with predictions, ground truth labels, and accuracy metrics

**Expected time**: ~10-15 minutes (including pool setup)  
**Expected cost**: ~$0.50-$1.00 (using spot L4 instances)

## Prerequisites

1. **Install SkyPilot nightly** (pools are in alpha):
   ```bash
   pip install -U skypilot-nightly[aws]  # or [gcp], [azure], etc.
   ```

2. **Configure cloud credentials**:
   ```bash
   sky check
   ```

3. **Verify GPU access**: Ensure you have quota for L4 GPUs (or modify `pool.yaml` to use T4/A10G)

## Quick Start

### Step 1: Create the Pool

Create a pool named `text-classify` with 3 workers:

```bash
sky jobs pool apply -p text-classify pool.yaml
```

This will:
- Launch 3 workers with L4 GPUs
- Install vLLM and dependencies on each worker
- Download and cache Llama-3.1-8B-Instruct (~16GB)

**Note**: Pool creation takes ~5-10 minutes. You can submit jobs immediately - they'll wait in PENDING state until workers are ready.

Check pool status:
```bash
sky jobs pool status text-classify
```

Expected output:
```
Pool: text-classify
Workers: 3/3 ready
Status: RUNNING
```

### Step 2: Submit Classification Jobs

Submit 10 parallel jobs to process 5,000 reviews:

```bash
sky jobs launch -p text-classify --num-jobs 10 classify.yaml
```

This command:
- Submits 10 jobs to the pool
- Each job gets a unique `$SKYPILOT_JOB_RANK` (0-9)
- Each job processes ~500 reviews (1/10th of the dataset)
- Results are saved to the `sky-batch-inference-results` bucket

**Note**: You can adjust the number of jobs with `--num-jobs N`. More jobs = more parallelism (up to the number of workers).

### Step 3: Monitor Progress

View all jobs:
```bash
sky jobs queue
```

Stream logs from a specific job:
```bash
sky jobs logs <job-id>
```

Or monitor all jobs at once:
```bash
watch -n 5 'sky jobs queue'
```

### Step 4: View Results

Once jobs complete, results are in the cloud storage bucket. Each job creates two files:
- `results_rank_N.jsonl`: Detailed predictions for each review
- `summary_rank_N.json`: Accuracy and performance metrics

View results using cloud CLI:
```bash
# AWS
aws s3 ls s3://sky-batch-inference-results/
aws s3 cp s3://sky-batch-inference-results/summary_rank_0.json -

# GCP
gsutil ls gs://sky-batch-inference-results/
gsutil cat gs://sky-batch-inference-results/summary_rank_0.json
```

Or access them directly from a SkyPilot cluster:
```bash
sky launch -c results-viewer --cloud aws -y -- \
  "aws s3 sync s3://sky-batch-inference-results/ ./results/ && \
   cat results/summary_rank_*.json | jq -s '{
     total_processed: map(.total_processed) | add,
     total_correct: map(.correct_predictions) | add,
     avg_accuracy: (map(.accuracy) | add / length)
   }'"
```

### Step 5: Clean Up

When done, terminate the pool to stop incurring costs:

```bash
sky jobs pool down text-classify
```

This will:
- Stop all workers in the pool
- Clean up all cloud resources
- Preserve results in the storage bucket

## File Overview

- **`pool.yaml`**: Pool configuration with vLLM setup
- **`classify.yaml`**: Job definition that runs on pool workers
- **`classify.py`**: Python script that performs the classification

## How It Works

### Pool Setup (`pool.yaml`)

The pool YAML defines:
1. **Resources**: L4 GPUs (cost-effective for inference)
2. **Setup commands**: Install vLLM and download the model to `/tmp/model`
3. **Worker count**: 3 workers (processes up to 3 jobs simultaneously)

The model is downloaded once during pool setup and cached, so all jobs can use it immediately.

### Job Execution (`classify.yaml`)

Each job:
1. Installs lightweight dependencies (datasets, tqdm)
2. Mounts the results bucket
3. Runs `classify.py` with job rank, total jobs, and model path

Environment variables automatically set by SkyPilot:
- `$SKYPILOT_JOB_RANK`: Job number (0, 1, 2, ...)
- `$SKYPILOT_NUM_JOBS`: Total number of jobs (e.g., 10)

### Classification Logic (`classify.py`)

The script:
1. Loads the IMDB dataset from HuggingFace
2. Calculates which partition to process based on job rank
3. Initializes vLLM with the pre-downloaded model using `LLM` class
4. Prepares all prompts and runs batch inference with `llm.generate()`
5. Compares predictions to ground truth labels
6. Saves results and accuracy metrics

The script uses vLLM's Python SDK directly for efficient batch processing, which is simpler and faster than using the API server.

## Performance and Cost

### With Pool (this example)
- **Setup time**: ~5-10 min (one-time, shared across all jobs)
- **Per-job time**: ~1-2 min (500 reviews)
- **Total time**: ~10-15 min (including setup)
- **Cost**: ~$0.50-$1.00 (3 workers × L4 spot @ ~$0.25/hr)

### Without Pool (traditional managed jobs)
- **Setup time**: ~5-10 min **per job**
- **Per-job time**: ~6-12 min (setup + inference)
- **Total time**: ~60-120 min for 10 jobs
- **Cost**: Similar, but much slower

**Key advantage**: Pools shine when running **many short jobs** that share the same environment.

## Customization

### Using a Different Model

Edit `pool.yaml` to use a different model:
```yaml
envs:
  MODEL_NAME: Qwen/Qwen2-7B-Instruct  # Or any HuggingFace model
```

### Processing More/Fewer Reviews

Edit the dataset size in `classify.yaml`:
```bash
python classify.py --dataset-size 10000 ...  # Process 10K reviews
```

### Using Different GPUs

Edit `resources` in `pool.yaml`:
```yaml
resources:
  accelerators: {T4: 1, L4: 1, A10G: 1}  # Flexible GPU selection
```

### Adjusting Pool Size

Edit `pool.workers` in `pool.yaml`:
```yaml
pool:
  workers: 5  # More workers = more parallelism
```

## Troubleshooting

### Pool workers not starting
- Check GPU quota: `sky check`
- Try different regions: Add `region: us-west-2` to resources
- Use on-demand instead of spot: Remove `use_spot: true`

### Jobs stuck in PENDING
- Check pool status: `sky jobs pool status text-classify`
- View controller logs: `sky jobs logs --controller <job-id>`

### vLLM initialization errors
- Verify model downloaded: `ls -lh /tmp/model/`
- Check job logs for detailed error: `sky jobs logs <job-id>`

### Out of memory errors
- Reduce max-model-len in `pool.yaml`: `--max-model-len 1024`
- Use a smaller model: `MODEL_NAME: Qwen/Qwen2-1.5B-Instruct`

## Learn More

- [SkyPilot Managed Jobs Pool Documentation](https://docs.skypilot.co/en/latest/examples/managed-jobs.html#pool)
- [vLLM Documentation](https://docs.vllm.ai/)
- [IMDB Dataset](https://huggingface.co/datasets/imdb)

## Feedback

Found an issue or have suggestions? Please [open an issue](https://github.com/skypilot-org/skypilot/issues/new) or join our [Slack community](http://slack.skypilot.co).

