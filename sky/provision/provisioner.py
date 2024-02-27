"""Cloud-neutral VM provision utils."""
import collections
import dataclasses
import json
import os
import shlex
import socket
import subprocess
import time
import traceback
from typing import Dict, List, Optional, Tuple

import colorama

from sky import clouds
from sky import provision
from sky import sky_logging
from sky import status_lib
from sky.adaptors import aws
from sky.backends import backend_utils
from sky.provision import common as provision_common
from sky.provision import instance_setup
from sky.provision import logging as provision_logging
from sky.provision import metadata_utils
from sky.skylet import constants
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

# Do not use __name__ as we do not want to propagate logs to sky.provision,
# which will be customized in sky.provision.logging.
logger = sky_logging.init_logger('sky.provisioner')

# The maximum number of retries for waiting for instances to be ready and
# teardown instances when provisioning fails.
_MAX_RETRY = 3
_TITLE = '\n\n' + '=' * 20 + ' {} ' + '=' * 20 + '\n'


@dataclasses.dataclass
class ClusterName:
    display_name: str
    name_on_cloud: str

    def __repr__(self) -> str:
        return repr(self.display_name)

    def __str__(self) -> str:
        return self.display_name


def _bulk_provision(
    cloud: clouds.Cloud,
    region: clouds.Region,
    zones: Optional[List[clouds.Zone]],
    cluster_name: ClusterName,
    bootstrap_config: provision_common.ProvisionConfig,
) -> provision_common.ProvisionRecord:
    provider_name = repr(cloud)
    region_name = region.name

    style = colorama.Style

    if not zones:
        # For Azure, zones is always an empty list.
        zone_str = 'all zones'
    else:
        zone_str = ','.join(z.name for z in zones)

    if isinstance(cloud, clouds.Kubernetes):
        # Omit the region name for Kubernetes.
        logger.info(f'{style.BRIGHT}Launching on {cloud}{style.RESET_ALL} '
                    f'{cluster_name!r}.')
    else:
        logger.info(f'{style.BRIGHT}Launching on {cloud} '
                    f'{region_name}{style.RESET_ALL} ({zone_str})')

    start = time.time()
    with rich_utils.safe_status('[bold cyan]Launching[/]') as status:
        try:
            # TODO(suquark): Should we cache the bootstrapped result?
            #  Currently it is not necessary as bootstrapping takes
            #  only ~3s, caching it seems over-engineering and could
            #  cause other issues like the cache is not synced
            #  with the cloud configuration.
            config = provision.bootstrap_instances(provider_name, region_name,
                                                   cluster_name.name_on_cloud,
                                                   bootstrap_config)
        except Exception as e:
            logger.error(f'{colorama.Fore.YELLOW}Failed to configure '
                         f'{cluster_name!r} on {cloud} {region} ({zone_str}) '
                         'with the following error:'
                         f'{colorama.Style.RESET_ALL}\n'
                         f'{common_utils.format_exception(e)}')
            raise

        provision_record = provision.run_instances(provider_name,
                                                   region_name,
                                                   cluster_name.name_on_cloud,
                                                   config=config)

        backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=3)
        logger.debug(
            f'\nWaiting for instances of {cluster_name!r} to be ready...')
        status.update('[bold cyan]Launching - Checking instance status[/]')
        # AWS would take a very short time (<<1s) updating the state of the
        # instance.
        time.sleep(1)
        for retry_cnt in range(_MAX_RETRY):
            try:
                provision.wait_instances(provider_name,
                                         region_name,
                                         cluster_name.name_on_cloud,
                                         state=status_lib.ClusterStatus.UP)
                break
            except (aws.botocore_exceptions().WaiterError, RuntimeError):
                time.sleep(backoff.current_backoff())
        else:
            raise RuntimeError(
                f'Failed to wait for instances of {cluster_name!r} to be '
                f'ready on the cloud provider after max retries {_MAX_RETRY}.')
        logger.debug(
            f'Instances of {cluster_name!r} are ready after {retry_cnt} '
            'retries.')

    logger.debug(
        f'\nProvisioning {cluster_name!r} took {time.time() - start:.2f} '
        f'seconds.')

    return provision_record


