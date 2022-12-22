"""Cloud-neutral VM provision utils."""
from typing import List, Optional, Dict
import collections
import functools
import json
import logging
import os
import pathlib
import subprocess
import socket
import time
import traceback

import botocore.exceptions
import colorama

from sky import clouds
from sky import provision
from sky import sky_logging

from sky.backends import backend_utils
from sky.provision import common as provision_comm
from sky.provision import metadata_utils
from sky.provision import instance_setup
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import log_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

_MAX_RETRY = 3
_TITLE = '\n\n' + '=' * 20 + ' {} ' + '=' * 20 + '\n'


def _bulk_provision(
    cloud: clouds.Cloud,
    region: clouds.Region,
    zones: Optional[List[clouds.Zone]],
    cluster_name: str,
    bootstrap_config: provision_comm.InstanceConfig,
) -> provision_comm.ProvisionMetadata:
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
    try:
        with log_utils.safe_rich_status(
                f'[bold cyan]Bootstrapping configurations for '
                f'[green]{cluster_name}[white] ...'):
            # TODO(suquark): Should we cache the bootstrapped result?
            #  Currently it is not necessary as bootstrapping takes
            #  only ~3s, caching it seems over-engineering and could
            #  cause other issues like the cache is not synced
            #  with the cloud configuration.
            config = provision.bootstrap(provider_name, region_name,
                                         cluster_name, bootstrap_config)
    except Exception:
        logger.error('Failed to bootstrap configurations for '
                     f'"{cluster_name}".')
        raise

    try:
        with log_utils.safe_rich_status(f'[bold cyan]Starting instances for '
                                        f'[green]{cluster_name}[white] ...'):
            provision_metadata = provision.start_instances(provider_name,
                                                           region_name,
                                                           cluster_name,
                                                           config=config)
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Starting instances for "{cluster_name}" '
                     f'failed. Stacktrace:\n{traceback.format_exc()}')
        logger.error(f'Failed to provision "{cluster_name}" after '
                     'maximum retries.')
        raise e

    backoff = common_utils.Backoff(initial_backoff=1, max_backoff_factor=3)
    logger.debug(f'Waiting "{cluster_name}" to be started...')
    with log_utils.safe_rich_status(
            f'[bold cyan]Waiting '
            f'[green]{cluster_name}[bold cyan] to be started...'):
        # AWS would take a very short time (<<1s) updating the state of
        # the instance. Wait 3 seconds should be enough.
        time.sleep(3)
        for retry_cnt in range(_MAX_RETRY):
            try:
                provision.wait_instances(provider_name, region_name,
                                         cluster_name, 'running')
            except botocore.exceptions.WaiterError:
                time.sleep(backoff.current_backoff())

    logger.debug(f'Cluster up takes {time.time() - start} seconds with '
                 f'{retry_cnt} retries.')

    plural = '' if config.count == 1 else 's'
    if not isinstance(cloud, clouds.Local):
        logger.info(f'{colorama.Fore.GREEN}Successfully provisioned '
                    f'or found existing VM{plural}.{style.RESET_ALL}')
    return provision_metadata


def bulk_provision(
    cloud: clouds.Cloud,
    region: clouds.Region,
    zones: Optional[List[clouds.Zone]],
    cluster_name: str,
    num_nodes: int,
    cluster_yaml: str,
    is_prev_cluster_healthy: bool,
    log_dir: str,
) -> Optional[provision_comm.ProvisionMetadata]:
    """Provisions a cluster and wait until fully provisioned."""
    log_dir = os.path.abspath(os.path.expanduser(log_dir))
    os.makedirs(log_dir, exist_ok=True)
    log_abs_path = os.path.join(log_dir, 'provision.log')

    original_config = common_utils.read_yaml(cluster_yaml)
    bootstrap_config = provision_comm.InstanceConfig(
        cluster_name=cluster_name,
        provider_config=original_config['provider'],
        authentication_config=original_config['auth'],
        # NOTE: (might be a legacy issue) we call it
        # 'ray_head_default' in 'gcp-ray.yaml'
        node_config=original_config['available_node_types']['ray.head.default']
        ['node_config'],
        count=num_nodes,
        tags={},
        resume_stopped_nodes=True)

    fh = logging.FileHandler(log_abs_path)
    fh.setLevel(logging.DEBUG)
    try:
        logger.addHandler(fh)
        logger.debug(_TITLE.format('Provisioning'))
        logger.debug('Provision config:\n'
                     f'{json.dumps(bootstrap_config.dict(), indent=2)}')
        return _bulk_provision(cloud, region, zones, cluster_name,
                               bootstrap_config)
    except Exception:  # pylint: disable=broad-except
        logger.error(
            f'*** Failed provisioning the cluster ({cluster_name}). ***')
        logger.debug(f'Starting instances for "{cluster_name}" '
                     f'failed. Stacktrace:\n{traceback.format_exc()}')
        # If cluster was previously UP or STOPPED, stop it; otherwise
        # terminate.
        # FIXME(zongheng): terminating a potentially live cluster is
        # scary. Say: users have an existing cluster that got into INIT, do
        # sky launch, somehow failed, then we may be terminating it here.
        terminate = not is_prev_cluster_healthy
        terminate_str = ('Terminating' if terminate else 'Stopping')
        logger.error(f'*** {terminate_str} the failed cluster. ***')
        # TODO(suquark): In the future we should not wait for cluster stopping
        #  or termination. This could speed up fail over quite a lot.
        teardown_cluster(repr(cloud),
                         region.name,
                         cluster_name,
                         terminate=terminate)
        return None
    finally:
        logger.removeHandler(fh)
        fh.close()


