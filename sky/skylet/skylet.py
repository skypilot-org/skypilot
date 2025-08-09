"""skylet: a daemon running on the head node of a cluster."""

import concurrent.futures
import os
import time

import grpc

import sky
from sky import sky_logging
from sky.schemas.generated import autostopv1_pb2_grpc
from sky.skylet import constants
from sky.skylet import events
from sky.skylet import services

# Use the explicit logger name so that the logger is under the
# `sky.skylet.skylet` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.skylet.skylet')
logger.info(f'Skylet started with version {constants.SKYLET_VERSION}; '
            f'SkyPilot v{sky.__version__} (commit: {sky.__commit__})')

EVENTS = [
    events.AutostopEvent(),
    events.JobSchedulerEvent(),
    # The managed job update event should be after the job update event.
    # Otherwise, the abnormal managed job status update will be delayed
    # until the next job update event.
    events.ManagedJobEvent(),
    # This is for monitoring controller job status. If it becomes
    # unhealthy, this event will correctly update the controller
    # status to CONTROLLER_FAILED.
    events.ServiceUpdateEvent(pool=False),
    # Status refresh for pool.
    events.ServiceUpdateEvent(pool=True),
    # Report usage heartbeat every 10 minutes.
    events.UsageHeartbeatReportEvent(),
]


def start_grpc_server(port: int = constants.SKYLET_GRPC_PORT) -> grpc.Server:
    """Start the gRPC server."""
    # This is the default value in Python 3.8 - 3.12,
    # putting it here for visibility.
    # TODO(kevin): Determine the optimal max number of threads.
    max_workers = min(32, (os.cpu_count() or 1) + 4)
    server = grpc.server(
        concurrent.futures.ThreadPoolExecutor(max_workers=max_workers))

    autostopv1_pb2_grpc.add_AutostopServiceServicer_to_server(
        services.AutostopServiceImpl(), server)

    listen_addr = f'127.0.0.1:{port}'
    server.add_insecure_port(listen_addr)

    server.start()
    logger.info(f'gRPC server started on {listen_addr}')

    return server


def run_event_loop():
    """Run the existing event loop."""

    while True:
        time.sleep(events.EVENT_CHECKING_INTERVAL_SECONDS)
        for event in EVENTS:
            event.run()


def main():
    grpc_server = start_grpc_server()
    try:
        run_event_loop()
    except KeyboardInterrupt:
        logger.info('Shutting down skylet...')
    finally:
        grpc_server.stop(grace=5)


if __name__ == '__main__':
    main()
