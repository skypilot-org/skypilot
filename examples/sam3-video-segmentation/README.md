# Scaling Video Segmentation with SAM3 and SkyPilot Pools

This example demonstrates how to use [SAM3 (Segment Anything 3)](https://huggingface.co/facebook/sam3) with SkyPilot's pools feature to process a [soccer video dataset](https://www.kaggle.com/datasets/shreyamainkar/football-soccer-videos-dataset) in parallel across multiple GPU workers.

<video src="https://i.imgur.com/xw1koxh.mp4" controls="controls"></video>

SAM3 is Meta's unified foundation model for promptable segmentation in images and videos. It can:
- Detect, segment, and track objects using text or visual prompts
- Handle open-vocabulary concepts specified by text phrases
- Process videos with state-of-the-art accuracy

## Prerequisites

1. [Kaggle API credentials](https://www.kaggle.com/docs/api) (`~/.kaggle/kaggle.json`)
2. S3 bucket for output storage

## Quick start: Single-node testing

For quick testing on a single node without pools, use `sam3-test-single.yaml` which combines setup and run in a single task:

```bash
sky launch -c sam3-test sam3-test-single.yaml \
  --env OUTPUT_BUCKET_NAME=my-bucket --secret HF_TOKEN
```

Note: Processing the entire dataset on a single node will be slow. Use pools (below) for production workloads.

## Scaling with pools

A **pool** is a collection of GPU instances that share an identical setup—dependencies, models, and datasets are installed once and reused across all jobs. Instead of provisioning new machines for each job (with cold-start delays for downloading models and datasets), pools keep workers warm and ready to execute immediately.

Why use pools for video segmentation?
- **Eliminate cold starts**: SAM3 model loading and dataset downloads happen once during pool creation, not per job
- **Parallel processing**: Submit dozens of jobs at once; SkyPilot automatically distributes them across available workers
- **Dynamic scaling**: Scale workers up or down with a single command based on your throughput needs
- **Efficient resource use**: Workers are reused across jobs, avoiding repeated setup overhead

For more details, see the [SkyPilot Pools documentation](https://docs.skypilot.co/en/latest/examples/pools.html).

![SkyPilot Pools with SAM3 Video Segmentation](https://i.imgur.com/dJEzdZp.png)

### Step 1: Create the pool

```bash
sky jobs pool apply -p sam3-pool sam3-pool.yaml --env OUTPUT_BUCKET_NAME=my-bucket
```

This spins up 3 GPU workers (`workers: 3`) with SAM3 and the dataset pre-loaded.

### Step 2: Check pool status

```bash
sky jobs pool status sam3-pool
```

Wait for all workers to show `READY` status.

### Step 3: Submit batch jobs

```bash
sky jobs launch --pool sam3-pool --num-jobs 10 --secret HF_TOKEN sam3-job.yaml
```

This submits 10 parallel jobs to process the entire dataset. Three will start immediately (one per worker), and the rest will queue up.

### Step 4: Monitor progress

View the dashboard:
```bash
sky dashboard
```

The dashboard shows pool workers and their status:

![SkyPilot Dashboard Pool Workers](https://i.imgur.com/lRW35zh.png)

Check job queue:
```bash
sky jobs queue
```

The jobs queue shows completed, running, and pending jobs:

![SkyPilot Dashboard Jobs Queue](https://i.imgur.com/HrWU0g6.png)

View logs:
```bash
sky jobs logs <job-id>
...
(sam3-segmentation-job, pid=3213) Model loaded!
(sam3-segmentation-job, pid=3213) Processing: 87
(sam3-segmentation-job, pid=3213)   50 frames (sampled at 1 fps from 25.0 fps)
(sam3-segmentation-job, pid=3213)   0%|          | 0/50 [00:00<?, ?it/s]kernels library is not installed. NMS post-processing, hole filling, and sprinkle removal will be skipped. Install it with `pip install kernels` for better mask quality.
100%|██████████| 50/50 [00:48<00:00,  1.03it/s]█▊| 49/50 [00:46<00:01,  1.09s/it]
...
```

### Step 5: Scale as needed

To process faster, scale up the pool:
```bash
sky jobs pool apply --pool sam3-pool --workers 10
sky jobs launch --pool sam3-pool --num-jobs 20 sam3-job.yaml
```

### Step 6: Cleanup

When done, tear down the pool:
```bash
sky jobs pool down sam3-pool
```

## How it works

### Pool configuration (`sam3-pool.yaml`)

The pool YAML defines the worker infrastructure:
- **Workers**: Number of GPU instances
- **Resources**: L40S GPU per worker
- **File mounts**: Kaggle credentials and S3 output bucket
- **Setup**: Runs once per worker to install dependencies and download the dataset

### Job configuration (`sam3-job.yaml`)

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
3. Uses text prompts ("soccer player", "ball") to detect and segment objects
4. Overlays colored masks on video frames
5. Saves segmented videos and metadata to S3

![Example Segmentation Output](https://i.imgur.com/9FHO8B3.png)
![Example Segmentation Output 2](https://i.imgur.com/5y5iSP1.png)
### Output

Results are synced to the S3 bucket specified via `OUTPUT_BUCKET_NAME`:
```
$ aws s3 ls s3://my-bucket/segmentation_results/ --recursive
2025-12-22 08:53:37          0 segmentation_results/
2025-12-22 08:54:22          0 segmentation_results/1/
2025-12-22 08:54:23        231 segmentation_results/1/1_metadata.json
2025-12-22 08:54:23    3041504 segmentation_results/1/1_segmented.mp4
2025-12-22 08:55:13          0 segmentation_results/10/
2025-12-22 08:55:13        234 segmentation_results/10/10_metadata.json
2025-12-22 08:55:13    4291581 segmentation_results/10/10_segmented.mp4
2025-12-22 08:56:12          0 segmentation_results/100/
2025-12-22 08:56:13        237 segmentation_results/100/100_metadata.json
2025-12-22 08:56:13    4232746 segmentation_results/100/100_segmented.mp4
...
```

Each metadata JSON contains:
- Number of frames processed
- Objects detected (players, balls)
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

By default, all sampled frames are processed. To limit this (useful for long videos or to avoid OOM), use the `--max-frames` argument:
```bash
# Process up to 200 frames per video
python process_segmentation.py video.mp4 --max-frames 200
```

### Change text prompts

Edit the `PROMPTS` list in `process_segmentation.py`:
```python
PROMPTS = ["person", "ball", "goal", "referee"]
```

### Use different GPU

Update `sam3-pool.yaml` and `sam3-job.yaml` to use a different accelerator:
```yaml
resources:
  accelerators: H100:1
```

## References

- [SkyPilot Pools Documentation](https://docs.skypilot.co/en/latest/examples/managed-jobs.html#using-pools-experimental)
- [SAM3 on Hugging Face](https://huggingface.co/facebook/sam3)
- [SAM3 Video Segmentation Demo](https://huggingface.co/spaces/merve/SAM3-video-segmentation)
- [Soccer Videos Dataset](https://www.kaggle.com/datasets/shreyamainkar/football-soccer-videos-dataset)
