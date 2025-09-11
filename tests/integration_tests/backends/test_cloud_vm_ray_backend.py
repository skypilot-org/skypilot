import concurrent.futures
import os
import random
import statistics
import time
from typing import List, Tuple

import pytest

from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.schemas.generated import autostopv1_pb2
from sky.utils import env_options

logger = sky_logging.init_logger(__name__)


@pytest.fixture(scope="session")
def test_cluster(request):
    """Session-scoped fixture to set up and tear down test cluster."""
    cluster_name = request.config.getoption('--backend-test-cluster')
    if not cluster_name:
        pytest.fail("cluster name is not provided")
    yield cluster_name


def _get_cluster_handle(cluster_name: str):
    """Get the cluster handle using the standard SkyPilot approach."""
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        raise RuntimeError(f"Cluster '{cluster_name}' not found")
    return handle


def _simulate_ssh_process_kill(handle, kill_probability: float) -> bool:
    """Simulate connection failure by killing SSH tunnel process."""
    tunnel = handle._get_skylet_ssh_tunnel() if handle else None
    if tunnel and random.random() < kill_probability:
        try:
            import psutil
            proc = psutil.Process(tunnel.pid)
            if proc.is_running():
                logger.warning(
                    f'Simulating connection failure by killing SSH tunnel process (PID: {proc.pid})'
                )
                proc.terminate()
                proc.wait(timeout=5)
                return True
        except Exception as e:
            logger.error(f'Error killing SSH process: {e}')
    return False


def _test_autostop(
        cluster_name: str, num: int, thread_id: int,
        kill_ssh_probability: float) -> Tuple[List[bool], List[float]]:
    """Worker function that tests skylet set_autostop integration repeatedly."""
    results = []
    latencies = []

    logger.info(f"Thread {thread_id}: Starting set_autostop integration tests")

    for i in range(num):
        handle = _get_cluster_handle(cluster_name)
        ssh_killed = _simulate_ssh_process_kill(handle, kill_ssh_probability)
        if ssh_killed:
            # logger.warning(
            # f"Thread {thread_id}: Simulated SSH kill before test")
            pass

        start_time = time.time()
        try:
            request = autostopv1_pb2.SetAutostopRequest(
                idle_minutes=30,
                backend=cloud_vm_ray_backend.CloudVmRayBackend.NAME,
                wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS_AND_SSH,
                down=True)
            backend_utils.invoke_skylet_with_retries(
                lambda: cloud_vm_ray_backend.SkyletClient(
                    handle.get_grpc_channel()).set_autostop(request))

            end_time = time.time()
            latency = (end_time - start_time) * 1000  # Convert to milliseconds
            latencies.append(latency)
            results.append(True)

            logger.debug(
                f"Thread {thread_id}, Test {i+1}: Success, Latency: {latency:.2f}ms"
            )

        except Exception as e:
            end_time = time.time()
            latency = (end_time - start_time) * 1000  # Convert to milliseconds
            latencies.append(latency)
            results.append(False)
            logger.error(
                f'Thread {thread_id}, Test {i+1}: Error during autostop integration test: {e}, Latency: {latency:.2f}ms'
            )

    return results, latencies


@pytest.mark.xdist_group(name="skylet_grpc_sequential")
@pytest.mark.parametrize("parallelism,num_tests,kill_prob", [
    (10, 50, 0.01),
    (10, 50, 0.05),
    (10, 50, 0.1),
    (10, 50, 0.2),
])
def test_skylet_grpc_connectivity(test_cluster, parallelism: int,
                                  num_tests: int, kill_prob: float):
    """Test skylet gRPC under load with optional SSH kill simulation."""
    cluster_name = test_cluster

    all_results = []
    all_latencies = []

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=parallelism) as executor:
        futures = []
        for thread_id in range(parallelism):
            future = executor.submit(_test_autostop, cluster_name, num_tests,
                                     thread_id, kill_prob)
            futures.append(future)

        for future in concurrent.futures.as_completed(futures):
            try:
                thread_results, thread_latencies = future.result()
                all_results.extend(thread_results)
                all_latencies.extend(thread_latencies)
            except Exception as exc:
                logger.error(f'Thread generated an exception: {exc}')

    # Calculate statistics
    success_rate = sum(all_results) / len(all_results) if all_results else 0

    if all_latencies:
        median_latency = statistics.median(all_latencies)
        mean_latency = statistics.mean(all_latencies)
        min_latency = min(all_latencies)
        max_latency = max(all_latencies)

        # Separate successful and failed request latencies
        success_latencies = [
            lat for result, lat in zip(all_results, all_latencies) if result
        ]
        failed_latencies = [
            lat for result, lat in zip(all_results, all_latencies) if not result
        ]

        logger.info(
            f"Overall Results - Success rate: {success_rate:.2%} ({sum(all_results)}/{len(all_results)} tests)"
        )
        logger.info(
            f"Overall Latency - Median: {median_latency:.2f}ms, Mean: {mean_latency:.2f}ms, "
            f"Min: {min_latency:.2f}ms, Max: {max_latency:.2f}ms")

        if success_latencies:
            success_median = statistics.median(success_latencies)
            logger.info(
                f"Successful requests - Count: {len(success_latencies)}, Median latency: {success_median:.2f}ms"
            )

        if failed_latencies:
            failed_median = statistics.median(failed_latencies)
            logger.info(
                f"Failed requests - Count: {len(failed_latencies)}, Median latency: {failed_median:.2f}ms"
            )
    else:
        logger.warning("No latency data collected")
