# Sky Batch Development Context

## Architecture Overview

Sky Batch distributes JSONL workloads across a pool of GPU/CPU workers via managed jobs.

```
ds.map()
  └─ sky.jobs.launch(task with inline run script)
       └─ Managed-job system launches a CPU cluster
            └─ BatchCoordinator.run()
                 ├─ Read SKYPILOT_MANAGED_JOB_ID from env
                 ├─ Count & split dataset into batches
                 ├─ Discover pool workers (SkyServe replicas)
                 ├─ Dispatch batches to workers via sky.exec()
                 ├─ Push progress to API server (POST /jobs/batch_progress)
                 ├─ Merge results
                 └─ Exit 0 (SUCCEEDED) or 1 (FAILED)
```

Client (`dataset.py`) polls `ManagedJobRecord.batch_total_batches` / `batch_completed_batches` from the jobs queue for progress display.

## Key Files

| File | Purpose |
|------|---------|
| `sky/batch/coordinator.py` | Runs on managed job cluster, dispatches batches to pool workers |
| `sky/batch/dataset.py` | Client-side: `Dataset.map()`, launches managed job, polls progress |
| `sky/batch/worker.py` | Long-running HTTP service on each pool worker, processes batches |
| `sky/batch/utils.py` | Cloud storage helpers, chunk file management, function serialization |
| `sky/batch/constants.py` | Ports, timeouts, chunk naming patterns |
| `sky/batch/formats/jsonl.py` | JSONL input/output format |
| `sky/batch/formats/image_dir.py` | Image directory output format (PNG + manifest.jsonl) |
| `sky/batch/remote.py` | `@remote_function` decorator |

## How managed_job_id Flows

1. `ds.map()` launches a managed job via `sky.jobs.launch()` -> gets `managed_job_id` back
2. The managed jobs controller sets `SKYPILOT_MANAGED_JOB_ID` env var on the task (defined in `sky/skylet/constants.py`, set in `sky/jobs/controller.py`)
3. Coordinator reads it from env at init time — no API call needed
4. Coordinator passes `str(managed_job_id)` as `SKY_BATCH_JOB_ID` env var to workers
5. Workers use it for chunk paths: `.sky_batch_tmp/{managed_job_id}/chunk_...`
6. Client uses same `str(managed_job_id)` for result merge paths

## Progress Reporting

- Coordinator pushes progress via `POST /jobs/batch_progress` (using `payloads.SetBatchProgressBody`)
- Client reads progress from `ManagedJobRecord` fields (`batch_total_batches`, `batch_completed_batches`) via `sky.jobs.queue_v2()`
- No S3/GCS progress file — everything goes through the API server and DB

## Coordinator -> API Server Communication

The coordinator runs on a remote K8s pod and needs to reach the user's API server:
- `dataset.py` computes the API endpoint via `_get_coordinator_api_endpoint()`
- For local API servers, `localhost` is replaced with `host.docker.internal`
- The URL is set via `SKYPILOT_API_SERVER_ENDPOINT` env var in the run script

## Testing

```bash
# Restart API server after code changes
sky api stop && sky api start

# Run the simple example (50 items, batch_size=2)
bash examples/batch/simple/run.sh

# Check jobs queue
sky jobs queue

# Check chunk paths
aws s3 ls s3://<bucket>/.sky_batch_tmp/
```

## Gotchas

- The coordinator runs under a different user context on the managed job cluster. Any `queue_v2` calls from the coordinator need `all_users=True`.
- `server_common.get_server_url()` is cached with `@lru_cache`. The `SKYPILOT_API_SERVER_ENDPOINT` env var must be set before Python starts.
- The run script is baked before `sky.jobs.launch()` returns, so `managed_job_id` cannot be hardcoded into it. That's why the env var approach (`SKYPILOT_MANAGED_JOB_ID`) exists.
