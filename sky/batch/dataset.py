"""Dataset class for Sky Batch.

Provides a simple interface for loading JSONL data from cloud storage
and distributing workloads across a pool of workers via managed jobs.
"""
import logging
import time
from typing import Callable, Optional
import uuid

import tqdm

import sky
from sky.batch import remote
from sky.batch import utils
from sky.batch.formats.io_formats import InputFormat
from sky.batch.formats.io_formats import OutputFormat
from sky.client import sdk
from sky.jobs import state as managed_job_state

logger = logging.getLogger(__name__)


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
    """Poll managed job status and report progress with a tqdm bar."""
    poll_interval = 2.0
    pbar = None

    try:
        while True:
            try:
                record = _get_managed_job_record(managed_job_id)
            except Exception as e:  # pylint: disable=broad-except
                # Transient API errors (e.g. request purged before read).
                # Log and retry on the next poll cycle.
                logger.debug('Transient error polling job %s: %s',
                             managed_job_id, e)
                time.sleep(poll_interval)
                continue
            status = record.status
            if status is None:
                raise RuntimeError(
                    f'Managed job {managed_job_id} has no status')

            completed = record.batch_completed_batches or 0
            total = record.batch_total_batches or 0

            if status.is_terminal():
                # Ensure the bar reaches 100% on success before closing.
                if pbar is not None and total > 0:
                    pbar.n = total
                    pbar.refresh()
                if status == managed_job_state.ManagedJobStatus.SUCCEEDED:
                    return
                raise RuntimeError(f'Managed job {managed_job_id} '
                                   f'failed with status: {status.value}')

            # Create the progress bar once we know the total.
            if pbar is None and total > 0:
                pbar = tqdm.tqdm(
                    total=total,
                    initial=completed,
                    desc=f'Job {managed_job_id}',
                    unit='batch',
                    unit_scale=False,
                    dynamic_ncols=True,
                )
            elif pbar is not None:
                if total != pbar.total:
                    pbar.total = total
                    pbar.refresh()
                if completed > pbar.n:
                    pbar.update(completed - pbar.n)

            # While waiting for total to appear, show status.
            if pbar is None and total == 0:
                logger.info('Job %s: %s (waiting for batches...)',
                            managed_job_id, status.value)

            time.sleep(poll_interval)
    finally:
        if pbar is not None:
            pbar.close()


class Dataset:
    """A dataset backed by a typed input format in cloud storage.

    This class provides an interface for batch processing of data stored
    in cloud storage. It supports distributing workloads across a pool
    of workers using the map() method.

    Attributes:
        path: Cloud storage path to the dataset.
        input_format: The typed input format descriptor.
    """

    def __init__(self, input_format: InputFormat):
        """Initialize a Dataset from a typed input format.

        Args:
            input_format: An ``InputFormat`` descriptor (e.g.
                          ``JsonInput('s3://bucket/data.jsonl')``).
        """
        self.input_format = input_format
        self.path = input_format.path

    def map(self,
            mapper_fn: Callable,
            pool_name: str,
            batch_size: int,
            output: OutputFormat,
            activate_env: Optional[str] = None) -> int:
        """Submit batch job as a managed job. Blocks until completion.

        The mapper function should be decorated with @sky.batch.remote_function
        and use sky.batch.load() and sky.batch.save_results() inside.

        Args:
            mapper_fn: Function containing the processing logic. Must be
                       decorated with @sky.batch.remote_function.
            pool_name: Name of the worker pool to use.
            batch_size: Number of items per batch sent to each worker.
            output: An ``OutputFormat`` descriptor (e.g.
                    ``JsonOutput('s3://bucket/out.jsonl')`` or
                    ``ImageOutput('s3://bucket/images/', column='image')``).
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

        output_path = output.path
        if not output_path:
            raise ValueError('output path cannot be empty')

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

        # The coordinator runs inline on the jobs controller (no
        # separate cluster).  Pass all config via task metadata.
        task = sky.Task(name=task_name, run=None)
        # pylint: disable=protected-access
        task._metadata = {
            'batch_coordinator': True,
            'batch_dataset_path': self.path,
            'batch_output_path': output_path,
            'batch_size': batch_size,
            'batch_pool_name': pool_name,
            'batch_serialized_fn': serialized_fn,
            'batch_activate_env': activate_env or '',
            'batch_input_format': self.input_format.to_dict(),
            'batch_output_format': output.to_dict(),
        }

        # Submit as regular managed job.  Pass pool_name so the job
        # shows up under the correct pool in ``sky jobs queue`` and
        # pool workers display the job in their USED_BY column.
        request_id = sky.jobs.launch(task, pool=pool_name)
        result = sdk.stream_and_get(request_id)
        job_ids, _ = result
        if not job_ids:
            raise RuntimeError('Failed to launch batch managed job')
        managed_job_id = job_ids[0]

        logger.info(f'Batch job submitted as managed job {managed_job_id}')
        _wait_for_managed_job_completion(managed_job_id)
        return managed_job_id

    def __repr__(self) -> str:
        return f'Dataset(input_format={self.input_format!r})'


def dataset(input_format: InputFormat) -> Dataset:
    """Create a Dataset from a typed input format.

    This is the main entry point for creating datasets in Sky Batch.

    Args:
        input_format: An ``InputFormat`` descriptor, e.g.:
            - ``sky.JsonInput('s3://bucket/data.jsonl')``

    Returns:
        A Dataset instance.

    Example:
        import sky

        ds = sky.dataset(sky.batch.JsonInput("s3://my-bucket/prompts.jsonl"))

        @sky.batch.remote_function
        def process():
            for batch in sky.batch.load():
                results = [{"output": item["text"] * 2} for item in batch]
                sky.batch.save_results(results)

        pool_name = sky.jobs.pool_apply("pool.yaml")
        ds.map(process, pool_name=pool_name, batch_size=32,
               output=sky.batch.JsonOutput("s3://my-bucket/output.jsonl"))
    """
    return Dataset(input_format)
