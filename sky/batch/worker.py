"""Long-running worker service for Sky Batch.

Each worker node runs a persistent background process that:
1. Starts a localhost HTTP server for batch-feeding IPC.
2. Deserializes and runs mapper function once (expensive setup is amortized).
3. ``sky.batch.load()`` blocks on an internal queue, yielding batches as they
   arrive via ``POST /feed_batch`` from ``sky.exec()`` notify scripts.
4. ``sky.batch.save_results()`` signals batch completion, causing the
   ``sky.exec()`` job to exit with SUCCEEDED.
5. ``POST /shutdown`` causes ``load()`` to stop iterating and the mapper to
   return naturally.
"""
import http.server as http_server
import json
import logging
import os
import queue
import threading
from typing import Any, Dict, Iterator, List, Optional

from sky.batch import constants
from sky.batch import io_formats
from sky.batch import utils

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Batch item & shutdown sentinel
# ---------------------------------------------------------------------------


class _BatchItem:
    """A single batch to be processed by the mapper."""

    def __init__(self, data: List[Dict[str, Any]], start_idx: int, end_idx: int,
                 batch_idx: int):
        self.data = data
        self.start_idx = start_idx
        self.end_idx = end_idx
        self.batch_idx = batch_idx
        self.done_event = threading.Event()
        self.error: Optional[str] = None


class _Shutdown:
    """Sentinel placed on the queue to tell ``load()`` to stop."""


# ---------------------------------------------------------------------------
# Module-level state (shared between HTTP handler and public APIs)
# ---------------------------------------------------------------------------

_batch_queue: queue.Queue = queue.Queue()
_current_batch: Optional[_BatchItem] = None
_current_batch_lock = threading.Lock()

_output_path: Optional[str] = None
_job_id: Optional[str] = None
_dataset_format: Optional[Any] = None  # InputDatasetFormat instance
_output_formats: List[Any] = []  # List of OutputFormat instances

# ---------------------------------------------------------------------------
# HTTP handler (localhost only)
# ---------------------------------------------------------------------------


class _WorkerHandler(http_server.BaseHTTPRequestHandler):
    """Handles ``/feed_batch``, ``/shutdown``, and ``/health``."""

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        if self.path == '/feed_batch':
            self._handle_feed_batch()
        elif self.path == '/shutdown':
            self._handle_shutdown()
        else:
            self.send_error(404)

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        if self.path == '/health':
            self._send_json(200, {'status': 'healthy'})
        else:
            self.send_error(404)

    # Suppress default stderr logging for each request.
    def log_message(self, format, *args) -> None:  # pylint: disable=redefined-builtin
        logger.debug('WorkerHandler: %s', format % args)

    # ---- helpers ----------------------------------------------------------

    def _read_json(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length))

    def _send_json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # ---- endpoints --------------------------------------------------------

    def _handle_feed_batch(self) -> None:
        """Download chunk from source, feed to load(), wait for completion."""
        body = self._read_json()
        dataset_path = body['dataset_path']
        start_idx = int(body['start_idx'])
        end_idx = int(body['end_idx'])
        batch_idx = int(body['batch_idx'])

        logger.info('Downloading chunk [%d-%d] from %s', start_idx, end_idx,
                    dataset_path)

        # Download chunk directly from source dataset using format handler
        assert _dataset_format is not None, 'Worker not initialized'
        # Use per-job cache directory to avoid stale data from previous jobs
        # that used the same S3 path with different content.
        cache_dir = f'/tmp/sky_batch_cache/{_job_id}'
        data = _dataset_format.download_chunk(dataset_path, start_idx, end_idx,
                                              cache_dir)
        logger.info('Loaded %d items for batch [%d-%d]', len(data), start_idx,
                    end_idx)

        item = _BatchItem(data=data,
                          start_idx=start_idx,
                          end_idx=end_idx,
                          batch_idx=batch_idx)
        _batch_queue.put(item)

        # Block until save_results() (or an error) sets the event.
        item.done_event.wait()

        if item.error:
            self._send_json(500, {'error': item.error})
        else:
            self._send_json(200, {'status': 'ok'})

    def _handle_shutdown(self) -> None:
        _batch_queue.put(_Shutdown())
        self._send_json(200, {'status': 'shutting_down'})


# ---------------------------------------------------------------------------
# Internal helpers for batch lifecycle
# ---------------------------------------------------------------------------


def get_next_batch() -> Optional[_BatchItem]:
    """Block until the next batch arrives or a shutdown signal is received.

    Returns:
        A ``_BatchItem`` with the batch data, or ``None`` on shutdown.
    """
    global _current_batch
    item = _batch_queue.get()
    if isinstance(item, _Shutdown):
        return None
    with _current_batch_lock:
        _current_batch = item
    return item


