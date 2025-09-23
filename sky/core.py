"""SDK functions for cluster/job management."""
import os
import shlex
import typing
from typing import Any, Dict, List, Optional, Tuple, Union

import colorama

from sky import admin_policy
from sky import backends
from sky import catalog
from sky import check as sky_check
from sky import clouds
from sky import dag as dag_lib
from sky import data
from sky import exceptions
from sky import global_user_state
from sky import models
from sky import optimizer
from sky import sky_logging
from sky import skypilot_config
from sky import task as task_lib
from sky.adaptors import common as adaptors_common
from sky.backends import backend_utils
from sky.backends import cloud_vm_ray_backend
from sky.clouds import cloud as sky_cloud
from sky.jobs.server import core as managed_jobs_core
from sky.provision.kubernetes import constants as kubernetes_constants
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.schemas.api import responses
from sky.skylet import autostop_lib
from sky.skylet import constants
from sky.skylet import job_lib
from sky.skylet import log_lib
from sky.usage import usage_lib
from sky.utils import admin_policy_utils
from sky.utils import common
from sky.utils import common_utils
from sky.utils import controller_utils
from sky.utils import resources_utils
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import ux_utils
from sky.utils.kubernetes import kubernetes_deploy_utils

if typing.TYPE_CHECKING:
    from sky import resources as resources_lib
    from sky.schemas.generated import jobsv1_pb2
else:
    jobsv1_pb2 = adaptors_common.LazyImport('sky.schemas.generated.jobsv1_pb2')

logger = sky_logging.init_logger(__name__)

# ======================
# = Cluster Management =
# ======================


@usage_lib.entrypoint
def optimize(
    dag: 'dag_lib.Dag',
    minimize: common.OptimizeTarget = common.OptimizeTarget.COST,
    blocked_resources: Optional[List['resources_lib.Resources']] = None,
    quiet: bool = False,
    request_options: Optional[admin_policy.RequestOptions] = None
) -> 'dag_lib.Dag':
    """Finds the best execution plan for the given DAG.

    Args:
        dag: the DAG to optimize.
        minimize: whether to minimize cost or time.
        blocked_resources: a list of resources that should not be used.
        quiet: whether to suppress logging.
        request_options: Request options used in enforcing admin policies.
            This is only required when a admin policy is in use,
            see: https://docs.skypilot.co/en/latest/cloud-setup/policy.html
    Returns:
        The optimized DAG.

    Raises:
        exceptions.ResourcesUnavailableError: if no resources are available
            for a task.
        exceptions.NoCloudAccessError: if no public clouds are enabled.
    """
    # TODO: We apply the admin policy only on the first DAG optimization which
    # is shown on `sky launch`. The optimizer is also invoked during failover,
    # but we do not apply the admin policy there. We should apply the admin
    # policy in the optimizer, but that will require some refactoring.
    with admin_policy_utils.apply_and_use_config_in_current_request(
            dag, request_options=request_options) as dag:
        dag.resolve_and_validate_volumes()
        return optimizer.Optimizer.optimize(dag=dag,
                                            minimize=minimize,
                                            blocked_resources=blocked_resources,
                                            quiet=quiet)


@usage_lib.entrypoint
def status(
    cluster_names: Optional[Union[str, List[str]]] = None,
    refresh: common.StatusRefreshMode = common.StatusRefreshMode.NONE,
    all_users: bool = False,
    include_credentials: bool = False,
    summary_response: bool = False,
) -> List[responses.StatusResponse]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Gets cluster statuses.

    If cluster_names is given, return those clusters. Otherwise, return all
    clusters.

    Each returned value has the following fields:

    .. code-block:: python

        {
            'name': (str) cluster name,
            'launched_at': (int) timestamp of last launch on this cluster,
            'handle': (ResourceHandle) an internal handle to the cluster,
            'last_use': (str) the last command/entrypoint that affected this
              cluster,
            'status': (sky.ClusterStatus) cluster status,
            'autostop': (int) idle time before autostop,
            'to_down': (bool) whether autodown is used instead of autostop,
            'metadata': (dict) metadata of the cluster,
            'user_hash': (str) user hash of the cluster owner,
            'user_name': (str) user name of the cluster owner,
            'resources_str': (str) the resource string representation of the
              cluster,
        }

    Each cluster can have one of the following statuses:

    - ``INIT``: The cluster may be live or down. It can happen in the following
      cases:

      - Ongoing provisioning or runtime setup. (A ``sky.launch()`` has started
        but has not completed.)
      - Or, the cluster is in an abnormal state, e.g., some cluster nodes are
        down, or the SkyPilot runtime is unhealthy. (To recover the cluster,
        try ``sky launch`` again on it.)

    - ``UP``: Provisioning and runtime setup have succeeded and the cluster is
      live.  (The most recent ``sky.launch()`` has completed successfully.)

    - ``STOPPED``: The cluster is stopped and the storage is persisted. Use
      ``sky.start()`` to restart the cluster.

    Autostop column:

    - The autostop column indicates how long the cluster will be autostopped
      after minutes of idling (no jobs running). If ``to_down`` is True, the
      cluster will be autodowned, rather than autostopped.

    Getting up-to-date cluster statuses:

    - In normal cases where clusters are entirely managed by SkyPilot (i.e., no
      manual operations in cloud consoles) and no autostopping is used, the
      table returned by this command will accurately reflect the cluster
      statuses.

    - In cases where the clusters are changed outside of SkyPilot (e.g., manual
      operations in cloud consoles; unmanaged spot clusters getting preempted)
      or for autostop-enabled clusters, use ``refresh=True`` to query the
      latest cluster statuses from the cloud providers.

    Args:
        cluster_names: a list of cluster names to query. If not
            provided, all clusters will be queried.
        refresh: whether to query the latest cluster statuses from the cloud
            provider(s).
        include_credentials: whether to fetch ssh credentials for cluster
            (credentials field in responses.StatusResponse)

    Returns:
        A list of dicts, with each dict containing the information of a
        cluster. If a cluster is found to be terminated or not found, it will
        be omitted from the returned list.
    """
    clusters = backend_utils.get_clusters(
        refresh=refresh,
        cluster_names=cluster_names,
        all_users=all_users,
        include_credentials=include_credentials,
        summary_response=summary_response)

    status_responses = []
    for cluster in clusters:
        try:
            status_responses.append(
                responses.StatusResponse.model_validate(cluster))
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to validate status responses for cluster '
                           f'{cluster.get("name")}: {e}')
    return status_responses


def status_kubernetes(
) -> Tuple[List['kubernetes_utils.KubernetesSkyPilotClusterInfoPayload'],
           List['kubernetes_utils.KubernetesSkyPilotClusterInfoPayload'],
           List[Dict[str, Any]], Optional[str]]:
    """Gets all SkyPilot clusters and jobs in the Kubernetes cluster.

    Managed jobs and services are also included in the clusters returned.
    The caller must parse the controllers to identify which clusters are run
    as managed jobs or services.
