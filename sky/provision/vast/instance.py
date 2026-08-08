"""Vast instance provisioning."""
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from sky import exceptions
from sky import sky_logging
from sky.provision import common
from sky.provision import docker_utils
from sky.provision.vast import utils
from sky.utils import common_utils
from sky.utils import resources_utils
from sky.utils import status_lib
from sky.utils import ux_utils

POLL_INTERVAL = 10
DEFAULT_PROVISION_TIMEOUT = 30 * 60
DIAGNOSTIC_LOG_TAIL = 2000

_PENDING_STATUSES = frozenset(
    ('NULL', 'CREATED', 'RESTARTING', 'REBOOTING', 'LOADING'))
_FAILED_PROVISIONING_STATUSES = frozenset(
    ('EXITED', 'STOPPED', 'FROZEN', 'UNKNOWN', 'OFFLINE'))

logger = sky_logging.init_logger(__name__)
# a much more convenient method
status_filter = lambda machine_dict, stat_list: {
    k: v for k, v in machine_dict.items() if v['status'] in stat_list
}


def _filter_instances(cluster_name_on_cloud: str,
                      status_filters: Optional[List[str]],
                      head_only: bool = False) -> Dict[str, Any]:

    instances = utils.list_instances()
    possible_names = [f'{cluster_name_on_cloud}-head']
    if not head_only:
        possible_names.append(f'{cluster_name_on_cloud}-worker')

    filtered_instances = {}
    for instance_id, instance in instances.items():
        if (status_filters is not None and
                instance['status'] not in status_filters):
            continue
        if instance.get('name') in possible_names:
            filtered_instances[instance_id] = instance
    return filtered_instances


def _get_head_instance_id(instances: Dict[str, Any]) -> Optional[str]:
    for inst_id, inst in instances.items():
        if inst.get('name') and inst['name'].endswith('-head'):
            return inst_id
    return None


def _format_instance_details(instances: Dict[str, Any]) -> str:
    """Return safe, user-facing Vast status metadata for a failure."""
    details = []
    for instance_id, instance in instances.items():
        status_msg = instance.get('status_msg')
        details.append(
            f'id={instance_id}, machine_id={instance.get("machine_id")}, '
            f'status={instance.get("status")}, '
            f'status_msg={status_msg!r}, '
            f'ssh_host={instance.get("ssh_host")}, '
            f'ssh_port={instance.get("ssh_port")}')
    return '; '.join(details)


def _provisioning_error(
        reason: str, instances: Dict[str, Any],
        created_instance_ids: List[str]) -> exceptions.VastProvisioningError:
    return exceptions.VastProvisioningError(
        f'Vast instance provisioning {reason}: '
        f'{_format_instance_details(instances)}.',
        instance_ids=created_instance_ids,
    )


