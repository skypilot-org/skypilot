"""Uvicorn wrapper for SkyPilot API server.

This module is a wrapper around uvicorn to customize the behavior of the
server.
"""
import functools
import os
import socket
import threading
from typing import Optional

import uvicorn
from uvicorn.supervisors import multiprocess

from sky.utils import context_utils
from sky.utils import subprocess_utils


def run(config: uvicorn.Config):
    """Run unvicorn server."""
    if config.reload:
        # Reload and multi-workers are mutually exclusive
        # in uvicorn. Since we do not use reload now, simply
        # guard by an exception.
        raise ValueError('Reload is not supported yet.')
    server = uvicorn.Server(config=config)
    run_server_process = functools.partial(_run_server_process, server)
    try:
        sock = config.bind_socket()
        _configure_tcp_keepalive(sock)
        if config.workers is not None and config.workers > 1:
            SlowStartMultiprocess(config,
                                  target=run_server_process,
                                  sockets=[sock]).run()
        else:
            run_server_process(sockets=[sock])
    finally:
        # Copied from unvicorn.run()
        if config.uds and os.path.exists(config.uds):
            os.remove(config.uds)


def _configure_tcp_keepalive(sock: socket.socket) -> None:
    """Configure TCP keepalive options for the server socket.
    
    This helps maintain long-lived connections and detect broken connections
    more reliably, especially when there are firewalls or load balancers
    between clients and the server.
    
    TCP keepalive works by sending periodic probe packets on idle connections
    to detect if the remote peer is still reachable. This is particularly
    useful for:
    - Detecting broken connections through firewalls/NAT devices
    - Preventing connection drops due to idle timeouts
    - Improving reliability of long-running API connections
    
    Platform support:
    - Linux: Supports all TCP keepalive options
    - macOS: Supports TCP_KEEPINTVL and TCP_KEEPCNT, but not TCP_KEEPIDLE
    - Windows: Limited support for TCP keepalive options
    
    Args:
        sock: The socket to configure.
    """
    try:
        # Enable TCP keepalive
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        
        # Configure keepalive parameters (platform-dependent)
        if hasattr(socket, 'TCP_KEEPIDLE'):
            # Time before sending keepalive probes (seconds)
            # Available on Linux, not on macOS
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        
        if hasattr(socket, 'TCP_KEEPINTVL'):
            # Interval between keepalive probes (seconds)
            # Available on Linux and macOS
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 30)
        
        if hasattr(socket, 'TCP_KEEPCNT'):
            # Number of failed probes before declaring connection dead
            # Available on Linux and macOS
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            
        # Log successful configuration
        import logging
        logger = logging.getLogger(__name__)
        logger.info('TCP keepalive configured successfully')
            
    except (OSError, AttributeError) as e:
        # Some platforms may not support all TCP keepalive options
        # Log the error but don't fail the server startup
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f'Failed to configure TCP keepalive: {e}')


def _run_server_process(server: uvicorn.Server, *args, **kwargs):
    """Run the server process with contextually aware."""
    context_utils.hijack_sys_attrs()
    server.run(*args, **kwargs)


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
