# Scaling Document OCR with DeepSeek and SkyPilot Pools

This example demonstrates how to use [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR) with SkyPilot's pools feature to process large volumes of scanned documents in parallel.

## Use case

Enterprise AI systems like RAG-based tools often struggle with scanned documents and images because traditional OCR loses document structure. DeepSeek OCR uses vision-language models to:
- Preserve tables and multi-column layouts
- Output clean markdown
- Handle mixed content without losing structure
- Perform context-aware text recognition

This example shows how to scale DeepSeek OCR processing across multiple GPU workers using SkyPilot pools.

## Prerequisites

1. [Kaggle API credentials](https://www.kaggle.com/docs/api) (`~/.kaggle/kaggle.json`)
2. S3 bucket for output storage

## Running the example

### Step 1: Create the pool

```bash
sky jobs pool apply -p deepseek-ocr-pool pool.yaml
```

This spins up 4 GPU workers (`workers: 4`) with DeepSeek OCR and the dataset pre-loaded.

### Step 2: Check pool status

```bash
sky jobs pool status deepseek-ocr-pool
```

Wait for all workers to show `READY` status.

### Step 3: Submit batch jobs

```bash
sky jobs launch --pool deepseek-ocr-pool --num-jobs 10 job.yaml
```

This submits 10 parallel jobs to process the entire dataset. Four will start immediately (one per worker), and the rest will queue up.

### Step 4: Monitor progress

View the dashboard:
```bash
sky dashboard
```

Check job queue:
```bash
sky jobs queue
```

View logs:
```bash
sky jobs logs <job-id>
```

### Step 5: Scale as needed

To process faster, scale up the pool:
```bash
sky jobs pool apply --pool deepseek-ocr-pool --workers 10
sky jobs launch --pool deepseek-ocr-pool --num-jobs 20 job.yaml
```

### Step 6: Cleanup

When done, tear down the pool:
```bash
sky jobs pool down deepseek-ocr-pool
```

## Single-node processing for testing

For quick testing on a single node without pools, create a `test-single.yaml` YAML that combines pool setup with a simple run command:

```yaml
# test-single.yaml
resources:
  accelerators: L40S:1

file_mounts:
  ~/.kaggle/kaggle.json: ~/.kaggle/kaggle.json
  /outputs:
    source: s3://my-skypilot-bucket

workdir: .

setup: |
  # Same setup as pool.yaml
  sudo apt-get update && sudo apt-get install -y unzip
  uv venv .venv --python 3.12
  source .venv/bin/activate
  git clone https://github.com/deepseek-ai/DeepSeek-OCR.git
  cd DeepSeek-OCR
  pip install kaggle
  uv pip install torch==2.6.0 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
  uv pip install vllm==0.8.5
  uv pip install flash-attn==2.7.3 --no-build-isolation
  uv pip install -r requirements.txt
  cd ..
  kaggle datasets download goapgo/book-scan-ocr-vlm-finetuning
  unzip -q book-scan-ocr-vlm-finetuning.zip -d book-scan-ocr
  echo "Setup complete!"

run: |
  source .venv/bin/activate
  # Process all images on a single node
  python process_ocr.py --start-idx 0 --end-idx -1
```

Then launch with:
```bash
sky launch -c deepseek-ocr-test test-single.yaml
```

Note: Processing the entire dataset on a single node will be slow. Use pools for production workloads.

## How it works

### Pool configuration (`pool.yaml`)

The pool YAML defines the worker infrastructure:
- **Workers**: Number of GPU instances (default: 4)
- **Resources**: L40S GPU per worker
- **File mounts**: Kaggle credentials and S3 output bucket
- **Setup**: Runs once per worker to install dependencies and download the dataset

### Job configuration (`job.yaml`)

The job YAML defines the workload:
- **Resources**: Must match pool resources (L40S GPU)
- **Run**: Processes assigned chunk of images on each job

### Work distribution

SkyPilot automatically distributes work using environment variables:
- `$SKYPILOT_JOB_RANK`: Current job index (0, 1, 2, ...)
- `$SKYPILOT_NUM_JOBS`: Total number of jobs

The bash script in the `run` section calculates which images each job should process based on these variables.

### Output

Results are synced to the S3 bucket specified in `file_mounts`:
```
s3://my-skypilot-bucket/ocr_results/
├── image_001.md
├── image_001_ocr.json
├── image_002.md
├── image_002_ocr.json
└── ...
```

## References

- [SkyPilot Pools Documentation](https://docs.skypilot.co/en/latest/examples/managed-jobs.html#using-pools-experimental)
- [DeepSeek OCR GitHub](https://github.com/deepseek-ai/DeepSeek-OCR)
- [Book-Scan-OCR Dataset](https://www.kaggle.com/datasets/goapgo/book-scan-ocr-vlm-finetuning)
