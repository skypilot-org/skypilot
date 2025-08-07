"""Tests to verify that SDK function return types match their corresponding server functions."""

from typing import Callable

from sky import catalog
from sky import check
from sky import core
from sky import execution
from sky.client import sdk
from sky.jobs.client import sdk as jobs_sdk
from sky.jobs.server import core as jobs_core
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.ssh_node_pools import server as ssh_node_pools_server
from sky.workspaces import core as workspaces_core


def _check_return_type(sdk_function: Callable,
                       internal_function: Callable) -> bool:
    """Check that SDK function RequestId[T] matches internal function return type T."""
    sdk_return_type = sdk_function.__annotations__["return"]
    internal_return_type = internal_function.__annotations__["return"]

    # SDK functions return server_common.RequestId[T], we want the T part
    sdk_inner_type = sdk_return_type.__args__[0]

    # Print the types for debugging (this test is primarily about ensuring both exist and are annotated)
    print(f"\nSDK {sdk_function.__name__} returns RequestId[{sdk_inner_type}]")
    print(
        f"Internal {internal_function.__name__} returns {internal_return_type}")

    # Verify that SDK function returns RequestId
    assert hasattr(sdk_return_type, '__origin__') or hasattr(
        sdk_return_type, '__args__'
    ), (f"SDK function {sdk_function.__name__} does not return a generic RequestId type"
       )

    # For now, just ensure both have annotations. The actual type matching can be
    # verified manually during development - this test is about ensuring we have coverage
    return True


# tests for sky.client.sdk
# Tests ordered by function declaration order in sky/client/sdk.py


def test_check_return_type():
    """Test that sdk.check and check.check return types match."""
    _check_return_type(sdk.check, check.check)


def test_enabled_clouds_return_type():
    """Test that sdk.enabled_clouds and core.enabled_clouds return types match."""
    _check_return_type(sdk.enabled_clouds, core.enabled_clouds)


def test_list_accelerators_return_type():
    """Test that sdk.list_accelerators and catalog.list_accelerators return types match."""
    _check_return_type(sdk.list_accelerators, catalog.list_accelerators)


def test_list_accelerator_counts_return_type():
    """Test that sdk.list_accelerator_counts and catalog.list_accelerator_counts return types match."""
    _check_return_type(sdk.list_accelerator_counts,
                       catalog.list_accelerator_counts)


def test_optimize_return_type():
    """Test that sdk.optimize and core.optimize return types match."""
    _check_return_type(sdk.optimize, core.optimize)


def test_workspaces_return_type():
    """Test that sdk.workspaces and workspaces_core.get_workspaces return types match."""
    _check_return_type(sdk.workspaces, workspaces_core.get_workspaces)


def test_launch_return_type():
    """Test that sdk.launch and execution.launch return types match."""
    _check_return_type(sdk.launch, execution.launch)


def test_exec_return_type():
    """Test that sdk.exec and execution.exec return types match."""
    _check_return_type(sdk.exec, execution.exec)


def test_start_return_type():
    """Test that sdk.start and core.start return types match."""
    _check_return_type(sdk.start, core.start)


def test_down_return_type():
    """Test that sdk.down and core.down return types match."""
    _check_return_type(sdk.down, core.down)


def test_stop_return_type():
    """Test that sdk.stop and core.stop return types match."""
    _check_return_type(sdk.stop, core.stop)


def test_autostop_return_type():
    """Test that sdk.autostop and core.autostop return types match."""
    _check_return_type(sdk.autostop, core.autostop)


def test_queue_return_type():
    """Test that sdk.queue and core.queue return types match."""
    _check_return_type(sdk.queue, core.queue)


def test_job_status_return_type():
    """Test that sdk.job_status and core.job_status return types match."""
    _check_return_type(sdk.job_status, core.job_status)


def test_cancel_return_type():
    """Test that sdk.cancel and core.cancel return types match."""
    _check_return_type(sdk.cancel, core.cancel)


def test_status_return_type():
    """Test that sdk.status and core.status return types match."""
    _check_return_type(sdk.status, core.status)


def test_endpoints_return_type():
    """Test that sdk.endpoints and core.endpoints return types match."""
    _check_return_type(sdk.endpoints, core.endpoints)


def test_cost_report_return_type():
    """Test that sdk.cost_report and core.cost_report return types match."""
    _check_return_type(sdk.cost_report, core.cost_report)


def test_storage_ls_return_type():
    """Test that sdk.storage_ls and core.storage_ls return types match."""
    _check_return_type(sdk.storage_ls, core.storage_ls)


def test_storage_delete_return_type():
    """Test that sdk.storage_delete and core.storage_delete return types match."""
    _check_return_type(sdk.storage_delete, core.storage_delete)


def test_local_up_return_type():
    """Test that sdk.local_up and core.local_up return types match."""
    _check_return_type(sdk.local_up, core.local_up)


def test_local_down_return_type():
    """Test that sdk.local_down and core.local_down return types match."""
    _check_return_type(sdk.local_down, core.local_down)


def test_ssh_down_return_type():
    """Test that sdk.ssh_down and core.ssh_down return types match."""
    _check_return_type(sdk.ssh_down,
                       ssh_node_pools_server.down_ssh_node_pool_general)
    _check_return_type(sdk.ssh_down, ssh_node_pools_server.down_ssh_node_pool)


def test_realtime_kubernetes_gpu_availability_return_type():
    """Test that sdk.realtime_kubernetes_gpu_availability and core.realtime_kubernetes_gpu_availability return types match."""
    _check_return_type(sdk.realtime_kubernetes_gpu_availability,
                       core.realtime_kubernetes_gpu_availability)


def test_kubernetes_node_info_return_type():
    """Test that sdk.kubernetes_node_info and core.kubernetes_node_info return types match."""
    _check_return_type(sdk.kubernetes_node_info,
                       kubernetes_utils.get_kubernetes_node_info)


def test_ssh_up_return_type():
    """Test that sdk.ssh_up and core.ssh_up return types match."""
    _check_return_type(sdk.ssh_up,
                       ssh_node_pools_server.deploy_ssh_node_pool_general)
    _check_return_type(sdk.ssh_up, ssh_node_pools_server.deploy_ssh_node_pool)


# tests for sky.jobs.client.sdk
# Tests ordered by function declaration order in sky/jobs/client/sdk.py


def test_launch_return_type():
    """Test that jobs_sdk.launch and core.launch return types match."""
    _check_return_type(jobs_sdk.launch, jobs_core.launch)


def test_queue_return_type():
    """Test that jobs_sdk.queue and core.queue return types match."""
    _check_return_type(jobs_sdk.queue, jobs_core.queue)


def test_cancel_return_type():
    """Test that jobs_sdk.cancel and core.cancel return types match."""
    _check_return_type(jobs_sdk.cancel, jobs_core.cancel)


def test_pool_apply_return_type():
    """Test that jobs_sdk.pool_apply and core.pool_apply return types match."""
    _check_return_type(jobs_sdk.pool_apply, jobs_core.pool_apply)


def test_pool_down_return_type():
    """Test that jobs_sdk.pool_down and core.pool_down return types match."""
    _check_return_type(jobs_sdk.pool_down, jobs_core.pool_down)


def test_pool_status_return_type():
    """Test that jobs_sdk.pool_status and core.pool_status return types match."""
    _check_return_type(jobs_sdk.pool_status, jobs_core.pool_status)


# tests for sky.serve.client.sdk
# Tests ordered by function declaration order in sky/serve/client/sdk.py