def bulk_provision(
    cloud: clouds.Cloud,
    region: clouds.Region,
    zones: Optional[List[clouds.Zone]],
    cluster_name: ClusterName,
    num_nodes: int,
    cluster_yaml: str,
    prev_cluster_ever_up: bool,
    log_dir: str,
) -> provision_common.ProvisionRecord:
    """Provisions a cluster and wait until fully provisioned.

    Raises:
        StopFailoverError: Raised when during failover cleanup, tearing
            down any potentially live cluster failed despite retries
        Cloud specific exceptions: If the provisioning process failed, cloud-
            specific exceptions will be raised by the cloud APIs.
    """
    original_config = common_utils.read_yaml(cluster_yaml)
    head_node_type = original_config['head_node_type']
    bootstrap_config = provision_common.ProvisionConfig(
        provider_config=original_config['provider'],
        authentication_config=original_config['auth'],
        docker_config=original_config.get('docker', {}),
        # NOTE: (might be a legacy issue) we call it
        # 'ray_head_default' in 'gcp-ray.yaml'
        node_config=original_config['available_node_types'][head_node_type]
        ['node_config'],
        count=num_nodes,
        tags={},
        resume_stopped_nodes=True)

    with provision_logging.setup_provision_logging(log_dir):
        try:
            logger.debug(_TITLE.format('Provisioning'))
            logger.debug(
                'Provision config:\n'
                f'{json.dumps(dataclasses.asdict(bootstrap_config), indent=2)}')
            return _bulk_provision(cloud, region, zones, cluster_name,
                                   bootstrap_config)
        except Exception:  # pylint: disable=broad-except
            zone_str = 'all zones'
            if zones:
                zone_str = ','.join(zone.name for zone in zones)
            logger.debug(f'Failed to provision {cluster_name.display_name!r} '
                         f'on {cloud} ({zone_str}).')
            logger.debug(f'bulk_provision for {cluster_name!r} '
                         f'failed. Stacktrace:\n{traceback.format_exc()}')
            # If the cluster was ever up, stop it; otherwise terminate it.
            terminate = not prev_cluster_ever_up
            terminate_str = ('Terminating' if terminate else 'Stopping')
            logger.debug(f'{terminate_str} the failed cluster.')
            retry_cnt = 1
            while True:
                try:
                    teardown_cluster(
                        repr(cloud),
                        cluster_name,
                        terminate=terminate,
                        provider_config=original_config['provider'])
                    break
                except NotImplementedError as e:
                    verb = 'terminate' if terminate else 'stop'
                    # If the underlying cloud does not support stopping
                    # instances, we should stop failover as well.
                    raise provision_common.StopFailoverError(
                        'During provisioner\'s failover, '
                        f'{terminate_str.lower()} {cluster_name!r} failed. '
                        f'We cannot {verb} the resources launched, as it is '
                        f'not supported by {cloud}. Please try launching the '
                        'cluster again, or terminate it with: '
                        f'sky down {cluster_name.display_name}') from e
                except Exception as e:  # pylint: disable=broad-except
                    logger.debug(f'{terminate_str} {cluster_name!r} failed.')
                    logger.debug(f'Stacktrace:\n{traceback.format_exc()}')
                    retry_cnt += 1
                    if retry_cnt <= _MAX_RETRY:
                        logger.debug(f'Retrying {retry_cnt}/{_MAX_RETRY}...')
                        time.sleep(5)
                        continue
                    formatted_exception = common_utils.format_exception(
                        e, use_bracket=True)
                    raise provision_common.StopFailoverError(
                        'During provisioner\'s failover, '
                        f'{terminate_str.lower()} {cluster_name!r} failed. '
                        'This can cause resource leakage. Please check the '
                        'failure and the cluster status on the cloud, and '
                        'manually terminate the cluster. '
                        f'Details: {formatted_exception}') from e
            raise


def teardown_cluster(cloud_name: str, cluster_name: ClusterName,
                     terminate: bool, provider_config: Dict) -> None:
    """Deleting or stopping a cluster.

    Raises:
        Cloud specific exceptions: If the teardown process failed, cloud-
            specific exceptions will be raised by the cloud APIs.
    """
    if terminate:
        provision.terminate_instances(cloud_name, cluster_name.name_on_cloud,
                                      provider_config)
        metadata_utils.remove_cluster_metadata(cluster_name.name_on_cloud)
    else:
        provision.stop_instances(cloud_name, cluster_name.name_on_cloud,
                                 provider_config)


