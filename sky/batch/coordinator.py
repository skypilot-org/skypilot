"""Batch coordinator — orchestrates batch processing across pool workers.

The ``BatchCoordinator`` runs inline on the jobs controller (no separate
cluster).  ``ds.map()`` passes all config via ``task._metadata`` and
``sky.jobs.launch()`` submits the task; the controller detects the
``batch_coordinator`` metadata flag and calls ``BatchCoordinator.run()``
directly via ``asyncio.to_thread()``.

Lifecycle::

    ds.map()
      └─ sky.jobs.launch(task with batch_coordinator metadata)
           └─ Jobs controller detects metadata flag
                └─ Runs BatchCoordinator.run() inline
                     ├─ Count & split dataset into batches
                     ├─ Discover pool workers (SkyServe replicas)
                     ├─ Dispatch batches to workers via sky.exec()
                     ├─ Write progress directly to DB
                     ├─ Merge results
                     └─ Return (success) or raise (failure)
"""
import collections
import logging
import signal
import sys
import textwrap
import threading
import time
from typing import Any, Deque, Dict, List, Optional

import sky
from sky.batch import constants
from sky.batch import utils
from sky.client import sdk
from sky.jobs import state as managed_job_state
from sky.skylet import constants as skylet_constants

logger = logging.getLogger(__name__)