def _wait_for_instances_ready(
        cluster_name_on_cloud: str,
        expected_count: int,
        deadline: float,
        created_instance_ids: List[str],
        resumed_instance_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Wait until every requested Vast instance is running and SSH-ready.

    Vast reports ``NULL`` while a contract is being created and ``LOADING``
    while an image or container starts.  Neither state identifies a healthy
    host, so this deadline is the final safety bound.  In contrast, terminal
    container states and missing host heartbeats fail immediately; keeping a
    SkyPilot provision call blocked cannot make those states recover.
    """
    resumed_instance_id_set: Set[str] = set(resumed_instance_ids or [])
    resumable_statuses = frozenset(('EXITED', 'STOPPED', 'FROZEN'))
    while True:
        instances = _filter_instances(cluster_name_on_cloud, None)
        failed_instances = {
            instance_id: instance
            for instance_id, instance in instances.items()
            if (instance['status'] in _FAILED_PROVISIONING_STATUSES and
                not (instance_id in resumed_instance_id_set and
                     instance['status'] in resumable_statuses))
        }
        if failed_instances:
            raise _provisioning_error('failed', failed_instances,
                                      created_instance_ids)

        ready_instances = {
            instance_id: instance
            for instance_id, instance in instances.items()
            if (instance['status'] == 'RUNNING' and
                instance.get('ssh_port') is not None)
        }
        logger.info('Waiting for Vast instances to be ready: '
                    f'({len(ready_instances)}/{expected_count}).')
        if len(ready_instances) >= expected_count:
            return ready_instances

        now = time.monotonic()
        if now >= deadline:
            raise _provisioning_error(
                'timed out waiting for RUNNING with '
                'an SSH port', instances, created_instance_ids)
        time.sleep(min(POLL_INTERVAL, deadline - now))


def _wait_for_no_pending_instances(cluster_name_on_cloud: str,
                                   deadline: float) -> Dict[str, Any]:
    """Do not launch duplicate nodes while an earlier request is pending."""
    while True:
        instances = _filter_instances(cluster_name_on_cloud, None)
        host_failures = {
            instance_id: instance
            for instance_id, instance in instances.items()
            if instance['status'] in ('UNKNOWN', 'OFFLINE')
        }
        if host_failures:
            raise _provisioning_error('failed', host_failures, [])

        pending_instances = {
            instance_id: instance
            for instance_id, instance in instances.items()
            if instance['status'] in _PENDING_STATUSES
        }
        if not pending_instances:
            return instances

        now = time.monotonic()
        if now >= deadline:
            raise _provisioning_error(
                'timed out waiting for a previous '
                'request to finish', pending_instances, [])
        logger.info(f'Waiting for {len(pending_instances)} existing Vast '
                    'instances to finish provisioning.')
        time.sleep(min(POLL_INTERVAL, deadline - now))


def _log_instance_diagnostics(instance_ids: List[str],
                              sensitive_values: List[str]) -> None:
    """Best-effort debug logging of bounded, redacted Vast instance logs."""
    for instance_id in instance_ids:
        for daemon_logs, source in ((False, 'container'), (True, 'daemon')):
            try:
                output = utils.get_instance_logs(instance_id,
                                                 daemon_logs=daemon_logs,
                                                 tail=DIAGNOSTIC_LOG_TAIL)
            except Exception as exc:  # pylint: disable=broad-except
                safe_error = utils.redact_log_output(str(exc), sensitive_values)
                logger.debug('Could not collect Vast %s logs for %s: %s',
                             source, instance_id, safe_error)
                continue
            logger.debug('Vast %s log tail for %s:\n%s', source, instance_id,
                         utils.redact_log_output(output, sensitive_values))


def _cleanup_failed_instances(
        instance_ids: List[str]) -> Tuple[List[Any], bool]:
    """Destroy only nodes from this attempt and retain their machine IDs.

    A replacement is safe only after this cleanup succeeds; otherwise a retry
    could create a duplicate paid instance while the failed contract remains.
    """
    try:
        instances = utils.list_instances()
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning('Could not inspect failed Vast instances before '
                       f'cleanup: {exc}')
        return [], False

    machine_ids: List[Any] = []
    for instance_id in instance_ids:
        machine_id = instances.get(str(instance_id), {}).get('machine_id')
        if machine_id is not None:
            machine_ids.append(machine_id)

    cleanup_succeeded = True
    for instance_id in instance_ids:
        try:
            utils.remove(str(instance_id))
        except Exception as exc:  # pylint: disable=broad-except
            cleanup_succeeded = False
            logger.warning(f'Failed to destroy Vast instance {instance_id}: '
                           f'{exc}')
    return machine_ids, cleanup_succeeded


def _get_sensitive_values(create_instance_kwargs: Dict[str, Any],
                          login_args: Optional[str],
                          docker_login_config: Optional[Any]) -> List[str]:
    """Collect known secret values that might be echoed in instance logs."""
    sensitive_values = []
    if login_args:
        sensitive_values.append(login_args)
    if docker_login_config is not None:
        sensitive_values.append(docker_login_config.password)
    for key in ('login', 'image_login'):
        value = create_instance_kwargs.get(key)
        if value:
            sensitive_values.append(str(value))
    try:
        env = utils.normalize_env(create_instance_kwargs.get('env'))
        sensitive_values.extend(
            str(value)
            for key, value in env.items()
            if key != '__SOURCE' and not isinstance(value, (dict, list, tuple)))
    except ValueError:
        # The launch path will report invalid env configuration separately.
        pass
    try:
        sensitive_values.append(utils.get_api_key())
    except Exception:  # pylint: disable=broad-except
        logger.debug('Could not read Vast API key for diagnostic redaction.')
    return sensitive_values


def _sanitize_launch_exception(exc: Exception,
                               sensitive_values: List[str]) -> Exception:
    """Preserve launch-error semantics while removing known secrets."""
    message = utils.redact_log_output(str(exc), sensitive_values)
    if isinstance(exc, ValueError):
        return ValueError(message)
    if isinstance(exc, TypeError):
        return TypeError(message)
    if isinstance(exc, exceptions.VastOfferUnavailableError):
        return exceptions.VastOfferUnavailableError(message)
    if isinstance(exc, exceptions.VastProvisioningError):
        return exceptions.VastProvisioningError(message,
                                                instance_ids=exc.instance_ids)
    return exceptions.VastProvisioningError(message)


def _build_docker_login_args(
        login_config: docker_utils.DockerLoginConfig) -> str:
    """Build Vast's single registry-login argument safely.

    Vast accepts login credentials as a space-delimited string rather than
    separate structured fields. Reject whitespace because the provider parser
    cannot safely distinguish it from argument separators.
    """
    credential_values = {
        'username': login_config.username,
        'password': login_config.password,
        'server': login_config.server,
    }
    invalid_names = [
        name for name, value in credential_values.items()
        if not isinstance(value, str) or not value or any(
            character.isspace() for character in value)
    ]
    if invalid_names:
        raise ValueError(
            'Vast Docker registry credentials must be non-empty and must not '
            'contain whitespace.')
    return (f'-u {login_config.username} -p {login_config.password} '
            f'{login_config.server}')


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Runs instances for the given cluster."""
    del cluster_name  # unused

    create_instance_kwargs = (config.provider_config.get(
        'create_instance_kwargs', {}) or {})
    provision_timeout = config.provider_config.get('provision_timeout',
                                                   DEFAULT_PROVISION_TIMEOUT)
    if (isinstance(provision_timeout, bool) or
            not isinstance(provision_timeout,
                           (int, float)) or provision_timeout <= 0):
        raise ValueError('Vast provision_timeout must be a positive number of '
                         'seconds.')
    deadline = time.monotonic() + provision_timeout

    # Get SSH public key path and read the content for vast.ai key injection
    ssh_public_key_path = config.authentication_config.get('ssh_public_key')
    ssh_public_key = None
    if ssh_public_key_path:
        try:
            expanded_path = Path(ssh_public_key_path).expanduser()
            with open(expanded_path, 'r', encoding='utf-8') as f:
                ssh_public_key = f.read().strip()
            logger.debug(f'Read SSH public key from {expanded_path}')
        except OSError as e:
            logger.warning(f'Failed to read SSH public key from '
                           f'{ssh_public_key_path}: {e}')

    docker_login_config = config.provider_config.get('docker_login_config')
    login_args = None
    login_config = None
    image_name = config.node_config['ImageId']
    if docker_login_config:
        login_config = (docker_login_config if isinstance(
            docker_login_config, docker_utils.DockerLoginConfig) else
                        docker_utils.DockerLoginConfig(**docker_login_config))
        login_args = _build_docker_login_args(login_config)
        image_name = login_config.format_image(image_name)

    sensitive_values = _get_sensitive_values(create_instance_kwargs, login_args,
                                             login_config)
    instances = _wait_for_no_pending_instances(cluster_name_on_cloud, deadline)

    running_instances = status_filter(instances, ['RUNNING'])
    head_instance_id = _get_head_instance_id(running_instances)
    stopped_instances = status_filter(instances,
                                      ['EXITED', 'STOPPED', 'FROZEN'])
    resumed_instance_ids = []

    if config.resume_stopped_nodes and stopped_instances:
        resumed_instance_ids = list(stopped_instances)
        for instance in stopped_instances.values():
            utils.start(instance['id'])

    to_start_count = config.count - (len(running_instances) +
                                     len(stopped_instances))
    if to_start_count < 0:
        raise RuntimeError(f'Cluster {cluster_name_on_cloud} already has '
                           f'{len(running_instances)} nodes, '
                           f'but {config.count} are required.')
    if to_start_count == 0:
        if head_instance_id is None and not (config.resume_stopped_nodes and
                                             stopped_instances):
            raise RuntimeError(
                f'Cluster {cluster_name_on_cloud} has no head node.')
        logger.info(f'Cluster {cluster_name_on_cloud} already has '
                    f'{len(running_instances)} nodes, no need to start more.')

    secure_only = config.provider_config.get('secure_only', False)
    reliable_hosts = config.provider_config.get('reliable_hosts', False)
    network_tier = resources_utils.NetworkTier(
        config.provider_config.get('network_tier', 'standard'))

    def _launch_missing_instances(count: int,
                                  current_head_instance_id: Optional[str],
                                  excluded_machine_ids: List[Any]) -> List[str]:
        created_instance_ids = []
        try:
            for _ in range(count):
                node_type = ('head'
                             if current_head_instance_id is None else 'worker')
                instance_id = utils.launch(
                    name=f'{cluster_name_on_cloud}-{node_type}',
                    instance_type=config.node_config['InstanceType'],
                    region=region,
                    disk_size=config.node_config['DiskSize'],
                    preemptible=config.node_config['Preemptible'],
                    image_name=image_name,
                    ports=config.ports_to_open_on_launch,
                    secure_only=secure_only,
                    reliable_hosts=reliable_hosts,
                    network_tier=network_tier,
                    excluded_machine_ids=excluded_machine_ids,
                    private_docker_registry=login_config is not None,
                    login=login_args,
                    create_instance_kwargs=create_instance_kwargs,
                    ssh_public_key=ssh_public_key,
                )
                logger.info(f'Launched Vast instance {instance_id}.')
                created_instance_ids.append(instance_id)
                if current_head_instance_id is None:
                    current_head_instance_id = instance_id
        except Exception as exc:  # pylint: disable=broad-except
            sanitized_exc = _sanitize_launch_exception(exc, sensitive_values)
            logger.warning('Vast instance launch failed: %s', sanitized_exc)
            if created_instance_ids:
                _cleanup_failed_instances(created_instance_ids)
            raise sanitized_exc from None
        return created_instance_ids

    created_instance_ids = _launch_missing_instances(to_start_count,
                                                     head_instance_id, [])
    excluded_machine_ids: List[Any] = []
    replacement_attempts_remaining = 1

    while True:
        try:
            _wait_for_instances_ready(cluster_name_on_cloud,
                                      expected_count=config.count,
                                      deadline=deadline,
                                      created_instance_ids=created_instance_ids,
                                      resumed_instance_ids=resumed_instance_ids)
            break
        except exceptions.VastProvisioningError:
            if not created_instance_ids:
                raise

            _log_instance_diagnostics(created_instance_ids, sensitive_values)
            failed_machine_ids, cleanup_succeeded = _cleanup_failed_instances(
                created_instance_ids)
            if not cleanup_succeeded:
                logger.warning('Will not retry Vast provisioning because '
                               'cleanup of the failed instance did not '
                               'complete.')
                raise
            if replacement_attempts_remaining == 0 or time.monotonic(
            ) >= deadline:
                raise

            excluded_machine_ids.extend(failed_machine_ids)
            replacement_attempts_remaining -= 1
            existing_running_instances = _filter_instances(
                cluster_name_on_cloud, ['RUNNING'])
            replacement_head_instance_id = _get_head_instance_id(
                existing_running_instances)
            logger.info('Retrying Vast provisioning on a different machine.')
            created_instance_ids = _launch_missing_instances(
                to_start_count, replacement_head_instance_id,
                excluded_machine_ids)

    head_instance_id = _get_head_instance_id(utils.list_instances())
    assert head_instance_id is not None, 'head_instance_id should not be None'
    return common.ProvisionRecord(provider_name='vast',
                                  cluster_name=cluster_name_on_cloud,
                                  region=region,
                                  zone=None,
                                  head_instance_id=head_instance_id,
                                  resumed_instance_ids=resumed_instance_ids,
                                  created_instance_ids=created_instance_ids)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    del region, cluster_name_on_cloud, state


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    return action_instances('stop', cluster_name_on_cloud, provider_config,
                            worker_only)


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    return action_instances('remove', cluster_name_on_cloud, provider_config,
                            worker_only)


def action_instances(
    fn: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config  # unused
    instances = _filter_instances(cluster_name_on_cloud, None)
    for inst_id, inst in instances.items():
        logger.debug(f'Instance {fn} {inst_id}: {inst}')
        if worker_only and inst['name'].endswith('-head'):
            continue
        try:
            getattr(utils, fn)(inst_id)
        except Exception as e:  # pylint: disable=broad-except
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    f'Failed to {fn} instance {inst_id}: '
                    f'{common_utils.format_exception(e, use_bracket=False)}'
                ) from e


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region  # unused
    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'])
    instances: Dict[str, List[common.InstanceInfo]] = {}
    head_instance_id = None
    for instance_id, instance_info in running_instances.items():
        # Vast.ai routes SSH through a gateway (ssh_host, e.g. ssh3.vast.ai).
        # Using public_ipaddr directly causes SSH timeouts because direct
        # access is blocked; the gateway is the only reachable path.
        # ssh_port is always set; ports['22/tcp'] may be None in newer API.
        ssh_host = (instance_info.get('ssh_host') or
                    instance_info.get('public_ipaddr', ''))
        ports_dict = instance_info.get('ports') or {}
        tcp22 = ports_dict.get('22/tcp') or []
        ssh_port = (int(tcp22[0]['HostPort'])
                    if tcp22 else instance_info.get('ssh_port'))
        instances[instance_id] = [
            common.InstanceInfo(
                instance_id=instance_id,
                internal_ip=instance_info['local_ipaddrs'].strip(),
                external_ip=ssh_host,
                ssh_port=ssh_port,
                tags={},
                node_name=instance_id,
            )
        ]
        if instance_info['name'].endswith('-head'):
            head_instance_id = instance_id

    return common.ClusterInfo(
        instances=instances,
        head_instance_id=head_instance_id,
        provider_name='vast',
        provider_config=provider_config,
    )


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    raise NotImplementedError('open_ports is not supported for Vast')


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, retry_if_missing  # unused
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    instances = _filter_instances(cluster_name_on_cloud, None)
    # Vast also reports NULL while a contract is being provisioned and can
    # introduce lifecycle states without a SkyPilot release.  Preserve a
    # usable cluster refresh result instead of raising a KeyError on either.
    status_map = {
        'NULL': status_lib.ClusterStatus.INIT,
        'CREATED': status_lib.ClusterStatus.INIT,
        'RESTARTING': status_lib.ClusterStatus.INIT,
        'REBOOTING': status_lib.ClusterStatus.INIT,
        'LOADING': status_lib.ClusterStatus.INIT,
        'UNKNOWN': status_lib.ClusterStatus.INIT,
        'OFFLINE': status_lib.ClusterStatus.INIT,
        'EXITED': status_lib.ClusterStatus.STOPPED,
        'STOPPED': status_lib.ClusterStatus.STOPPED,
        'FROZEN': status_lib.ClusterStatus.STOPPED,
        'RUNNING': status_lib.ClusterStatus.UP,
    }
    statuses: Dict[str, Tuple[Optional['status_lib.ClusterStatus'],
                              Optional[str]]] = {}
    for inst_id, inst in instances.items():
        status = status_map.get(inst['status'], status_lib.ClusterStatus.INIT)
        if non_terminated_only and status is None:
            continue
        statuses[inst_id] = (status, inst.get('status_msg'))
    return statuses


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, ports, provider_config  # Unused.


def query_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    head_ip: Optional[str] = None,
    provider_config: Optional[Dict[str, Any]] = None,
) -> Dict[int, List[common.Endpoint]]:
    """Returns externally-accessible endpoints for the given ports.

    Vast.ai exposes container ports via SSH reverse-proxy with a fixed mapping:
      container:22   → ssh_host:ssh_port
      container:N    → ssh_host:(ssh_port + N - 21)

    In practice, only port 8080 (the second forwarded port) is supported:
      container:8080 → ssh_host:(ssh_port + 1)

    Using the raw ssh_host + service port (e.g. :8080) fails because the
    Vast.ai SSH gateway does not relay arbitrary ports directly. The correct
    externally-accessible endpoint is ssh_host:(ssh_port+1).
    """
    del head_ip, provider_config  # Unused.
    ports_to_query = resources_utils.port_ranges_to_set(ports)

    running_instances = _filter_instances(cluster_name_on_cloud, ['RUNNING'],
                                          head_only=True)
    if not running_instances:
        return {}

    head_inst = list(running_instances.values())[0]
    ssh_host = head_inst.get('ssh_host') or head_inst.get('public_ipaddr', '')
    ssh_port = head_inst.get('ssh_port')

    if not ssh_host or ssh_port is None:
        return {}

    # Vast.ai port forward: container:8080 → ssh_host:(ssh_port+1)
    # Only port 8080 is supported via the gateway's second reverse-tunnel slot.
    result: Dict[int, List[common.Endpoint]] = {}
    for port in ports_to_query:
        if port == 8080:
            external_port = ssh_port + 1
        else:
            # For other ports, fall back to the host with the original port.
            # Note: these are unlikely to be reachable unless the user has
            # configured additional port forwarding on their Vast.ai instance.
            logger.warning(
                f'Port {port} requested but Vast.ai only natively forwards '
                f'port 8080 via ssh_host:(ssh_port+1). Port {port} may not '
                f'be reachable. Use port 8080 for service endpoints.')
            external_port = port
        result[port] = [
            common.SocketEndpoint(host=ssh_host, port=external_port)
        ]

    return result