all_clusters, unmanaged_clusters, all_jobs, context
    Returns:
        A tuple containing:
        - all_clusters: List of KubernetesSkyPilotClusterInfoPayload with info
            for all clusters, including managed jobs, services and controllers.
        - unmanaged_clusters: List of KubernetesSkyPilotClusterInfoPayload with
            info for all clusters excluding managed jobs and services.
            Controllers are included.
        - all_jobs: List of managed jobs from all controllers. Each entry is a
            dictionary job info, see jobs.queue_from_kubernetes_pod for details.
        - context: Kubernetes context used to fetch the cluster information.
    """
    context = kubernetes_utils.get_current_kube_config_context_name()
    try:
        pods = kubernetes_utils.get_skypilot_pods(context)
    except exceptions.ResourcesUnavailableError as e:
        with ux_utils.print_exception_no_traceback():
            raise ValueError('Failed to get SkyPilot pods from '
                             f'Kubernetes: {str(e)}') from e
    all_clusters, jobs_controllers, _ = (kubernetes_utils.process_skypilot_pods(
        pods, context))
    all_jobs = []
    with rich_utils.safe_status(
            ux_utils.spinner_message(
                '[bold cyan]Checking in-progress managed jobs[/]')) as spinner:
        for i, job_controller_info in enumerate(jobs_controllers):
            user = job_controller_info.user
            pod = job_controller_info.pods[0]
            status_message = '[bold cyan]Checking managed jobs controller'
            if len(jobs_controllers) > 1:
                status_message += f's ({i + 1}/{len(jobs_controllers)})'
            spinner.update(f'{status_message}[/]')
            try:
                job_list = managed_jobs_core.queue_from_kubernetes_pod(
                    pod.metadata.name)
            except RuntimeError as e:
                logger.warning('Failed to get managed jobs from controller '
                               f'{pod.metadata.name}: {str(e)}')
                job_list = []
            # Add user field to jobs
            for job in job_list:
                job['user'] = user
            all_jobs.extend(job_list)
    # Reconcile cluster state between managed jobs and clusters:
    # To maintain a clear separation between regular SkyPilot clusters
    # and those from managed jobs, we need to exclude the latter from
    # the main cluster list.
    # We do this by reconstructing managed job cluster names from each
    # job's name and ID. We then use this set to filter out managed
    # clusters from the main cluster list. This is necessary because there
    # are no identifiers distinguishing clusters from managed jobs from
    # regular clusters.
    managed_job_cluster_names = set()
    for job in all_jobs:
        # Managed job cluster name is <job_name>-<job_id>
        managed_cluster_name = f'{job["job_name"]}-{job["job_id"]}'
        managed_job_cluster_names.add(managed_cluster_name)
    unmanaged_clusters = [
        c for c in all_clusters
        if c.cluster_name not in managed_job_cluster_names
    ]
    all_clusters = [
        kubernetes_utils.KubernetesSkyPilotClusterInfoPayload.from_cluster(c)
        for c in all_clusters
    ]
    unmanaged_clusters = [
        kubernetes_utils.KubernetesSkyPilotClusterInfoPayload.from_cluster(c)
        for c in unmanaged_clusters
    ]
    return all_clusters, unmanaged_clusters, all_jobs, context


def endpoints(cluster: str,
              port: Optional[Union[int, str]] = None) -> Dict[int, str]:
    """Gets the endpoint for a given cluster and port number (endpoint).

    Args:
        cluster: The name of the cluster.
        port: The port number to get the endpoint for. If None, endpoints
            for all ports are returned..

    Returns: A dictionary of port numbers to endpoints. If port is None,
        the dictionary will contain all ports:endpoints exposed on the cluster.

    Raises:
    ValueError: if the cluster is not UP or the endpoint is not exposed.
        RuntimeError: if the cluster has no ports to be exposed or no endpoints
            are exposed yet.
    """
    with rich_utils.safe_status(
            ux_utils.spinner_message(
                f'Fetching endpoints for cluster {cluster}')):
        result = backend_utils.get_endpoints(cluster=cluster, port=port)
        return result


@usage_lib.entrypoint
def cost_report(
        days: Optional[int] = None,
        dashboard_summary_response: bool = False,
        cluster_hashes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Get all cluster cost reports, including those that have been downed.

    Each returned value has the following fields:

    .. code-block:: python

        {
            'name': (str) cluster name,
            'launched_at': (int) timestamp of last launch on this cluster,
            'duration': (int) total seconds that cluster was up and running,
            'last_use': (str) the last command/entrypoint that affected this
            'num_nodes': (int) number of nodes launched for cluster,
            'resources': (resources.Resources) type of resource launched,
            'cluster_hash': (str) unique hash identifying cluster,
            'usage_intervals': (List[Tuple[int, int]]) cluster usage times,
            'total_cost': (float) cost given resources and usage intervals,
            'cloud': (str) cloud of the cluster,
            'region': (str) region of the cluster,
            'cpus': (str) number of vCPUs of the cluster,
            'memory': (str) memory of the cluster,
            'accelerators': (str) accelerators of the cluster,
            'resources_str': (str) resources string of the cluster,
            'resources_str_full': (str) full resources string of the cluster,
        }

    The estimated cost column indicates price for the cluster based on the type
    of resources being used and the duration of use up until the call to
    status. This means if the cluster is UP, successive calls to report will
    show increasing price. The estimated cost is calculated based on the local
    cache of the cluster status, and may not be accurate for the cluster with
    autostop/use_spot set or terminated/stopped on the cloud console.

    Args:
        days: Number of days to look back from now. Active clusters are always
            included. Historical clusters are only included if they were last
            used within the past 'days' days. Defaults to 30 days.

    Returns:
        A list of dicts, with each dict containing the cost information of a
        cluster.
    """
    if days is None:
        days = constants.COST_REPORT_DEFAULT_DAYS

    abbreviate_response = dashboard_summary_response and cluster_hashes is None

    cluster_reports = global_user_state.get_clusters_from_history(
        days=days,
        abbreviate_response=abbreviate_response,
        cluster_hashes=cluster_hashes)
    logger.debug(
        f'{len(cluster_reports)} clusters found from history with {days} days.')

    def _process_cluster_report(
            cluster_report: Dict[str, Any]) -> Dict[str, Any]:
        """Process cluster report by calculating cost and adding fields."""
        # Make a copy to avoid modifying the original
        report = cluster_report.copy()

        def get_total_cost(cluster_report: dict) -> float:
            duration = cluster_report['duration']
            launched_nodes = cluster_report['num_nodes']
            launched_resources = cluster_report['resources']

            cost = (launched_resources.get_cost(duration) * launched_nodes)
            return cost

        try:
            report['total_cost'] = get_total_cost(report)
        except Exception as e:  # pylint: disable=broad-except
            # Ok to skip the total cost as this is just for display purposes.
            logger.warning(f'Failed to get total cost for cluster '
                           f'{report["name"]}: {str(e)}')
            report['total_cost'] = 0.0

        return report

    # Process clusters in parallel
    if not cluster_reports:
        return []

    if not abbreviate_response:
        cluster_reports = subprocess_utils.run_in_parallel(
            _process_cluster_report, cluster_reports)

    def _update_record_with_resources(record: Dict[str, Any]) -> None:
        """Add resource fields for dashboard compatibility."""
        if record is None:
            return
        resources = record.get('resources')
        if resources is None:
            return
        if not dashboard_summary_response:
            fields = ['cloud', 'region', 'cpus', 'memory', 'accelerators']
        else:
            fields = ['cloud']
        for field in fields:
            try:
                record[field] = str(getattr(resources, field))
            except Exception as e:  # pylint: disable=broad-except
                # Ok to skip the fields as this is just for display
                # purposes.
                logger.debug(f'Failed to get resources.{field} for cluster '
                             f'{record["name"]}: {str(e)}')
                record[field] = None

        # Add resources_str and resources_str_full for dashboard
        # compatibility
        num_nodes = record.get('num_nodes', 1)
        try:
            resource_str_simple = resources_utils.format_resource(resources,
                                                                  simplify=True)
            record['resources_str'] = f'{num_nodes}x{resource_str_simple}'
            if not abbreviate_response:
                resource_str_full = resources_utils.format_resource(
                    resources, simplify=False)
                record[
                    'resources_str_full'] = f'{num_nodes}x{resource_str_full}'
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Failed to get resources_str for cluster '
                         f'{record["name"]}: {str(e)}')
            for field in fields:
                record[field] = None
            record['resources_str'] = '-'
            if not abbreviate_response:
                record['resources_str_full'] = '-'

    for report in cluster_reports:
        _update_record_with_resources(report)
        if dashboard_summary_response:
            report.pop('usage_intervals')
            report.pop('user_hash')
            report.pop('resources')

    return cluster_reports


