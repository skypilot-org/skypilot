"""skylet: a daemon running on the head node of a cluster."""

import argparse
import concurrent.futures
import os
import signal
import sys
import time

import grpc

import sky
from sky import sky_logging
from sky.schemas.generated import autostopv1_pb2_grpc
from sky.schemas.generated import jobsv1_pb2_grpc
from sky.schemas.generated import managed_jobsv1_pb2_grpc
from sky.schemas.generated import servev1_pb2_grpc
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import events
from sky.skylet import services
from sky.utils import cluster_utils
from sky.utils import yaml_utils

# Use the explicit logger name so that the logger is under the
# `sky.skylet.skylet` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.skylet.skylet')
logger.info(f'Skylet started with version {constants.SKYLET_VERSION}; '
            f'SkyPilot v{sky.__version__} (commit: {sky.__commit__})')

EVENTS = [
    events.SpotTerminationEvent(),  # Must be first for early detection
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
    # This is the default value in Python 3.9 - 3.12,
    # putting it here for visibility.
    # TODO(kevin): Determine the optimal max number of threads.
    max_workers = min(32, (os.cpu_count() or 1) + 4)
    # There's only a single skylet process per cluster, so disable
    # SO_REUSEPORT to raise an error if the port is already in use.
    options = (('grpc.so_reuseport', 0),)
    server = grpc.server(
        concurrent.futures.ThreadPoolExecutor(max_workers=max_workers),
        options=options)

    autostopv1_pb2_grpc.add_AutostopServiceServicer_to_server(
        services.AutostopServiceImpl(), server)
    jobsv1_pb2_grpc.add_JobsServiceServicer_to_server(
        services.JobsServiceImpl(), server)
    servev1_pb2_grpc.add_ServeServiceServicer_to_server(
        services.ServeServiceImpl(), server)
    managed_jobsv1_pb2_grpc.add_ManagedJobsServiceServicer_to_server(
        services.ManagedJobsServiceImpl(), server)

    listen_addr = f'127.0.0.1:{port}'
    server.add_insecure_port(listen_addr)

    server.start()
    logger.info(f'gRPC server started on {listen_addr}')

    return server


def run_event_loop():
    """Run the existing event loop."""

    for event in EVENTS:
        event.start()

    while True:
        time.sleep(events.EVENT_CHECKING_INTERVAL_SECONDS)
        for event in EVENTS:
            event.run()


def _handle_sigterm(signum, frame):
    """Handle SIGTERM by running autostop hook before exit.

    On Kubernetes, this handler is not registered because the container
    entrypoint traps SIGTERM to keep the pod alive (for HA). SIGTERM
    from pod termination never reaches the skylet process.
    """
    del signum, frame  # Unused.
    if not autostop_lib.set_preemption_hook_if_not_set():
        sys.exit(0)

    config = autostop_lib.get_autostop_config()
    if config.hook:
        # Determine cloud provider for grace period
        try:
            config_path = os.path.abspath(
                os.path.expanduser(cluster_utils.SKY_CLUSTER_YAML_REMOTE_PATH))
            cluster_config = yaml_utils.read_yaml(config_path)
            provider_name = cluster_utils.get_provider_name(cluster_config)
        except Exception:  # pylint: disable=broad-except
            provider_name = 'unknown'

        grace = autostop_lib.get_preemption_grace_seconds(provider_name)
        capped_timeout = min(config.hook_timeout, grace)
        if capped_timeout < config.hook_timeout:
            logger.warning(
                f'Hook timeout capped from {config.hook_timeout}s to '
                f'{capped_timeout}s due to cloud grace period.')
        logger.info(f'SIGTERM received. Running preemption hook '
                    f'(timeout: {capped_timeout}s)...')
        autostop_lib.execute_autostop_hook(config.hook, capped_timeout)

    sys.exit(0)


def _is_kubernetes() -> bool:
    """Check if the current cluster is running on Kubernetes."""
    try:
        config_path = os.path.abspath(
            os.path.expanduser(cluster_utils.SKY_CLUSTER_YAML_REMOTE_PATH))
        cluster_config = yaml_utils.read_yaml(config_path)
        provider_name = cluster_utils.get_provider_name(cluster_config)
        return provider_name == 'kubernetes'
    except Exception:  # pylint: disable=broad-except
        return False


def main():
    # On Kubernetes, the container entrypoint traps SIGTERM to keep the pod
    # alive for HA. SIGTERM from pod termination never reaches the skylet,
    # so registering the handler is unnecessary (and would cause the skylet
    # to exit on any direct SIGTERM, e.g. from attempt_skylet restarts).
    if not _is_kubernetes():
        signal.signal(signal.SIGTERM, _handle_sigterm)

    parser = argparse.ArgumentParser(description='Start skylet daemon')
    parser.add_argument('--port',
                        type=int,
                        default=constants.SKYLET_GRPC_PORT,
                        help=f'gRPC port to listen on (default: '
                        f'{constants.SKYLET_GRPC_PORT})')
    args = parser.parse_args()

    grpc_server = start_grpc_server(port=args.port)
    try:
        run_event_loop()
    except KeyboardInterrupt:
        logger.info('Shutting down skylet...')
    finally:
        grpc_server.stop(grace=5)


if __name__ == '__main__':
    main()
