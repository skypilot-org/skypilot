# Sky Batch Development Context

## Architecture Overview

Sky Batch distributes workloads across a pool of GPU/CPU workers via managed jobs. The coordinator runs inline on the jobs controller (no separate cluster).

```
ds.map()
  └─ sky.jobs.launch(task with batch_coordinator metadata)
       └─ Jobs controller detects metadata flag
            └─ Runs BatchCoordinator.run() inline via asyncio.to_thread()
                 ├─ Resolve typed input/output formats from dicts
                 ├─ Count items & split dataset into batches
                 ├─ Persist all batches to DB as PENDING
                 ├─ Discover pool workers (ready replicas)
                 ├─ Launch long-running worker service on each worker
                 ├─ Dispatch batches via sky.exec() notify scripts
                 ├─ Track progress via batch_state DB table
                 ├─ Reduce results using output format handlers
                 ├─ Cleanup temp files
                 └─ Return (success) or raise (failure)
```

Client (`dataset.py`) polls `batch_total_batches` / `batch_completed_batches` from the jobs queue for tqdm progress display.

## Key Files

| File | Purpose |
|------|---------|
| `sky/batch/__init__.py` | Public API exports: `Dataset`, `load`, `save_results`, `remote_function`, format classes |
| `sky/batch/coordinator.py` | Runs inline on jobs controller; splits dataset, discovers workers, dispatches batches, reduces/cleans up results |
| `sky/batch/dataset.py` | Client-side: `Dataset.map()`, launches managed job, polls progress with tqdm |
| `sky/batch/worker.py` | Long-running HTTP service on each pool worker; hosts `/feed_batch`, `/shutdown`, `/health` endpoints |
| `sky/batch/utils.py` | Cloud storage helpers (S3/GCS), batch file management, function serialization (source-code-based) |
| `sky/batch/constants.py` | Ports, timeouts, chunk naming patterns, retry settings |
| `sky/batch/io_formats.py` | Typed format classes: `JsonInput`, `JsonOutput`, `ImageOutput` with `InputReader`/`OutputWriter` base classes |
| `sky/batch/remote.py` | `@remote_function` decorator with closure/global validation via AST |
| `sky/jobs/controller.py` | `_run_batch_coordinator_task()` — detects `batch_coordinator` metadata, runs coordinator |
| `sky/jobs/state.py` | `batch_state` table and helper functions for batch persistence and progress aggregation |

## Worker Architecture

Workers use a **long-running service + notify pattern**:

1. Coordinator launches a persistent HTTP worker service on each cluster via `sky.exec()`.
2. For each batch, coordinator sends a lightweight notify script (also via `sky.exec()`) that `curl`s `/feed_batch`.
3. `/feed_batch` downloads the batch from cloud storage using the `InputReader`, puts it on an internal queue, and blocks until `save_results()` signals completion.
4. The notify `sky.exec()` job exits with SUCCEEDED, which the coordinator detects via `sdk.job_status()` polling.
5. On completion or cancellation, coordinator sends `/shutdown` to stop the worker service.

Worker service listens on `127.0.0.1:8290` (localhost only, not exposed to cloud).

## Typed I/O Format System

Formats are defined in `io_formats.py` as paired descriptor + handler classes:

**Base classes:**
- `InputReader(ABC)` — Abstract dataclass with `__len__()` and `download_batch(start_idx, end_idx, cache_dir)` methods
- `OutputWriter(ABC)` — Abstract dataclass with `upload_batch()`, `reduce_results()`, and `cleanup()` methods

**Built-in formats:**
- `JsonInput(path)` — JSONL input; downloads full file, caches locally per job, extracts line ranges
- `JsonOutput(path, column=None)` — JSONL output; supports column filtering; reduces by merging batch files
- `ImageOutput(path, column='image')` — Writes PIL Images as individual PNGs; no reduce step needed

