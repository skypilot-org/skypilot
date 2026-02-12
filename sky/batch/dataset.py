"""Dataset class for Sky Batch.

Provides a simple interface for loading JSONL data from cloud storage
and distributing workloads across a pool of workers.
"""
import datetime
import logging
import time
from typing import Callable, Optional
import uuid

import requests

from sky.batch import constants
from sky.batch import remote
from sky.batch import utils
import sky.client.sdk as sdk
import sky.jobs

logger = logging.getLogger(__name__)


def _get_batch_controller_url(pool_name: str) -> str:
    """Get the public batch controller URL for *pool_name*.

    Queries pool status to find the endpoint (stored in the
    ``load_balancer_port`` field, resolved to a public address by
    ``sky/serve/server/impl.py``).
    """
    request_id = sky.jobs.pool_status([pool_name])
    pool_statuses = sdk.stream_and_get(request_id)
    if not pool_statuses:
        raise RuntimeError(f'Pool {pool_name!r} not found. '
                           f'Create it first with sky jobs pool apply.')

    pool_record = pool_statuses[0]
    endpoint = pool_record.get('endpoint')
    if endpoint:
        return endpoint

    # Fallback: try to construct from load_balancer_port (for local testing)
    lb_port = pool_record.get('load_balancer_port')
    if lb_port is not None:
        return f'http://localhost:{lb_port}'

    raise RuntimeError(
        f'Could not determine batch controller URL for pool {pool_name!r}. '
        'Ensure the pool is up and running.')


def _wait_for_job_completion(controller_url: str,
                             job_id: str,
                             poll_interval: float = 2.0) -> None:
    """Poll the batch controller until the job is complete."""
    status_url = (f'{controller_url}{constants.ENDPOINT_JOB_STATUS}'
                  f'?job_id={job_id}')

    start_time = time.time()
    last_completed = 0
    last_message_time = time.time()

    def _timestamp() -> str:
        """Return formatted timestamp with elapsed time."""
        elapsed = int(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        now = datetime.datetime.now().strftime('%H:%M:%S')
        return f'[{now} +{mins:02d}:{secs:02d}]'

    while True:
        try:
            resp = requests.get(status_url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.warning(f'{_timestamp()} Failed to poll job status: {e}')
            time.sleep(poll_interval)
            continue

        status = data.get('status', 'unknown')
        completed = data.get('completed', 0)
        total = data.get('total_batches', 0)
        workers_ready = data.get('workers_ready', 0)
        workers_expected = data.get('workers_expected', 0)

        if status == 'completed':
            elapsed = time.time() - start_time
            logger.info(f'{_timestamp()} Job {job_id} completed successfully! '
                        f'Processed {total} batches in {elapsed:.1f}s')
            return

        if status == 'failed':
            error = data.get('error', 'unknown error')
            raise RuntimeError(f'{_timestamp()} Job {job_id} failed: {error}')

        # Determine phase and show informative message
        if status == 'pending':
            if total == 0:
                # Loading dataset and creating batches
                logger.info(f'{_timestamp()} Job {job_id}: Loading dataset and '
                            f'preparing batches...')
            else:
                # Batches created, waiting for workers to be ready
                if workers_expected > 0:
                    logger.info(f'{_timestamp()} Job {job_id}: Waiting for '
                                f'workers to be ready ({workers_ready}/'
                                f'{workers_expected} ready)...')
                else:
                    logger.info(f'{_timestamp()} Job {job_id}: Waiting for '
                                f'workers to be ready...')
        elif status == 'running':
            if completed == 0:
                # Workers are ready, launching services
                # and processing first batch.
                if time.time() - last_message_time >= 10:  # Log every 10s
                    logger.info(
                        f'{_timestamp()} Job {job_id}: Launching worker '
                        f'service and processing first batch... '
                        f'(0/{total} batches complete)')
                    last_message_time = time.time()
            elif completed > last_completed:
                # Progress made - show it immediately
                percent = (completed / total * 100) if total > 0 else 0
                rate = completed / (time.time() - start_time)
                eta = (total - completed) / rate if rate > 0 else 0
                logger.info(
                    f'{_timestamp()} Job {job_id}: Processing batches... '
                    f'{completed}/{total} complete ({percent:.1f}%) | '
                    f'Rate: {rate:.2f} batches/s | ETA: {eta:.0f}s')
                last_completed = completed
            else:
                # No progress, but still running - log less frequently
                if time.time() - last_message_time >= 10:  # Log every 10s
                    logger.info(
                        f'{_timestamp()} Job {job_id}: Processing batch... '
                        f'({completed}/{total} complete)')
                    last_message_time = time.time()
        elif status == 'merging':
            logger.info(f'{_timestamp()} Job {job_id}: Merging output results '
                        f'from {total} batches...')
        else:
            logger.info(f'{_timestamp()} Job {job_id}: {status} '
                        f'({completed}/{total} batches complete)')

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
            job_id: Optional[str] = None) -> None:
        """Distribute workload across pool workers using the mapper function.

        Submits the job to the pool's BatchController via HTTP and polls
        for completion.

        The mapper function should be decorated with @sky.batch.remote_function
        and use sky.batch.load() and sky.batch.save_results() inside.

        Args:
            mapper_fn: Function containing the processing logic. Must be
                       decorated with @sky.batch.remote_function.
            pool_name: Name of the worker pool to use.
            batch_size: Number of items per batch sent to each worker.
            output_path: Cloud storage path for output results.
            job_id: Optional job ID for tracking. If not provided, a UUID
                    will be generated.

        Raises:
            ValueError: If mapper_fn is not a remote function or if
                        parameters are invalid.
        """
        # Validate mapper function
        if not remote.is_remote_function(mapper_fn):
            raise ValueError('Mapper function must be decorated with '
                             '@sky.batch.remote_function')

        # Validate batch_size
        if batch_size <= 0:
            raise ValueError(f'batch_size must be positive, got: {batch_size}')

        # Validate output_path
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

        # Generate job ID if not provided
        if job_id is None:
            job_id = str(uuid.uuid4())[:8]

        # Serialize the mapper function
        serialized_fn = utils.serialize_function(mapper_fn)

        # Get the batch controller URL for this pool.
        controller_url = _get_batch_controller_url(pool_name)
        logger.info(f'Submitting job {job_id} to batch controller at '
                    f'{controller_url}')

        # Submit job via HTTP.
        submit_url = f'{controller_url}{constants.ENDPOINT_SUBMIT_JOB}'
        payload = {
            'job_id': job_id,
            'dataset_path': self.path,
            'batch_size': batch_size,
            'output_path': output_path,
            'serialized_fn': serialized_fn,
        }
        resp = requests.post(submit_url, json=payload, timeout=60)
        resp.raise_for_status()
        logger.info(f'Job {job_id} submitted successfully.')

        # Poll for completion.
        _wait_for_job_completion(controller_url, job_id)

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
