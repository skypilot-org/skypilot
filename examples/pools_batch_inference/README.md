# Batch Text Classification with vLLM and SkyPilot Pools

This example demonstrates how to use SkyPilot's **Pools** for efficient offline batch inference. We'll classify sentiment from movie reviews using gpt-oss-20b running on vLLM.

## What is a Pool?

A **pool** is a collection of pre-configured workers that can process multiple jobs without cold starts. Key benefits:

- **Fast job execution**: Workers are pre-provisioned with models loaded
- **No setup overhead**: Each job starts immediately without reinstalling dependencies or downloading models
- **Simple parallelism**: Submit multiple jobs with a single command using `--num-jobs`, limit concurrency to the number of workers in the pool (queueing is handled by SkyPilot)

## Using Pools for Batch Inference

This example:
1. Creates a pool of workers with environments pre-configured for batch inference
2. Submits 10 classification jobs that process the IMDB movie reviews dataset in parallel
3. Each job uses vLLM's Python SDK to classify reviews in batches
4. Results are saved to a cloud storage bucket

Files in this example:

- `pool.yaml`: Pool configuration with vLLM setup
- `classify.yaml`: Job definition that runs on pool workers
- `classify.py`: Python script that performs the classification using vLLM's Python SDK

### Step 1: Create the pool

Create a pool named `text-classify` with 2 workers:

```bash
# Set your unique bucket name for the results (must be globally unique)
export BUCKET_NAME=batch-inference-results-${USER}
# Create the pool
sky jobs pool apply --env BUCKET_NAME -p text-classify pool.yaml
```

This will:
- Launch 2 workers with H100 GPUs
- Install vLLM and dependencies on each worker
- Mount your cloud storage bucket at `/results`
- Download and cache gpt-oss-20b

Check pool status with:
```bash
sky jobs pool status text-classify
```

<details>
<summary>Example output</summary>

<pre>
Pools
NAME           VERSION  UPTIME  STATUS  WORKERS
text-classify  1        5m 39s  READY   2/2

Pool Workers
POOL_NAME      ID  VERSION  LAUNCHED    INFRA       RESOURCES                             STATUS  USED_BY
text-classify  1   1        6 mins ago  Kubernetes  1x(gpus=H200:1, cpus=4, mem=16, ...)  READY   -
text-classify  2   1        6 mins ago  Kubernetes  1x(gpus=H200:1, cpus=4, mem=16, ...)  READY   -
</pre>

</details>



### Step 2: Submit text classification jobs

Submit 10 parallel jobs to process the IMDB movie reviews dataset:

```bash
sky jobs launch -p text-classify --num-jobs 10 classify.yaml
```

This command:
- Submits 10 jobs to the pool
- Each job gets a unique `$SKYPILOT_JOB_RANK` (0-9)
- Each job processes a partition of the dataset based on the job rank
- Results are saved to your configured cloud storage bucket

**Note**: You can adjust the number of jobs with `--num-jobs N`. More jobs = more parallelism (up to the number of workers).

### Step 3: Monitor progress

View all jobs:
```bash
sky jobs queue
```
<details>
<summary>Example output</summary>

<pre>
Fetching managed job statuses...
Managed jobs
In progress tasks: 4 PENDING, 2 RUNNING
ID  TASK  NAME            REQUESTED   SUBMITTED   TOT. DURATION  JOB DURATION  #RECOVERIES  STATUS     POOL
10  -     batch-classify  1x[H200:1]  2 mins ago  2m 35s         -             0            PENDING    text-classify
9   -     batch-classify  1x[H200:1]  2 mins ago  2m 35s         -             0            PENDING    text-classify
8   -     batch-classify  1x[H200:1]  2 mins ago  2m 37s         -             0            PENDING    text-classify
7   -     batch-classify  1x[H200:1]  2 mins ago  2m 10s         49s           0            SUCCEEDED  text-classify
6   -     batch-classify  1x[H200:1]  2 mins ago  2m 37s         17s           0            RUNNING    text-classify (worker=1)
5   -     batch-classify  1x[H200:1]  2 mins ago  2m 42s         14s           0            RUNNING    text-classify (worker=2)
4   -     batch-classify  1x[H200:1]  2 mins ago  2m 17s         49s           0            SUCCEEDED  text-classify
3   -     batch-classify  1x[H200:1]  2 mins ago  2m 45s         -             0            PENDING    text-classify
2   -     batch-classify  1x[H200:1]  2 mins ago  1m 19s         1m 18s        0            SUCCEEDED  text-classify
1   -     batch-classify  1x[H200:1]  2 mins ago  1m 20s         1m 19s        0            SUCCEEDED  text-classify
</pre>

</details>



Stream logs from a specific job:
```bash
sky jobs logs <job-id>
```

Or use the SkyPilot dashboard to view the progress of the jobs:
```bash
sky dashboard
```


<p align="center">
  <img src="https://i.imgur.com/N4mQfbG.png" width="800" alt="Batch Inference Dashboard" />
</p>

### Step 4: View Results

Once jobs complete, results are in the cloud storage bucket. Each job creates two files:
- `results_rank_N.jsonl`: Detailed predictions for each review
- `summary_rank_N.json`: Accuracy and performance metrics

View results using cloud CLI (replace `BUCKET_NAME` with your bucket name):
```bash
BUCKET_NAME=batch-inference-results-${USER}  # Replace with your bucket name
# AWS
aws s3 ls s3://${BUCKET_NAME}/
aws s3 cp s3://${BUCKET_NAME}/summary_rank_0.json -

# GCP
gsutil ls gs://${BUCKET_NAME}/
gsutil cat gs://${BUCKET_NAME}/summary_rank_0.json
```

### Step 5: Clean Up

When done, terminate the pool to stop incurring costs:

```bash
sky jobs pool down text-classify
```

This will stop all workers in the pool and clean up all cloud resources. Results are preserved in the storage bucket.

### Appendix: Running Without Cloud Buckets

**Don't have access to cloud storage buckets?** You can use local worker storage instead.

1. In `pool.yaml`, comment out the entire `file_mounts` section
2. In `classify.yaml`, change the `--output-dir` from `/results` to `~/sky_workdir`

## Learn More

- [SkyPilot Managed Jobs Pool Documentation](https://docs.skypilot.co/en/latest/examples/managed-jobs.html#pool)

