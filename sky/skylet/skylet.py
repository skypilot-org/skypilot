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
from sky.skylet import hook_executor
from sky.skylet import preemption_poller
from sky.skylet import services

# Use the explicit logger name so that the logger is under the
# `sky.skylet.skylet` namespace when executed directly, so as
# to inherit the setup from the `sky` logger.
logger = sky_logging.init_logger('sky.skylet.skylet')
logger.info(f'Skylet started with version {constants.SKYLET_VERSION}; '
            f'SkyPilot v{sky.__version__} (commit: {sky.__commit__})')

EVENTS = [
    events.StopEvent(),
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

    listen_addr = f'0.0.0.0:{port}'
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


def _detect_cloud_for_preemption_poller():
    """Best-effort probe of the cluster's cloud identity.

    Returns one of ``'aws'``, ``'gcp'``, ``'azure'``, or ``None`` when
    the node isn't on a cloud VM (e.g., Kubernetes, Slurm, bare-metal).
    Probes are cheap reads of the local metadata endpoints with tight
    timeouts — a K8s pod with no metadata service gets ``None`` in ~1s.

    K8s short-circuit: every K8s pod has ``KUBERNETES_SERVICE_HOST``
    set automatically by kubelet. We return ``None`` immediately on
    that signal so EKS pods (where the EC2 IMDS may be reachable from
    the underlying node) don't misdetect as AWS and start a poller
    that races the K8s preStop bridge.
    """
    if os.environ.get('KUBERNETES_SERVICE_HOST'):
        return None

    import urllib.error  # pylint: disable=import-outside-toplevel
    import urllib.request  # pylint: disable=import-outside-toplevel

    # AWS IMDSv2: PUT /latest/api/token returns a token when on EC2.
    try:
        req = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            method='PUT',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'})
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200 and resp.read():
                return 'aws'
    except Exception:  # pylint: disable=broad-except
        pass
    # GCP metadata server returns 200 with Metadata-Flavor: Google.
    try:
        req = urllib.request.Request(
            'http://metadata.google.internal/computeMetadata/v1/instance/id',
            headers={'Metadata-Flavor': 'Google'})
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200:
                return 'gcp'
    except Exception:  # pylint: disable=broad-except
        pass
    # Azure IMDS: GET /metadata/instance with Metadata: true header.
    try:
        req = urllib.request.Request(
            'http://169.254.169.254/metadata/instance?api-version=2021-02-01',
            headers={'Metadata': 'true'})
        with urllib.request.urlopen(req, timeout=1) as resp:
            if resp.status == 200:
                return 'azure'
    except Exception:  # pylint: disable=broad-except
        pass
    return None


def _sigterm_handler(signum, frame):  # pylint: disable=unused-argument
    """Run preemption hooks on SIGTERM before the pod is SIGKILLed.

    On Kubernetes the kubelet sends SIGTERM for preemption / eviction
    / drain. We claim the preemption teardown slot via the file-lock
    CAS so a concurrent `sky down` subprocess sees the claim and
    skips its own hooks, then run any matching preemption hooks, then
    exit cleanly within the pod's terminationGracePeriodSeconds.
    """
    logger.info('Skylet received SIGTERM; running preemption hooks.')
    if hook_executor.try_claim_teardown(hook_executor.PREEMPTION):
        try:
            hook_executor.run(hook_executor.PREEMPTION,
                              autostop_lib.get_hooks())
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Preemption hook execution failed: {e}')
    sys.exit(0)


def _should_install_preemption_sigterm_handler() -> bool:
    """True iff the skylet is running inside a Kubernetes pod.

    SIGTERM-driven preemption handling is K8s-specific: kubelet sends
    SIGTERM to pod containers on delete / scale-down / eviction. On VM
    clouds (AWS/GCP/Azure), preemption is detected via the metadata
    poller (introduced in PR2), so installing a SIGTERM handler here
    would be dead code and could mask normal-shutdown signal handling.

    Detection uses the standard ``KUBERNETES_SERVICE_HOST`` env var
    that the kubelet injects into every pod.
    """
    return 'KUBERNETES_SERVICE_HOST' in os.environ


def main():
    parser = argparse.ArgumentParser(description='Start skylet daemon')
    parser.add_argument('--port',
                        type=int,
                        default=constants.SKYLET_GRPC_PORT,
                        help=f'gRPC port to listen on (default: '
                        f'{constants.SKYLET_GRPC_PORT})')
    args = parser.parse_args()

    # Clear any stale teardown-claim marker from a prior crashed skylet so
    # this fresh boot does not see a blocked slot.
    hook_executor.clear_teardown_claim()
    if _should_install_preemption_sigterm_handler():
        signal.signal(signal.SIGTERM, _sigterm_handler)

    # Start per-cloud preemption poller. Skipped on Kubernetes and
    # anywhere without a cloud metadata endpoint (the SIGTERM handler
    # already covers K8s preemption via kubelet signals).
    cloud = _detect_cloud_for_preemption_poller()
    if cloud is not None:
        logger.info(f'Starting VM preemption poller for cloud={cloud}')
        preemption_poller.start(cloud)
    else:
        logger.info('No cloud metadata endpoint detected; '
                    'VM preemption poller not started.')

    grpc_server = start_grpc_server(port=args.port)
    try:
        run_event_loop()
    except KeyboardInterrupt:
        logger.info('Shutting down skylet...')
    finally:
        grpc_server.stop(grace=5)


if __name__ == '__main__':
    main()