def _ssh_probe_command(ip: str,
                       ssh_port: int,
                       ssh_user: str,
                       ssh_private_key: str,
                       ssh_proxy_command: Optional[str] = None) -> List[str]:
    # NOTE: Ray uses 'uptime' command and 10s timeout, we use the same
    # setting here.
    command = [
        'ssh',
        '-T',
        '-i',
        ssh_private_key,
        f'{ssh_user}@{ip}',
        '-p',
        str(ssh_port),
        '-o',
        'StrictHostKeyChecking=no',
        '-o',
        'ConnectTimeout=10s',
        '-o',
        f'UserKnownHostsFile={os.devnull}',
        '-o',
        'IdentitiesOnly=yes',
        '-o',
        'ExitOnForwardFailure=yes',
        '-o',
        'ServerAliveInterval=5',
        '-o',
        'ServerAliveCountMax=3',
    ]
    if ssh_proxy_command is not None:
        command += ['-o', f'ProxyCommand={ssh_proxy_command}']
    command += ['uptime']
    return command


def _shlex_join(command: List[str]) -> str:
    """Join a command list into a shell command string.

    This is copied from Python 3.8's shlex.join, which is not available in
    Python 3.7.
    """
    return ' '.join(shlex.quote(arg) for arg in command)


def _wait_ssh_connection_direct(ip: str,
                                ssh_port: int,
                                ssh_user: str,
                                ssh_private_key: str,
                                ssh_control_name: Optional[str] = None,
                                ssh_proxy_command: Optional[str] = None,
                                **kwargs) -> Tuple[bool, str]:
    """Wait for SSH connection using raw sockets, and a SSH connection.

    Using raw socket is more efficient than using SSH command to probe the
    connection, before the SSH connection is ready. We use a actual SSH command
    connection to test the connection, after the raw socket connection is ready
    to make sure the SSH connection is actually ready.

    Returns:
        A tuple of (success, stderr).
    """
    del kwargs  # unused
    assert ssh_proxy_command is None, 'SSH proxy command is not supported.'
    try:
        success = False
        stderr = ''
        with socket.create_connection((ip, ssh_port), timeout=1) as s:
            if s.recv(100).startswith(b'SSH'):
                # Wait for SSH being actually ready, otherwise we may get the
                # following error:
                # "System is booting up. Unprivileged users are not permitted to
                # log in yet".
                success = True
        if success:
            return _wait_ssh_connection_indirect(ip, ssh_port, ssh_user,
                                                 ssh_private_key,
                                                 ssh_control_name,
                                                 ssh_proxy_command)
    except socket.timeout:  # this is the most expected exception
        stderr = f'Timeout: SSH connection to {ip} is not ready.'
    except Exception as e:  # pylint: disable=broad-except
        stderr = f'Error: {common_utils.format_exception(e)}'
    command = _ssh_probe_command(ip, ssh_port, ssh_user, ssh_private_key,
                                 ssh_proxy_command)
    logger.debug(f'Waiting for SSH to {ip}. Try: '
                 f'{_shlex_join(command)}. '
                 f'{stderr}')
    return False, stderr


def _wait_ssh_connection_indirect(ip: str,
                                  ssh_port: int,
                                  ssh_user: str,
                                  ssh_private_key: str,
                                  ssh_control_name: Optional[str] = None,
                                  ssh_proxy_command: Optional[str] = None,
                                  **kwargs) -> Tuple[bool, str]:
    """Wait for SSH connection using SSH command.

    Returns:
        A tuple of (success, stderr).
    """
    del ssh_control_name, kwargs  # unused
    command = _ssh_probe_command(ip, ssh_port, ssh_user, ssh_private_key,
                                 ssh_proxy_command)
    logger.debug(f'Waiting for SSH using command: {_shlex_join(command)}')
    proc = subprocess.run(command,
                          shell=False,
                          check=False,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE)
    if proc.returncode != 0:
        stderr = proc.stderr.decode('utf-8')
        stderr = f'Error: {stderr}'
        logger.debug(
            f'Waiting for SSH to {ip} with command: {_shlex_join(command)}\n'
            f'{stderr}')
        return False, stderr
    return True, ''


