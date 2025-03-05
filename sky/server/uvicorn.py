"""Uvicorn wrapper for Sky server."""
import os
import threading
import time

import uvicorn
from uvicorn.supervisors import multiprocess


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
    """Uvicorn Multiprocess wrapper with slow start."""

    def __init__(self,
                 config: uvicorn.Config,
                 slow_start_delay: float = 2.0,
                 **kwargs):
        """Initialize the multiprocess wrapper.

        Args:
            config: The uvicorn config.
            slow_start_delay: The delay between starting each worker process,
                default to 2.0 seconds based on profile.
        """
        super().__init__(config, **kwargs)
        self.slow_start_delay = slow_start_delay

    def init_processes(self) -> None:
        # Slow start worker processes asynchronously to avoid blocking signal
        # handling of uvicorn.
        threading.Thread(target=self.slow_start_processes, daemon=True).start()

    def slow_start_processes(self) -> None:
        """Initialize remaining processes with slow start."""
        batch_size = 1
        processes_left = self.processes_num

        # Though the processes are started asynchronously, we still have to be
        # slow to avoid overwhelming the CPU on startup.
        while processes_left > 0:
            current_batch = min(batch_size, processes_left)
            for _ in range(current_batch):
                process = multiprocess.Process(self.config, self.target,
                                               self.sockets)
                # Must start the process before appending to self.processes
                # since uvicorn periodically restarts unresponsive workers.
                process.start()
                self.processes.append(process)
                processes_left -= 1
            if processes_left <= 0:
                break
            time.sleep(self.slow_start_delay)
            batch_size *= 2
