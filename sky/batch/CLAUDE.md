# Sky Batch Development Context

## Architecture Overview

Sky Batch distributes JSONL workloads across a pool of GPU/CPU workers via managed jobs.

```
ds.map()
  └─ sky.jobs.launch(task with batch_coordinator metadata)
       └─ Jobs controller detects metadata flag
            └─ Runs BatchCoordinator.run() inline via asyncio.to_thread()
                 ├─ Count & split dataset into batches
                 ├─ Discover pool workers (SkyServe replicas)
                 ├─ Dispatch batches to workers via sky.exec()
                 ├─ Write progress directly to DB
                 ├─ Merge results
                 └─ Return (success) or raise (failure)
```

Client (`dataset.py`) polls `ManagedJobRecord.batch_total_batches` / `batch_completed_batches` from the jobs queue for progress display.

## Key Files

| File | Purpose |
|------|---------|
| `sky/batch/coordinator.py` | Runs inline on jobs controller, dispatches batches to pool workers |
| `sky/batch/dataset.py` | Client-side: `Dataset.map()`, launches managed job, polls progress |
| `sky/batch/worker.py` | Long-running HTTP service on each pool worker, processes batches |
| `sky/batch/utils.py` | Cloud storage helpers, chunk file management, function serialization |
| `sky/batch/constants.py` | Ports, timeouts, chunk naming patterns |
| `sky/batch/formats/jsonl.py` | JSONL input/output format |
| `sky/batch/formats/image_dir.py` | Image directory output format (PNG + manifest.jsonl) |
| `sky/batch/remote.py` | `@remote_function` decorator |
| `sky/jobs/controller.py` | `_run_batch_coordinator_task()` — detects batch metadata, runs coordinator |

## How managed_job_id Flows

1. `ds.map()` launches a managed job via `sky.jobs.launch()` -> gets `managed_job_id` back
2. The jobs controller passes `job_id` directly to `BatchCoordinator(job_id=self._job_id)`
3. Coordinator passes `str(managed_job_id)` as `SKY_BATCH_JOB_ID` env var to workers
4. Workers use it for chunk paths: `.sky_batch_tmp/{managed_job_id}/chunk_...`
5. Client uses same `str(managed_job_id)` for result merge paths

## Progress Reporting

- Coordinator writes progress directly to DB via `managed_job_state.set_batch_progress()`
- Client reads progress from `ManagedJobRecord` fields (`batch_total_batches`, `batch_completed_batches`) via `sky.jobs.queue_v2()`
- No HTTP roundtrip — coordinator runs in the same process as the DB

## Batch Task Detection

- `ds.map()` creates a `sky.Task(run=None)` with `task._metadata['batch_coordinator'] = True`
- All batch config is passed via `task._metadata` (survives YAML serialization)
- In `controller.py:_run_one_task()`, the metadata flag is checked before the `task.run is None` check
- This routes to `_run_batch_coordinator_task()` which runs the coordinator inline

## Cancellation

- When `sky jobs cancel` is called, the async task gets `CancelledError`
- The controller catches it and calls `coordinator.cancel()`
- `cancel()` sets `_cancelled = True` flag and shuts down active workers
- The dispatch loop checks `_cancelled` to break early

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

- The coordinator runs inline on the controller — no separate cluster is provisioned
- `BatchCoordinator.__init__` still supports env var fallback for backward compat
- The run script is baked before `sky.jobs.launch()` returns, so `managed_job_id` cannot be hardcoded into it. That's why the `job_id` parameter approach exists.