def wait_for_ssh(cluster_info: provision_common.ClusterInfo,
                 ssh_credentials: Dict[str, str]):
    """Wait until SSH is ready.

    Raises:
        RuntimeError: If the SSH connection is not ready after timeout.
    """
    if (cluster_info.has_external_ips() and
            ssh_credentials.get('ssh_proxy_command') is None):
        # If we can access public IPs, then it is more efficient to test SSH
        # connection with raw sockets.
        waiter = _wait_ssh_connection_direct
    else:
        # See https://github.com/skypilot-org/skypilot/pull/1512
        waiter = _wait_ssh_connection_indirect
    ip_list = cluster_info.get_feasible_ips()
    port_list = cluster_info.get_ssh_ports()

    timeout = 60 * 10  # 10-min maximum timeout
    start = time.time()
    # use a queue for SSH querying
    ips = collections.deque(ip_list)
    ssh_ports = collections.deque(port_list)
    while ips:
        ip = ips.popleft()
        ssh_port = ssh_ports.popleft()
        success, stderr = waiter(ip, ssh_port, **ssh_credentials)
        if not success:
            ips.append(ip)
            ssh_ports.append(ssh_port)
            if time.time() - start > timeout:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        f'Failed to SSH to {ip} after timeout {timeout}s, with '
                        f'{stderr}')
            logger.debug('Retrying in 1 second...')
            time.sleep(1)


