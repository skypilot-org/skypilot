# Sky Batch: Distributed Batch Inference

Sky Batch enables scalable batch inference across cloud GPU clusters. Process millions of prompts efficiently by distributing workloads across a pool of workers.

This guide shows how to run batch inference using vLLM for LLM generation.

## How It Works

1. **Dataset** - Your input data in cloud storage (JSONL format)
2. **Pool** - Define cluster resources and setup
3. **Map** - Define inference logic and process batches

---

## Step 1: Prepare Your Dataset

Create a JSONL file where each line is a valid JSON object following the same schema:

```jsonl
{"prompt": "Summarize the theory of relativity"}
{"prompt": "Write a haiku about mountains"}
```

Upload to cloud storage:

```bash
aws s3 cp prompts.jsonl s3://my-bucket/prompts.jsonl
```

Reference the dataset in Python:

```python
import sky

ds = sky.dataset("s3://my-bucket/prompts.jsonl")
```

Supported storage backends:
- `s3://bucket/path/file.jsonl` - AWS S3

More storage backends coming soon.

**Supported formats:**

JSONL is the first supported format. Support for images and other data types (e.g., one file per image) is coming soon. Multi-file datasets for larger workloads will also be supported in the future.

---

## Step 2: Configure Your Worker Pool

Create a `pool.yaml` file to define cluster resources and setup:

```yaml
pool:
  workers: 3          # Number of parallel workers

resources:
  accelerators: L4:1  # GPU type and count per worker

setup: uv pip install vllm
```

| Field | Description | Examples |
|-------|-------------|----------|
| `pool.workers` | Number of parallel workers | `1`, `10`, `100` |
| `resources.accelerators` | GPU type and count per worker | `L4:1`, `A100:2`, `H100:8` |
| `setup` | Command to install dependencies | `pip install vllm`, `uv pip install vllm` |

Start the worker pool:

```python
import sky

pool_name = sky.jobs.pool_apply("pool.yaml")
```

---

## Step 3: Define Mapper and Process Dataset

This is the key to interacting with Sky Batch. Define a mapper function that contains your inference logic, then pass it to `map_batches()`.

The dataset will be split into batches based on `batch_size`. Each worker processes one batch at a time, and batches are distributed across all workers in the pool.

```python
import sky


# This decorator defines a remote function that runs on the worker pool.
# All dependencies must be imported inside the function.
# The pool's setup command must install all required packages.
@sky.batch.remote_function
def llm_inference():
    import vllm

    # Initialize model (runs once per worker)
    llm = vllm.LLM(model="Qwen/Qwen3-4B-Instruct-2507")

    # Process batches continuously
    for batch in sky.batch.load():
        # batch is a list of dicts from your JSONL
        # e.g., [{"prompt": "hello"}, {"prompt": "world"}]
        # length of batch is specified by batch_size below

        outputs = llm.generate([b["prompt"] for b in batch])

        # Save results (order must match input)
        # Be careful for concurrent write! Maybe write to small files and then merge them.
        sky.batch.save_results([{"output": o.outputs[0].text} for o in outputs])

# Process all batches
ds.map(
    llm_inference,  # Mapper function
    pool_name=pool_name,
    batch_size=32,  # Items per batch sent to each worker
    output_path="s3://my-bucket/output.jsonl",  # Order matches input
)
```

### Mapper Function

The mapper function defines your inference logic. Inside the function:
- Use `sky.batch.load()` to receive batches continuously. It will pause when there is no data to process and resume when new batches are available.
- Process each batch with your model
- Use `sky.batch.save_results()` to save results in the same order as the input batch

| Function | Description |
|----------|-------------|
| `sky.batch.load()` | Generator yielding batches. Each batch is a list of dicts from input JSONL. |
| `sky.batch.save_results(results)` | Save a list of results corresponding to the order of the current batch. |

### `ds.map()` Parameters

| Parameter | Description |
|-----------|-------------|
| `mapper_fn` | Function containing inference logic |
| `pool_name` | Name of the worker pool |
| `batch_size` | Number of items per batch |
| `output_path` | S3 path for output results (order matches input) |

---

## Comparison: Ray Data

For reference, here's how you would achieve similar LLM batch inference using Ray Data:

```python
import ray
from ray.data import read_json
from vllm import LLM

# Initialize Ray cluster
ray.init()

# Read dataset
ds = read_json("s3://my-bucket/prompts.jsonl")

# Define inference class
class LLMPredictor:
    def __init__(self):
        self.llm = LLM(model="Qwen/Qwen3-4B-Instruct-2507")

    def __call__(self, batch):
        outputs = self.llm.generate(batch["prompt"])
        batch["output"] = [o.outputs[0].text for o in outputs]
        return batch

# Process dataset
ds = ds.map_batches(
    LLMPredictor,
    concurrency=3,  # Number of workers
    batch_size=32,
    num_gpus=1,
)

# Write results
ds.write_json("s3://my-bucket/output")
```
