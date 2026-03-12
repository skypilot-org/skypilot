# Migrating from Ray Data to Sky Batch

This guide maps Ray Data's I/O APIs to Sky Batch equivalents for users familiar with Ray Data.

## Why Sky Batch over Ray Data?

Sky Batch offers similar ease of use with additional benefits:

| Feature | Sky Batch | Ray Data |
|---------|-----------|----------|
| Cluster management | Automatic provisioning | Manual Ray cluster setup |
| Multi-cloud / multi-cluster | Scale across AWS, GCP, Azure, and multiple k8s clusters | Single Ray cluster |
| Spot instance | Multi-region, improved availability & reduced cost | Single-region, limited capacity |
| Cost optimization | Automatic cloud selection | Fixed to one cloud/cluster |

## Reading Data

### Ray Data

```python
import ray

# Structured formats
ds = ray.data.read_json("s3://bucket/data.jsonl")
ds = ray.data.read_csv("s3://bucket/data.csv")
ds = ray.data.read_parquet("s3://bucket/data.parquet")

# Unstructured formats
ds = ray.data.read_images("s3://bucket/images/")
ds = ray.data.read_text("s3://bucket/docs/")
ds = ray.data.read_binary_files("s3://bucket/files/")

# In-memory
ds = ray.data.from_items([{"prompt": "hello"}, {"prompt": "world"}])
ds = ray.data.from_pandas(df)
```

### Sky Batch

```python
import sky

# JSONL input
ds = sky.dataset(sky.batch.JsonInput("s3://bucket/data.jsonl"))
```

**API naming convention:** Sky Batch format classes map directly to Ray Data function names:

| Ray Data | Sky Batch | Status |
|----------|-----------|--------|
| `ray.data.read_json()` | `sky.batch.JsonInput()` | Available |
| `ray.data.read_csv()` | `sky.batch.CsvInput()` | Planned |
| `ray.data.read_parquet()` | `sky.batch.ParquetInput()` | Planned |
| `ray.data.read_images()` | `sky.batch.ImageInput()` | Planned |

**Key differences:**

- Ray Data returns a lazy `Dataset` that can be chained with transforms. Sky Batch returns a `Dataset` that you call `.map()` on with a single mapper function.
- Ray Data supports in-memory sources (`from_items`, `from_pandas`). Sky Batch reads from cloud storage only.
- Ray Data supports local files. Sky Batch requires cloud paths (`s3://`, `gs://`, `r2://`).

## Writing Data

### Ray Data

```python
# Write after transforms
ds.write_json("s3://bucket/output/")
ds.write_csv("s3://bucket/output/")
ds.write_parquet("s3://bucket/output/")
ds.write_images("s3://bucket/images/", column="image")
```

### Sky Batch

```python
# Output format is passed to ds.map()
ds.map(
    mapper_fn,
    pool_name="my-pool",
    batch_size=32,
    output=sky.batch.JsonOutput("s3://bucket/output.jsonl"),
)

# Image output
ds.map(
    mapper_fn,
    pool_name="my-pool",
    batch_size=4,
    output=sky.batch.ImageOutput("s3://bucket/images/", column="image"),
)

# Multi-output: images + metadata manifest
ds.map(
    mapper_fn,
    pool_name="my-pool",
    batch_size=4,
    output=[
        sky.batch.ImageOutput("s3://bucket/images/", column="image"),
        sky.batch.JsonOutput("s3://bucket/manifest.jsonl", column=["name", "prompt"]),
    ],
)

# JsonOutput with column filter (write only selected keys)
ds.map(
    mapper_fn,
    pool_name="my-pool",
    batch_size=32,
    output=sky.batch.JsonOutput("s3://bucket/output.jsonl", column=["name", "score"]),
)
```

| Ray Data | Sky Batch | Status |
|----------|-----------|--------|
| `ds.write_json()` | `sky.batch.JsonOutput()` | Available |
| `ds.write_json()` (selected cols) | `sky.batch.JsonOutput(column=...)` | Available |
| `ds.write_images(column=...)` | `sky.batch.ImageOutput(column=...)` | Available |
| Multiple write calls | `output=[ImageOutput(...), JsonOutput(...)]` | Available |
| `ds.write_csv()` | `sky.batch.CsvOutput()` | Planned |
| `ds.write_parquet()` | `sky.batch.ParquetOutput()` | Planned |

**Key differences:**

- Ray Data calls write as a separate step after transforms. Sky Batch specifies the output format upfront in `ds.map()`.
- Ray Data `write_json` writes a directory of partitioned JSON files. Sky Batch `JsonOutput` writes a single merged `.jsonl` file.
- Ray Data `write_images` requires a `column` parameter for the image column. Sky Batch `ImageOutput` has the same `column` parameter (defaults to `"image"`).
- Sky Batch supports multiple outputs in a single `ds.map()` call via a list of output formats. `JsonOutput(column=...)` filters which keys to include in the JSONL.

## Processing Data