def _start(
    cluster_name: str,
    idle_minutes_to_autostop: Optional[int] = None,
    wait_for: Optional[autostop_lib.AutostopWaitFor] = (
        autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR),
    retry_until_up: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    force: bool = False,
) -> backends.CloudVmRayResourceHandle:

    cluster_status, handle = backend_utils.refresh_cluster_status_handle(
        cluster_name)
    if handle is None:
        raise exceptions.ClusterDoesNotExist(
            f'Cluster {cluster_name!r} does not exist.')
    if not force and cluster_status == status_lib.ClusterStatus.UP:
        sky_logging.print(f'Cluster {cluster_name!r} is already up.')
        return handle
    assert force or cluster_status in (
        status_lib.ClusterStatus.INIT,
        status_lib.ClusterStatus.STOPPED), cluster_status

    backend = backend_utils.get_backend_from_handle(handle)
    if not isinstance(backend, backends.CloudVmRayBackend):
        raise exceptions.NotSupportedError(
            f'Starting cluster {cluster_name!r} with backend {backend.NAME} '
            'is not supported.')

    controller = controller_utils.Controllers.from_name(cluster_name)
    if controller is not None:
        if down or idle_minutes_to_autostop:
            arguments = []
            if down:
                arguments.append('`down`')
            if idle_minutes_to_autostop is not None:
                arguments.append('`idle_minutes_to_autostop`')
            arguments_str = ' and '.join(arguments) + ' argument'
            if len(arguments) > 1:
                arguments_str += 's'
            raise ValueError(
                'Passing per-request autostop/down settings is currently not '
                'supported when starting SkyPilot controllers. To '
                f'fix: omit the {arguments_str} to use the '
                f'default autostop settings from config.')

        # Get the autostop resources, from which we extract the correct autostop
        # config.
        controller_resources = controller_utils.get_controller_resources(
            controller, [])
        # All resources should have the same autostop config.
        controller_autostop_config = list(
            controller_resources)[0].autostop_config
        if (controller_autostop_config is not None and
                controller_autostop_config.enabled):
            idle_minutes_to_autostop = controller_autostop_config.idle_minutes
            down = controller_autostop_config.down

    usage_lib.record_cluster_name_for_current_operation(cluster_name)

    with dag_lib.Dag():
        dummy_task = task_lib.Task().set_resources(handle.launched_resources)
        dummy_task.num_nodes = handle.launched_nodes
    (handle, _) = backend.provision(dummy_task,
                                    to_provision=handle.launched_resources,
                                    dryrun=False,
                                    stream_logs=True,
                                    cluster_name=cluster_name,
                                    retry_until_up=retry_until_up)
    storage_mounts = backend.get_storage_mounts_metadata(handle.cluster_name)
    # Passing all_file_mounts as None ensures the local source set in Storage
    # to not redundantly sync source to the bucket.
    backend.sync_file_mounts(handle=handle,
                             all_file_mounts=None,
                             storage_mounts=storage_mounts)
    if idle_minutes_to_autostop is not None:
        backend.set_autostop(handle, idle_minutes_to_autostop, wait_for, down)
    return handle


