# Scaling Video Segmentation with SAM3 and SkyPilot Pools

This example demonstrates how to use [SAM3 (Segment Anything 3)](https://huggingface.co/facebook/sam3) with SkyPilot's pools feature to process large volumes of soccer videos in parallel.

## Use case

SAM3 is Meta's unified foundation model for promptable segmentation in images and videos. It can:
- Detect, segment, and track objects using text or visual prompts
- Handle open-vocabulary concepts specified by text phrases
- Process videos with state-of-the-art accuracy

This example shows how to scale SAM3 video segmentation across multiple GPU workers using SkyPilot pools to process a soccer video dataset.

## Prerequisites

1. [Kaggle API credentials](https://www.kaggle.com/docs/api) (`~/.kaggle/kaggle.json`)
2. S3 bucket for output storage

## Quick start: Single-node testing

For quick testing on a single node without pools, create a `test-single.yaml` YAML that combines setup with a simple run command:

```yaml
# test-single.yaml
resources:
  accelerators: L40S:1

file_mounts:
  ~/.kaggle/kaggle.json: ~/.kaggle/kaggle.json
  /outputs:
    source: s3://my-skypilot-bucket

secrets:
  HF_TOKEN: null 

workdir: .

setup: |
  # Same setup as pool.yaml
  sudo apt-get update && sudo apt-get install -y unzip ffmpeg
  uv venv .venv --python 3.12
  source .venv/bin/activate
  pip install kaggle
  uv pip install torch==2.6.0 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
  # Install transformers from specific commit with SAM3 support
  uv pip install git+https://github.com/huggingface/transformers.git@c3fb1b1a6ca1102f62b139c83a088a97e5a55477
  uv pip install accelerate opencv-python pillow numpy kernels
  # Download soccer video dataset from Kaggle (store in S3 to avoid re-downloading)
  DATASET_PATH=/outputs/datasets/soccer-videos
  if [ ! -d "$DATASET_PATH" ]; then
    echo "Downloading dataset from Kaggle to S3..."
    mkdir -p /outputs/datasets
    kaggle datasets download shreyamainkar/football-soccer-videos-dataset
    unzip -q football-soccer-videos-dataset.zip -d $DATASET_PATH
    rm -f football-soccer-videos-dataset.zip
  fi
  ln -sf $DATASET_PATH soccer-videos
  echo "Setup complete!"

run: |
  source .venv/bin/activate
  # Process all videos on a single node
  for video in soccer-videos/*.mp4; do
    echo "Processing: $video"
    python process_segmentation.py "$video" || echo "Failed: $video"
  done
  echo "All videos processed!"
```

Then launch with:
```bash
export HF_TOKEN=... # SAM3 is a gated model; set your Hugging Face token here
sky launch -c sam3-test test-single.yaml --secret HF_TOKEN
```

Note: Processing the entire dataset on a single node will be slow. Use pools (below) for production workloads.

## Scaling with pools

### Step 1: Create the pool

```bash
sky jobs pool apply -p sam3-pool pool.yaml
```

This spins up 3 GPU workers (`workers: 3`) with SAM3 and the dataset pre-loaded.

### Step 2: Check pool status

```bash
sky jobs pool status sam3-pool
```

Wait for all workers to show `READY` status.

### Step 3: Submit batch jobs

```bash
sky jobs launch --pool sam3-pool --num-jobs 10 job.yaml
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
sky jobs pool apply --pool sam3-pool --workers 10
sky jobs launch --pool sam3-pool --num-jobs 20 job.yaml
```

### Step 6: Cleanup

When done, tear down the pool:
```bash
sky jobs pool down sam3-pool
```

## How it works

### Pool configuration (`pool.yaml`)

The pool YAML defines the worker infrastructure:
- **Workers**: Number of GPU instances
- **Resources**: L40S GPU per worker
- **File mounts**: Kaggle credentials and S3 output bucket
- **Setup**: Runs once per worker to install dependencies and download the dataset

### Job configuration (`job.yaml`)

The job YAML defines the workload:
- **Resources**: Must match pool resources (L40S GPU)
- **Run**: Processes assigned chunk of videos on each job

### Work distribution

SkyPilot automatically distributes work using environment variables:
- `$SKYPILOT_JOB_RANK`: Current job index (0, 1, 2, ...)
- `$SKYPILOT_NUM_JOBS`: Total number of jobs

The bash script in the `run` section calculates which videos each job should process based on these variables.

### Segmentation process

The `process_segmentation.py` script:
1. Loads SAM3 model from Hugging Face
2. Processes each video frame-by-frame
3. Uses text prompts ("person", "ball") to detect and segment objects
4. Overlays colored masks on video frames
5. Saves segmented videos and metadata to S3

### Output

Results are synced to the S3 bucket specified in `file_mounts`:
```
s3://my-skypilot-bucket/segmentation_results/
├── video_001/
│   ├── video_001_segmented.mp4
│   └── video_001_metadata.json
├── video_002/
│   ├── video_002_segmented.mp4
│   └── video_002_metadata.json
└── ...
```

Each metadata JSON contains:
- Number of frames processed
- Objects detected (persons, balls)
- Output video path

## Customization

### Adjust sample rate

By default, the script samples 1 frame per second. To change this, use the `--sample-fps` argument:
```bash
# Sample 2 frames per second
python process_segmentation.py video.mp4 --sample-fps 2

# Process all frames (use 0 or negative value)
python process_segmentation.py video.mp4 --sample-fps 0
```

### Limit frames per video

By default, the script limits processing to 200 frames per video to avoid GPU out-of-memory errors on long videos. To change this, use the `--max-frames` argument:
```bash
# Process up to 300 frames per video
python process_segmentation.py video.mp4 --max-frames 300

# No limit (process all sampled frames)
python process_segmentation.py video.mp4 --max-frames 0
```

### Custom output directory

By default, outputs are saved to `/outputs/segmentation_results`. To change this:
```bash
python process_segmentation.py video.mp4 --output-dir ./my-results
```

### Change text prompts

Edit the `PROMPTS` list in `process_segmentation.py`:
```python
PROMPTS = ["person", "ball", "goal", "referee"]
```

### Use different GPU

Update `pool.yaml` and `job.yaml` to use a different accelerator:
```yaml
resources:
  accelerators: A100:1
```

## References

- [SkyPilot Pools Documentation](https://docs.skypilot.co/en/latest/examples/managed-jobs.html#using-pools-experimental)
- [SAM3 on Hugging Face](https://huggingface.co/facebook/sam3)
- [SAM3 Video Segmentation Demo](https://huggingface.co/spaces/merve/SAM3-video-segmentation)
- [Soccer Videos Dataset](https://www.kaggle.com/datasets/shreyamainkar/football-soccer-videos-dataset)
