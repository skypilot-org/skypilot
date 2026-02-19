"""Per-pool batch controller for Sky Batch.

This module implements the BatchController, a long-lived per-pool service
that runs on the SkyServe controller cluster. It accepts job submissions
from user machines (``ds.map()``) and dispatches individual batches to
pool replicas via ``sky.exec()``.

Architecture (long-running workers with continuous feeding)::

    User Machine                  Controller (local)             Worker Nodes
    ds.map() ── HTTP ──────────► BatchController (:8280)
      POST /submit_job            ├─ Load dataset, split batches
      GET  /job_status            ├─ Upload input chunks to cloud
                                  ├─ Discover workers
                                  ├─ Thread pool (1 thread/worker):
                                  │   for each pending batch:
                                  │     sky.exec(notify_script)
                                  │       ├─ Start worker svc (1st time)
                                  │       └─ curl /feed_batch (blocks)
                                  │     poll sdk.job_status()
                                  │   sky.exec(shutdown_script)
                                  └─ Concatenate result chunks

Workers run a persistent mapper process.  ``sky.batch.load()`` blocks
until a batch is fed via the local HTTP service, and
``sky.batch.save_results()`` signals completion (causing the sky.exec
notify job to exit with SUCCEEDED).
"""
import collections
import dataclasses
import logging
import textwrap
import threading
import time
from typing import Deque, Dict, List, Optional

import fastapi
import pydantic
import uvicorn

import sky
from sky.batch import constants
from sky.batch import utils
from sky.client import sdk
from sky.serve import serve_state
from sky.serve import serve_utils
from sky.skylet import constants as skylet_constants

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BatchJob:
    """State for a single batch job managed by the controller."""

    job_id: str
    dataset_path: str
    batch_size: int
    output_path: str
    serialized_fn: str
    activate_env: str = ''  # Shell command to activate user's venv
    status: str = 'pending'  # pending | running | completed | failed

    # Batch metadata: List of [start_idx, end_idx] tuples.
    batches: List[List[int]] = dataclasses.field(default_factory=list)

    # Runtime tracking.
    pending_batches: Deque[int] = dataclasses.field(
        default_factory=collections.deque)
    completed_count: int = 0

    # Worker tracking (for status display).
    workers_ready: int = 0
    workers_expected: int = 0

    error: Optional[str] = None