@usage_lib.entrypoint
def start(
    cluster_name: str,
    idle_minutes_to_autostop: Optional[int] = None,
    wait_for: Optional[autostop_lib.AutostopWaitFor] = (
        autostop_lib.DEFAULT_AUTOSTOP_WAIT_FOR),
    retry_until_up: bool = False,
    down: bool = False,  # pylint: disable=redefined-outer-name
    force: bool = False,
) -> backends.CloudVmRayResourceHandle:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Restart a cluster.

    If a cluster is previously stopped (status is STOPPED) or failed in
    provisioning/runtime installation (status is INIT), this function will
    attempt to start the cluster.  In the latter case, provisioning and runtime
    installation will be retried.

    Auto-failover provisioning is not used when restarting a stopped
    cluster. It will be started on the same cloud, region, and zone that were
    chosen before.

    If a cluster is already in the UP status, this function has no effect.

    Args:
        cluster_name: name of the cluster to start.
        idle_minutes_to_autostop: automatically stop the cluster after this
            many minute of idleness, i.e., no running or pending jobs in the
            cluster's job queue. Idleness gets reset whenever setting-up/
            running/pending jobs are found in the job queue. Setting this
            flag is equivalent to running ``sky.launch()`` and then
            ``sky.autostop(idle_minutes=<minutes>)``. If not set, the
            cluster will not be autostopped.
        retry_until_up: whether to retry launching the cluster until it is
            up.
        down: Autodown the cluster: tear down the cluster after specified
            minutes of idle time after all jobs finish (successfully or
            abnormally). Requires ``idle_minutes_to_autostop`` to be set.
        force: whether to force start the cluster even if it is already up.
            Useful for upgrading SkyPilot runtime.

    Raises:
        ValueError: argument values are invalid: (1) if ``down`` is set to True
          but ``idle_minutes_to_autostop`` is None; (2) if the specified
          cluster is the managed jobs controller, and either
          ``idle_minutes_to_autostop`` is not None or ``down`` is True (omit
          them to use the default autostop settings).
        sky.exceptions.ClusterDoesNotExist: the specified cluster does not
          exist.
        sky.exceptions.NotSupportedError: if the cluster to restart was
          launched using a non-default backend that does not support this
          operation.
        sky.exceptions.ClusterOwnerIdentitiesMismatchError: if the cluster to
            restart was launched by a different user.
    """
    if down and idle_minutes_to_autostop is None:
        raise ValueError(
            '`idle_minutes_to_autostop` must be set if `down` is True.')
    return _start(cluster_name,
                  idle_minutes_to_autostop,
                  wait_for,
                  retry_until_up,
                  down,
                  force=force)


def _stop_not_supported_message(resources: 'resources_lib.Resources') -> str:
    if resources.use_spot:
        message = ('Stopping spot instances is currently not supported on '
                   f'{resources.cloud}')
    else:
        cloud_name = resources.cloud.display_name(
        ) if resources.cloud else resources.cloud
        message = ('Stopping is currently not supported for '
                   f'{cloud_name}')
    return message


@usage_lib.entrypoint
def down(cluster_name: str, purge: bool = False) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Tears down a cluster.

    Tearing down a cluster will delete all associated resources (all billing
    stops), and any data on the attached disks will be lost.  Accelerators
    (e.g., TPUs) that are part of the cluster will be deleted too.

    Args:
        cluster_name: name of the cluster to down.
        purge: (Advanced) Forcefully remove the cluster from SkyPilot's cluster
            table, even if the actual cluster termination failed on the cloud.
            WARNING: This flag should only be set sparingly in certain manual
            troubleshooting scenarios; with it set, it is the user's
            responsibility to ensure there are no leaked instances and related
            resources.

    Raises:
        sky.exceptions.ClusterDoesNotExist: the specified cluster does not
          exist.
        RuntimeError: failed to tear down the cluster.
        sky.exceptions.NotSupportedError: the specified cluster is the managed
          jobs controller.
    """
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        raise exceptions.ClusterDoesNotExist(
            f'Cluster {cluster_name!r} does not exist.')

    usage_lib.record_cluster_name_for_current_operation(cluster_name)
    backend = backend_utils.get_backend_from_handle(handle)
    backend.teardown(handle, terminate=True, purge=purge)