def teardown_cluster(cloud_name: str, region: str, cluster_name: str,
                     terminate: bool) -> None:
    """Deleting or stopping a cluster."""
    if terminate:
        provision.terminate_instances(cloud_name, region, cluster_name)
        metadata_utils.remove_cluster_metadata(cluster_name)
    else:
        provision.stop_instances(cloud_name, region, cluster_name)

    # TODO(suquark): In theory, users do not need to wait for the cluster
    #  to be taken down completely and can interrupt the CLI at any time.
    #  However, we cannot notify users this fact because there is a
    #  progress bar currently running.
    status_to_wait = 'terminated' if terminate else 'stopped'
    provision.wait_instances(cloud_name, region, cluster_name, status_to_wait)


def _wait_ssh_connection_direct(ip: str) -> bool:
    try:
        with socket.create_connection((ip, 22), timeout=1) as s:
            if s.recv(100).startswith(b'SSH'):
                return True
    except socket.timeout:  # this is the most expected exception
        pass
    except Exception:  # pylint: disable=broad-except
        pass
    return False


def _wait_ssh_connection_indirect(
        ip: str,
        ssh_user: str,
        ssh_private_key: str,
        ssh_control_name: Optional[str] = None,
        ssh_proxy_command: Optional[str] = None) -> bool:
    del ssh_control_name
    # We test ssh with 'echo', because it is of the most common
    # commandline programs on both Unix-like and Windows platforms.
    # NOTE: Ray uses 'uptime' command and 10s timeout.
    command = [
        'ssh', '-T', '-i', ssh_private_key, f'{ssh_user}@{ip}', '-o',
        'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=20s', '-o',
        f'ProxyCommand={ssh_proxy_command}', 'echo'
    ]
    proc = subprocess.run(command,
                          shell=False,
                          check=False,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def wait_for_ssh(cluster_metadata: provision_comm.ClusterMetadata,
                 ssh_credentials: Dict[str, str]):
    """Wait until SSH is ready."""
    ips = cluster_metadata.get_feasible_ips()
    if cluster_metadata.has_public_ips():
        # If we can access public IPs, then it is more efficient to test SSH
        # connection with raw sockets.
        waiter = _wait_ssh_connection_direct
    else:
        # See https://github.com/skypilot-org/skypilot/pull/1512
        waiter = functools.partial(_wait_ssh_connection_indirect,
                                   **ssh_credentials)

    timeout = 60 * 10  # 10-min maximum timeout
    start = time.time()
    # use a queue for SSH querying
    ips = collections.deque(ips)
    while ips:
        ip = ips.popleft()
        if not waiter(ip):
            ips.append(ip)
            if time.time() - start > timeout:
                with ux_utils.print_exception_no_traceback():
                    raise TimeoutError(
                        f'Wait SSH timeout ({timeout}s) exceeded.')
            time.sleep(1)


def _post_provision_setup(
        cloud_name: str, cluster_name: str, cluster_yaml: str,
        local_wheel_path: pathlib.Path, wheel_hash: str,
        provision_metadata: provision_comm.ProvisionMetadata,
        custom_resource: Optional[str]) -> provision_comm.ClusterMetadata:
    cluster_metadata = provision.get_cluster_metadata(cloud_name,
                                                      provision_metadata.region,
                                                      cluster_name)

    logger.debug(f'Provision metadata: {repr(provision_metadata)}\n'
                 f'Cluster metadata: {repr(cluster_metadata)}')

    head_instance = cluster_metadata.get_head_instance()
    if head_instance is None:
        raise RuntimeError(f'Provision failed for cluster "{cluster_name}". '
                           'Could not find any head instance.')

    # TODO(suquark): Move wheel build here in future PRs.
    config_from_yaml = common_utils.read_yaml(cluster_yaml)
    ip_list = cluster_metadata.get_feasible_ips()

    # TODO(suquark): Handle TPU VMs when dealing with GCP later.
    # if tpu_utils.is_tpu_vm_pod(handle.launched_resources):
    #     logger.info(f'{style.BRIGHT}Setting up TPU VM Pod workers...'
    #                 f'{style.RESET_ALL}')
    #     RetryingVmProvisioner._tpu_pod_setup(
    #         None, handle.cluster_yaml, handle)

    ssh_credentials = backend_utils.ssh_credential_from_yaml(cluster_yaml)

    logger.debug(f'Waiting SSH connection for "{cluster_name}" ...')
    with log_utils.safe_rich_status(f'[bold cyan]Waiting SSH connection for '
                                    f'[green]{cluster_name}[white] ...'):
        wait_for_ssh(cluster_metadata, ssh_credentials)

    # We mount the metadata with sky wheel for speedup.
    # NOTE: currently we mount all credentials for all nodes, because
    # (1) spot controllers need permission to launch/down nodes of
    #     multiple clouds
    # (2) head instances need permission for auto stop or auto down
    #     nodes for the current cloud
    # (3) all instances need permission to mount storage for all clouds
    # It is possible to have a "smaller" permission model, but we leave that
    # for later.
    metadata_path = metadata_utils.generate_reflection_metadata(
        provision_metadata)
    file_mounts = {
        backend_utils.SKY_REMOTE_PATH + '/' + wheel_hash: str(local_wheel_path),
        metadata_utils.SKY_REMOTE_REFLECTION_METADATA_PATH: str(metadata_path),
        **config_from_yaml.get('file_mounts', {})
    }

    with log_utils.safe_rich_status(f'[bold cyan]Mounting internal files for '
                                    f'[green]{cluster_name}[white] ...'):
        instance_setup.internal_file_mounts(cluster_name,
                                            file_mounts,
                                            cluster_metadata,
                                            ssh_credentials,
                                            wheel_hash=wheel_hash)

    with log_utils.safe_rich_status(
            f'[bold cyan]Setting up SkyPilot runtime for '
            f'[green]{cluster_name}[white] ...'):
        instance_setup.internal_dependencies_setup(
            cluster_name, config_from_yaml['setup_commands'], cluster_metadata,
            ssh_credentials)

    head_runner = command_runner.SSHCommandRunner(ip_list[0], **ssh_credentials)

    with log_utils.safe_rich_status(
            f'[bold cyan]Checking and starting Skylet for '
            f'[green]{cluster_name}[white] ...'):
        instance_setup.start_skylet(head_runner)

    full_ray_setup = True
    if not provision_metadata.is_instance_just_booted(
            head_instance.instance_id):
        # Check if head node Ray is alive
        with log_utils.safe_rich_status(f'[bold cyan]Checking Ray status for '
                                        f'[green]{cluster_name}[white] ...'):
            returncode = head_runner.run('ray status', stream_logs=False)
        if returncode:
            logger.error('Check result: Head node Ray is not up.')
        else:
            logger.debug('Check result: Head node Ray is up.')
        full_ray_setup = bool(returncode)

    if full_ray_setup:
        logger.debug('Start Ray on the whole cluster.')
        with log_utils.safe_rich_status(
                f'[bold cyan]Starting Ray on the head node for '
                f'[green]{cluster_name}[white] ...'):
            instance_setup.start_ray_head_node(head_runner,
                                               custom_resource=custom_resource)
    else:
        logger.debug('Start Ray only on worker nodes.')

    # NOTE: We have to check all worker nodes to make sure they are all
    #  healthy, otherwise we can only start Ray on newly started worker
    #  nodes like this:
    #
    # worker_ips = []
    # for inst in cluster_metadata.instances.values():
    #     if provision_metadata.is_instance_just_booted(inst.instance_id):
    #         worker_ips.append(inst.public_ip)

    runners = command_runner.SSHCommandRunner.make_runner_list(
        ip_list[1:], **ssh_credentials)

    if runners:
        with log_utils.safe_rich_status(
                f'[bold cyan]Starting Ray on the worker nodes for '
                f'[green]{cluster_name}[white] ...'):
            instance_setup.start_ray_worker_nodes(
                runners,
                head_instance.private_ip,
                no_restart=not full_ray_setup,
                custom_resource=custom_resource)

    return cluster_metadata


def post_provision_setup(cloud_name: str, cluster_name: str, cluster_yaml: str,
                         local_wheel_path: pathlib.Path, wheel_hash: str,
                         provision_metadata: provision_comm.ProvisionMetadata,
                         custom_resource: Optional[str],
                         log_dir: str) -> provision_comm.ClusterMetadata:
    """Run internal setup commands after provisioning and before
    user setup."""
    log_path = os.path.join(log_dir, 'provision.log')
    log_abs_path = os.path.abspath(os.path.expanduser(log_path))
    fh = logging.FileHandler(log_abs_path)
    fh.setLevel(logging.DEBUG)
    try:
        logger.addHandler(fh)
        logger.debug(_TITLE.format('System Setup After Provision'))
        per_instance_log_dir = metadata_utils.get_instance_log_dir(
            cluster_name, '*')
        logger.debug(
            f'For per-instance logs, see "{str(per_instance_log_dir)}".')
        return _post_provision_setup(cloud_name,
                                     cluster_name,
                                     cluster_yaml=cluster_yaml,
                                     local_wheel_path=local_wheel_path,
                                     wheel_hash=wheel_hash,
                                     provision_metadata=provision_metadata,
                                     custom_resource=custom_resource)
    except Exception:  # pylint: disable=broad-except
        logger.error(
            f'*** Failed setting up cluster {cluster_name} after provision. ***'
        )
        logger.debug(f'Stacktrace:\n{traceback.format_exc()}')
        with ux_utils.print_exception_no_traceback():
            raise
    finally:
        logger.removeHandler(fh)
        fh.close()
