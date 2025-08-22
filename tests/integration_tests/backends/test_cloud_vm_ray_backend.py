import concurrent.futures
import random
from typing import List

import pytest

from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.schemas.generated import autostopv1_pb2

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
    if handle and handle.skylet_ssh_tunnel and random.random(
    ) < kill_probability:
        try:
            import psutil
            proc = psutil.Process(handle.skylet_ssh_tunnel.pid)
            if proc.is_running():
                logger.warning(
                    f'Simulating connection failure by killing SSH tunnel process (PID: {proc.pid})'
                )
                proc.terminate()
                proc.wait(timeout=5)
                handle.skylet_ssh_tunnel = None
                return True
        except Exception as e:
            logger.error(f'Error killing SSH process: {e}')
    return False


def _test_autostop(cluster_name: str, num: int, thread_id: int,
                   kill_ssh_probability: float) -> List[bool]:
    """Worker function that tests skylet set_autostop integration repeatedly."""
    results = []

    logger.info(f"Thread {thread_id}: Starting set_autostop integration tests")

    for i in range(num):
        ssh_killed = _simulate_ssh_process_kill(handle, kill_ssh_probability)
        if ssh_killed:
            logger.warning(
                f"Thread {thread_id}: Simulated SSH kill before test")

        try:
            handle = _get_cluster_handle(cluster_name)
            request = autostopv1_pb2.SetAutostopRequest(
                idle_minutes=1,
                backend=cloud_vm_ray_backend.CloudVmRayBackend.NAME,
                wait_for=autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS_AND_SSH,
                down=True)
            backend_utils.invoke_skylet_with_retries(
                handle, lambda: cloud_vm_ray_backend.SkyletClient(
                    handle.get_grpc_channel()).set_autostop(request))

            results.append(True)
        except Exception as e:
            logger.error(f'Error during autostop integration test: {e}')
            results.append(False)
    return results


@pytest.mark.xdist_group(name="skylet_grpc_sequential")
@pytest.mark.parametrize("parallelism,num_tests,kill_prob", [
    (2, 3, 0.0),
    (3, 5, 0.1),
    (5, 10, 0.2),
])
def test_skylet_grpc_connectivity(test_cluster, parallelism: int,
                                  num_tests: int, kill_prob: float):
    """Test skylet gRPC under load with optional SSH kill simulation."""
    cluster_name = test_cluster

    all_results = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=parallelism) as executor:
        futures = []
        for thread_id in range(parallelism):
            future = executor.submit(_test_autostop, cluster_name, num_tests,
                                     thread_id, kill_prob)
            futures.append(future)

        for future in concurrent.futures.as_completed(futures):
            try:
                thread_results = future.result()
                all_results.extend(thread_results)
            except Exception as exc:
                logger.error(f'Thread generated an exception: {exc}')

    # all_results is now a flat list of bools
    success_rate = sum(all_results) / len(all_results) if all_results else 0

    logger.info(
        f"Success rate: {success_rate:.2%} ({sum(all_results)}/{len(all_results)} tests)"
    )