@usage_lib.entrypoint
def stop(cluster_name: str, purge: bool = False) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Stops a cluster.

    Data on attached disks is not lost when a cluster is stopped.  Billing for
    the instances will stop, while the disks will still be charged.  Those
    disks will be reattached when restarting the cluster.

    Currently, spot instance clusters cannot be stopped (except for GCP, which
    does allow disk contents to be preserved when stopping spot VMs).

    Args:
        cluster_name: name of the cluster to stop.
        purge: (Advanced) Forcefully mark the cluster as stopped in SkyPilot's
            cluster table, even if the actual cluster stop operation failed on
            the cloud. WARNING: This flag should only be set sparingly in
            certain manual troubleshooting scenarios; with it set, it is the
            user's responsibility to ensure there are no leaked instances and
            related resources.

    Raises:
        sky.exceptions.ClusterDoesNotExist: the specified cluster does not
          exist.
        RuntimeError: failed to stop the cluster.
        sky.exceptions.NotSupportedError: if the specified cluster is a spot
          cluster, or a TPU VM Pod cluster, or the managed jobs controller.
    """
    if controller_utils.Controllers.from_name(cluster_name) is not None:
        raise exceptions.NotSupportedError(
            f'Stopping SkyPilot controller {cluster_name!r} '
            f'is not supported.')
    handle = global_user_state.get_handle_from_cluster_name(cluster_name)
    if handle is None:
        raise exceptions.ClusterDoesNotExist(
            f'Cluster {cluster_name!r} does not exist.')

    global_user_state.add_cluster_event(
        cluster_name, status_lib.ClusterStatus.STOPPED,
        'Cluster was stopped by user.',
        global_user_state.ClusterEventType.STATUS_CHANGE)

    backend = backend_utils.get_backend_from_handle(handle)

    if isinstance(backend, backends.CloudVmRayBackend):
        assert isinstance(handle, backends.CloudVmRayResourceHandle), handle
        # Check cloud supports stopping instances
        cloud = handle.launched_resources.cloud
        assert cloud is not None, handle
        try:
            cloud.check_features_are_supported(
                handle.launched_resources,
                {clouds.CloudImplementationFeatures.STOP})
        except exceptions.NotSupportedError as e:
            raise exceptions.NotSupportedError(
                f'{colorama.Fore.YELLOW}Stopping cluster '
                f'{cluster_name!r}... skipped.{colorama.Style.RESET_ALL}\n'
                f'  {_stop_not_supported_message(handle.launched_resources)}.\n'
                '  To terminate the cluster instead, run: '
                f'{colorama.Style.BRIGHT}sky down {cluster_name}') from e

    usage_lib.record_cluster_name_for_current_operation(cluster_name)
    backend.teardown(handle, terminate=False, purge=purge)


@usage_lib.entrypoint
def autostop(
        cluster_name: str,
        idle_minutes: int,
        wait_for: Optional[autostop_lib.AutostopWaitFor] = autostop_lib.
    DEFAULT_AUTOSTOP_WAIT_FOR,
        down: bool = False,  # pylint: disable=redefined-outer-name
) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Schedules an autostop/autodown for a cluster.

    Autostop/autodown will automatically stop or teardown a cluster when it
    becomes idle for a specified duration.  Idleness means there are no
    in-progress (pending/running) jobs in a cluster's job queue.

    Idleness time of a cluster is reset to zero, whenever:

    - A job is submitted (``sky.launch()`` or ``sky.exec()``).

    - The cluster has restarted.

    - An autostop is set when there is no active setting. (Namely, either
      there's never any autostop setting set, or the previous autostop setting
      was canceled.) This is useful for restarting the autostop timer.

    Example: say a cluster without any autostop set has been idle for 1 hour,
    then an autostop of 30 minutes is set. The cluster will not be immediately
    autostopped. Instead, the idleness timer only starts counting after the
    autostop setting was set.

    When multiple autostop settings are specified for the same cluster, the
    last setting takes precedence.

    Args:
        cluster_name: name of the cluster.
        idle_minutes: the number of minutes of idleness (no pending/running
          jobs) after which the cluster will be stopped automatically. Setting
          to a negative number cancels any autostop/autodown setting.
        down: if true, use autodown (tear down the cluster; non-restartable),
          rather than autostop (restartable).

    Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend or the cluster is TPU VM Pod.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    is_cancel = idle_minutes < 0
    verb = 'Cancelling' if is_cancel else 'Scheduling'
    option_str = 'down' if down else 'stop'
    if is_cancel:
        option_str = '{stop,down}'
    operation = f'{verb} auto{option_str}'
    if controller_utils.Controllers.from_name(cluster_name) is not None:
        raise exceptions.NotSupportedError(
            f'{operation} SkyPilot controller {cluster_name!r} '
            f'is not supported.')
    handle = backend_utils.check_cluster_available(
        cluster_name,
        operation=operation,
    )
    backend = backend_utils.get_backend_from_handle(handle)

    resources = handle.launched_resources.assert_launchable()
    # Check cloud supports stopping spot instances
    cloud = resources.cloud

    if not isinstance(backend, backends.CloudVmRayBackend):
        raise exceptions.NotSupportedError(
            f'{operation} cluster {cluster_name!r} with backend '
            f'{backend.__class__.__name__!r} is not supported.')

    # Check if autostop/autodown is required and supported
    if not is_cancel:
        try:
            if down:
                cloud.check_features_are_supported(
                    resources, {clouds.CloudImplementationFeatures.AUTODOWN})
            else:
                cloud.check_features_are_supported(
                    resources, {clouds.CloudImplementationFeatures.STOP})
                cloud.check_features_are_supported(
                    resources, {clouds.CloudImplementationFeatures.AUTOSTOP})
        except exceptions.NotSupportedError as e:
            raise exceptions.NotSupportedError(
                f'{colorama.Fore.YELLOW}{operation} on cluster '
                f'{cluster_name!r}...skipped.{colorama.Style.RESET_ALL}\n'
                f'  Auto{option_str} is not supported on {cloud!r} - '
                f'see reason above.') from e

    usage_lib.record_cluster_name_for_current_operation(cluster_name)
    backend.set_autostop(handle, idle_minutes, wait_for, down)


# ==================
# = Job Management =
# ==================


@usage_lib.entrypoint
def queue(cluster_name: str,
          skip_finished: bool = False,
          all_users: bool = False) -> List[dict]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Gets the job queue of a cluster.

    Please refer to the sky.cli.queue for the document.

    Returns:
        List[dict]:
        [
            {
                'job_id': (int) job id,
                'job_name': (str) job name,
                'username': (str) username,
                'user_hash': (str) user hash,
                'submitted_at': (int) timestamp of submitted,
                'start_at': (int) timestamp of started,
                'end_at': (int) timestamp of ended,
                'resources': (str) resources,
                'status': (job_lib.JobStatus) job status,
                'log_path': (str) log path,
            }
        ]
    raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
        exceptions.CommandError: if failed to get the job queue with ssh.
    """
    all_jobs = not skip_finished
    if all_users:
        user_hash = None
    else:
        user_hash = common_utils.get_current_user().id

    handle = backend_utils.check_cluster_available(
        cluster_name,
        operation='getting the job queue',
    )
    backend = backend_utils.get_backend_from_handle(handle)

    use_legacy = not handle.is_grpc_enabled_with_flag

    if handle.is_grpc_enabled_with_flag:
        try:
            request = jobsv1_pb2.GetJobQueueRequest(user_hash=user_hash,
                                                    all_jobs=all_jobs)
            response = backend_utils.invoke_skylet_with_retries(
                lambda: cloud_vm_ray_backend.SkyletClient(
                    handle.get_grpc_channel()).get_job_queue(request))
            jobs = []
            for job_info in response.jobs:
                job_dict = {
                    'job_id': job_info.job_id,
                    'job_name': job_info.job_name,
                    'submitted_at': job_info.submitted_at,
                    'status': job_lib.JobStatus.from_protobuf(job_info.status),
                    'run_timestamp': job_info.run_timestamp,
                    'start_at': job_info.start_at
                                if job_info.HasField('start_at') else None,
                    'end_at': job_info.end_at
                              if job_info.HasField('end_at') else None,
                    'resources': job_info.resources,
                    'log_path': job_info.log_path,
                    'user_hash': job_info.username,
                }
                # Copied from job_lib.load_job_queue.
                user = global_user_state.get_user(job_dict['user_hash'])
                job_dict['username'] = user.name if user is not None else None
                jobs.append(job_dict)
        except exceptions.SkyletMethodNotImplementedError:
            use_legacy = True

    if use_legacy:
        code = job_lib.JobLibCodeGen.get_job_queue(user_hash, all_jobs)
        returncode, jobs_payload, stderr = backend.run_on_head(
            handle, code, require_outputs=True, separate_stderr=True)
        subprocess_utils.handle_returncode(
            returncode,
            command=code,
            error_msg=f'Failed to get job queue on cluster {cluster_name}.',
            stderr=f'{jobs_payload + stderr}',
            stream_logs=True)
        jobs = job_lib.load_job_queue(jobs_payload)
    return jobs