**Serialization:**
- `to_dict()` / `from_dict()` — Serialize to/from dicts for transport via `task._metadata` and env vars
- Custom formats (defined outside the module) automatically include `_class_source` in serialization for remote reconstruction
- Workers receive formats as JSON env vars: `SKY_BATCH_INPUT_FORMAT`, `SKY_BATCH_OUTPUT_FORMATS`

**Multi-output:** `ds.map(..., output=[ImageOutput(...), JsonOutput(...)])` — each writer independently uploads, reduces, and cleans up.

**Custom formats:** Subclass `InputReader`/`OutputWriter`, register via `@registry.INPUT_READER_REGISTRY.type_register(name='...')` or `@registry.OUTPUT_WRITER_REGISTRY.type_register(name='...')`. Source code is automatically serialized. See `examples/batch/custom_formats/`.

## Batch Task Detection and Metadata Flow

1. `ds.map()` creates `sky.Task(run=None)` with batch config in `task._metadata`:
   - `batch_coordinator = True` — routing flag
   - `batch_dataset_path`, `batch_output_path`, `batch_size`, `batch_pool_name`
   - `batch_serialized_fn` — base64-encoded source code
   - `batch_activate_env` — optional env activation command
   - `batch_input_format` — input format dict
   - `batch_output_formats` — list of output format dicts
2. In `controller.py:_run_one_task()`, the metadata flag routes to `_run_batch_coordinator_task()`
3. Coordinator reconstructs formats from dicts and runs inline via `asyncio.to_thread()`

## Job ID Flow

1. `ds.map()` launches managed job → gets `managed_job_id`
2. Jobs controller passes `job_id` to `BatchCoordinator(job_id=self._job_id)`
3. Coordinator passes `str(managed_job_id)` as `SKY_BATCH_JOB_ID` env var to workers
4. Workers use it for temp paths: `.sky_batch_tmp/{managed_job_id}/batch_XXXXX-YYYYY.jsonl`
5. Client uses same `managed_job_id` to poll progress from DB

## Progress Reporting

- Coordinator writes per-batch records to `batch_state` table via `managed_job_state.save_batch_states()` and `set_batch_status()`
- `batch_total_batches` and `batch_completed_batches` are derived by a SQL aggregation subquery on the `batch_state` table (not denormalized columns)
- Client reads these derived fields from `ManagedJobRecord` via `sky.jobs.queue_v2()`
- No HTTP roundtrip for progress — coordinator runs in the same process as the DB
- Client displays tqdm progress bar with batch-level granularity

## Database Schema

`batch_state` table (defined in `sky/schemas/db/spot_jobs/018_add_batch_state.py`):

| Column | Type | Description |
|--------|------|-------------|
| `job_id` | Integer (PK) | Managed job ID |
| `batch_idx` | Integer (PK) | 0-based batch index |
| `start_idx` | Integer | Data start index (inclusive) |
| `end_idx` | Integer | Data end index (inclusive) |
| `status` | Text | PENDING, DISPATCHED, COMPLETED, or FAILED |
| `worker_cluster` | Text | Which worker processed this batch |
| `retry_count` | Integer | Number of retries for this batch |
| `updated_at` | Float | Last update timestamp |

**State functions** in `sky/jobs/state.py`:
- `save_batch_states(job_id, batches)` — Bulk insert all batches atomically
- `get_batch_states(job_id)` — Read all batch records
- `set_batch_status(job_id, batch_idx, status, worker_cluster, retry_count)` — Update single batch
- `reset_dispatched_batches(job_id)` — Reset DISPATCHED → PENDING for crash recovery
- `is_batch_job(job_id)` — Check if job has batch state records

## HA Recovery

- Batch states are persisted to DB with statuses: PENDING → DISPATCHED → COMPLETED (or FAILED)
- On controller crash, `_resume_from_db()` resets DISPATCHED batches back to PENDING
- Retry counts are persisted across resumes so batches cannot retry indefinitely
- `_shutdown_stale_workers()` cleans up old worker services that may hold the port after a crash
- Max retries per batch: `MAX_RETRIES = 3` with exponential backoff (`RETRY_BACKOFF_BASE = 2`)