### Ray Data

```python
# Class-based mapper (stateful — model loaded once in __init__)
class LLMPredictor:
    def __init__(self):
        self.llm = LLM(model="Qwen/Qwen3-4B-Instruct-2507")

    def __call__(self, batch):
        outputs = self.llm.generate(batch["prompt"])
        batch["output"] = [o.outputs[0].text for o in outputs]
        return batch

ds = ds.map_batches(LLMPredictor, concurrency=3, batch_size=32, num_gpus=1)
ds.write_json("s3://bucket/output/")
```

### Sky Batch

```python
# Function-based mapper (stateful — model loaded once before the loop)
@sky.batch.remote_function
def llm_inference():
    from vllm import LLM

    llm = LLM(model="Qwen/Qwen3-4B-Instruct-2507")

    for batch in sky.batch.load():
        outputs = llm.generate([item["prompt"] for item in batch])
        results = [{"output": o.outputs[0].text} for o in outputs]
        sky.batch.save_results(results)

ds.map(llm_inference, pool_name="my-pool", batch_size=32,
       output=sky.batch.JsonOutput("s3://bucket/output.jsonl"))
```

**Key differences:**

| | Ray Data | Sky Batch |
|-|----------|-----------|
| Mapper style | Class with `__call__` | Function with `for batch in sky.batch.load()` loop |
| Statefulness | `__init__` runs once per worker | Code before the loop runs once per worker |
| Batch format | Dict of columns (`batch["prompt"]` is a list) | List of dicts (`[{"prompt": "..."}, ...]`) |
| Saving results | Return the modified batch | Call `sky.batch.save_results(results)` |
| Concurrency | `concurrency=N` in `map_batches` | Set `pool.workers: N` in pool YAML |
| GPU allocation | `num_gpus=1` in `map_batches` | `resources.accelerators: L4:1` in pool YAML |

## End-to-End Comparison

### Ray Data

```python
import ray

ds = ray.data.read_json("s3://bucket/prompts.jsonl")

class Predictor:
    def __init__(self):
        self.llm = LLM(model="meta-llama/Llama-3.1-8B-Instruct")

    def __call__(self, batch):
        batch["output"] = [
            o.outputs[0].text
            for o in self.llm.generate(batch["prompt"])
        ]
        return batch

ds = ds.map_batches(Predictor, concurrency=3, batch_size=32, num_gpus=1)
ds.write_json("s3://bucket/output/")
```

### Sky Batch

```python
import sky

ds = sky.dataset(sky.batch.JsonInput("s3://bucket/prompts.jsonl"))

@sky.batch.remote_function
def predict():
    from vllm import LLM
    llm = LLM(model="meta-llama/Llama-3.1-8B-Instruct")

    for batch in sky.batch.load():
        results = [
            {"output": o.outputs[0].text}
            for o in llm.generate([item["prompt"] for item in batch])
        ]
        sky.batch.save_results(results)

ds.map(predict, pool_name="my-pool", batch_size=32,
       output=sky.batch.JsonOutput("s3://bucket/output.jsonl"))
```

Pool YAML (`pool.yaml`):

```yaml
pool:
  workers: 3

resources:
  accelerators: L4:1

setup: uv pip install vllm
```

```python
sky.jobs.pool_apply("pool.yaml")
```

## Image Generation Example

### Ray Data

```python
import ray

ds = ray.data.read_json("s3://bucket/prompts.jsonl")

class ImageGenerator:
    def __init__(self):
        from diffusers import StableDiffusionPipeline
        import torch
        self.pipe = StableDiffusionPipeline.from_pretrained(
            "stable-diffusion-v1-5/stable-diffusion-v1-5",
            torch_dtype=torch.float16,
        ).to("cuda")

    def __call__(self, batch):
        result = self.pipe(batch["prompt"])
        batch["image"] = result.images
        return batch

ds = ds.map_batches(ImageGenerator, concurrency=2, batch_size=4, num_gpus=1)
ds.write_images("s3://bucket/images/", column="image")
```

### Sky Batch

```python
import sky

ds = sky.dataset(sky.batch.JsonInput("s3://bucket/prompts.jsonl"))

@sky.batch.remote_function
def generate_images():
    from diffusers import StableDiffusionPipeline
    import torch

    pipe = StableDiffusionPipeline.from_pretrained(
        "stable-diffusion-v1-5/stable-diffusion-v1-5",
        torch_dtype=torch.float16,
    ).to("cuda")

    for batch in sky.batch.load():
        result = pipe([item["prompt"] for item in batch])
        results = [
            {"prompt": item["prompt"], "image": img}
            for item, img in zip(batch, result.images)
        ]
        sky.batch.save_results(results)

ds.map(generate_images, pool_name="diffusion-pool", batch_size=4,
       output=[
           sky.batch.ImageOutput("s3://bucket/images/", column="image"),
           sky.batch.JsonOutput("s3://bucket/manifest.jsonl", column=["prompt"]),
       ])
```