def _post_provision_setup(
        cloud_name: str, cluster_name: ClusterName, cluster_yaml: str,
        provision_record: provision_common.ProvisionRecord,
        custom_resource: Optional[str]) -> provision_common.ClusterInfo:
    config_from_yaml = common_utils.read_yaml(cluster_yaml)
    provider_config = config_from_yaml.get('provider')
    cluster_info = provision.get_cluster_info(cloud_name,
                                              provision_record.region,
                                              cluster_name.name_on_cloud,
                                              provider_config=provider_config)

    if cluster_info.num_instances > 1:
        # Only worker nodes have logs in the per-instance log directory. Head
        # node's log will be redirected to the main log file.
        per_instance_log_dir = metadata_utils.get_instance_log_dir(
            cluster_name.name_on_cloud, '*')
        logger.debug('For per-instance logs, run: '
                     f'tail -n 100 -f {per_instance_log_dir}/*.log')

    logger.debug(
        'Provision record:\n'
        f'{json.dumps(dataclasses.asdict(provision_record), indent=2)}\n'
        'Cluster info:\n'
        f'{json.dumps(dataclasses.asdict(cluster_info), indent=2)}')

    head_instance = cluster_info.get_head_instance()
    if head_instance is None:
        raise RuntimeError(
            f'Provision failed for cluster {cluster_name!r}. '
            'Could not find any head instance. To fix: refresh '
            'status with: sky status -r; and retry provisioning.')

    # TODO(suquark): Move wheel build here in future PRs.
    ip_list = cluster_info.get_feasible_ips()
    port_list = cluster_info.get_ssh_ports()
    # We don't set docker_user here, as we are configuring the VM itself.
    ssh_credentials = backend_utils.ssh_credential_from_yaml(
        cluster_yaml, ssh_user=cluster_info.ssh_user)

    with rich_utils.safe_status(
            '[bold cyan]Launching - Waiting for SSH access[/]') as status:

        logger.debug(
            f'\nWaiting for SSH to be available for {cluster_name!r} ...')
        wait_for_ssh(cluster_info, ssh_credentials)
        logger.debug(f'SSH Conection ready for {cluster_name!r}')
        plural = '' if len(cluster_info.instances) == 1 else 's'
        logger.info(f'{colorama.Fore.GREEN}Successfully provisioned '
                    f'or found existing instance{plural}.'
                    f'{colorama.Style.RESET_ALL}')

        docker_config = config_from_yaml.get('docker', {})
        if docker_config:
            status.update(
                '[bold cyan]Lauching - Initializing docker container[/]')
            docker_user = instance_setup.initialize_docker(
                cluster_name.name_on_cloud,
                docker_config=docker_config,
                cluster_info=cluster_info,
                ssh_credentials=ssh_credentials)
            if docker_user is None:
                raise RuntimeError(
                    f'Failed to retrieve docker user for {cluster_name!r}. '
                    'Please check your docker configuration.')

            cluster_info.docker_user = docker_user
            ssh_credentials['docker_user'] = docker_user
            logger.debug(f'Docker user: {docker_user}')

        # We mount the metadata with sky wheel for speedup.
        # NOTE: currently we mount all credentials for all nodes, because
        # (1) spot controllers need permission to launch/down nodes of
        #     multiple clouds
        # (2) head instances need permission for auto stop or auto down
        #     nodes for the current cloud
        # (3) all instances need permission to mount storage for all clouds
        # It is possible to have a "smaller" permission model, but we leave that
        # for later.
        file_mounts = config_from_yaml.get('file_mounts', {})

        runtime_preparation_str = ('[bold cyan]Preparing SkyPilot '
                                   'runtime ({step}/3 - {step_name})')
        status.update(
            runtime_preparation_str.format(step=1, step_name='initializing'))
        instance_setup.internal_file_mounts(cluster_name.name_on_cloud,
                                            file_mounts, cluster_info,
                                            ssh_credentials)

        status.update(
            runtime_preparation_str.format(step=2, step_name='dependencies'))
        instance_setup.setup_runtime_on_cluster(
            cluster_name.name_on_cloud, config_from_yaml['setup_commands'],
            cluster_info, ssh_credentials)

        head_runner = command_runner.SSHCommandRunner(ip_list[0],
                                                      port=port_list[0],
                                                      **ssh_credentials)

        status.update(
            runtime_preparation_str.format(step=3, step_name='runtime'))
        full_ray_setup = True
        ray_port = constants.SKY_REMOTE_RAY_PORT
        if not provision_record.is_instance_just_booted(
                head_instance.instance_id):
            # Check if head node Ray is alive
            returncode, stdout, _ = head_runner.run(
                instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND,
                stream_logs=False,
                require_outputs=True)
            if returncode:
                logger.debug('Ray cluster on head is not up. Restarting...')
            else:
                logger.debug('Ray cluster on head is up.')
                ray_port = common_utils.decode_payload(stdout)['ray_port']
            full_ray_setup = bool(returncode)

        if full_ray_setup:
            logger.debug('Starting Ray on the entire cluster.')
            instance_setup.start_ray_on_head_node(
                cluster_name.name_on_cloud,
                custom_resource=custom_resource,
                cluster_info=cluster_info,
                ssh_credentials=ssh_credentials)

        # NOTE: We have to check all worker nodes to make sure they are all
        #  healthy, otherwise we can only start Ray on newly started worker
        #  nodes like this:
        #
        # worker_ips = []
        # for inst in cluster_info.instances.values():
        #     if provision_record.is_instance_just_booted(inst.instance_id):
        #         worker_ips.append(inst.public_ip)

        if len(ip_list) > 1:
            instance_setup.start_ray_on_worker_nodes(
                cluster_name.name_on_cloud,
                no_restart=not full_ray_setup,
                custom_resource=custom_resource,
                # Pass the ray_port to worker nodes for backward compatibility
                # as in some existing clusters the ray_port is not dumped with
                # instance_setup._DUMP_RAY_PORTS. We should use the ray_port
                # from the head node for worker nodes.
                ray_port=ray_port,
                cluster_info=cluster_info,
                ssh_credentials=ssh_credentials)

        instance_setup.start_skylet_on_head_node(cluster_name.name_on_cloud,
                                                 cluster_info, ssh_credentials)

    logger.info(f'{colorama.Fore.GREEN}Successfully provisioned cluster: '
                f'{cluster_name}{colorama.Style.RESET_ALL}')
    return cluster_info


def post_provision_runtime_setup(
        cloud_name: str, cluster_name: ClusterName, cluster_yaml: str,
        provision_record: provision_common.ProvisionRecord,
        custom_resource: Optional[str],
        log_dir: str) -> provision_common.ClusterInfo:
    """Run internal setup commands after provisioning and before user setup.

    Here are the steps:
    1. Wait for SSH to be ready.
    2. Mount the cloud credentials, skypilot wheel,
       and other necessary files to the VM.
    3. Run setup commands to install dependencies.
    4. Start ray cluster and skylet.

    Raises:
        RuntimeError: If the setup process encounters any error.
    """
    with provision_logging.setup_provision_logging(log_dir):
        try:
            logger.debug(_TITLE.format('System Setup After Provision'))
            return _post_provision_setup(cloud_name,
                                         cluster_name,
                                         cluster_yaml=cluster_yaml,
                                         provision_record=provision_record,
                                         custom_resource=custom_resource)
        except Exception:  # pylint: disable=broad-except
            logger.error('*** Failed setting up cluster. ***')
            logger.debug(f'Stacktrace:\n{traceback.format_exc()}')
            with ux_utils.print_exception_no_traceback():
                raise