def signal_batch_done(error: Optional[str] = None) -> None:
    """Signal that the current batch is complete (or failed).

    Unblocks the HTTP handler waiting on ``done_event``, which in turn
    causes the ``curl`` in the ``sky.exec()`` notify script to return,
    completing the SkyPilot job.
    """
    global _current_batch
    with _current_batch_lock:
        if _current_batch is not None:
            _current_batch.error = error
            _current_batch.done_event.set()
            _current_batch = None


# ---------------------------------------------------------------------------
# Public APIs for mapper functions (load / save_results)
# ---------------------------------------------------------------------------


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
    while True:
        batch_item = get_next_batch()
        if batch_item is None:
            # Shutdown signal received — stop iterating.
            return

        try:
            yield batch_item.data
        except GeneratorExit:
            # Mapper broke out of the loop or was garbage-collected.
            signal_batch_done(error='Mapper stopped iterating')
            return

        # After yield: the user's loop body has executed.  Verify that
        # save_results() was called (which sets done_event).
        if not batch_item.done_event.is_set():
            error_msg = ('save_results() must be called after processing '
                         'each batch. Did you forget to call '
                         'sky.batch.save_results()?')
            signal_batch_done(error=error_msg)
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
    with _current_batch_lock:
        batch_item = _current_batch
    if batch_item is None:
        raise RuntimeError(
            'save_results() called without a current batch. '
            'Make sure to call it inside the loop over sky.batch.load().')

    if len(results) != len(batch_item.data):
        raise ValueError(
            f'Results length ({len(results)}) does not match batch length '
            f'({len(batch_item.data)}). Results must have one entry per '
            'input item.')

    # Upload results using all output formats.
    assert _output_formats, 'Worker not initialized'
    for fmt in _output_formats:
        chunk_path = fmt.upload_chunk(results, fmt.path, batch_item.batch_idx,
                                      batch_item.start_idx, batch_item.end_idx,
                                      _job_id)
        logger.info('Saved results to %s', chunk_path)

    # Signal completion — unblocks the HTTP handler in worker.py.
    signal_batch_done()


# ---------------------------------------------------------------------------
# Format resolution helpers
# ---------------------------------------------------------------------------


def _resolve_input_format() -> io_formats.InputFormat:
    """Resolve input format from the ``SKY_BATCH_INPUT_FORMAT`` env var."""
    env_val = os.environ.get('SKY_BATCH_INPUT_FORMAT')
    if not env_val:
        raise ValueError('SKY_BATCH_INPUT_FORMAT env var is required')
    return io_formats.InputFormat.from_dict(json.loads(env_val))


def _resolve_output_formats() -> List[io_formats.OutputFormat]:
    """Resolve output formats from the ``SKY_BATCH_OUTPUT_FORMATS`` env var."""
    env_val = os.environ.get('SKY_BATCH_OUTPUT_FORMATS')
    if not env_val:
        raise ValueError('SKY_BATCH_OUTPUT_FORMATS env var is required')
    dicts = json.loads(env_val)
    if not dicts:
        raise ValueError('SKY_BATCH_OUTPUT_FORMATS env var is empty')
    return [io_formats.OutputFormat.from_dict(d) for d in dicts]


# ---------------------------------------------------------------------------
# Entry point — started once per worker node
# ---------------------------------------------------------------------------


def start_worker(serialized_fn: str, output_path: str, job_id: str) -> None:
    """Start the long-running worker service.

    1. Launch a localhost HTTP server in a daemon thread.
    2. Deserialize and invoke the mapper function.  The mapper runs
       ``for batch in sky.batch.load(): …`` which blocks on the internal
       queue until batches arrive or shutdown is signaled.
    """
    global _output_path, _job_id, _dataset_format, _output_formats
    _output_path = output_path
    _job_id = job_id
    _dataset_format = _resolve_input_format()
    _output_formats = _resolve_output_formats()

    # Start HTTP server.
    server = http_server.HTTPServer(
        ('127.0.0.1', constants.WORKER_SERVICE_PORT), _WorkerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    logger.info('Worker service listening on 127.0.0.1:%d',
                constants.WORKER_SERVICE_PORT)

    # Deserialize and run the mapper.
    mapper_fn = utils.deserialize_function(serialized_fn)
    try:
        mapper_fn()
    except Exception as e:  # pylint: disable=broad-except
        logger.error('Mapper function raised: %s', e)
        # If the mapper crashed while processing a batch, unblock the
        # HTTP handler so the sky.exec job can fail cleanly.
        signal_batch_done(error=str(e))
    finally:
        server.shutdown()
        logger.info('Worker service stopped')
