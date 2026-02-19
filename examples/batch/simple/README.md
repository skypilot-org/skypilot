# Simple Batch Processing Example

This example demonstrates the basic usage of Sky Batch for distributed text processing.

## What it does

The example takes a JSONL file where each line has a "text" field, and outputs a JSONL file where each line has an "output" field containing the doubled text.

Input:
```json
{"text": "hello"}
{"text": "world"}
```

Output:
```json
{"output": "hellohello"}
{"output": "worldworld"}
```

## Prerequisites

1. AWS credentials configured (for S3 access)
2. SkyPilot installed with AWS support

## Steps

All commands are run from the **project root**.

### 1. Prepare your dataset

Create a simple JSONL test file:

```bash
echo '{"text": "hello"}' > /tmp/test.jsonl
echo '{"text": "world"}' >> /tmp/test.jsonl
echo '{"text": "sky"}' >> /tmp/test.jsonl
echo '{"text": "batch"}' >> /tmp/test.jsonl
```

Upload to S3:

```bash
aws s3 cp /tmp/test.jsonl s3://sky-batch-test-20260212/test.jsonl
```

### 2. Create the pool

```bash
sky jobs pool apply examples/batch/simple/pool.yaml --pool sky-pool-cca0 -y
```

Wait for the pool to be ready:

```bash
sky jobs pool status sky-pool-cca0
```

### 3. Update the script

Edit `double_text.py` and update the S3 paths:

```python
input_path = 's3://sky-batch-test-20260212/test.jsonl'
output_path = 's3://sky-batch-test-20260212/output.jsonl'
```

### 4. Run the example

```bash
python examples/batch/simple/double_text.py
```

This will:
1. Submit a batch job to the pool's batch controller via HTTP
2. The controller discovers ready worker replicas
3. The controller submits worker tasks to each replica via `sky.exec()`
4. Workers process batches and report completion back to the controller
5. Results are aggregated and written to the output path

### 5. Check the output

```bash
aws s3 cp s3://sky-batch-test-20260212/output.jsonl /tmp/output.jsonl
cat /tmp/output.jsonl
```

## Pool Configuration

The `pool.yaml` defines the worker pool:

```yaml
pool:
  workers: 2  # Number of parallel workers

resources:
  cloud: kubernetes
  cpus: 1
  ports: 8279  # Worker HTTP server port
```

You can modify:
- `workers`: Number of parallel workers
- `cpus`: CPU count per worker
- `ports`: Must include 8279 for worker communication

## Understanding the Code

### The mapper function

```python
@sky.batch.remote_function
def double_text():
    for batch in sky.batch.load():
        results = [{"output": item["text"] * 2} for item in batch]
        sky.batch.save_results(results)
```

Key points:
- `@sky.batch.remote_function` marks this for remote execution
- `sky.batch.load()` yields batches of data from the input
- `sky.batch.save_results()` saves results maintaining order

### The main function

```python
ds = sky.dataset(input_path)
ds.map(double_text, pool_name=pool_name, batch_size=5, output_path=output_path)
```

Key points:
- `sky.dataset()` creates a dataset from cloud storage
- `ds.map()` submits the job to the pool's batch controller and polls for completion
- The pool must be created beforehand with `sky jobs pool apply`

## Architecture

```
User Machine                     Controller Cluster              Worker Clusters
ds.map() --- HTTP -------------> BatchController (:8280)
  POST /submit_job                 |- sky.exec() ------------>  Worker 0 (:8279)
  GET  /job_status                 |- sky.exec() ------------>  Worker 1 (:8279)
                                   |- /register     <--------  (workers register)
                                   |- /batch_complete <------  (workers report)
```