class BatchController:
    """Per-pool batch controller.

    Runs as a ``multiprocessing.Process`` on the SkyServe controller cluster
    (started from ``sky/serve/service.py``).  Exposes a FastAPI server so
    that user machines can submit jobs and poll status.

    Workers are driven exclusively via ``sky.exec()`` — no inbound HTTP
    from workers is required.
    """

    def __init__(self, pool_name: str, host: str, port: int):
        self.pool_name = pool_name
        self.host = host
        self.port = port

        # Active jobs keyed by job_id.
        self._jobs: Dict[str, BatchJob] = {}
        self._jobs_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Worker discovery
    # ------------------------------------------------------------------

    def _get_expected_worker_count(self) -> int:
        """Get the expected number of workers from the pool configuration.

        Returns:
            The target replica count specified in the pool configuration.
            Returns 1 if unable to determine (fallback for safety).
        """

        try:
            service_name = self.pool_name
            service = serve_state.get_service_from_name(service_name)
            if service is None:
                logger.warning(f'Could not find service {service_name}, '
                               f'defaulting to 1 worker')
                return 1

            # Get the service spec to access min_replicas
            version = service['version']
            spec = serve_state.get_spec(service_name, version)
            if spec is None:
                logger.warning(f'Could not find spec for {service_name} '
                               f'version {version}, defaulting to 1 worker')
                return 1

            # Get min_replicas from the spec
            min_replicas = spec.min_replicas
            logger.info(f'Pool {self.pool_name} expects {min_replicas} workers')
            return max(1, min_replicas)  # At least 1 worker
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to get expected worker count: {e}, '
                           f'defaulting to 1 worker')
            return 1

    def _discover_workers(self) -> List[str]:
        """Return ``[cluster_name, ...]`` for ready replicas.

        Uses the existing SkyServe replica state on the controller.
        """
        replicas = serve_utils.get_ready_replicas(self.pool_name)
        return [info.cluster_name for info in replicas]

    # ------------------------------------------------------------------
    # Pool resource detection
    # ------------------------------------------------------------------

    def _get_pool_resources(self) -> Optional['sky.Resources']:
        """Return the ``sky.Resources`` configured for this pool's workers.

        This is used to set matching resources on ``sky.exec()`` tasks so
        that Ray allocates GPUs (and other accelerators) to the worker
        processes.
        """
        try:
            service = serve_state.get_service_from_name(self.pool_name)
            if service is None:
                return None
            yaml_content = serve_state.get_yaml_content(
                self.pool_name, service['version'])
            if yaml_content is None:
                return None
            task = sky.Task.from_yaml_str(yaml_content)
            # Return the first resource spec (pools don't use ordered resources).
            for r in task.resources:
                return r
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to get pool resources: %s', e)
        return None

    # ------------------------------------------------------------------
    # Dataset format detection
    # ------------------------------------------------------------------

    def _get_dataset_format(self, dataset_path: str):
        """Detect dataset format and return appropriate handler.

        Args:
            dataset_path: Cloud storage path to dataset

        Returns:
            DatasetFormat instance for this dataset type
        """
        from sky.batch.formats.jsonl import JSONLDataset

        if dataset_path.endswith('.jsonl'):
            return JSONLDataset()
        else:
            raise ValueError(f'Unsupported dataset format: {dataset_path}. '
                             f'Supported: .jsonl')

    # ------------------------------------------------------------------
    # Worker code generation
    # ------------------------------------------------------------------

    def _generate_worker_startup_code(self, job: BatchJob) -> str:
        """Generate code to start the long-running worker service.

        This runs as a separate SkyPilot job that blocks in the mapper function.
        """
        # Build the activation preamble.  When activate_env is provided
        # (e.g. "source .venv/bin/activate") it runs before every shell
        # snippet so that ``python`` and ``pip`` resolve to the user's
        # virtual-env — the same one configured in the pool's ``setup``.
        activate = job.activate_env.strip()
        activate_line = f'{activate} &&' if activate else ''

        # SkyPilot runtime env contains the dev sky package.  We add its
        # site-packages to PYTHONPATH so the user's python can import
        # sky.batch without installing skypilot separately.
        sky_runtime = skylet_constants.SKY_REMOTE_PYTHON_ENV

        return textwrap.dedent(f"""\
            set -e
            export SKY_BATCH_SERIALIZED_FN='{job.serialized_fn}'
            export SKY_BATCH_OUTPUT_PATH='{job.output_path}'
            export SKY_BATCH_JOB_ID='{job.job_id}'
            export SKY_BATCH_DATASET_PATH='{job.dataset_path}'

            # Make sky.batch visible to the user's python by adding the
            # SkyPilot runtime site-packages to PYTHONPATH.
            SKY_SITE=$({sky_runtime}/bin/python -c \
              "import site; print(site.getsitepackages()[0])")
            export PYTHONPATH="${{SKY_SITE}}:${{PYTHONPATH}}"

            # Ensure boto3 is available in the user env.
            {activate_line} pip install boto3 2>/dev/null

            # Start worker service in the activated environment.
            {activate_line} python -u -c '
            import os
            from sky.batch.worker import start_worker
            start_worker(
                serialized_fn=os.environ["SKY_BATCH_SERIALIZED_FN"],
                output_path=os.environ["SKY_BATCH_OUTPUT_PATH"],
                job_id=os.environ["SKY_BATCH_JOB_ID"],
                dataset_path=os.environ["SKY_BATCH_DATASET_PATH"],
            )
            ' 2>&1 | tee /tmp/sky_batch_worker.log
            """)

    def _generate_notify_code(self, batch_idx: int, job: BatchJob) -> str:
        """Generate lightweight notify script for a single batch."""
        start_idx, end_idx = job.batches[batch_idx]
        port = constants.WORKER_SERVICE_PORT

        return textwrap.dedent(f"""\
            set -e
            curl -sf -X POST http://127.0.0.1:{port}/feed_batch \\
                -H 'Content-Type: application/json' \\
                -d '{{"dataset_path": "{job.dataset_path}", "start_idx": {start_idx}, "end_idx": {end_idx}, "batch_idx": {batch_idx}}}'
            """)

    def _generate_shutdown_code(self) -> str:
        """Generate a script that shuts down the worker service."""
        port = constants.WORKER_SERVICE_PORT
        return textwrap.dedent(f"""\
            curl -sf -X POST http://127.0.0.1:{port}/shutdown || true
            """)

    # ------------------------------------------------------------------
    # Worker service lifecycle
    # ------------------------------------------------------------------

    def _launch_worker_service(self, cluster_name: str, job: BatchJob) -> int:
        """Launch worker service as a long-running SkyPilot job.

        Returns:
            The SkyPilot job ID of the worker service.
        """

        # Submit worker startup job with the pool's resources so that
        # Ray allocates accelerators (GPUs) to the worker process.
        startup_code = self._generate_worker_startup_code(job)
        task = sky.Task(name=f'batch-worker-{job.job_id}', run=startup_code)
        pool_resources = self._get_pool_resources()
        if pool_resources is not None:
            task.set_resources(pool_resources)
        request_id = sdk.exec(task, cluster_name=cluster_name)
        worker_job_id, _ = sdk.get(request_id)
        assert worker_job_id is not None, 'Failed to get worker job ID'

        logger.info(f'[{job.job_id}] Launched worker service as job '
                    f'{worker_job_id} on {cluster_name}')

        # Wait for worker to be ready via a single polling health-check job
        port = constants.WORKER_SERVICE_PORT
        timeout = constants.WORKER_SERVICE_STARTUP_TIMEOUT
        health_code = textwrap.dedent(f"""\
            set -e
            for i in $(seq 1 {timeout}); do
                if curl -s http://127.0.0.1:{port}/health > /dev/null 2>&1; then
                    echo "Worker service ready after $i seconds"
                    exit 0
                fi
                sleep 1
            done
            echo "ERROR: Worker service did not start within {timeout}s"
            exit 1
            """)
        health_task = sky.Task(name=f'health-check-{job.job_id}',
                               run=health_code)
        try:
            req_id = sdk.exec(health_task, cluster_name=cluster_name)
            sdk.get(req_id)
            logger.info(f'[{job.job_id}] Worker service ready on '
                        f'{cluster_name}')
            return worker_job_id
        except Exception as e:  # pylint: disable=broad-except
            raise RuntimeError(
                f'Worker service on {cluster_name} failed to start: '
                f'{e}') from e

    def _shutdown_worker(self,
                         cluster_name: str,
                         worker_job_id: Optional[int] = None) -> None:
        """Send shutdown signal and cancel worker job if provided.

        Sends a shutdown signal to the worker service, then forcefully
        cancels the worker job to ensure it terminates.
        """
        # Send shutdown signal via curl
        shutdown_code = self._generate_shutdown_code()
        task = sky.Task(name=f'batch-shutdown-{cluster_name}',
                        run=shutdown_code)
        try:
            request_id = sdk.exec(task, cluster_name=cluster_name)
            sdk.get(request_id)
            logger.info('Sent shutdown to worker service on %s', cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to send shutdown to %s: %s', cluster_name, e)

        # Cancel worker job if specified
        if worker_job_id is not None:
            time.sleep(5)  # Brief wait for graceful exit
            try:
                cancel_req_id = sdk.cancel(cluster_name,
                                           job_ids=[worker_job_id])
                sdk.get(cancel_req_id)
                logger.info(f'Cancelled worker job {worker_job_id} on '
                            f'{cluster_name}')
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Failed to cancel worker job {worker_job_id}: '
                               f'{e}')

    # ------------------------------------------------------------------
    # Per-worker dispatch loop (runs in its own thread)
    # ------------------------------------------------------------------

    def _worker_dispatch_loop(self, cluster_name: str, job: BatchJob) -> None:
        """Dispatch batches to *cluster_name* until the queue is empty.

        1. Launch worker service once as a separate long-running job.
        2. For each batch: upload input chunk, submit notify job, poll status.
        3. Shutdown worker service and cancel worker job when done.
        """

        retry_counts: Dict[int, int] = {}

        # Launch worker service once
        worker_job_id = self._launch_worker_service(cluster_name, job)

        try:
            while True:
                # Grab next batch index.
                try:
                    batch_idx = job.pending_batches.popleft()
                except IndexError:
                    # Queue empty — this worker is done.
                    return

                retries = retry_counts.get(batch_idx, 0)
                try:
                    # Generate and submit notify script
                    notify_code = self._generate_notify_code(batch_idx, job)
                    task = sky.Task(
                        name=f'batch-notify-{job.job_id}-{batch_idx}',
                        run=notify_code)
                    request_id = sdk.exec(task, cluster_name=cluster_name)
                    job_id_on_cluster, _ = sdk.get(request_id)
                    assert job_id_on_cluster is not None, 'Failed to get job ID'

                    logger.info(f'[{job.job_id}] Batch {batch_idx} running as '
                                f'job {job_id_on_cluster} on {cluster_name}')

                    # Poll until terminal
                    while True:
                        time.sleep(constants.BATCH_POLL_INTERVAL)
                        req_id = sdk.job_status(cluster_name,
                                                [job_id_on_cluster])
                        statuses = sdk.get(req_id)
                        status = statuses.get(job_id_on_cluster)
                        if status is not None and status.is_terminal():
                            if status != sky.JobStatus.SUCCEEDED:
                                raise RuntimeError(
                                    f'Batch {batch_idx} failed with status '
                                    f'{status.value}')
                            logger.info(
                                f'[{job.job_id}] Batch {batch_idx} SUCCEEDED '
                                f'on {cluster_name}')
                            break

                    with self._jobs_lock:
                        job.completed_count += 1
                    logger.info(f'[{job.job_id}] Batch {batch_idx} completed '
                                f'({job.completed_count}/{len(job.batches)})')
                except Exception as e:  # pylint: disable=broad-except
                    logger.error(f'[{job.job_id}] Batch {batch_idx} failed on '
                                 f'{cluster_name}: {e}')
                    if retries < constants.MAX_RETRIES:
                        retry_counts[batch_idx] = retries + 1
                        job.pending_batches.append(batch_idx)
                        backoff = (constants.RETRY_BACKOFF_BASE**
                                   retry_counts[batch_idx])
                        logger.info(
                            f'[{job.job_id}] Re-queued batch {batch_idx} '
                            f'(retry {retry_counts[batch_idx]}/'
                            f'{constants.MAX_RETRIES}), '
                            f'backoff {backoff}s')
                        time.sleep(backoff)
                    else:
                        raise RuntimeError(
                            f'Batch {batch_idx} failed after '
                            f'{constants.MAX_RETRIES} retries: {e}') from e
        finally:
            self._shutdown_worker(cluster_name, worker_job_id=worker_job_id)

    # ------------------------------------------------------------------
    # Per-job processing (background thread)
    # ------------------------------------------------------------------

    def _process_job(self, job: BatchJob) -> None:
        """Process a single batch job end-to-end.

        Runs in its own daemon thread so the FastAPI server stays responsive.
        """
        try:
            # Keep status as 'pending' until workers are ready
            # (dataset loading and worker discovery happen in 'pending' phase)

            # 1. Detect input and output format handlers.
            dataset_format = self._get_dataset_format(job.dataset_path)
            output_format = utils.get_output_format(job.output_path)

            # 2. Count total items (format-specific, may download for counting)
            logger.info(f'[{job.job_id}] Counting items in {job.dataset_path}')
            total_items = dataset_format.count_items(job.dataset_path)
            logger.info(f'[{job.job_id}] Dataset contains {total_items} items')

            # 3. Create batches as index ranges (no actual data!)
            job.batches = []
            for i in range(0, total_items, job.batch_size):
                start_idx = i
                end_idx = min(i + job.batch_size - 1, total_items - 1)
                job.batches.append([start_idx, end_idx])  # [start_idx, end_idx]

            job.pending_batches = collections.deque(range(len(job.batches)))
            logger.info(
                f'[{job.job_id}] Created {len(job.batches)} batches '
                f'(total_items: {total_items}, batch_size: {job.batch_size})')

            if not job.batches:
                job.status = 'completed'
                return

            # 4. Discover workers, waiting for all expected workers to be ready.
            expected_workers = self._get_expected_worker_count()
            job.workers_expected = expected_workers
            workers = self._discover_workers()
            job.workers_ready = len(workers)
            timeout = constants.WORKER_DISCOVERY_TIMEOUT

            if len(workers) < expected_workers:
                logger.info(
                    f'[{job.job_id}] Waiting for all {expected_workers} '
                    f'workers to be ready (currently {len(workers)} ready)...')
                deadline = time.monotonic() + timeout

                while len(workers) < expected_workers and time.monotonic(
                ) < deadline:
                    time.sleep(5)
                    workers = self._discover_workers()
                    job.workers_ready = len(workers)
                    if len(workers) < expected_workers:
                        remaining = int(deadline - time.monotonic())
                        logger.info(
                            f'[{job.job_id}] {len(workers)}/{expected_workers} '
                            f'workers ready (waiting up to {remaining}s more)')

                if len(workers) < expected_workers:
                    logger.warning(
                        f'[{job.job_id}] Only {len(workers)}/{expected_workers}'
                        f' workers ready after waiting {timeout}s. '
                        'Proceeding with available workers.')

            if not workers:
                raise RuntimeError(f'No ready workers found in pool '
                                   f'{self.pool_name} after waiting '
                                   f'{timeout}s')

            logger.info(f'[{job.job_id}] Starting with {len(workers)} workers '
                        f'({expected_workers} expected)')
            job.workers_ready = len(workers)

            # 5. Set status to 'running' now that workers are ready
            job.status = 'running'

            # 6. Launch one dispatch thread per worker.
            dispatch_threads: List[threading.Thread] = []
            errors: List[Exception] = []

            def _dispatch_wrapper(cname: str) -> None:
                try:
                    self._worker_dispatch_loop(cname, job)
                except Exception as e:  # pylint: disable=broad-except
                    errors.append(e)

            for cluster_name in workers:
                t = threading.Thread(target=_dispatch_wrapper,
                                     args=(cluster_name,),
                                     daemon=True)
                t.start()
                dispatch_threads.append(t)

            # 7. Wait for all dispatch threads to finish.
            for t in dispatch_threads:
                t.join()

            if errors:
                raise errors[0]

            # 8. Verify all batches completed.
            if job.completed_count != len(job.batches):
                raise RuntimeError(
                    f'Expected {len(job.batches)} completed batches, '
                    f'got {job.completed_count}')

            # 9. Merge results (uses output format abstraction).
            job.status = 'merging'
            logger.info(f'[{job.job_id}] Merging results...')
            output_format.merge_results(job.output_path, job.job_id)
            logger.info(f'[{job.job_id}] Results written to {job.output_path}')

            job.status = 'completed'
            logger.info(f'[{job.job_id}] Job completed successfully')

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'[{job.job_id}] Job failed: {e}')
            job.status = 'failed'
            job.error = str(e)

            # Clean up temporary files even on failure
            try:
                logger.info(f'[{job.job_id}] Cleaning up temporary files...')
                utils.delete_chunk_files(job.output_path, job_id=job.job_id)
                logger.info(f'[{job.job_id}] Temporary files cleaned up')
            except Exception as cleanup_error:  # pylint: disable=broad-except
                logger.warning(f'[{job.job_id}] Failed to clean up temporary '
                               f'files: {cleanup_error}')

    # ------------------------------------------------------------------
    # FastAPI server
    # ------------------------------------------------------------------

    def _run_server(self) -> None:
        """Start the FastAPI server (blocking)."""
        app = fastapi.FastAPI()

        # ---- Pydantic models ----

        class SubmitJobRequest(pydantic.BaseModel):
            job_id: str
            dataset_path: str
            batch_size: int
            output_path: str
            serialized_fn: str
            activate_env: str = ''

        # ---- Endpoints ----

        @app.post(constants.ENDPOINT_SUBMIT_JOB)
        def submit_job(req: SubmitJobRequest):
            """Called by ``ds.map()`` on the user machine."""
            job = BatchJob(
                job_id=req.job_id,
                dataset_path=req.dataset_path,
                batch_size=req.batch_size,
                output_path=req.output_path,
                serialized_fn=req.serialized_fn,
                activate_env=req.activate_env,
            )
            with self._jobs_lock:
                self._jobs[job.job_id] = job

            # Process in a background thread.
            threading.Thread(target=self._process_job, args=(job,),
                             daemon=True).start()

            return {'status': 'submitted', 'job_id': job.job_id}

        @app.get(constants.ENDPOINT_JOB_STATUS)
        def job_status(job_id: str):
            """Called by ``ds.map()`` to poll completion."""
            with self._jobs_lock:
                job = self._jobs.get(job_id)
            if job is None:
                return {'error': f'Job {job_id} not found'}
            return {
                'job_id': job.job_id,
                'status': job.status,
                'total_batches': len(job.batches),
                'completed': job.completed_count,
                'workers_ready': job.workers_ready,
                'workers_expected': job.workers_expected,
                'error': job.error,
            }

        @app.get(constants.ENDPOINT_STATUS)
        def status():
            """General controller status."""
            with self._jobs_lock:
                jobs_summary = {jid: j.status for jid, j in self._jobs.items()}
            return {
                'pool_name': self.pool_name,
                'jobs': jobs_summary,
            }

        @app.post(constants.ENDPOINT_SHUTDOWN)
        def shutdown():
            """Shutdown the controller."""
            return {'status': 'shutting_down'}

        # ---- Run ----

        uvicorn.run(app, host=self.host, port=self.port, log_level='warning')

    # ------------------------------------------------------------------
    # Main entry point (called by multiprocessing.Process)
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the batch controller (blocking)."""
        logger.info(f'BatchController for pool {self.pool_name!r} starting '
                    f'on {self.host}:{self.port}')
        self._run_server()


def run_batch_controller(pool_name: str, host: str, port: int) -> None:
    """Entry point for the batch controller process.

    Called from ``sky/serve/service.py`` as a ``multiprocessing.Process``
    target.
    """
    ctrl = BatchController(pool_name=pool_name, host=host, port=port)
    ctrl.run()
