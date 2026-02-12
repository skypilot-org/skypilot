"""Worker-side APIs for Sky Batch.

This module provides the APIs used inside mapper functions on workers:

- ``load()``: Blocking generator that continuously yields batches as they
  arrive from the controller via the worker service.
- ``save_results()``: Uploads results and signals the current batch as
  complete, causing the ``sky.exec()`` notify job to exit with SUCCEEDED.

The worker service (``worker.py``) must be running before these APIs are
called.  ``start_worker()`` handles that automatically.
"""
import logging
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# Worker state set by worker.start_worker() before the mapper runs.
_output_path: Optional[str] = None
_job_id: Optional[str] = None


def set_worker_state(output_path: str, job_id: str) -> None:
    """Install worker state used by ``save_results()``.

    Called once by ``worker.start_worker()`` before the mapper function
    is invoked.
    """
    global _output_path, _job_id
    _output_path = output_path
    _job_id = job_id


def load() -> Iterator[List[Dict[str, Any]]]:
    """Blocking generator that yields batches as they arrive.

    Each iteration blocks until the controller pushes a new batch via
    the worker service's ``/feed_batch`` endpoint.  The generator stops
    when a shutdown signal is received.

    After each ``yield``, the caller **must** call ``save_results()``
    before the next iteration.  Failing to do so raises ``RuntimeError``.

    Yields:
        List of dictionaries representing the current batch.

    Example::

        @sky.batch.remote_function
        def process():
            model = load_expensive_model()   # runs once
            for batch in sky.batch.load():
                results = [model.predict(item) for item in batch]
                sky.batch.save_results(results)
    """
    from sky.batch import worker

    while True:
        batch_item = worker.get_next_batch()
        if batch_item is None:
            # Shutdown signal received — stop iterating.
            return

        try:
            yield batch_item.data
        except GeneratorExit:
            # Mapper broke out of the loop or was garbage-collected.
            worker.signal_batch_done(error='Mapper stopped iterating')
            return

        # After yield: the user's loop body has executed.  Verify that
        # save_results() was called (which sets done_event).
        if not batch_item.done_event.is_set():
            error_msg = ('save_results() must be called after processing '
                         'each batch. Did you forget to call '
                         'sky.batch.save_results()?')
            worker.signal_batch_done(error=error_msg)
            raise RuntimeError(error_msg)


def save_results(results: List[Dict[str, Any]]) -> None:
    """Save results for the current batch.

    Uploads the result chunk to cloud storage and signals the worker
    service that this batch is complete.  This causes the ``sky.exec()``
    notify job on the worker to exit with SUCCEEDED, which the controller
    detects via ``sdk.job_status()`` polling.

    Must be called exactly once per batch yielded by ``load()``.

    Args:
        results: List of result dictionaries, one per input item.
                 Order must match the input batch order.

    Raises:
        RuntimeError: If no batch is currently in progress.
        ValueError: If results length doesn't match batch length.
    """
    from sky.batch import worker

    with worker._current_batch_lock:
        batch_item = worker._current_batch
    if batch_item is None:
        raise RuntimeError(
            'save_results() called without a current batch. '
            'Make sure to call it inside the loop over sky.batch.load().')

    if len(results) != len(batch_item.data):
        raise ValueError(
            f'Results length ({len(results)}) does not match batch length '
            f'({len(batch_item.data)}). Results must have one entry per '
            'input item.')

    # Upload results using format-specific logic
    dataset_format = worker._dataset_format
    assert dataset_format is not None, 'Worker not initialized'
    chunk_path = dataset_format.upload_chunk(results, _output_path,
                                             batch_item.batch_idx,
                                             batch_item.start_idx,
                                             batch_item.end_idx, _job_id)
    logger.info('Saved results to %s', chunk_path)

    # Signal completion — unblocks the HTTP handler in worker.py.
    worker.signal_batch_done()
