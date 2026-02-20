"""Dataset class for Sky Batch.

Provides a simple interface for loading JSONL data from cloud storage
and distributing workloads across a pool of workers via managed jobs.
"""
import datetime
import logging
import textwrap
import time
from typing import Callable, Optional
import uuid

import sky
from sky.batch import remote
from sky.batch import utils
from sky.client import sdk
from sky.jobs import state as managed_job_state
from sky.server import common as server_common

logger = logging.getLogger(__name__)

# Addresses that only resolve to the local machine and are
# unreachable from a remote cluster / K8s pod.
_LOCAL_HOSTS = ('127.0.0.1', 'localhost', '0.0.0.0')


def _get_coordinator_api_endpoint() -> str:
    """Return an API server URL reachable from the coordinator cluster.

    When the API server is local (localhost / 0.0.0.0), remote pods
    cannot reach it directly.  We try ``host.docker.internal`` which
    Docker Desktop (macOS / Windows) and kind map to the host.  For
    remote API servers the URL is returned as-is.
    """
    # pylint: disable=import-outside-toplevel
    from urllib.parse import urlparse
    from urllib.parse import urlunparse

    endpoint = server_common.get_server_url()
    parsed = urlparse(endpoint)
    if parsed.hostname in _LOCAL_HOSTS:
        # Replace the hostname but keep scheme, port, path, etc.
        host_port = f'host.docker.internal:{parsed.port or 46580}'
        replaced = parsed._replace(netloc=host_port)
        return urlunparse(replaced)
    return endpoint


def _get_managed_job_record(managed_job_id: int):
    """Get the current record of a managed job."""
    request_id = sky.jobs.queue_v2(
        refresh=False,
        job_ids=[managed_job_id],
        fields=['status', 'batch_total_batches', 'batch_completed_batches'])
    records, _, _, _ = sdk.stream_and_get(request_id)
    if not records:
        raise RuntimeError(f'Managed job {managed_job_id} not found')
    return records[0]


