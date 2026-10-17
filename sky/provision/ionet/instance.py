"""SkyPilot lifecycle adapter for io.net VMaaS."""

import contextlib
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sky import exceptions
from sky import sky_logging
from sky.provision import common
from sky.provision.ionet import utils
from sky.utils import status_lib
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

POLL_INTERVAL = 10
MAX_POLL_ATTEMPTS = 180
_TERMINATION_REQUESTED = 'termination requested'
# Deployments in these statuses no longer hold a usable VM.
_TERMINAL_STATUSES = frozenset(
    ('failed', 'completed', 'destroyed', _TERMINATION_REQUESTED))
_AUTH_ERROR_CODES = (401, 403)


def _client() -> utils.IONetClient:
    return utils.IONetClient()


def _find_deployment(client: utils.IONetClient, cluster_name: str,
                     metadata: utils.IONetMetadata) -> Optional[str]:
    """Discover by remote name so API server restarts do not lose clusters."""
    matches = []
    for page in range(1, utils.MAX_PAGES + 1):
        response = client.list_deployments(page)
        data = response.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('deployments'),
                                                        list):
            raise utils.IONetError(
                'io.net returned a malformed deployments list.')
        deployments = data['deployments']
        for deployment in deployments:
            if deployment.get('name') == cluster_name:
                deployment_id = str(deployment['id'])
                if deployment.get('status') in _TERMINAL_STATUSES:
                    metadata.set_deployment(deployment_id, None)
                else:
                    matches.append(deployment_id)
        if page * utils.PAGE_SIZE >= data.get('total', len(deployments)):
            break
    else:
        raise utils.IONetError(
            'io.net deployment list exceeded pagination limit.')
    if len(set(matches)) > 1:
        raise utils.IONetError(
            f'Multiple io.net deployments match cluster {cluster_name}: '
            f'{matches}')
    if matches:
        deployment_id = matches[0]
        if deployment_id not in metadata.list_deployments():
            metadata.set_deployment(deployment_id,
                                    {'cluster_name_on_cloud': cluster_name})
        return deployment_id
    existing = metadata.list_deployments()
    if len(existing) > 1:
        raise utils.IONetError(
            f'Multiple local io.net deployments match cluster {cluster_name}.')
    existing_id: Optional[str] = next(iter(existing), None)
    if existing_id is not None:
        details = client.get_deployment_details(existing_id).get('data', {})
        if details.get('status') in _TERMINAL_STATUSES:
            metadata.set_deployment(existing_id, None)
            return None
    return existing_id


def _get_workers(client: utils.IONetClient,
                 deployment_id: str) -> List[Dict[str, Any]]:
    workers: List[Dict[str, Any]] = []
    for page in range(1, utils.MAX_PAGES + 1):
        data = client.get_deployment_vms(deployment_id, page).get('data', {})
        workers.extend(data.get('workers', []))
        if page * utils.PAGE_SIZE >= data.get('total', len(workers)):
            return workers
    raise utils.IONetError('io.net deployment has more than '
                           f'{utils.MAX_PAGES * utils.PAGE_SIZE} VMs.')