@usage_lib.entrypoint
# pylint: disable=redefined-builtin
def cancel(
    cluster_name: str,
    all: bool = False,
    all_users: bool = False,
    job_ids: Optional[List[int]] = None,
    # pylint: disable=invalid-name
    # Internal only:
    _try_cancel_if_cluster_is_init: bool = False,
) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Cancels jobs on a cluster.

    Please refer to the sky.cli.cancel for the document.

    When none of `job_ids`, `all` and `all_users` is set, cancel the latest
    running job.

    Additional arguments:
        try_cancel_if_cluster_is_init: (bool) whether to try cancelling the job
            even if the cluster is not UP, but the head node is still alive.
            This is used by the jobs controller to cancel the job when the
            worker node is preempted in the spot cluster.

    Raises:
        ValueError: if arguments are invalid.
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the specified cluster is a
          controller that does not support this operation.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    controller_utils.check_cluster_name_not_controller(
        cluster_name, operation_str='Cancelling jobs')

    if all and job_ids is not None:
        raise exceptions.NotSupportedError(
            'Cannot specify both --all and job IDs.')

    # Check the status of the cluster.
    handle = None
    try:
        handle = backend_utils.check_cluster_available(
            cluster_name,
            operation='cancelling jobs',
        )
    except exceptions.ClusterNotUpError as e:
        if not _try_cancel_if_cluster_is_init:
            raise
        assert (e.handle is None or
                isinstance(e.handle, backends.CloudVmRayResourceHandle)), e
        if (e.handle is None or e.handle.head_ip is None):
            raise
        handle = e.handle
        # Even if the cluster is not UP, we can still try to cancel the job if
        # the head node is still alive. This is useful when a spot cluster's
        # worker node is preempted, but we can still cancel the job on the head
        # node.

    assert handle is not None, (
        f'handle for cluster {cluster_name!r} should not be None')

    backend = backend_utils.get_backend_from_handle(handle)
    user_hash: Optional[str] = common_utils.get_current_user().id

    if all_users:
        user_hash = None
        sky_logging.print(
            f'{colorama.Fore.YELLOW}'
            f'Cancelling all users\' jobs on cluster {cluster_name!r}...'
            f'{colorama.Style.RESET_ALL}')
    elif all:
        sky_logging.print(
            f'{colorama.Fore.YELLOW}'
            f'Cancelling all your jobs on cluster {cluster_name!r}...'
            f'{colorama.Style.RESET_ALL}')
    elif job_ids is not None:
        jobs_str = ', '.join(map(str, job_ids))
        sky_logging.print(
            f'{colorama.Fore.YELLOW}'
            f'Cancelling jobs ({jobs_str}) on cluster {cluster_name!r}...'
            f'{colorama.Style.RESET_ALL}')
    else:
        sky_logging.print(
            f'{colorama.Fore.YELLOW}'
            f'Cancelling latest running job on cluster {cluster_name!r}...'
            f'{colorama.Style.RESET_ALL}')

    backend.cancel_jobs(handle,
                        job_ids,
                        cancel_all=all or all_users,
                        user_hash=user_hash)


@usage_lib.entrypoint
def tail_logs(cluster_name: str,
              job_id: Optional[int],
              follow: bool = True,
              tail: int = 0) -> int:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Tails the logs of a job.

    Please refer to the sky.cli.tail_logs for the document.

    Raises:
        ValueError: if arguments are invalid or the cluster is not supported.
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.

    Returns:
        Return code based on success or failure of the job. 0 if success,
          100 if the job failed. Note: This is not the return code of the job
          script.

    """
    # Check the status of the cluster.
    handle = backend_utils.check_cluster_available(
        cluster_name,
        operation='tailing logs',
    )
    backend = backend_utils.get_backend_from_handle(handle)

    usage_lib.record_cluster_name_for_current_operation(cluster_name)
    # Although tail_logs returns an int when require_outputs=False (default),
    # we need to check returnval as an int to avoid type checking errors.
    returnval = backend.tail_logs(handle, job_id, follow=follow, tail=tail)
    assert isinstance(returnval,
                      int), (f'returnval must be an int, but got {returnval}')
    return returnval


@usage_lib.entrypoint
def download_logs(
        cluster_name: str,
        job_ids: Optional[List[str]],
        local_dir: str = constants.SKY_LOGS_DIRECTORY) -> Dict[str, str]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Downloads the logs of jobs.

    Args:
        cluster_name: (str) name of the cluster.
        job_ids: (List[str]) job ids.
    Returns:
        Dict[str, str]: a mapping of job_id to local log path.
    Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    # Check the status of the cluster.
    handle = backend_utils.check_cluster_available(
        cluster_name,
        operation='downloading logs',
    )
    backend = backend_utils.get_backend_from_handle(handle)
    assert isinstance(backend, backends.CloudVmRayBackend), backend

    if job_ids is not None and not job_ids:
        return {}

    usage_lib.record_cluster_name_for_current_operation(cluster_name)
    sky_logging.print(f'{colorama.Fore.YELLOW}'
                      'Syncing down logs to local...'
                      f'{colorama.Style.RESET_ALL}')
    local_log_dirs = backend.sync_down_logs(handle, job_ids, local_dir)
    return local_log_dirs


@usage_lib.entrypoint
def job_status(cluster_name: str,
               job_ids: Optional[List[int]],
               stream_logs: bool = False
              ) -> Dict[Optional[int], Optional[job_lib.JobStatus]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Get the status of jobs.

    Args:
        cluster_name: (str) name of the cluster.
        job_ids: (List[str]) job ids. If None, get the status of the last job.
    Returns:
        Dict[Optional[str], Optional[job_lib.JobStatus]]: A mapping of job_id to
        job statuses. The status will be None if the job does not exist.
        If job_ids is None and there is no job on the cluster, it will return
        {None: None}.
    Raises:
        sky.exceptions.ClusterDoesNotExist: if the cluster does not exist.
        sky.exceptions.ClusterNotUpError: if the cluster is not UP.
        sky.exceptions.NotSupportedError: if the cluster is not based on
          CloudVmRayBackend.
        sky.exceptions.ClusterOwnerIdentityMismatchError: if the current user is
          not the same as the user who created the cluster.
        sky.exceptions.CloudUserIdentityError: if we fail to get the current
          user identity.
    """
    # Check the status of the cluster.
    handle = backend_utils.check_cluster_available(
        cluster_name,
        operation='getting job status',
    )
    backend = backend_utils.get_backend_from_handle(handle)
    if not isinstance(backend, backends.CloudVmRayBackend):
        raise exceptions.NotSupportedError(
            f'Getting job status is not supported for cluster {cluster_name!r} '
            f'of type {backend.__class__.__name__!r}.')
    assert isinstance(handle, backends.CloudVmRayResourceHandle), handle

    if job_ids is not None and not job_ids:
        return {}

    sky_logging.print(f'{colorama.Fore.YELLOW}'
                      'Getting job status...'
                      f'{colorama.Style.RESET_ALL}')

    usage_lib.record_cluster_name_for_current_operation(cluster_name)
    statuses = backend.get_job_status(handle, job_ids, stream_logs=stream_logs)
    return statuses


