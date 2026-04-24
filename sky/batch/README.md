# Sky Batch: Distributed Batch Processing

Sky Batch enables scalable batch processing across cloud GPU/CPU clusters. Process large datasets efficiently by distributing workloads across a pool of workers managed by SkyPilot.

## How It Works

1. **Dataset** - Define your input data with a typed `InputReader`
2. **Pool** - Define cluster resources and setup via a YAML file
3. **Map** - Define processing logic and distribute across workers

---

## Step 1: Create a Dataset

Create a dataset by wrapping an `InputReader`. Built-in readers include `JsonReader` for JSONL files in cloud storage:

```python
import sky

ds = sky.batch.Dataset(sky.batch.JsonReader("s3://my-bucket/prompts.jsonl"))
```

The JSONL file should have one JSON object per line, all following the same schema:

```jsonl
{"prompt": "Summarize the theory of relativity"}
{"prompt": "Write a haiku about mountains"}
```

Supported cloud storage backends: S3 (`s3://`) and GCS (`gs://`).

### Custom Input Readers

You can define custom input formats by subclassing `InputReader` and registering with the format registry:

```python
from dataclasses import dataclass
from sky.batch.io_formats import InputReader
from sky.utils.registry import INPUT_READER_REGISTRY

@INPUT_READER_REGISTRY.type_register(name='range')
@dataclass
class RangeReader(InputReader):
    count: int

    def __len__(self) -> int:
        return self.count

    def download_batch(self, start_idx, end_idx, cache_dir):
        return [{'index': i} for i in range(start_idx, end_idx + 1)]
```

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
sky.jobs.pool_apply("pool.yaml", pool_name="my_pool")
```

---

## Step 3: Define Mapper and Process Dataset

Define a mapper function decorated with `@sky.batch.remote_function`, then call `ds.map()` to distribute processing across the pool.

```python
@sky.batch.remote_function
def llm_inference():
    import vllm

    # Initialize model (runs once per worker)
    llm = vllm.LLM(model="Qwen/Qwen3-4B-Instruct-2507")

    # Process batches continuously
    for batch in sky.batch.load():
        # batch is a list of dicts from your input
        # e.g., [{"prompt": "hello"}, {"prompt": "world"}]
        outputs = llm.generate([b["prompt"] for b in batch])

        # Save results (order must match input batch)
        sky.batch.save_results([{"output": o.outputs[0].text} for o in outputs])
```

### Understanding `save_results` and Columns

Think of `sky.batch.save_results()` as writing rows to a table. Each call writes one row per item in the list, and the **dict keys are the column names**.

For example, if your mapper calls:

```python
sky.batch.save_results([
    {"prompt": "hello", "output": "Hello! How can I help?", "score": 0.95},
    {"prompt": "world", "output": "The world is vast.",     "score": 0.87},
])
```

you are writing two rows to a table like this:

| prompt | output | score |
|--------|--------|-------|
| hello | Hello! How can I help? | 0.95 |
| world | The world is vast. | 0.87 |

The `column` parameter on an output writer selects which columns to keep in the final output — like a SQL `SELECT`. If you omit `column`, all columns are written:

```python
# Writes all columns: prompt, output, score
sky.batch.JsonWriter("s3://bucket/full.jsonl")

# Writes only the "output" column
sky.batch.JsonWriter("s3://bucket/outputs.jsonl", column="output")

# Writes "prompt" and "score" columns
sky.batch.JsonWriter("s3://bucket/meta.jsonl", column=["prompt", "score"])

# Extracts the PIL Image from the "image" column and saves as PNG files
sky.batch.ImageWriter("s3://bucket/images/", column="image")
```

This means you can call `save_results` with a rich dict containing everything, then use multiple output writers each selecting different columns from the same results:

```python
# save_results writes: {"prompt": ..., "image": ..., "score": ...}
# Each writer picks the columns it needs:
ds.map(
    my_mapper,
    pool_name=pool_name,
    batch_size=32,
    output=[
        sky.batch.ImageWriter("s3://bucket/images/", column="image"),
        sky.batch.JsonWriter("s3://bucket/meta.jsonl", column=["prompt", "score"]),
    ],
)
```

### Mapper Function Rules

The mapper function runs on remote workers. Key constraints enforced by `@remote_function`:

- **No closures**: Cannot capture variables from enclosing scope
- **No global references**: Cannot reference module-level variables
- **All imports inside**: All dependencies must be imported inside the function
- **Use `sky.batch.load()`**: Generator that yields batches; blocks when no data is available
- **Use `sky.batch.save_results()`**: Must be called once per batch, with results in the same order as input

### Output Formats

| Format | Description |
|--------|-------------|
| `JsonWriter(path, column=None)` | JSONL output. Optional `column` filters which keys to include. |
| `ImageWriter(path, column='image')` | Saves PIL Images as individual PNGs to a directory. |

**Multi-output** is supported by passing a list:

```python
ds.map(
    mapper_fn,
    pool_name=pool_name,
    batch_size=32,
    output=[
        sky.batch.ImageWriter("s3://bucket/images/", column='image'),
        sky.batch.JsonWriter("s3://bucket/manifest.jsonl", column=['prompt']),
    ],
)
```

### `ds.map()` Parameters

| Parameter | Description |
|-----------|-------------|
| `mapper_fn` | Function decorated with `@sky.batch.remote_function` |
| `pool_name` | Name of the worker pool |
| `batch_size` | Number of items per batch |
| `output` | `OutputWriter` or list of `OutputWriter` instances |
| `activate_env` | (Optional) Environment activation command (e.g., `conda activate myenv`) |
| `stream` | (Optional) Whether to stream the managed job progress. Default is True. |

If `stream` is True, `ds.map()` blocks until all batches are processed, displaying a tqdm progress bar.

---

## Features

### Progress Tracking

- `ds.map()` displays a tqdm progress bar with batch-level granularity
- Progress is also visible in `sky jobs queue` via `batch_completed_batches / batch_total_batches`

### HA Recovery

- Batch states are persisted to a database (PENDING, DISPATCHED, COMPLETED, FAILED)
- If the controller crashes, dispatched batches are reset to PENDING and retried
- Retry counts survive across restarts so batches cannot retry indefinitely

### Dynamic Worker Scaling

- The coordinator periodically re-discovers workers in the pool
- Newly scaled-up replicas are picked up automatically
- Individual worker failures are tolerated; other workers process remaining batches

### Cancellation

- `sky jobs cancel` gracefully stops the coordinator and shuts down worker services (the HTTP process on each worker)
- The pool clusters themselves remain running — they are a shared resource managed separately via `sky jobs pool` and can be reused for other jobs.

---

## Examples

| Example | Description | Location |
|---------|-------------|----------|
| Simple | Text doubling with CPU workers | `examples/batch/simple/` |
| Diffusion | Image generation with GPU workers, multi-output | `examples/batch/diffusion/` |
| Custom Formats | Custom InputReader/OutputWriter classes | `examples/batch/custom_formats/` |

Run an example:

```bash
bash examples/batch/simple/run.sh
```

