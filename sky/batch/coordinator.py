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
import contextvars
import json
import logging
import os
import signal
import sys
import textwrap
import threading
import time
from typing import Any, Deque, Dict, List, Optional

import sky
from sky.batch import constants
from sky.batch import io_formats
from sky.client import sdk
from sky.jobs import state as managed_job_state
from sky.serve import serve_utils
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
                 input_format_dict: Dict[str, Any],
                 output_formats_dict: List[Dict[str, Any]],
                 activate_env: str = '',
                 job_id: Optional[int] = None,
                 is_resume: bool = False):
        self.dataset_path = dataset_path
        self.output_path = output_path
        self.batch_size = batch_size
        self.pool_name = pool_name
        self.serialized_fn = serialized_fn
        self.activate_env = activate_env
        self._is_resume = is_resume

        self._input_format_dict = input_format_dict
        self._output_formats_dict = output_formats_dict

        # Use explicit job_id if provided (inline on controller),
        # otherwise fall back to env var (backward compat).
        if job_id is not None:
            self._managed_job_id: int = job_id
        else:
            env_var = skylet_constants.MANAGED_JOB_ID_ENV_VAR
            raw = os.environ.get(env_var)
            if raw is None:
                raise RuntimeError(f'{env_var} not set. The coordinator must '
                                   'run as a managed job.')
            self._managed_job_id = int(raw)

        # Batch metadata: list of [start_idx, end_idx] tuples.
        self.batches: List[List[int]] = []
        self.pending_batches: Deque[int] = collections.deque()
        self.completed_count: int = 0

        # Retry tracking: batch_idx -> retry count.  Persisted across
        # resume so that a batch cannot be retried indefinitely.
        self._retry_counts: Dict[int, int] = {}

        # Worker tracking: cluster_name → worker_job_id
        self._active_workers: Dict[str, int] = {}
        self._active_workers_lock = threading.Lock()

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
            self._resolve_formats()

            if self._is_resume:
                self._resume_from_db()
            else:
                self._count_and_split()
                if not self.batches:
                    logger.info('No items in dataset — nothing to do.')
                    return
                self._save_batches_to_db()

            if self.completed_count == len(self.batches):
                # Crash happened after all batches done but before merge.
                logger.info('All batches already completed, skipping '
                            'to merge.')
                self._reduce_results_and_cleanup()
                return

            self._discover_workers()
            self._shutdown_stale_workers()
            self._dispatch_all()
            self._reduce_results_and_cleanup()
            logger.info('Batch job completed successfully.')
        except Exception:
            self._print_partial_results_instructions()
            raise

    # ------------------------------------------------------------------
    # SIGTERM handler (sky jobs cancel)
    # ------------------------------------------------------------------

    def _handle_sigterm(self, signum, frame) -> None:  # pylint: disable=unused-argument
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
        with self._active_workers_lock:
            workers_snapshot = list(self._active_workers.items())
        for cluster_name, worker_job_id in workers_snapshot:
            try:
                self._shutdown_worker(cluster_name, worker_job_id)
            except Exception:  # pylint: disable=broad-except
                logger.warning(f'Failed to shutdown worker on {cluster_name}')

    # ------------------------------------------------------------------
    # Dataset counting & splitting
    # ------------------------------------------------------------------

    def _resolve_formats(self) -> None:
        """Resolve typed input/output format handlers from dicts."""
        self._input_format = io_formats.InputReader.from_dict(
            self._input_format_dict)
        self._output_formats = [
            io_formats.OutputWriter.from_dict(d)
            for d in self._output_formats_dict
        ]

    def _count_and_split(self) -> None:
        """Count dataset items and create batch index ranges."""
        logger.info(f'Counting items in {self.dataset_path}')
        total_items = len(self._input_format)
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

    # ------------------------------------------------------------------
    # DB persistence for HA recovery
    # ------------------------------------------------------------------

    def _save_batches_to_db(self) -> None:
        """Write all batch records to DB with PENDING status."""
        managed_job_state.save_batch_states(self._managed_job_id, self.batches)
        logger.info(f'Saved {len(self.batches)} batch records to DB')

    def _resume_from_db(self) -> None:
        """Restore coordinator state from DB after a controller crash.

        Resets any DISPATCHED (in-flight) batches back to PENDING, then
        rebuilds in-memory state from the persisted records.
        """
        managed_job_state.reset_dispatched_batches(self._managed_job_id)
        records = managed_job_state.get_batch_states(self._managed_job_id)
        if not records:
            raise RuntimeError(
                f'No batch records found for job {self._managed_job_id} '
                'during resume. The job may need to be re-submitted.')

        self.batches = []
        self.pending_batches = collections.deque()
        self.completed_count = 0
        self._retry_counts = {}

        for i, rec in enumerate(records):
            batch_idx = rec['batch_idx']
            assert batch_idx == i, (
                f'Batch records not contiguous: expected batch_idx={i}, '
                f'got {batch_idx}. DB may be corrupted.')
            self.batches.append([rec['start_idx'], rec['end_idx']])
            status = rec['status']
            if status == 'PENDING':
                self.pending_batches.append(batch_idx)
            elif status == 'COMPLETED':
                self.completed_count += 1
            # FAILED batches stay failed — not re-queued.
            self._retry_counts[batch_idx] = rec['retry_count']

        logger.info(f'Resumed from DB: {len(self.batches)} batches, '
                    f'{self.completed_count} completed, '
                    f'{len(self.pending_batches)} pending')
        logger.info(f'BATCH_RESUME total={len(self.batches)} '
                    f'completed={self.completed_count} '
                    f'pending={len(self.pending_batches)}')

    def _shutdown_stale_workers(self) -> None:
        """Shut down any stale worker services on discovered workers.

        After a crash, old worker processes may still hold port 8290.
        Send /shutdown to each worker before launching fresh services.
        """
        for cluster_name in self._workers:
            try:
                self._shutdown_worker(cluster_name)
            except Exception:  # pylint: disable=broad-except
                logger.debug(f'No stale worker to shut down on '
                             f'{cluster_name}')

    # ------------------------------------------------------------------
    # Worker discovery
    # ------------------------------------------------------------------

    def _discover_workers(self) -> None:
        """Discover all ready workers in the pool.

        Uses all available workers — no fixed ``target_num_replicas``.
        If no workers are found immediately, waits up to the discovery
        timeout for at least one to appear.
        """
        workers = self._get_ready_workers()

        if not workers:
            logger.info('No workers ready yet, waiting for at least one...')
            deadline = time.monotonic() + constants.WORKER_DISCOVERY_TIMEOUT

            while not workers and time.monotonic() < deadline:
                time.sleep(5)
                workers = self._get_ready_workers()
                if not workers:
                    remaining = int(deadline - time.monotonic())
                    logger.info(f'No workers ready yet '
                                f'(waiting up to {remaining}s more)')

        if not workers:
            raise RuntimeError(
                f'No ready workers found in pool {self.pool_name} '
                f'after waiting {constants.WORKER_DISCOVERY_TIMEOUT}s')

        self._workers = workers
        logger.info(f'Discovered {len(workers)} ready workers')

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

    def _get_ready_workers(self) -> List[str]:
        """Return cluster names for ready replicas from serve state."""
        replicas = serve_utils.get_ready_replicas(self.pool_name)
        return [info.cluster_name for info in replicas]

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

        # Serialize typed format dicts as JSON env vars for workers.
        input_format_json = json.dumps(self._input_format.to_dict()).replace(
            '\'', '\'\\\'\'')
        # Pass output formats as a JSON array for multi-output support.
        output_formats_json = json.dumps(self._output_formats_dict or
                                         []).replace('\'', '\'\\\'\'')

        return textwrap.dedent(f"""\
            set -e
            export SKY_BATCH_SERIALIZED_FN='{self.serialized_fn}'
            export SKY_BATCH_OUTPUT_PATH='{self.output_path}'
            export SKY_BATCH_JOB_ID='{job_id}'
            export SKY_BATCH_INPUT_FORMAT='{input_format_json}'
            export SKY_BATCH_OUTPUT_FORMATS='{output_formats_json}'

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
    # Per-worker dispatch loop (runs in its own thread)
    # ------------------------------------------------------------------

    def _worker_dispatch_loop(self, cluster_name: str) -> None:
        """Dispatch batches to *cluster_name* until the queue is empty.

        1. Launch worker service once as a separate long-running job.
        2. For each batch: submit notify job, poll status.
        3. Shutdown worker service when done.
        """
        job_id = str(self._managed_job_id)

        worker_job_id = self._launch_worker_service(cluster_name)
        with self._active_workers_lock:
            self._active_workers[cluster_name] = worker_job_id

        try:
            while not self._cancelled:
                try:
                    batch_idx = self.pending_batches.popleft()
                except IndexError:
                    return

                retries = self._retry_counts.get(batch_idx, 0)

                # Mark batch as dispatched in DB.
                managed_job_state.set_batch_status(self._managed_job_id,
                                                   batch_idx,
                                                   'DISPATCHED',
                                                   worker_cluster=cluster_name)

                try:
                    notify_code = self._generate_notify_code(batch_idx)
                    task = sky.Task(name=f'batch-notify-{job_id}-{batch_idx}',
                                    run=notify_code)
                    request_id = sdk.exec(task, cluster_name=cluster_name)
                    job_id_on_cluster, _ = sdk.get(request_id)
                    assert job_id_on_cluster is not None

                    logger.info(f'Batch {batch_idx} running as '
                                f'job {job_id_on_cluster} on {cluster_name}')

                    # Poll until terminal.  If the cluster goes away
                    # (e.g. rolling update) we'll get repeated None
                    # statuses — treat that as a failure after a grace
                    # period so the batch can be retried.
                    none_count = 0
                    max_none = 12  # ~60s at 5s poll interval
                    while True:
                        time.sleep(constants.BATCH_POLL_INTERVAL)
                        req_id = sdk.job_status(cluster_name,
                                                [job_id_on_cluster])
                        statuses = sdk.get(req_id)
                        status = statuses.get(job_id_on_cluster)
                        if status is None:
                            none_count += 1
                            if none_count >= max_none:
                                raise RuntimeError(
                                    f'Batch {batch_idx}: lost contact '
                                    f'with {cluster_name} (job status '
                                    f'unavailable for {none_count} '
                                    f'consecutive polls)')
                            continue
                        none_count = 0
                        if status.is_terminal():
                            if status != sky.JobStatus.SUCCEEDED:
                                raise RuntimeError(
                                    f'Batch {batch_idx} failed with '
                                    f'status {status.value}')
                            logger.info(f'Batch {batch_idx} SUCCEEDED '
                                        f'on {cluster_name}')
                            break

                    # Mark batch as completed in DB.
                    managed_job_state.set_batch_status(self._managed_job_id,
                                                       batch_idx, 'COMPLETED')
                    self.completed_count += 1
                    if self.completed_count == len(self.batches):
                        managed_job_state.set_winding_down(self._managed_job_id,
                                                           task_id=0)
                    logger.info(
                        f'Batch {batch_idx} completed on {cluster_name} '
                        f'({self.completed_count}/{len(self.batches)})')
                except Exception as e:  # pylint: disable=broad-except
                    logger.error(f'Batch {batch_idx} failed on '
                                 f'{cluster_name}: {e}')
                    if retries < constants.MAX_RETRIES:
                        self._retry_counts[batch_idx] = retries + 1
                        managed_job_state.set_batch_status(
                            self._managed_job_id,
                            batch_idx,
                            'PENDING',
                            retry_count=self._retry_counts[batch_idx])
                        self.pending_batches.append(batch_idx)
                        backoff = (constants.RETRY_BACKOFF_BASE**
                                   self._retry_counts[batch_idx])
                        logger.info(
                            f'Re-queued batch {batch_idx} '
                            f'(retry {self._retry_counts[batch_idx]}/'
                            f'{constants.MAX_RETRIES}), backoff {backoff}s')
                        time.sleep(backoff)
                    else:
                        managed_job_state.set_batch_status(self._managed_job_id,
                                                           batch_idx,
                                                           'FAILED',
                                                           retry_count=retries +
                                                           1)
                        raise RuntimeError(
                            f'Batch {batch_idx} failed after '
                            f'{constants.MAX_RETRIES} retries: {e}') from e
        finally:
            self._shutdown_worker(cluster_name, worker_job_id=worker_job_id)
            with self._active_workers_lock:
                self._active_workers.pop(cluster_name, None)

    # ------------------------------------------------------------------
    # Dispatch orchestration
    # ------------------------------------------------------------------

    def _dispatch_all(self) -> None:
        """Launch dispatch threads per worker and dynamically add new ones.

        Periodically re-discovers workers so that newly scaled-up pool
        replicas are picked up automatically.  Individual worker thread
        failures are tolerated as long as other workers can pick up the
        remaining batches.
        """
        active_threads: Dict[str, threading.Thread] = {}
        errors: List[Exception] = []

        def _dispatch_wrapper(cname: str) -> None:
            try:
                self._worker_dispatch_loop(cname)
            except Exception as e:  # pylint: disable=broad-except
                logger.info(f'Worker thread for {cname} failed: {e}')
                errors.append(e)

        def _start_worker_thread(cluster_name: str) -> None:
            # Each thread needs its own context copy so that the log
            # redirect set up by the jobs controller is inherited.
            # contextvars.Context.run() is not re-entrant, so each
            # thread must use a separate copy.
            thread_ctx = contextvars.copy_context()
            t = threading.Thread(target=thread_ctx.run,
                                 args=(_dispatch_wrapper, cluster_name),
                                 daemon=True)
            t.start()
            active_threads[cluster_name] = t

        # Start initial workers.
        for cluster_name in self._workers:
            _start_worker_thread(cluster_name)

        # Monitor until all batches complete, periodically discovering
        # new workers and spawning threads for them.
        while not self._cancelled:
            if self.completed_count >= len(self.batches):
                break

            alive = any(t.is_alive() for t in active_threads.values())

            if not alive and not self.pending_batches:
                # No threads running and no pending work — done.
                break

            # Re-discover workers and start threads for idle ones.
            started_new = False
            try:
                current_workers = self._get_ready_workers()
                for w in current_workers:
                    already_active = (w in active_threads and
                                      active_threads[w].is_alive())
                    if not already_active and self.pending_batches:
                        logger.info(f'Discovered new/idle worker: {w}')
                        try:
                            self._shutdown_worker(w)
                        except Exception:  # pylint: disable=broad-except
                            pass
                        _start_worker_thread(w)
                        started_new = True
            except Exception:  # pylint: disable=broad-except
                pass

            # If all threads are dead, work remains, and we couldn't
            # start any new threads, there's nothing more we can do.
            if not alive and self.pending_batches and not started_new:
                break

            time.sleep(10)

        # Wait for remaining threads to finish.
        for t in active_threads.values():
            t.join(timeout=60)

        if self.completed_count != len(self.batches):
            if errors:
                raise errors[0]
            raise RuntimeError(
                f'Expected {len(self.batches)} completed batches, '
                f'got {self.completed_count}')

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    def _reduce_results_and_cleanup(self) -> None:
        """Reduce per-batch results into the final output and clean up."""
        job_id = str(self._managed_job_id)
        logger.info('Reducing results...')
        for fmt in self._output_formats:
            logger.info(f'Handling output format: {type(fmt).__name__}')
            fmt.reduce_results(job_id)
            logger.info(f'Results written to {fmt.path}')
            fmt.cleanup(job_id)
            logger.info(f'Cleaned up temp files for {fmt.path}')

    # ------------------------------------------------------------------
    # Partial results recovery
    # ------------------------------------------------------------------

    def _print_partial_results_instructions(self) -> None:
        """Print instructions for recovering partial results on failure."""
        output_formats = getattr(self, '_output_formats', [])
        if not output_formats:
            return
        job_id = str(self._managed_job_id)
        logger.info(
            '\n'
            '============================================================\n'
            'Partial results are preserved. To merge completed batches\n'
            'into the final output, run:\n'
            '\n'
            '    import sky.batch\n'
            '\n')
        for fmt in output_formats:
            fmt_name = type(fmt).__name__
            fields = ', '.join(f'{k}={v!r}' for k, v in fmt.to_dict().items()
                               if k not in ('format', '_class_source'))
            logger.info(f'    writer = sky.batch.{fmt_name}({fields})\n'
                        f'    writer.reduce_results({job_id!r})\n'
                        f'    writer.cleanup({job_id!r})\n')
        logger.info(
            '============================================================')
