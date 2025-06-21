"""Uvicorn wrapper for SkyPilot API server.

This module is a wrapper around uvicorn to customize the behavior of the
server.
"""
import asyncio
import os
import signal
import threading
import time
from types import FrameType
from typing import Optional, Union

import filelock
import uvicorn
from uvicorn.supervisors import multiprocess

from sky import sky_logging
from sky.server import state
from sky.server.requests import requests as requests_lib
from sky.utils import context_utils
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

# File lock path for coordinating graceful shutdown across processes
_GRACEFUL_SHUTDOWN_LOCK_PATH = '/tmp/skypilot_graceful_shutdown.lock'

# Interval to check for on-going requests.
_WAIT_REQUESTS_INTERVAL_SECONDS = 5

# Seconds to wait for readiness probe marks us as unhealthy.
# TODO(aylei): make this configurable.
_WAIT_READINESS_PROBE_FAIL_SECONDS = 5

# TODO(aylei): use decorator to register requests that need to be proactively
# cancelled instead of hardcoding here.
_LONG_RUNNING_REQUEST_NAMES = [
    'sky.logs',
    'sky.jobs.logs',
    'sky.serve.logs',
]


class Server(uvicorn.Server):
    """Server wrapper for uvicorn.

    Extended functionalities:
    - Handle exit signal and perform custom graceful shutdown.
    - Run the server process with contextually aware.
    """

    def __init__(self, config: uvicorn.Config):
        super().__init__(config=config)
        self.exiting: bool = False

    def handle_exit(self, sig: int, frame: Union[FrameType, None]) -> None:
        """Handle exit signal.

        When a server process receives a SIGTERM or SIGINT signal, a graceful
        shutdown will be initiated. If a SIGINT signal is received again, the
        server will be forcefully shutdown.
        """
        if self.exiting and sig == signal.SIGINT:
            # The server has been siganled to exit and recieved a SIGINT again,
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
                             daemon=True).start()

    def _graceful_shutdown(self) -> None:
        """Perform graceful shutdown.

        Graceful shutdown process:
        1. All handlers EXCEPT `/api/${verb}` will return 503 with custom
           headers for new requests;
        2. On-going `resources/${logs}` requests will be interrupted and
           return 503 with custom headers;
        3. Wait until there is no on-going requests, then shutdown the
           uvicorn server.
        """
        # Block new requests so that we can wait until all on-going requests
        # are finished. Note that /api/$verb operations are still allowed in
        # this stage to ensure the client can still operate the on-going
        # requests, e.g. /api/logs, /api/cancel, etc.
        logger.info(f'Graceful shutdown: block new requests being submitted '
                    f'in worker {os.getpid()}.')
        state.set_block_requests(True)
        # Ensure the block_requests are set on all workers before next step.
        # TODO(aylei): hacky, need a reliable solution.
        time.sleep(1)

        lock = filelock.FileLock(_GRACEFUL_SHUTDOWN_LOCK_PATH)
        # Elect a coordinator process to handle on-going requests check
        with lock.acquire():
            logger.info(f'Graceful shutdown: worker {os.getpid()} elected as '
                        'shutdown coordinator')
            self._wait_requests()

        # Block all API operations and wait us getting removed from the
        # backend list. We do not close the server process here so that
        # we can return a clear retryable error to the client.
        state.set_block_all(True)
        logger.info('Graceful shutdown: wait for '
                    f'{_WAIT_READINESS_PROBE_FAIL_SECONDS} to ')
        time.sleep(_WAIT_READINESS_PROBE_FAIL_SECONDS)

        self.should_exit = True
        super().handle_exit(signal.SIGINT, None)

    def _wait_requests(self) -> None:
        """Wait until all on-going requests are finished or cancelled."""
        while True:
            statuses = [
                requests_lib.RequestStatus.PENDING,
                requests_lib.RequestStatus.RUNNING,
            ]
            reqs = requests_lib.get_request_tasks(status=statuses)
            if not reqs:
                break
            logger.info(f'Graceful shutdown: {len(reqs)} on-going requests '
                        'found, waiting for them to finish...')
            # Proactively cancel internal requests and logs requests since
            # they can run for infinite time.
            internal_request_ids = [
                d.id for d in requests_lib.INTERNAL_REQUEST_DAEMONS
            ]
            for req in reqs:
                if req.request_id in internal_request_ids:
                    requests_lib.set_request_cancelled(req.request_id)
                    logger.info(f'Graceful shutdown: internal request ' \
                                f'{req.request_id} cancelled')
                elif req.name in _LONG_RUNNING_REQUEST_NAMES:
                    requests_lib.set_request_cancelled(req.request_id)
                    logger.info(f'Graceful shutdown: long running request ' \
                                f'{req.request_id} cancelled')
            time.sleep(_WAIT_REQUESTS_INTERVAL_SECONDS)

    def run(self, *args, **kwargs):
        """Run the server process."""
        context_utils.hijack_sys_attrs()
        with self.capture_signals():
            asyncio.run(self.serve(*args, **kwargs))


def run(config: uvicorn.Config):
    """Run unvicorn server."""
    if config.reload:
        # Reload and multi-workers are mutually exclusive
        # in uvicorn. Since we do not use reload now, simply
        # guard by an exception.
        raise ValueError('Reload is not supported yet.')
    server = Server(config=config)
    try:
        if config.workers is not None and config.workers > 1:
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