def _wait_for_managed_job_completion(managed_job_id: int) -> None:
    """Poll managed job status and report progress from the DB."""
    start_time = time.time()
    last_completed = 0
    last_message_time = time.time()
    poll_interval = 2.0

    def _timestamp() -> str:
        elapsed = int(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        now = datetime.datetime.now().strftime('%H:%M:%S')
        return f'[{now} +{mins:02d}:{secs:02d}]'

    while True:
        try:
            record = _get_managed_job_record(managed_job_id)
        except Exception as e:
            # Transient API errors (e.g. request purged before read).
            # Log and retry on the next poll cycle.
            logger.debug(f'Transient error polling job {managed_job_id}: {e}')
            time.sleep(poll_interval)
            continue
        status = record.status
        if status is None:
            raise RuntimeError(f'Managed job {managed_job_id} has no status')

        if status.is_terminal():
            if status == managed_job_state.ManagedJobStatus.SUCCEEDED:
                elapsed = time.time() - start_time
                logger.info(f'{_timestamp()} Managed job '
                            f'{managed_job_id} completed successfully '
                            f'in {elapsed:.1f}s')
                return
            raise RuntimeError(f'{_timestamp()} Managed job {managed_job_id} '
                               f'failed with status: {status.value}')

        # Read progress from DB (pushed by coordinator via API).
        completed = record.batch_completed_batches or 0
        total = record.batch_total_batches or 0

        if completed > last_completed:
            percent = (completed / total * 100) if total > 0 else 0
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            eta = (total - completed) / rate if rate > 0 else 0
            logger.info(f'{_timestamp()} Job {managed_job_id}: '
                        f'{completed}/{total} batches ({percent:.1f}%) | '
                        f'Rate: {rate:.2f} batches/s | ETA: {eta:.0f}s')
            last_completed = completed
            last_message_time = time.time()
        elif time.time() - last_message_time >= 10:
            if total > 0:
                logger.info(f'{_timestamp()} Job {managed_job_id}: '
                            f'{status.value} ({completed}/{total} batches)')
            else:
                logger.info(f'{_timestamp()} Job {managed_job_id}: '
                            f'{status.value}')
            last_message_time = time.time()

        time.sleep(poll_interval)


class Dataset:
    """A dataset backed by a JSONL file in cloud storage.

    This class provides an interface for batch processing of data stored
    in cloud storage. It supports distributing workloads across a pool
    of workers using the map() method.

    Attributes:
        path: Cloud storage path to the JSONL file.
    """

    def __init__(self, path: str):
        """Initialize a Dataset from a cloud storage path.

        Args:
            path: Cloud storage path to the JSONL file.
                  Supported formats: s3://, gs://, r2://

        Raises:
            ValueError: If the path format is invalid or unsupported.
        """
        self.path = path
        self._validate_path()

    def _validate_path(self) -> None:
        """Validate the cloud storage path format.

        Raises:
            ValueError: If the path format is invalid.
        """
        if not self.path:
            raise ValueError('Dataset path cannot be empty')

        supported_prefixes = ('s3://', 'gs://', 'r2://')
        if not self.path.startswith(supported_prefixes):
            raise ValueError(
                f'Unsupported storage path: {self.path}. '
                f'Supported prefixes: {", ".join(supported_prefixes)}')

        if not self.path.endswith('.jsonl'):
            raise ValueError(
                f'Dataset must be a JSONL file (ending with .jsonl): '
                f'{self.path}')

    def map(self,
            mapper_fn: Callable,
            pool_name: str,
            batch_size: int,
            output_path: str,
            activate_env: Optional[str] = None) -> int:
        """Submit batch job as a managed job. Blocks until completion.

        The mapper function should be decorated with @sky.batch.remote_function
        and use sky.batch.load() and sky.batch.save_results() inside.

        Args:
            mapper_fn: Function containing the processing logic. Must be
                       decorated with @sky.batch.remote_function.
            pool_name: Name of the worker pool to use.
            batch_size: Number of items per batch sent to each worker.
            output_path: Cloud storage path for output results.
            activate_env: Optional shell command to activate the Python
                          environment before running the mapper function.
                          Example: ``'source .venv/bin/activate'``

        Returns:
            The managed job ID.

        Raises:
            ValueError: If mapper_fn is not a remote function or if
                        parameters are invalid.
            RuntimeError: If the batch job fails.
        """
        # Validate mapper function
        if not remote.is_remote_function(mapper_fn):
            raise ValueError('Mapper function must be decorated with '
                             '@sky.batch.remote_function')

        if batch_size <= 0:
            raise ValueError(f'batch_size must be positive, got: {batch_size}')

        if not output_path:
            raise ValueError('output_path cannot be empty')

        # Check if output file already exists and confirm override
        if utils.cloud_path_exists(output_path):
            response = input(
                f'\nOutput file {output_path} already exists.\n'
                f'Do you want to overwrite it? [y/N]: ').strip().lower()
            if response not in ('y', 'yes'):
                raise RuntimeError(f'Output file {output_path} already exists. '
                                   f'Operation cancelled by user.')
            logger.info(f'Overwriting existing output file: {output_path}')

        # Short random suffix for unique task name.
        short_id = uuid.uuid4().hex[:4]
        task_name = f'sky-batch-{short_id}'

        serialized_fn = utils.serialize_function(mapper_fn)

        # Build inline run script with all config hardcoded.
        # Use the SkyPilot runtime Python env (which has sky installed).
        # Write a temp .py file to avoid shell quoting issues and
        # ensure the exit code propagates correctly.
        # pylint: disable=import-outside-toplevel
        from sky.skylet import constants as skylet_constants
        activate_sky = skylet_constants.ACTIVATE_SKY_REMOTE_PYTHON_ENV

        # The coordinator needs to reach the user's API server to
        # discover pool workers and dispatch batches via sky.exec().
        # Pass the endpoint so the remote cluster doesn't start its
        # own (empty) API server.
        api_server_endpoint = _get_coordinator_api_endpoint()
        env_var_name = skylet_constants.SKY_API_SERVER_URL_ENV_VAR

        run_script = textwrap.dedent(f"""\
            set -e
            {activate_sky}
            export {env_var_name}={api_server_endpoint}
            cat > /tmp/sky_batch_coordinator.py << 'SKY_BATCH_EOF'
            from sky.batch.coordinator import BatchCoordinator
            BatchCoordinator(
                dataset_path={self.path!r},
                output_path={output_path!r},
                batch_size={batch_size},
                pool_name={pool_name!r},
                serialized_fn={serialized_fn!r},
                activate_env={(activate_env or "")!r},
            ).run()
            SKY_BATCH_EOF
            python /tmp/sky_batch_coordinator.py
        """)

        # Install cloud storage dependencies in the SkyPilot runtime
        # env so the coordinator can access cloud storage.
        setup_script = textwrap.dedent(f"""\
            {activate_sky}
            pip install boto3 google-cloud-storage 2>/dev/null || true
        """)

        task = sky.Task(name=task_name, setup=setup_script, run=run_script)
        task.set_resources(sky.Resources(cpus='2+'))

        # Submit as regular managed job
        request_id = sky.jobs.launch(task)
        result = sdk.stream_and_get(request_id)
        job_ids, _ = result
        if not job_ids:
            raise RuntimeError('Failed to launch batch managed job')
        managed_job_id = job_ids[0]

        logger.info(f'Batch job submitted as managed job {managed_job_id}')
        _wait_for_managed_job_completion(managed_job_id)
        return managed_job_id

    def __repr__(self) -> str:
        return f'Dataset(path={self.path!r})'


def dataset(path: str) -> Dataset:
    """Create a Dataset from a cloud storage path.

    This is the main entry point for creating datasets in Sky Batch.

    Args:
        path: Cloud storage path to the JSONL file.
              Supported formats:
              - s3://bucket/path/file.jsonl (AWS S3)
              - gs://bucket/path/file.jsonl (Google Cloud Storage)
              - r2://bucket/path/file.jsonl (Cloudflare R2)

    Returns:
        A Dataset instance.

    Example:
        import sky

        ds = sky.dataset("s3://my-bucket/prompts.jsonl")

        @sky.batch.remote_function
        def process():
            for batch in sky.batch.load():
                results = [{"output": item["text"] * 2} for item in batch]
                sky.batch.save_results(results)

        pool_name = sky.jobs.pool_apply("pool.yaml")
        ds.map(process, pool_name=pool_name, batch_size=32,
               output_path="s3://my-bucket/output.jsonl")
    """
    return Dataset(path)
