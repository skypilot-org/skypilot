"""Uvicorn wrapper for SkyPilot API server.

This module is a wrapper around uvicorn to customize the behavior of the
server.
"""
import asyncio
import logging
import os
import signal
import sys
import threading
import time
from types import FrameType
from typing import Optional, Union

import filelock
import uvicorn
from uvicorn.supervisors import multiprocess

from sky import sky_logging
from sky.server import daemons
from sky.server import metrics as metrics_lib
from sky.server import plugins
from sky.server import state
from sky.server.requests import requests as requests_lib
from sky.skylet import constants
from sky.utils import context_utils
from sky.utils import env_options
from sky.utils import perf_utils
from sky.utils import subprocess_utils
from sky.utils.db import db_utils

logger = sky_logging.init_logger(__name__)

# File lock path for coordinating graceful shutdown across processes
_GRACEFUL_SHUTDOWN_LOCK_PATH = '/tmp/skypilot_graceful_shutdown.lock'

# Interval to check for on-going requests.
_WAIT_REQUESTS_INTERVAL_SECONDS = 5

# Timeout for waiting for on-going requests to finish.
try:
    _WAIT_REQUESTS_TIMEOUT_SECONDS = int(
        os.environ.get(constants.GRACE_PERIOD_SECONDS_ENV_VAR, '60'))
except ValueError:
    _WAIT_REQUESTS_TIMEOUT_SECONDS = 60

# TODO(aylei): use decorator to register requests that need to be proactively
# cancelled instead of hardcoding here.
_RETRIABLE_REQUEST_NAMES = {
    'sky.logs',
    'sky.jobs.logs',
    'sky.serve.logs',
}


def add_timestamp_prefix_for_server_logs() -> None:
    """Configure logging for API server.

    Note: we only do this in the main API server process and uvicorn processes,
    to avoid affecting executor logs (including in modules like
    sky.server.requests) that may get sent to the client.
    """
    server_logger = sky_logging.init_logger('sky.server')
    # Clear existing handlers first to prevent duplicates
    server_logger.handlers.clear()
    # Disable propagation to avoid the root logger of SkyPilot being affected
    server_logger.propagate = False
    # Add date prefix to the log message printed by loggers under
    # server.
    stream_handler = logging.StreamHandler(sys.stdout)
    if env_options.Options.SHOW_DEBUG_INFO.get():
        stream_handler.setLevel(logging.DEBUG)
    else:
        stream_handler.setLevel(logging.INFO)
    stream_handler.flush = sys.stdout.flush  # type: ignore
    stream_handler.setFormatter(sky_logging.FORMATTER)
    server_logger.addHandler(stream_handler)
    # Add date prefix to the log message printed by uvicorn.
    for name in ['uvicorn', 'uvicorn.access']:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(stream_handler)