class BatchCoordinator:
    """Orchestrates batch processing across pool workers.

    Runs inline on the jobs controller.  Config is passed via
    ``task._metadata`` by ``ds.map()``.  Dispatches batches to pool
    workers via ``sky.exec()``.  Writes progress directly to the DB.
    """

    def __init__(self,
                 dataset_path: str,
                 output_path: str,
                 batch_size: int,
                 pool_name: str,
                 serialized_fn: str,
                 activate_env: str = '',
                 job_id: Optional[int] = None):
        self.dataset_path = dataset_path
        self.output_path = output_path
        self.batch_size = batch_size
        self.pool_name = pool_name
        self.serialized_fn = serialized_fn
        self.activate_env = activate_env

        # Use explicit job_id if provided (inline on controller),
        # otherwise fall back to env var (backward compat).
        if job_id is not None:
            self._managed_job_id: int = job_id
        else:
            import os  # pylint: disable=import-outside-toplevel
            env_var = skylet_constants.MANAGED_JOB_ID_ENV_VAR
            raw = os.environ.get(env_var)
            if raw is None:
                raise RuntimeError(
                    f'{env_var} not set. The coordinator must run '
                    f'as a managed job.')
            self._managed_job_id = int(raw)

        # Batch metadata: list of [start_idx, end_idx] tuples.
        self.batches: List[List[int]] = []
        self.pending_batches: Deque[int] = collections.deque()
        self.completed_count: int = 0
        self._completed_lock = threading.Lock()

        # Worker tracking: cluster_name → worker_job_id
        self._active_workers: Dict[str, int] = {}

        # Cancellation flag for inline (controller) mode.
        self._cancelled = False

        # Register SIGTERM handler for graceful cancellation.
        signal.signal(signal.SIGTERM, self._handle_sigterm)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main entry point.  Returns on success, raises on failure."""
        try:
            logger.info(f'managed_job_id={self._managed_job_id}')
            self._count_and_split()
            if not self.batches:
                logger.info('No items in dataset — nothing to do.')
                return
            self._discover_workers()
            self._dispatch_all()
            self._merge_results()
            logger.info('Batch job completed successfully.')
        except Exception:
            self._cleanup_on_failure()
            raise

    # ------------------------------------------------------------------
    # SIGTERM handler (sky jobs cancel)
    # ------------------------------------------------------------------

    def _handle_sigterm(self, signum, frame):
        """Graceful shutdown on ``sky jobs cancel``."""
        logger.info('Received SIGTERM — shutting down workers...')
        self.cancel()
        sys.exit(1)

    def cancel(self) -> None:
        """Cancel the coordinator and shut down active workers.

        Sets the ``_cancelled`` flag so the dispatch loop breaks early,
        then shuts down any active worker services.
        """
        self._cancelled = True
        for cluster_name, worker_job_id in list(self._active_workers.items()):
            try:
                self._shutdown_worker(cluster_name, worker_job_id)
            except Exception:  # pylint: disable=broad-except
                logger.warning(f'Failed to shutdown worker on {cluster_name}')

    # ------------------------------------------------------------------
    # Dataset counting & splitting
    # ------------------------------------------------------------------

    def _count_and_split(self) -> None:
        """Count dataset items and create batch index ranges."""
        dataset_format = self._get_dataset_format()
        output_format = utils.get_output_format(self.output_path)
        self._output_format = output_format

        logger.info(f'Counting items in {self.dataset_path}')
        total_items = dataset_format.count_items(self.dataset_path)
        logger.info(f'Dataset contains {total_items} items')

        self.batches = []
        for i in range(0, total_items, self.batch_size):
            start_idx = i
            end_idx = min(i + self.batch_size - 1, total_items - 1)
            self.batches.append([start_idx, end_idx])

        self.pending_batches = collections.deque(range(len(self.batches)))
        logger.info(f'Created {len(self.batches)} batches '
                    f'(total_items: {total_items}, '
                    f'batch_size: {self.batch_size})')

    def _get_dataset_format(self):
        """Detect dataset format and return appropriate handler."""
        from sky.batch.formats.jsonl import (
            JSONLDataset)  # pylint: disable=import-outside-toplevel

        if self.dataset_path.endswith('.jsonl'):
            return JSONLDataset()
        else:
            raise ValueError(f'Unsupported dataset format: '
                             f'{self.dataset_path}. Supported: .jsonl')

    # ------------------------------------------------------------------
    # Worker discovery
    # ------------------------------------------------------------------

    def _discover_workers(self) -> None:
        """Wait for pool workers and populate ``self._workers``."""
        expected = self._get_expected_worker_count()
        workers = self._get_ready_workers()

        if len(workers) < expected:
            logger.info(f'Waiting for all {expected} workers to be ready '
                        f'(currently {len(workers)} ready)...')
            deadline = time.monotonic() + constants.WORKER_DISCOVERY_TIMEOUT

            while (len(workers) < expected and time.monotonic() < deadline):
                time.sleep(5)
                workers = self._get_ready_workers()
                if len(workers) < expected:
                    remaining = int(deadline - time.monotonic())
                    logger.info(f'{len(workers)}/{expected} workers ready '
                                f'(waiting up to {remaining}s more)')

            if len(workers) < expected:
                logger.warning(
                    f'Only {len(workers)}/{expected} workers ready after '
                    f'waiting {constants.WORKER_DISCOVERY_TIMEOUT}s. '
                    'Proceeding with available workers.')

        if not workers:
            raise RuntimeError(
                f'No ready workers found in pool {self.pool_name} '
                f'after waiting {constants.WORKER_DISCOVERY_TIMEOUT}s')

        self._workers = workers
        logger.info(f'Starting with {len(workers)} workers '
                    f'({expected} expected)')

    def _fetch_pool_status(self) -> Optional[Dict[str, Any]]:
        """Fetch pool status via the SDK.

        Returns the first matching pool record dict, or None.
        """
        try:
            request_id = sky.jobs.pool_status([self.pool_name])
            pool_statuses = sdk.stream_and_get(request_id)
            if pool_statuses:
                return pool_statuses[0]
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to fetch pool status: {e}')
        return None

    def _get_expected_worker_count(self) -> int:
        """Get expected worker count from pool configuration."""
        status = self._fetch_pool_status()
        if status is None:
            logger.warning(f'Could not find pool {self.pool_name}, '
                           'defaulting to 1 worker')
            return 1
        target = status.get('target_num_replicas')
        if target is None:
            logger.warning('target_num_replicas not available, '
                           'defaulting to 1 worker')
            return 1
        logger.info(f'Pool {self.pool_name} expects {target} workers')
        return max(1, int(target))

    def _get_ready_workers(self) -> List[str]:
        """Return cluster names for ready replicas via SDK."""
        status = self._fetch_pool_status()
        if status is None:
            return []
        replica_infos = status.get('replica_info', [])
        ready = []
        for info in replica_infos:
            replica_status = str(info.get('status', ''))
            if 'READY' in replica_status:
                name = info.get('name')
                if name:
                    ready.append(name)
        return ready

    # ------------------------------------------------------------------
    # Pool resource detection
    # ------------------------------------------------------------------

    def _get_pool_resources(self) -> Optional['sky.Resources']:
        """Return the ``sky.Resources`` for pool workers."""
        status = self._fetch_pool_status()
        if status is None:
            return None
        yaml_content = status.get('pool_yaml') or status.get('yaml_content')
        if not yaml_content:
            return None
        try:
            task = sky.Task.from_yaml_str(str(yaml_content))
            for r in task.resources:
                return r
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to parse pool resources: %s', e)
        return None

    # ------------------------------------------------------------------
    # Worker code generation
    # ------------------------------------------------------------------

    def _generate_worker_startup_code(self) -> str:
        """Generate code to start the long-running worker service."""
        job_id = str(self._managed_job_id)
        activate = self.activate_env.strip()
        activate_line = f'{activate} &&' if activate else ''
        sky_runtime = skylet_constants.SKY_REMOTE_PYTHON_ENV

        return textwrap.dedent(f"""\
            set -e
            export SKY_BATCH_SERIALIZED_FN='{self.serialized_fn}'
            export SKY_BATCH_OUTPUT_PATH='{self.output_path}'
            export SKY_BATCH_JOB_ID='{job_id}'
            export SKY_BATCH_DATASET_PATH='{self.dataset_path}'

            # Make sky.batch visible to the user's python.
            SKY_SITE=$({sky_runtime}/bin/python -c \\
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

    def _generate_notify_code(self, batch_idx: int) -> str:
        """Generate lightweight notify script for a single batch."""
        start_idx, end_idx = self.batches[batch_idx]
        port = constants.WORKER_SERVICE_PORT

        return textwrap.dedent(f"""\
            set -e
            curl -sf -X POST http://127.0.0.1:{port}/feed_batch \\
                -H 'Content-Type: application/json' \\
                -d '{{"dataset_path": "{self.dataset_path}", "start_idx": {start_idx}, "end_idx": {end_idx}, "batch_idx": {batch_idx}}}'
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

    def _launch_worker_service(self, cluster_name: str) -> int:
        """Launch worker service as a long-running SkyPilot job.

        Returns:
            The SkyPilot job ID of the worker service.
        """
        job_id = str(self._managed_job_id)
        startup_code = self._generate_worker_startup_code()
        task = sky.Task(name=f'batch-worker-{job_id}', run=startup_code)
        pool_resources = self._get_pool_resources()
        if pool_resources is not None:
            task.set_resources(pool_resources)
        logger.info(f'Submitting exec to {cluster_name} '
                    f'with resources={pool_resources}')
        try:
            request_id = sdk.exec(task, cluster_name=cluster_name)
        except Exception as e:
            logger.error(f'sdk.exec() failed: {e}', exc_info=True)
            raise
        try:
            worker_job_id, _ = sdk.get(request_id)
        except Exception as e:
            logger.error(f'sdk.get() for exec failed: {e}', exc_info=True)
            raise
        assert worker_job_id is not None, 'Failed to get worker job ID'

        logger.info(f'Launched worker service as job '
                    f'{worker_job_id} on {cluster_name}')

        # Wait for worker to be ready
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
        health_task = sky.Task(name=f'health-check-{job_id}', run=health_code)
        try:
            req_id = sdk.exec(health_task, cluster_name=cluster_name)
            sdk.get(req_id)
            logger.info(f'Worker service ready on {cluster_name}')
            return worker_job_id
        except Exception as e:  # pylint: disable=broad-except
            raise RuntimeError(
                f'Worker service on {cluster_name} failed to start: '
                f'{e}') from e

    def _shutdown_worker(self,
                         cluster_name: str,
                         worker_job_id: Optional[int] = None) -> None:
        """Send shutdown signal and cancel worker job."""
        shutdown_code = self._generate_shutdown_code()
        task = sky.Task(name=f'batch-shutdown-{cluster_name}',
                        run=shutdown_code)
        try:
            request_id = sdk.exec(task, cluster_name=cluster_name)
            sdk.get(request_id)
            logger.info('Sent shutdown to worker service on %s', cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to send shutdown to %s: %s', cluster_name, e)

        if worker_job_id is not None:
            time.sleep(5)
            try:
                cancel_req_id = sdk.cancel(cluster_name,
                                           job_ids=[worker_job_id])
                sdk.get(cancel_req_id)
                logger.info(f'Cancelled worker job {worker_job_id} on '
                            f'{cluster_name}')
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(f'Failed to cancel worker job '
                               f'{worker_job_id}: {e}')

    # ------------------------------------------------------------------
    # Progress reporting
    # ------------------------------------------------------------------

    def _write_progress(self) -> None:
        """Write batch progress directly to the DB."""
        try:
            managed_job_state.set_batch_progress(self._managed_job_id,
                                                 len(self.batches),
                                                 self.completed_count)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to write progress: {e}')

    # ------------------------------------------------------------------
    # Per-worker dispatch loop (runs in its own thread)
    # ------------------------------------------------------------------

    def _worker_dispatch_loop(self, cluster_name: str) -> None:
        """Dispatch batches to *cluster_name* until the queue is empty.

        1. Launch worker service once as a separate long-running job.
        2. For each batch: submit notify job, poll status.
        3. Shutdown worker service when done.
        """
        job_id = str(self._managed_job_id)
        retry_counts: Dict[int, int] = {}

        worker_job_id = self._launch_worker_service(cluster_name)
        self._active_workers[cluster_name] = worker_job_id

        try:
            while not self._cancelled:
                try:
                    batch_idx = self.pending_batches.popleft()
                except IndexError:
                    return

                retries = retry_counts.get(batch_idx, 0)
                try:
                    notify_code = self._generate_notify_code(batch_idx)
                    task = sky.Task(name=f'batch-notify-{job_id}-{batch_idx}',
                                    run=notify_code)
                    request_id = sdk.exec(task, cluster_name=cluster_name)
                    job_id_on_cluster, _ = sdk.get(request_id)
                    assert job_id_on_cluster is not None

                    logger.info(f'Batch {batch_idx} running as '
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
                                    f'Batch {batch_idx} failed with '
                                    f'status {status.value}')
                            logger.info(f'Batch {batch_idx} SUCCEEDED '
                                        f'on {cluster_name}')
                            break

                    with self._completed_lock:
                        self.completed_count += 1
                    self._write_progress()
                    logger.info(f'Batch {batch_idx} completed '
                                f'({self.completed_count}/'
                                f'{len(self.batches)})')
                except Exception as e:  # pylint: disable=broad-except
                    logger.error(f'Batch {batch_idx} failed on '
                                 f'{cluster_name}: {e}')
                    if retries < constants.MAX_RETRIES:
                        retry_counts[batch_idx] = retries + 1
                        self.pending_batches.append(batch_idx)
                        backoff = (constants.RETRY_BACKOFF_BASE**
                                   retry_counts[batch_idx])
                        logger.info(
                            f'Re-queued batch {batch_idx} '
                            f'(retry {retry_counts[batch_idx]}/'
                            f'{constants.MAX_RETRIES}), backoff {backoff}s')
                        time.sleep(backoff)
                    else:
                        raise RuntimeError(
                            f'Batch {batch_idx} failed after '
                            f'{constants.MAX_RETRIES} retries: {e}') from e
        finally:
            self._shutdown_worker(cluster_name, worker_job_id=worker_job_id)
            self._active_workers.pop(cluster_name, None)

    # ------------------------------------------------------------------
    # Dispatch orchestration
    # ------------------------------------------------------------------

    def _dispatch_all(self) -> None:
        """Launch one dispatch thread per worker and wait."""
        # Write initial progress
        self._write_progress()

        dispatch_threads: List[threading.Thread] = []
        errors: List[Exception] = []

        def _dispatch_wrapper(cname: str) -> None:
            try:
                self._worker_dispatch_loop(cname)
            except Exception as e:  # pylint: disable=broad-except
                errors.append(e)

        for cluster_name in self._workers:
            t = threading.Thread(target=_dispatch_wrapper,
                                 args=(cluster_name,),
                                 daemon=True)
            t.start()
            dispatch_threads.append(t)

        for t in dispatch_threads:
            t.join()

        if errors:
            raise errors[0]

        if self.completed_count != len(self.batches):
            raise RuntimeError(
                f'Expected {len(self.batches)} completed batches, '
                f'got {self.completed_count}')

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    def _merge_results(self) -> None:
        """Merge per-batch results into the final output."""
        job_id = str(self._managed_job_id)
        logger.info('Merging results...')
        self._output_format.merge_results(self.output_path, job_id)
        logger.info(f'Results written to {self.output_path}')

    # ------------------------------------------------------------------
    # Failure cleanup
    # ------------------------------------------------------------------

    def _cleanup_on_failure(self) -> None:
        """Clean up temporary files on failure."""
        job_id = str(self._managed_job_id)
        try:
            logger.info('Cleaning up temporary files...')
            utils.delete_chunk_files(self.output_path, job_id=job_id)
            logger.info('Temporary files cleaned up')
        except Exception as cleanup_error:  # pylint: disable=broad-except
            logger.warning(f'Failed to clean up temporary files: '
                           f'{cleanup_error}')
