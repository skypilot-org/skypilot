"""A cloud-neutral VM provision backend."""
from typing import List, Optional
import json
import logging
import os
import pathlib
import time
import traceback

import colorama

from sky import clouds
from sky import provision
from sky import sky_logging

from sky.backends import backend_utils
from sky.provision import common as provision_comm
from sky.provision import setup as provision_setup
from sky.provision import utils as provision_utils
from sky.utils import command_runner
from sky.utils import common_utils

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

    # 5 seconds to 180 seconds. We need backoff for e.g., rate limit per
    # minute errors.
    backoff = common_utils.Backoff(initial_backoff=5,
                                   max_backoff_factor=180 // 5)

    # TODO(suquark): Should we just check the cluster status
    #  if 'cluster_exists' is true? Then we can skip bootstrapping
    #  etc if all nodes are ready. This is a known issue before.

    try:
        with backend_utils.safe_console_status(
                f'[bold cyan]Bootstrapping configurations for '
                f'[green]{cluster_name}[white] ...'):
            config = provision.bootstrap(provider_name, region_name,
                                         cluster_name, bootstrap_config)
    except Exception:
        logger.error('Failed to bootstrap configurations for '
                     f'"{cluster_name}".')
        raise

    for retry_cnt in range(_MAX_RETRY):
        try:
            with backend_utils.safe_console_status(
                    f'[bold cyan]Starting instances for '
                    f'[green]{cluster_name}[white] ...'):
                provision_metadata = provision.start_instances(provider_name,
                                                               region_name,
                                                               cluster_name,
                                                               config=config)
            break
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Starting instances for "{cluster_name}" '
                         f'failed. Stacktrace:\n{traceback.format_exc()}')
            if retry_cnt >= _MAX_RETRY - 1:
                logger.error(f'Failed to provision "{cluster_name}" after '
                             'maximum retries.')
                raise e
            sleep = backoff.current_backoff()
            logger.info('Retrying launching in {:.1f} seconds.'.format(sleep))
            time.sleep(sleep)

    logger.debug(f'Waiting "{cluster_name}" to be started...')
    with backend_utils.safe_console_status(
            f'[bold cyan]Waiting '
            f'[green]{cluster_name}[bold cyan] to be started...'):
        # AWS would take a very short time (<<1s) updating the state of
        # the instance. Wait 3 seconds should be enough.
        time.sleep(3)
        provision.wait_instances(provider_name, region_name, cluster_name,
                                 'running')

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
        logger.exception(f'Provision cluster {cluster_name} failed.')
        logger.error('*** Failed provisioning the cluster. ***')

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
        provision_utils.remove_cluster_profile(cluster_name)
    else:
        provision.stop_instances(cloud_name, region, cluster_name)
    try:
        teardown_verb = 'Terminating' if terminate else 'Stopping'
        with backend_utils.safe_console_status(
                f'[bold cyan]{teardown_verb} '
                f'[green]{cluster_name}\n'
                f'[white] Press Ctrl+C to send the task to background.'):
            if terminate:
                provision.wait_instances(cloud_name, region, cluster_name,
                                         'terminated')
            else:
                provision.wait_instances(cloud_name, region, cluster_name,
                                         'stopped')

    except KeyboardInterrupt:
        pass


def _post_provision_setup(
        cloud_name: str, cluster_name: str, cluster_yaml: str,
        local_wheel_path: pathlib.Path, wheel_hash: str,
        provision_metadata: provision_comm.ProvisionMetadata,
        custom_resource: Optional[str]) -> provision_comm.ClusterMetadata:
    # TODO(suquark): in the future, we only need to mount credentials
    #  for controllers.

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
    with backend_utils.safe_console_status(
            f'[bold cyan]Waiting SSH connection for '
            f'[green]{cluster_name}[white] ...'):
        provision_utils.wait_for_ssh(cluster_metadata, ssh_credentials)

    # we mount the metadata with sky wheel for speedup
    # TODO(suquark): only mount credentials for spot controller.
    metadata_path = provision_utils.generate_metadata(cloud_name, cluster_name)
    common_file_mounts = {
        backend_utils.SKY_REMOTE_PATH + '/' + wheel_hash: str(local_wheel_path),
        backend_utils.SKY_REMOTE_METADATA_PATH: str(metadata_path),
    }
    head_node_file_mounts = {
        **common_file_mounts,
        **config_from_yaml.get('file_mounts', {})
    }

    with backend_utils.safe_console_status(
            f'[bold cyan]Mounting internal files for '
            f'[green]{cluster_name}[white] ...'):
        provision_setup.internal_file_mounts(cluster_name,
                                             common_file_mounts,
                                             head_node_file_mounts,
                                             cluster_metadata,
                                             ssh_credentials,
                                             wheel_hash=wheel_hash)

    with backend_utils.safe_console_status(
            f'[bold cyan]Running setup commands for '
            f'[green]{cluster_name}[white] ...'):
        provision_setup.internal_dependencies_setup(
            cluster_name, config_from_yaml['setup_commands'], cluster_metadata,
            ssh_credentials)

    head_runner = command_runner.SSHCommandRunner(ip_list[0], **ssh_credentials)

    with backend_utils.safe_console_status(
            f'[bold cyan]Checking and starting Skylet for '
            f'[green]{cluster_name}[white] ...'):
        provision_setup.start_skylet(head_runner)

    full_ray_setup = True
    if not provision_metadata.is_instance_just_booted(
            head_instance.instance_id):
        # Check if head node Ray is alive
        with backend_utils.safe_console_status(
                f'[bold cyan]Checking Ray status for '
                f'[green]{cluster_name}[white] ...'):
            returncode = head_runner.run('ray status', stream_logs=False)
        if returncode:
            logger.error('Check result: Head node Ray is not up.')
        else:
            logger.debug('Check result: Head node Ray is up.')
        full_ray_setup = bool(returncode)

    if full_ray_setup:
        logger.debug('Start Ray on the whole cluster.')
        with backend_utils.safe_console_status(
                f'[bold cyan]Starting Ray on the head node for '
                f'[green]{cluster_name}[white] ...'):
            provision_setup.start_ray_head_node(head_runner,
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
        with backend_utils.safe_console_status(
                f'[bold cyan]Starting Ray on the worker nodes for '
                f'[green]{cluster_name}[white] ...'):
            provision_setup.start_ray_worker_nodes(
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
        return _post_provision_setup(cloud_name,
                                     cluster_name,
                                     cluster_yaml=cluster_yaml,
                                     local_wheel_path=local_wheel_path,
                                     wheel_hash=wheel_hash,
                                     provision_metadata=provision_metadata,
                                     custom_resource=custom_resource)
    except Exception:  # pylint: disable=broad-except
        logger.exception('Post provision setup of cluster '
                         f'{cluster_name} failed.')
        raise
    finally:
        logger.removeHandler(fh)
        fh.close()