def _wait_for_ready(client: utils.IONetClient, deployment_id: str) -> None:
    log_every = max(1, 60 // max(POLL_INTERVAL, 1))
    for attempt in range(MAX_POLL_ATTEMPTS):
        details = client.get_deployment_details(deployment_id).get('data', {})
        status = details.get('status')
        if attempt % log_every == 0:
            logger.info(f'Waiting for io.net deployment {deployment_id}: '
                        f'status {status}.')
        if status == 'running':
            workers = _get_workers(client, deployment_id)
            if len(workers) == 1 and workers[0].get('vm_id') and workers[0].get(
                    'ssh_access'):
                utils.parse_ssh_access(workers[0]['ssh_access'])
                return
        if status in _TERMINAL_STATUSES:
            raise exceptions.ResourcesUnavailableError(
                f'io.net deployment {deployment_id} ended with status {status}.'
            )
        time.sleep(POLL_INTERVAL)
    raise exceptions.ResourcesUnavailableError(
        f'io.net deployment {deployment_id} did not become ready.')


def _destroy_created_deployment(client: utils.IONetClient,
                                metadata: utils.IONetMetadata,
                                deployment_id: str) -> None:
    """Best-effort cleanup so a failed launch does not leak a paid VM."""
    logger.info(f'Terminating io.net deployment {deployment_id} after a '
                'failed launch.')
    try:
        client.destroy_deployment(deployment_id)
    except utils.IONetError as exc:
        if exc.status_code != 404:
            logger.warning(
                f'Failed to terminate io.net deployment {deployment_id}: '
                f'{exc}. Terminate it manually to avoid charges.')
            return
    metadata.set_deployment(deployment_id, None)


@contextlib.contextmanager
def _map_auth_errors() -> Iterator[None]:
    try:
        yield
    except utils.IONetError as exc:
        if exc.status_code in _AUTH_ERROR_CODES:
            with ux_utils.print_exception_no_traceback():
                raise exceptions.InvalidCloudCredentials(
                    f'io.net rejected the API key: {exc}') from exc
        raise


def _get_instance_id(client: utils.IONetClient, deployment_id: str) -> str:
    workers = _get_workers(client, deployment_id)
    if len(workers) != 1 or not workers[0].get('vm_id'):
        raise utils.IONetError('io.net deployment has no unique VM ID.')
    return str(workers[0]['vm_id'])


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    del cluster_name
    if config.count != 1:
        raise utils.IONetError('io.net currently supports one VM per cluster.')
    # make_deploy_resources_variables has validated these values.
    node = config.node_config
    with _map_auth_errors():
        client = _client()
        metadata = utils.IONetMetadata(cluster_name_on_cloud)
        deployment_id = _find_deployment(client, cluster_name_on_cloud,
                                         metadata)
        created = False
        if deployment_id is None:
            request = {
                'resource_private_name': cluster_name_on_cloud,
                'duration_hours': node['DurationHours'],
                'hardware_id': node['HardwareId'],
                'gpus_per_vm': node['GpusPerVm'],
                'location_ids': [region],
                'vms_qty': 1,
                'vm_image_type': 'general',
                'ssh_keys': {
                    'skypilot': node['PublicKey']
                },
                # VMaaS exposes SSH itself; OPEN_PORTS is unsupported.
                'network_services': {},
            }
            try:
                response = client.deploy_vms(request)
            except utils.IONetError as exc:
                # A transport error leaves the outcome unknown, so do not fail
                # over; an auth error is handled by _map_auth_errors.
                if (exc.status_code is None or
                        exc.status_code in _AUTH_ERROR_CODES):
                    raise
                with ux_utils.print_exception_no_traceback():
                    raise exceptions.ResourcesUnavailableError(
                        f'io.net rejected the deployment in region {region}: '
                        f'{exc}') from exc
            deployment_id = response.get('deployment_id')
            if not deployment_id:
                raise utils.IONetError(
                    'io.net deploy returned no deployment_id; '
                    'inspect remote deployments before retrying.')
            deployment_id = str(deployment_id)
            metadata.set_deployment(
                deployment_id, {'cluster_name_on_cloud': cluster_name_on_cloud})
            created = True
            logger.info(f'Created io.net deployment {deployment_id}.')
        try:
            _wait_for_ready(client, deployment_id)
            instance_id = _get_instance_id(client, deployment_id)
        except Exception as exc:
            # Only a deployment this call created is destroyed; a reused one
            # may belong to a cluster that is still in use.
            if created:
                _destroy_created_deployment(client, metadata, deployment_id)
            if isinstance(exc, exceptions.ResourcesUnavailableError):
                with ux_utils.print_exception_no_traceback():
                    raise
            raise
        return common.ProvisionRecord(
            provider_name='ionet',
            region=region,
            zone=None,
            cluster_name=cluster_name_on_cloud,
            head_instance_id=instance_id,
            resumed_instance_ids=[],
            created_instance_ids=[instance_id] if created else [])


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    # run_instances has already waited and verified the remote VM identity.
    del region, cluster_name_on_cloud, state


def stop_instances(cluster_name_on_cloud: str,
                   provider_config: Optional[Dict[str, Any]] = None,
                   worker_only: bool = False) -> None:
    del cluster_name_on_cloud, provider_config, worker_only
    raise NotImplementedError('io.net VMaaS does not support stopping VMs.')


def terminate_instances(cluster_name_on_cloud: str,
                        provider_config: Optional[Dict[str, Any]] = None,
                        worker_only: bool = False) -> None:
    del provider_config
    if worker_only:
        raise utils.IONetError(
            'io.net cannot terminate only workers of a deployment.')
    client = _client()
    metadata = utils.IONetMetadata(cluster_name_on_cloud)
    deployment_id = _find_deployment(client, cluster_name_on_cloud, metadata)
    if deployment_id is None:
        return
    logger.info(f'Terminating io.net deployment {deployment_id}.')
    client.destroy_deployment(deployment_id)
    status = None
    for _ in range(MAX_POLL_ATTEMPTS):
        try:
            details = client.get_deployment_details(deployment_id).get(
                'data', {})
        except utils.IONetError as exc:
            if exc.status_code == 404:
                break
            raise
        status = details.get('status')
        if (status in _TERMINAL_STATUSES and status != _TERMINATION_REQUESTED):
            break
        time.sleep(POLL_INTERVAL)
    else:
        if status != _TERMINATION_REQUESTED:
            raise utils.IONetError(
                f'io.net deployment {deployment_id} did not terminate.')
        # The destroy was accepted; io.net finishes it asynchronously.
        logger.warning(f'io.net deployment {deployment_id} is still '
                       f'{status!r}; not waiting further.')
    metadata.set_deployment(deployment_id, None)


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region
    client = _client()
    metadata = utils.IONetMetadata(cluster_name_on_cloud)
    deployment_id = _find_deployment(client, cluster_name_on_cloud, metadata)
    if deployment_id is None:
        return common.ClusterInfo({}, None, 'ionet', provider_config)
    workers = _get_workers(client, deployment_id)
    instances: Dict[str, List[common.InstanceInfo]] = {}
    ssh_user = None
    for worker in workers:
        instance_id = str(worker['vm_id'])
        ssh_access = worker.get('ssh_access')
        if not ssh_access:
            raise utils.IONetError(
                f'io.net VM {instance_id} has no SSH access.')
        user, ip, port = utils.parse_ssh_access(ssh_access)
        ssh_user = user
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=ip,
                external_ip=ip,
                ssh_port=port,
                tags={},
            )
        ]
    if len(instances) != 1:
        raise utils.IONetError('io.net cluster has an unexpected VM count.')
    return common.ClusterInfo(instances,
                              next(iter(instances)),
                              'ionet',
                              provider_config,
                              ssh_user=ssh_user)


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    del cluster_name, provider_config, retry_if_missing
    client = _client()
    metadata = utils.IONetMetadata(cluster_name_on_cloud)
    deployment_id = _find_deployment(client, cluster_name_on_cloud, metadata)
    if deployment_id is None:
        return {}
    details = client.get_deployment_details(deployment_id).get('data', {})
    status = details.get('status')
    if status == 'running':
        sky_status = status_lib.ClusterStatus.UP
    elif status in _TERMINAL_STATUSES:
        sky_status = None
    else:
        sky_status = status_lib.ClusterStatus.INIT
    if sky_status is None and non_terminated_only:
        return {}
    workers = _get_workers(client, deployment_id)
    if not workers:
        # The deployment is pending and has no VM yet. Report it by
        # deployment ID so it is not treated as terminated and leaked.
        return {deployment_id: (sky_status, None)}
    return {str(worker['vm_id']): (sky_status, None) for worker in workers}


def cleanup_ports(cluster_name_on_cloud: str,
                  ports: List[str],
                  provider_config: Optional[Dict[str, Any]] = None) -> None:
    del cluster_name_on_cloud, ports, provider_config


def open_ports(cluster_name_on_cloud: str,
               ports: List[str],
               provider_config: Optional[Dict[str, Any]] = None) -> None:
    del cluster_name_on_cloud, ports, provider_config
