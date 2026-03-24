# Sky Batch Development Context

## Architecture Overview

Sky Batch distributes workloads across a pool of GPU/CPU workers via managed jobs. The coordinator runs inline on the jobs controller (no separate cluster).

```
ds.map()
  └─ sky.jobs.launch(task with batch_coordinator metadata)
       └─ Jobs controller detects metadata flag
            └─ Runs BatchCoordinator.run() inline via asyncio.to_thread()
                 ├─ Resolve typed input/output formats
                 ├─ Count & split dataset into batches
                 ├─ Discover pool workers (ready replicas)
                 ├─ Launch long-running worker service on each worker
                 ├─ Dispatch batches via sky.exec() notify scripts
                 ├─ Write progress directly to DB
                 ├─ Merge results using output format handlers
                 └─ Return (success) or raise (failure)
```

Client (`dataset.py`) polls `ManagedJobRecord.batch_total_batches` / `batch_completed_batches` from the jobs queue for progress display via tqdm.

## Key Files

| File | Purpose |
|------|---------|
| `sky/batch/coordinator.py` | Runs inline on jobs controller; splits dataset, discovers workers, dispatches batches, merges results |
| `sky/batch/dataset.py` | Client-side: `Dataset.map()`, launches managed job, polls progress with tqdm |
| `sky/batch/worker.py` | Long-running HTTP service on each pool worker; hosts `/feed_batch`, `/shutdown`, `/health` endpoints |
| `sky/batch/utils.py` | Cloud storage helpers (S3/GCS), chunk file management, function serialization (source-code-based) |
| `sky/batch/constants.py` | Ports, timeouts, chunk naming patterns, retry settings |
| `sky/batch/io_formats.py` | Typed format classes: `JsonInput`, `JsonOutput`, `ImageOutput` (with `InputFormat`/`OutputFormat` base) |
| `sky/batch/remote.py` | `@remote_function` decorator with closure/global validation via AST |
| `sky/batch/__init__.py` | Public API exports: `Dataset`, `load`, `save_results`, format classes |
| `sky/jobs/controller.py` | `_run_batch_coordinator_task()` — detects `batch_coordinator` metadata, runs coordinator |

## Worker Architecture

Workers use a **long-running service + notify pattern**:

1. Coordinator launches a persistent HTTP worker service on each cluster via `sky.exec()`.
2. For each batch, coordinator sends a lightweight notify script (also via `sky.exec()`) that `curl`s `/feed_batch`.
3. The `/feed_batch` handler downloads the chunk from cloud storage, puts it on an internal queue, and blocks until `save_results()` signals completion.
4. The notify `sky.exec()` job exits with SUCCEEDED, which the coordinator detects via `sdk.job_status()` polling.
5. On completion or cancellation, coordinator sends `/shutdown` to stop the worker service.

## Typed I/O Formats

Formats are defined in `io_formats.py` as paired descriptor + handler classes:

- **`JsonInput(path)`** — JSONL input; counts items, downloads chunks with local caching
- **`JsonOutput(path, column=None)`** — JSONL output; supports column filtering
- **`ImageOutput(path, column='image')`** — Writes PIL images as individual PNGs to a directory

Formats are serialized to dicts (`to_dict()`/`from_dict()`) and passed through `task._metadata` to the coordinator, and as JSON env vars (`SKY_BATCH_INPUT_FORMAT`, `SKY_BATCH_OUTPUT_FORMATS`) to workers.

Multi-output is supported: `ds.map(..., output=[ImageOutput(...), JsonOutput(...)])`.

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
- Client displays progress using tqdm with batch-level granularity

## Batch Task Detection

- `ds.map()` creates a `sky.Task(run=None)` with `task._metadata['batch_coordinator'] = True`
- All batch config is passed via `task._metadata` (survives YAML serialization)
- In `controller.py:_run_one_task()`, the metadata flag is checked before the `task.run is None` check
- This routes to `_run_batch_coordinator_task()` which runs the coordinator inline

## HA Recovery

- Batch states are persisted to DB with statuses: PENDING, DISPATCHED, COMPLETED, FAILED
- On controller crash, `_resume_from_db()` resets DISPATCHED batches back to PENDING
- Retry counts are persisted across resumes so batches can't retry indefinitely
- `_shutdown_stale_workers()` cleans up old worker services that may hold the port after a crash

## Cancellation

- `sky jobs cancel` sends SIGTERM to the controller process
- Coordinator's `_handle_sigterm()` calls `cancel()` then `sys.exit(1)`
- `cancel()` sets `_cancelled = True` flag and shuts down active workers via `/shutdown`
- The dispatch loop checks `_cancelled` to break early

## Dynamic Worker Scaling

- `_dispatch_all()` periodically re-discovers workers via `_get_ready_workers()`
- Newly scaled-up pool replicas are picked up automatically
- Each worker gets its own dispatch thread (with `contextvars.copy_context()` for log redirection)
- Individual worker failures are tolerated — other workers pick up remaining batches

## Function Serialization

- Uses **source-code-based** serialization (not cloudpickle) for cross-Python-version compatibility
- `serialize_function()` extracts source via `inspect.getsource()`, encodes as base64 JSON
- `deserialize_function()` executes source in a clean namespace with `sky` pre-imported
- `@remote_function` validates no closures or global references via AST analysis

## Testing

```bash
# Restart API server after code changes
sky api stop && sky api start

# Run the simple example (50 items, batch_size=2)
bash examples/batch/simple/run.sh

# Run the diffusion example (image generation)
bash examples/batch/diffusion/run.sh

# Check jobs queue
sky jobs queue

# Check chunk paths
aws s3 ls s3://<bucket>/.sky_batch_tmp/
```

## Gotchas

- The coordinator runs inline on the controller — no separate cluster is provisioned
- `BatchCoordinator.__init__` still supports env var fallback for backward compat
- Worker format resolution also has env var fallback chains for backward compat (`SKY_BATCH_OUTPUT_FORMATS` -> `SKY_BATCH_OUTPUT_FORMAT` -> path-based detection)
- Worker downloads the full dataset once and caches locally per job_id to avoid stale data from previous jobs
- Cloud storage support: S3 (`s3://`), GCS (`gs://`), R2 (`r2://`) for inputs; S3 and GCS for upload operations
