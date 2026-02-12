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
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import logging
import queue
import threading
from typing import Any, Dict, List, Optional

from sky.batch import constants

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
# Module-level state (shared between HTTP handler and api.py)
# ---------------------------------------------------------------------------

_batch_queue: queue.Queue = queue.Queue()
_current_batch: Optional[_BatchItem] = None
_current_batch_lock = threading.Lock()

_output_path: Optional[str] = None
_job_id: Optional[str] = None
_dataset_format: Optional[Any] = None  # DatasetFormat instance

# ---------------------------------------------------------------------------
# HTTP handler (localhost only)
# ---------------------------------------------------------------------------


class _WorkerHandler(BaseHTTPRequestHandler):
    """Handles ``/feed_batch``, ``/shutdown``, and ``/health``."""

    def do_POST(self):
        if self.path == '/feed_batch':
            self._handle_feed_batch()
        elif self.path == '/shutdown':
            self._handle_shutdown()
        else:
            self.send_error(404)

    def do_GET(self):
        if self.path == '/health':
            self._send_json(200, {'status': 'healthy'})
        else:
            self.send_error(404)

    # Suppress default stderr logging for each request.
    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
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
        cache_dir = '/tmp/sky_batch_cache'
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
# Public helpers called by api.py
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
# Dataset format helper
# ---------------------------------------------------------------------------


def _get_dataset_format(dataset_path: str):
    """Get dataset format handler (same logic as controller)."""
    from sky.batch.formats.jsonl import JSONLDataset

    if dataset_path.endswith('.jsonl'):
        return JSONLDataset()
    else:
        raise ValueError(f'Unsupported dataset format: {dataset_path}')


# ---------------------------------------------------------------------------
# Entry point — started once per worker node
# ---------------------------------------------------------------------------


def start_worker(serialized_fn: str, output_path: str, job_id: str,
                 dataset_path: str) -> None:
    """Start the long-running worker service.

    1. Launch a localhost HTTP server in a daemon thread.
    2. Deserialize and invoke the mapper function.  The mapper runs
       ``for batch in sky.batch.load(): …`` which blocks on the internal
       queue until batches arrive or shutdown is signaled.
    """
    from sky.batch import api
    from sky.batch import utils

    global _output_path, _job_id, _dataset_format
    _output_path = output_path
    _job_id = job_id
    _dataset_format = _get_dataset_format(dataset_path)

    # Start HTTP server.
    server = HTTPServer(('127.0.0.1', constants.WORKER_SERVICE_PORT),
                        _WorkerHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    logger.info('Worker service listening on 127.0.0.1:%d',
                constants.WORKER_SERVICE_PORT)

    # Install worker state for api.py.
    api.set_worker_state(output_path, job_id)

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