## Cancellation

- `sky jobs cancel` sends SIGTERM to the controller process
- Coordinator's `_handle_sigterm()` calls `cancel()` then `sys.exit(1)`
- `cancel()` sets `_cancelled = True` flag and shuts down active worker **services** (the HTTP process on port 8290) via `/shutdown`
- The dispatch loop checks `_cancelled` to break early
- The pool clusters themselves are **not** terminated — they are a shared resource managed separately via `sky jobs pool`

## Dynamic Worker Scaling

- `_dispatch_all()` periodically re-discovers workers via `_get_ready_workers()`
- Newly scaled-up pool replicas are picked up automatically
- Each worker gets its own dispatch thread (with `contextvars.copy_context()` for log redirection)
- Individual worker failures are tolerated — other workers pick up remaining batches

## Function Serialization

- Uses **source-code-based** serialization (not cloudpickle) for cross-Python-version compatibility
- `serialize_function()` extracts source via `inspect.getsource()`, encodes as base64 JSON
- `deserialize_function()` executes source in a clean namespace with `sky` pre-imported
- `@remote_function` validates no closures or global references via AST analysis at decoration time

## Coordinator Dispatch Loop (per worker thread)

1. `_launch_worker_service(cluster_name)` — Submit long-running job, wait for `/health` 200
2. While pending batches exist:
   a. Pop batch index from `pending_batches` deque
   b. Generate notify script (`curl /feed_batch`) and submit via `sdk.exec()`
   c. Poll `sdk.job_status()` until terminal state
   d. Update DB: DISPATCHED → COMPLETED (or FAILED → PENDING for retry)
   e. Increment `completed_count`
3. Finally: `_shutdown_worker()` via `/shutdown` endpoint

Error tolerance: up to 12 consecutive `None` status polls (~60s) before treating as failure.

## Result Merging

`_reduce_results_and_cleanup()` runs after all batches complete:
1. For each output format: `format.reduce_results(job_id)` — merge batch files into final output
2. For each output format: `format.cleanup(job_id)` — delete temp batch files
3. `_print_partial_results_instructions()` provides recovery code if merging fails

Temp batch files stored in: `{output_base}/.sky_batch_tmp/{job_id}/batch_XXXXX-YYYYY.jsonl`

## Testing

```bash
# Unit tests
pytest tests/unit_tests/test_batch_validation.py      # Function validation
pytest tests/unit_tests/test_batch_serialization.py    # Serialization roundtrip
pytest tests/unit_tests/test_batch_manual_run.py       # Manual run tests

# Smoke tests (require cloud resources)
pytest tests/smoke_tests/test_batch.py::test_batch_simple
pytest tests/smoke_tests/test_batch.py::test_batch_diffusion
pytest tests/smoke_tests/test_batch.py::test_batch_custom_formats
pytest tests/smoke_tests/test_batch.py::test_batch_cancel
pytest tests/smoke_tests/test_batch.py::test_batch_ha_kill_running

# Run examples
bash examples/batch/simple/run.sh
bash examples/batch/diffusion/run.sh
bash examples/batch/custom_formats/run.sh
```

## Gotchas

- The coordinator runs inline on the controller — no separate cluster is provisioned
- Workers require `SKY_BATCH_INPUT_FORMAT` and `SKY_BATCH_OUTPUT_FORMATS` env vars (no fallback)
- Worker downloads the full dataset once and caches locally per `job_id` to avoid stale data
- Cloud storage support: S3 (`s3://`) and GCS (`gs://`) for both inputs and outputs
- Worker service uses fixed port 8290 on localhost — only one batch job per worker at a time
- Batch indices are inclusive: `[start_idx, end_idx]`
- Batch file names are zero-padded to 8 digits for proper lexicographic sorting
- Result merging is not atomic at the file level — partial results can be recovered manually
