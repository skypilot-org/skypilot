# Scaling Document OCR with DeepSeek and SkyPilot Pools

This example demonstrates how to use [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR) with SkyPilot's pools feature to process large volumes of scanned documents in parallel.

## Use Case

Enterprise AI systems like RAG-based tools often struggle with scanned documents and images because traditional OCR loses document structure. DeepSeek OCR uses vision-language models to:
- Preserve tables and multi-column layouts
- Output clean markdown
- Handle mixed content without losing structure
- Perform context-aware text recognition

This example shows how to scale DeepSeek OCR processing across multiple GPU workers using SkyPilot pools.

## Prerequisites

1. [Kaggle API credentials](https://www.kaggle.com/docs/api) (`~/.kaggle/kaggle.json`)
2. S3 bucket for output storage

## Running the Example

### Step 1: Create the pool

```bash
sky jobs pool apply -p deepseek-ocr-pool deepseek_ocr.sky.yaml
```

This spins up 3 GPU workers (`workers: 3`)with DeepSeek OCR and the dataset pre-loaded.

### Step 2: Check pool status

```bash
sky jobs pool status deepseek-ocr-pool
```

Wait for all workers to show `READY` status.

### Step 3: Submit batch jobs

```bash
sky jobs launch --pool deepseek-ocr-pool --num-jobs 10 deepseek_ocr.sky.yaml
```

This submits 10 parallel jobs to process the entire dataset. Three will start immediately (one per worker), and the rest will queue up.

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
sky jobs launch --pool deepseek-ocr-pool --num-jobs 20 deepseek_ocr.sky.yaml
```

### Step 6: Cleanup

When done, tear down the pool:
```bash
sky jobs pool down deepseek-ocr-pool
```

## How It Works

### Pool Configuration

The pool YAML defines:
- **Workers**: Number of GPU instances (default: 3)
- **Resources**: L40S GPU per worker
- **File mounts**: Kaggle credentials and S3 output bucket
- **Setup**: Runs once per worker to install dependencies and download the dataset
- **Run**: Processes assigned chunk of images on each job

### Work Distribution

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
