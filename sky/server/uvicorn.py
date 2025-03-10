"""Uvicorn wrapper for SkyPilot API server.

This module is a wrapper around uvicorn to customize the behavior of the
server.
"""
import os
import threading

import uvicorn
from uvicorn.supervisors import multiprocess

from sky.utils import subprocess_utils


def run(config: uvicorn.Config):
    """Run unvicorn server."""
    if config.reload:
        # Reload and multi-workers are mutually exclusive
        # in uvicorn. Since we do not use reload now, simply
        # guard by an exception.
        raise ValueError('Reload is not supported yet.')
    server = uvicorn.Server(config=config)
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

    def init_processes(self) -> None:
        # Slow start worker processes asynchronously to avoid blocking signal
        # handling of uvicorn.
        threading.Thread(target=self.slow_start_processes, daemon=True).start()

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
                                              on_start=self.processes.append)
