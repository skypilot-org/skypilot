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
from typing import Dict, List, Optional

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
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

# Do not use __name__ as we do not want to propagate logs to sky.provision,
# which will be customized in sky.provision.logging.
logger = sky_logging.init_logger('sky.provisioner')

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

    if isinstance(cloud, clouds.Local):
        logger.info(f'{style.BRIGHT}Launching on local cluster '
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
            # UX: for users we print "configure the cloud" vs. "bootstrap".
            logger.error(f'{colorama.Fore.YELLOW}Failed to configure the cloud '
                         f'for {cluster_name!r} with the following error:'
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

    logger.debug(f'\nProvisioning {cluster_name!r} took {time.time() - start} '
                 f'seconds.')

    return provision_record


def bulk_provision(
    cloud: clouds.Cloud,
    region: clouds.Region,
    zones: Optional[List[clouds.Zone]],
    cluster_name: ClusterName,
    num_nodes: int,
    cluster_yaml: str,
    is_prev_cluster_healthy: bool,
    log_dir: str,
) -> Optional[provision_common.ProvisionRecord]:
    """Provisions a cluster and wait until fully provisioned."""
    original_config = common_utils.read_yaml(cluster_yaml)
    bootstrap_config = provision_common.ProvisionConfig(
        provider_config=original_config['provider'],
        authentication_config=original_config['auth'],
        docker_config=original_config.get('docker', {}),
        # NOTE: (might be a legacy issue) we call it
        # 'ray_head_default' in 'gcp-ray.yaml'
        node_config=original_config['available_node_types']['ray.head.default']
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
            # If cluster was previously UP or STOPPED, stop it; otherwise
            # terminate.
            # FIXME(zongheng): terminating a potentially live cluster is
            # scary. Say: users have an existing cluster that got into INIT, do
            # sky launch, somehow failed, then we may be terminating it here.
            terminate = not is_prev_cluster_healthy
            terminate_str = ('Terminating' if terminate else 'Stopping')
            logger.debug(f'{terminate_str} the failed cluster.')
            teardown_cluster(repr(cloud),
                             cluster_name,
                             terminate=terminate,
                             provider_config=original_config['provider'])
            return None


def teardown_cluster(cloud_name: str, cluster_name: ClusterName,
                     terminate: bool, provider_config: Dict) -> None:
    """Deleting or stopping a cluster."""
    if terminate:
        provision.terminate_instances(cloud_name, cluster_name.name_on_cloud,
                                      provider_config)
        metadata_utils.remove_cluster_metadata(cluster_name.name_on_cloud)
    else:
        provision.stop_instances(cloud_name, cluster_name.name_on_cloud,
                                 provider_config)


def _ssh_probe_command(ip: str,
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


def _wait_ssh_connection_direct(
        ip: str,
        ssh_user: str,
        ssh_private_key: str,
        ssh_control_name: Optional[str] = None,
        ssh_proxy_command: Optional[str] = None) -> bool:
    assert ssh_proxy_command is None, 'SSH proxy command is not supported.'
    try:
        with socket.create_connection((ip, 22), timeout=1) as s:
            if s.recv(100).startswith(b'SSH'):
                # Wait for SSH being actually ready, otherwise we may get the
                # following error:
                # "System is booting up. Unprivileged users are not permitted to
                # log in yet".
                return _wait_ssh_connection_indirect(ip, ssh_user,
                                                     ssh_private_key,
                                                     ssh_control_name,
                                                     ssh_proxy_command)
    except socket.timeout:  # this is the most expected exception
        pass
    except Exception:  # pylint: disable=broad-except
        pass
    command = _ssh_probe_command(ip, ssh_user, ssh_private_key,
                                 ssh_proxy_command)
    logger.debug(f'Waiting for SSH to {ip}. Try: '
                 f'{_shlex_join(command)}')
    return False


def _wait_ssh_connection_indirect(
        ip: str,
        ssh_user: str,
        ssh_private_key: str,
        ssh_control_name: Optional[str] = None,
        ssh_proxy_command: Optional[str] = None) -> bool:
    del ssh_control_name
    command = _ssh_probe_command(ip, ssh_user, ssh_private_key,
                                 ssh_proxy_command)
    proc = subprocess.run(command,
                          shell=False,
                          check=False,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE)
    if proc.returncode != 0:
        logger.debug(
            f'Waiting for SSH to {ip} with command: {_shlex_join(command)}\n'
            f'Error: {proc.stderr.decode("utf-8")}')
    return proc.returncode == 0


def wait_for_ssh(cluster_info: provision_common.ClusterInfo,
                 ssh_credentials: Dict[str, str]):
    """Wait until SSH is ready."""
    if (cluster_info.has_external_ips() and
            ssh_credentials.get('ssh_proxy_command') is None):
        # If we can access public IPs, then it is more efficient to test SSH
        # connection with raw sockets.
        waiter = _wait_ssh_connection_direct
    else:
        # See https://github.com/skypilot-org/skypilot/pull/1512
        waiter = _wait_ssh_connection_indirect
    ip_list = cluster_info.get_feasible_ips()

    timeout = 60 * 10  # 10-min maximum timeout
    start = time.time()
    # use a queue for SSH querying
    ips = collections.deque(ip_list)
    while ips:
        ip = ips.popleft()
        if not waiter(ip, **ssh_credentials):
            ips.append(ip)
            if time.time() - start > timeout:
                with ux_utils.print_exception_no_traceback():
                    raise RuntimeError(
                        f'Failed to SSH to {ip} after timeout {timeout}s.')
            logger.debug('Retrying in 1 second...')
            time.sleep(1)


def _post_provision_setup(
        cloud_name: str, cluster_name: ClusterName, cluster_yaml: str,
        provision_record: provision_common.ProvisionRecord,
        custom_resource: Optional[str]) -> provision_common.ClusterInfo:
    cluster_info = provision.get_cluster_info(cloud_name,
                                              provision_record.region,
                                              cluster_name.name_on_cloud)

    if len(cluster_info.instances) > 1:
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
        raise RuntimeError(f'Provision failed for cluster {cluster_name!r}. '
                           'Could not find any head instance.')

    # TODO(suquark): Move wheel build here in future PRs.
    config_from_yaml = common_utils.read_yaml(cluster_yaml)
    ip_list = cluster_info.get_feasible_ips()
    ssh_credentials = backend_utils.ssh_credential_from_yaml(cluster_yaml)

    # TODO(suquark): Handle TPU VMs when dealing with GCP later.
    # if tpu_utils.is_tpu_vm_pod(handle.launched_resources):
    #     logger.info(f'{style.BRIGHT}Setting up TPU VM Pod workers...'
    #                 f'{style.RESET_ALL}')
    #     RetryingVmProvisioner._tpu_pod_setup(
    #         None, handle.cluster_yaml, handle)

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
                                                      port=22,
                                                      **ssh_credentials)

        status.update(
            runtime_preparation_str.format(step=3, step_name='runtime'))
        full_ray_setup = True
        if not provision_record.is_instance_just_booted(
                head_instance.instance_id):
            # Check if head node Ray is alive
            returncode = head_runner.run(
                instance_setup.RAY_STATUS_WITH_SKY_RAY_PORT_COMMAND,
                stream_logs=False)
            if returncode:
                logger.info('Ray cluster on head is not up. Restarting...')
            else:
                logger.debug('Ray cluster on head is up.')
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