# ======================
# = Storage Management =
# ======================
@usage_lib.entrypoint
def storage_ls() -> List[Dict[str, Any]]:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Gets the storages.

    Returns:
        [
            {
                'name': str,
                'launched_at': int timestamp of creation,
                'store': List[sky.StoreType],
                'last_use': int timestamp of last use,
                'status': sky.StorageStatus,
            }
        ]
    """
    storages = global_user_state.get_storage()
    for storage in storages:
        storage['store'] = list(storage.pop('handle').sky_stores.keys())
    return storages


@usage_lib.entrypoint
def storage_delete(name: str) -> None:
    # NOTE(dev): Keep the docstring consistent between the Python API and CLI.
    """Deletes a storage.

    Raises:
        ValueError: If the storage does not exist.
    """
    # TODO(zhwu): check the storage owner matches the current user
    handle = global_user_state.get_handle_from_storage_name(name)
    if handle is None:
        raise ValueError(f'Storage name {name!r} not found.')
    else:
        storage_object = data.Storage(name=handle.storage_name,
                                      source=handle.source,
                                      sync_on_reconstruction=False)
        storage_object.delete()


# ===================
# = Catalog Observe =
# ===================
@usage_lib.entrypoint
def enabled_clouds(workspace: Optional[str] = None,
                   expand: bool = False) -> List[str]:
    if workspace is None:
        workspace = skypilot_config.get_active_workspace()
    cached_clouds = global_user_state.get_cached_enabled_clouds(
        sky_cloud.CloudCapability.COMPUTE, workspace=workspace)
    with skypilot_config.local_active_workspace_ctx(workspace):
        if not expand:
            return [cloud.canonical_name() for cloud in cached_clouds]
        enabled_ssh_infras = []
        enabled_k8s_infras = []
        enabled_cloud_infras = []
        for cloud in cached_clouds:
            cloud_infra = cloud.expand_infras()
            if isinstance(cloud, clouds.SSH):
                enabled_ssh_infras.extend(cloud_infra)
            elif isinstance(cloud, clouds.Kubernetes):
                enabled_k8s_infras.extend(cloud_infra)
            else:
                enabled_cloud_infras.extend(cloud_infra)
        all_infras = sorted(enabled_ssh_infras) + sorted(
            enabled_k8s_infras) + sorted(enabled_cloud_infras)
        return all_infras


@usage_lib.entrypoint
def realtime_kubernetes_gpu_availability(
    context: Optional[str] = None,
    name_filter: Optional[str] = None,
    quantity_filter: Optional[int] = None,
    is_ssh: Optional[bool] = None
) -> List[Tuple[str, List[models.RealtimeGpuAvailability]]]:

    if context is None:
        # Include contexts from both Kubernetes and SSH clouds
        kubernetes_contexts = clouds.Kubernetes.existing_allowed_contexts()
        ssh_contexts = clouds.SSH.existing_allowed_contexts()
        if is_ssh is None:
            context_list = kubernetes_contexts + ssh_contexts
        elif is_ssh:
            context_list = ssh_contexts
        else:
            context_list = kubernetes_contexts
    else:
        context_list = [context]

    def _realtime_kubernetes_gpu_availability_single(
        context: Optional[str] = None,
        name_filter: Optional[str] = None,
        quantity_filter: Optional[int] = None
    ) -> List[models.RealtimeGpuAvailability]:
        counts, capacity, available = catalog.list_accelerator_realtime(
            gpus_only=True,
            clouds='ssh' if is_ssh else 'kubernetes',
            name_filter=name_filter,
            region_filter=context,
            quantity_filter=quantity_filter,
            case_sensitive=False)
        assert (set(counts.keys()) == set(capacity.keys()) == set(
            available.keys())), (f'Keys of counts ({list(counts.keys())}), '
                                 f'capacity ({list(capacity.keys())}), '
                                 f'and available ({list(available.keys())}) '
                                 'must be the same.')
        realtime_gpu_availability_list: List[
            models.RealtimeGpuAvailability] = []

        for gpu, _ in sorted(counts.items()):
            realtime_gpu_availability_list.append(
                models.RealtimeGpuAvailability(
                    gpu,
                    counts.pop(gpu),
                    capacity[gpu],
                    available[gpu],
                ))
        return realtime_gpu_availability_list

    availability_lists: List[Tuple[str,
                                   List[models.RealtimeGpuAvailability]]] = []
    cumulative_count = 0
    parallel_queried = subprocess_utils.run_in_parallel(
        lambda ctx: _realtime_kubernetes_gpu_availability_single(
            context=ctx,
            name_filter=name_filter,
            quantity_filter=quantity_filter), context_list)

    cloud_identity = 'ssh' if is_ssh else 'kubernetes'
    cloud_identity_capital = 'SSH' if is_ssh else 'Kubernetes'

    for ctx, queried in zip(context_list, parallel_queried):
        cumulative_count += len(queried)
        if len(queried) == 0:
            # don't add gpu results for clusters that don't have any
            logger.debug(f'No gpus found in {cloud_identity} cluster {ctx}')
            continue
        availability_lists.append((ctx, queried))

    if cumulative_count == 0:
        err_msg = f'No GPUs found in any {cloud_identity_capital} clusters. '
        debug_msg = 'To further debug, run: sky check '
        if name_filter is not None:
            gpu_info_msg = f' {name_filter!r}'
            if quantity_filter is not None:
                gpu_info_msg += (' with requested quantity'
                                 f' {quantity_filter}')
            err_msg = (f'Resources{gpu_info_msg} not found '
                       f'in {cloud_identity_capital} clusters. ')
            debug_msg = (f'To show available accelerators on {cloud_identity}, '
                         f' run: sky show-gpus --cloud {cloud_identity} ')
        full_err_msg = (err_msg + kubernetes_constants.NO_GPU_HELP_MESSAGE +
                        debug_msg)
        raise ValueError(full_err_msg)
    return availability_lists


# =================
# = Local Cluster =
# =================
@usage_lib.entrypoint
def local_up(gpus: bool,
             ips: Optional[List[str]],
             ssh_user: Optional[str],
             ssh_key: Optional[str],
             cleanup: bool,
             context_name: Optional[str] = None,
             password: Optional[str] = None) -> None:
    """Creates a local or remote cluster."""

    def _validate_args(ips, ssh_user, ssh_key, cleanup):
        # If any of --ips, --ssh-user, or --ssh-key-path is specified,
        # all must be specified
        if bool(ips) or bool(ssh_user) or bool(ssh_key):
            if not (ips and ssh_user and ssh_key):
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'All ips, ssh_user, and ssh_key must be specified '
                        'together.')

        # --cleanup can only be used if --ips, --ssh-user and --ssh-key-path
        # are all provided
        if cleanup and not (ips and ssh_user and ssh_key):
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    'cleanup can only be used with ips, ssh_user and ssh_key.')

    _validate_args(ips, ssh_user, ssh_key, cleanup)

    # If remote deployment arguments are specified, run remote up script
    if ips:
        assert ssh_user is not None and ssh_key is not None
        kubernetes_deploy_utils.deploy_remote_cluster(ips, ssh_user, ssh_key,
                                                      cleanup, context_name,
                                                      password)
    else:
        # Run local deployment (kind) if no remote args are specified
        kubernetes_deploy_utils.deploy_local_cluster(gpus)


def local_down() -> None:
    """Tears down the Kubernetes cluster started by local_up."""
    cluster_removed = False

    path_to_package = os.path.dirname(__file__)
    down_script_path = os.path.join(path_to_package, 'utils/kubernetes',
                                    'delete_cluster.sh')

    cwd = os.path.dirname(os.path.abspath(down_script_path))
    run_command = shlex.split(down_script_path)

    # Setup logging paths
    run_timestamp = sky_logging.get_run_timestamp()
    log_path = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp,
                            'local_down.log')

    with rich_utils.safe_status(
            ux_utils.spinner_message('Removing local cluster',
                                     log_path=log_path,
                                     is_local=True)):

        returncode, stdout, stderr = log_lib.run_with_log(cmd=run_command,
                                                          log_path=log_path,
                                                          require_outputs=True,
                                                          stream_logs=False,
                                                          cwd=cwd)
        stderr = stderr.replace('No kind clusters found.\n', '')

        if returncode == 0:
            cluster_removed = True
        elif returncode == 100:
            logger.info(ux_utils.error_message('Local cluster does not exist.'))
        else:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError('Failed to create local cluster. '
                                   f'Stdout: {stdout}'
                                   f'\nError: {stderr}')
    if cluster_removed:
        # Run sky check
        with rich_utils.safe_status(
                ux_utils.spinner_message('Running sky check...')):
            sky_check.check_capability(sky_cloud.CloudCapability.COMPUTE,
                                       clouds=['kubernetes'],
                                       quiet=True)
        logger.info(
            ux_utils.finishing_message('Local cluster removed.',
                                       log_path=log_path,
                                       is_local=True))


@usage_lib.entrypoint
def ssh_up(infra: Optional[str] = None, cleanup: bool = False) -> None:
    """Deploys or tears down a Kubernetes cluster on SSH targets.

    Args:
        infra: Name of the cluster configuration in ssh_node_pools.yaml.
            If None, the first cluster in the file is used.
        cleanup: If True, clean up the cluster instead of deploying.
    """
    kubernetes_deploy_utils.deploy_ssh_cluster(
        cleanup=cleanup,
        infra=infra,
    )


@usage_lib.entrypoint
def ssh_status(context_name: str) -> Tuple[bool, str]:
    """Check the status of an SSH Node Pool context.

    Args:
        context_name: The SSH context name (e.g., 'ssh-my-cluster')

    Returns:
        Tuple[bool, str]: (is_ready, reason)
            - is_ready: True if the SSH Node Pool is ready, False otherwise
            - reason: Explanation of the status
    """
    try:
        is_ready, reason = clouds.SSH.check_single_context(context_name)
        return is_ready, reason
    except Exception as e:  # pylint: disable=broad-except
        return False, ('Failed to check SSH context: '
                       f'{common_utils.format_exception(e)}')


def get_all_contexts() -> List[str]:
    """Get all available contexts from Kubernetes and SSH clouds.

    Returns:
        List[str]: A list of all available context names.
    """
    kube_contexts = clouds.Kubernetes.existing_allowed_contexts()
    ssh_contexts = clouds.SSH.get_ssh_node_pool_contexts()
    # Ensure ssh_contexts are prefixed appropriately if not already
    # For now, assuming get_ssh_node_pool_contexts already returns them
    # in the desired format (e.g., 'ssh-my-cluster')
    return sorted(list(set(kube_contexts + ssh_contexts)))