class Server(uvicorn.Server):
    """Server wrapper for uvicorn.

    Extended functionalities:
    - Handle exit signal and perform custom graceful shutdown.
    - Run the server process with contextually aware.
    """

    def __init__(self,
                 config: uvicorn.Config,
                 max_db_connections: Optional[int] = None):
        super().__init__(config=config)
        self.exiting: bool = False
        self.max_db_connections = max_db_connections

    def handle_exit(self, sig: int, frame: Union[FrameType, None]) -> None:
        """Handle exit signal.

        When a server process receives a SIGTERM or SIGINT signal, a graceful
        shutdown will be initiated. If a SIGINT signal is received again, the
        server will be forcefully shutdown.
        """
        if self.exiting and sig == signal.SIGINT:
            # The server has been signaled to exit and received a SIGINT again,
            # do force shutdown.
            logger.info('Force shutdown.')
            self.should_exit = True
            super().handle_exit(sig, frame)
            return
        if not self.exiting:
            self.exiting = True
            # Perform graceful shutdown in a separate thread to avoid blocking
            # the main thread.
            threading.Thread(target=self._graceful_shutdown,
                             args=(sig, frame),
                             daemon=True).start()

    def _graceful_shutdown(self, sig: int, frame: Union[FrameType,
                                                        None]) -> None:
        """Perform graceful shutdown."""
        # Block new requests so that we can wait until all on-going requests
        # are finished. Note that /api/$verb operations are still allowed in
        # this stage to ensure the client can still operate the on-going
        # requests, e.g. /api/logs, /api/cancel, etc.
        logger.info('Block new requests being submitted in worker '
                    f'{os.getpid()}.')
        state.set_block_requests(True)
        # Ensure the shutting_down are set on all workers before next step.
        # TODO(aylei): hacky, need a reliable solution.
        time.sleep(1)

        lock = filelock.FileLock(_GRACEFUL_SHUTDOWN_LOCK_PATH)
        # Elect a coordinator process to handle on-going requests check
        with lock.acquire():
            logger.info(f'Worker {os.getpid()} elected as shutdown coordinator')
            self._wait_requests()

        logger.info('Shutting down server...')
        self.should_exit = True
        super().handle_exit(sig, frame)

    def _wait_requests(self) -> None:
        """Wait until all on-going requests are finished or cancelled."""
        start_time = time.time()
        while True:
            statuses = [
                requests_lib.RequestStatus.PENDING,
                requests_lib.RequestStatus.RUNNING,
            ]
            requests = [(request_task.request_id, request_task.name)
                        for request_task in requests_lib.get_request_tasks(
                            req_filter=requests_lib.RequestTaskFilter(
                                status=statuses, fields=['request_id', 'name']))
                       ]
            if not requests:
                break
            logger.info(f'{len(requests)} on-going requests '
                        'found, waiting for them to finish...')
            # Proactively cancel internal requests and logs requests since
            # they can run for infinite time.
            internal_request_ids = {
                d.id for d in daemons.INTERNAL_REQUEST_DAEMONS
            }
            if time.time() - start_time > _WAIT_REQUESTS_TIMEOUT_SECONDS:
                logger.warning('Timeout waiting for on-going requests to '
                               'finish, cancelling all on-going requests.')
                for request_id, _ in requests:
                    self.interrupt_request_for_retry(request_id)
                break
            interrupted = 0
            for request_id, name in requests:
                if (name in _RETRIABLE_REQUEST_NAMES or
                        request_id in internal_request_ids):
                    self.interrupt_request_for_retry(request_id)
                    interrupted += 1
                # TODO(aylei): interrupt pending requests to accelerate the
                # shutdown.
            # If some requests are not interrupted, wait for them to finish,
            # otherwise we just check again immediately to accelerate the
            # shutdown process.
            if interrupted < len(requests):
                time.sleep(_WAIT_REQUESTS_INTERVAL_SECONDS)

    def interrupt_request_for_retry(self, request_id: str) -> None:
        """Interrupt a request for retry."""
        with requests_lib.update_request(request_id) as req:
            if req is None:
                return
            if req.pid is not None:
                try:
                    os.kill(req.pid, signal.SIGTERM)
                except ProcessLookupError:
                    logger.debug(f'Process {req.pid} already finished.')
            req.status = requests_lib.RequestStatus.CANCELLED
            req.should_retry = True
        logger.info(
            f'Request {request_id} interrupted and will be retried by client.')

    def run(self, *args, **kwargs):
        """Run the server process."""
        if self.max_db_connections is not None:
            db_utils.set_max_connections(self.max_db_connections)
        add_timestamp_prefix_for_server_logs()
        context_utils.hijack_sys_attrs()
        # Use default loop policy of uvicorn (use uvloop if available).
        self.config.setup_event_loop()
        lag_threshold = perf_utils.get_loop_lag_threshold()
        if lag_threshold is not None:
            event_loop = asyncio.get_event_loop()
            # Same as set PYTHONASYNCIODEBUG=1, but with custom threshold.
            event_loop.set_debug(True)
            event_loop.slow_callback_duration = lag_threshold
        stop_monitor = threading.Event()
        monitor = threading.Thread(target=metrics_lib.process_monitor,
                                   args=('server', stop_monitor),
                                   daemon=True)
        monitor.start()
        try:
            with self.capture_signals():
                asyncio.run(self.serve(*args, **kwargs))
        finally:
            stop_monitor.set()
            monitor.join()


def run(config: uvicorn.Config, max_db_connections: Optional[int] = None):
    """Run unvicorn server."""
    if config.reload:
        # Reload and multi-workers are mutually exclusive
        # in uvicorn. Since we do not use reload now, simply
        # guard by an exception.
        raise ValueError('Reload is not supported yet.')
    server = Server(config=config, max_db_connections=max_db_connections)
    try:
        if config.workers is not None and config.workers > 1:
            # When workers > 1, uvicorn does not run server app in the main
            # process. In this case, plugins are not loaded at this point, so
            # load plugins here without uvicorn app.
            plugins.load_plugins(plugins.ExtensionContext())
            sock = config.bind_socket()
            SlowStartMultiprocess(config, target=server.run,
                                  sockets=[sock]).run()
        else:
            server.run()
    finally:
        # Copied from unvicorn.run()
        if config.uds and os.path.exists(config.uds):
            os.remove(config.uds)


class SlowStartMultiprocess(multiprocess.Multiprocess):
    """Uvicorn Multiprocess wrapper with slow start.

    Slow start offers faster and more stable  start time.
    Profile shows the start time is more stable and accelerated from
    ~7s to ~3.3s on a 12-core machine after switching LONG workers and
    Uvicorn workers to slow start.
    Refer to subprocess_utils.slow_start_processes() for more details.
    """

    def __init__(self, config: uvicorn.Config, **kwargs):
        """Initialize the multiprocess wrapper.

        Args:
            config: The uvicorn config.
        """
        super().__init__(config, **kwargs)
        self._init_thread: Optional[threading.Thread] = None

    def init_processes(self) -> None:
        # Slow start worker processes asynchronously to avoid blocking signal
        # handling of uvicorn.
        self._init_thread = threading.Thread(target=self.slow_start_processes,
                                             daemon=True)
        self._init_thread.start()

    def slow_start_processes(self) -> None:
        """Initialize processes with slow start."""
        to_start = []
        # Init N worker processes
        for _ in range(self.processes_num):
            to_start.append(
                multiprocess.Process(self.config, self.target, self.sockets))
        # Start the processes with slow start, we only append start to
        # self.processes because Uvicorn periodically restarts unstarted
        # workers.
        subprocess_utils.slow_start_processes(to_start,
                                              on_start=self.processes.append,
                                              should_exit=self.should_exit)

    def terminate_all(self) -> None:
        """Wait init thread to finish before terminating all processes."""
        if self._init_thread is not None:
            self._init_thread.join()
        super().terminate_all()
