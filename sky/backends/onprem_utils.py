"""Util constants/functions for Sky Onprem."""
import ast
import json
import os
import socket
import tempfile
import textwrap
from typing import Any, Dict, List, Optional, Tuple

import click
import rich.console as rich_console
import yaml

from sky import global_user_state
from sky import sky_logging
from sky.backends import backend_utils
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import schemas
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)
console = rich_console.Console()

# Placeholder variable for generated cluster config when
# `sky admin deploy` is run.
AUTH_PLACEHOLDER = 'PLACEHOLDER'
SKY_USER_LOCAL_CONFIG_PATH = '~/.sky/local/{}.yml'

_SKY_GET_ACCELERATORS_SCRIPT_PATH = '~/.sky/get_accelerators.py'


def check_if_local_cloud(cluster: str) -> bool:
    """Checks if cluster name is a local cloud.

    If cluster is a public cloud, this function will not check local
    cluster configs. If this cluster is a private cloud, this function
    will run correctness tests for cluster configs.
    """
    config_path = os.path.expanduser(SKY_USER_LOCAL_CONFIG_PATH.format(cluster))
    if not os.path.exists(config_path):
        # Public clouds go through no error checking.
        return False
    # Go through local cluster check to raise potential errors.
    check_and_get_local_clusters(suppress_error=False)
    return True


def check_and_get_local_clusters(suppress_error: bool = False) -> List[str]:
    """Lists all local clusters and checks cluster config validity.

    Args:
        suppress_error: Whether to suppress any errors raised.
    """
    local_dir = os.path.expanduser(os.path.dirname(SKY_USER_LOCAL_CONFIG_PATH))
    os.makedirs(local_dir, exist_ok=True)
    local_cluster_paths = [
        os.path.join(local_dir, f) for f in os.listdir(local_dir)
    ]
    # Filter out folders.
    local_cluster_paths = [
        path for path in local_cluster_paths
        if os.path.isfile(path) and path.endswith('.yml')
    ]

    local_cluster_names = []
    name_to_path_dict = {}

    for path in local_cluster_paths:
        with open(path, 'r') as f:
            yaml_config = yaml.safe_load(f)
            if not suppress_error:
                backend_utils.validate_schema(yaml_config,
                                              schemas.get_cluster_schema(),
                                              'Invalid cluster YAML: ')
            user_config = yaml_config['auth']
            cluster_name = yaml_config['cluster']['name']
        sky_local_path = SKY_USER_LOCAL_CONFIG_PATH

        if not suppress_error and (AUTH_PLACEHOLDER
                                   in (user_config['ssh_user'],
                                       user_config['ssh_private_key'])):
            with ux_utils.print_exception_no_traceback():
                raise ValueError('Authentication into local cluster requires '
                                 'specifying `ssh_user` and `ssh_private_key` '
                                 'under the `auth` dictionary. Please fill '
                                 'aforementioned fields in '
                                 f'{sky_local_path.format(cluster_name)}.')
        if cluster_name in local_cluster_names:
            if not suppress_error:
                with ux_utils.print_exception_no_traceback():
                    raise ValueError(
                        'Multiple configs in ~/.sky/local/ have the same '
                        f'cluster name {cluster_name!r}. '
                        'Fix the duplication and retry:'
                        f'\nCurrent config: {path}'
                        f'\nExisting config: {name_to_path_dict[cluster_name]}')
        else:
            name_to_path_dict[cluster_name] = path
            local_cluster_names.append(cluster_name)

    # Remove clusters that are in global user state but are not in
    # ~/.sky/local.
    records = backend_utils.get_clusters(
        include_reserved=False,
        refresh=False,
        cloud_filter=backend_utils.CloudFilter.LOCAL)
    saved_clusters = [r['name'] for r in records]
    for cluster_name in saved_clusters:
        if cluster_name not in local_cluster_names:
            logger.warning(f'Removing local cluster {cluster_name} from '
                           '`sky status`. No config found in ~/.sky/local.')
            global_user_state.remove_cluster(cluster_name, terminate=True)

    return local_cluster_names


def get_local_ips(cluster_name: str) -> List[str]:
    """Returns IP addresses of the local cluster."""
    config = get_local_cluster_config_or_error(cluster_name)
    ips = config['cluster']['ips']
    if isinstance(ips, str):
        ips = [ips]
    return ips


def get_local_auth_config(cluster_name: str) -> List[str]:
    """Returns IP addresses of the local cluster."""
    config = get_local_cluster_config_or_error(cluster_name)
    return config['auth']


def get_python_executable(cluster_name: str) -> str:
    """Returns the Ray cluster's python path."""
    config = get_local_cluster_config_or_error(cluster_name)
    return config['python']


def get_job_owner(cluster_yaml: dict) -> str:
    """Get the owner of the job."""
    cluster_config = common_utils.read_yaml(os.path.expanduser(cluster_yaml))
    # User name is guaranteed to exist (on all jinja files)
    return cluster_config['auth']['ssh_user']


def get_local_cluster_config_or_error(cluster_name: str) -> Dict[str, Any]:
    """Gets the local cluster config in ~/.sky/local/."""
    local_file = os.path.expanduser(
        SKY_USER_LOCAL_CONFIG_PATH.format(cluster_name))
    if os.path.isfile(local_file):
        return common_utils.read_yaml(local_file)
    raise ValueError(f'Cluster config {local_file} not found.')


def check_local_installation(ips: List[str], auth_config: Dict[str, str]):
    """Checks if the Sky dependencies are properly installed on the machine.

    This function checks for the following dependencies on the root user:
        - Sky
        - Ray
        - Python3 (>=3.6)
    This function assumes that the user is a system administrator and has sudo
    access to the machines.

    Args:
        ips: List of ips in the local cluster. 0-index corresponds to the head
          node's ip.
        auth_config: An authentication config that authenticates into the
        cluster.
    """
    ssh_user = auth_config['ssh_user']
    ssh_key = auth_config['ssh_private_key']
    ssh_credentials = (ssh_user, ssh_key, 'sky-admin-deploy')
    runners = command_runner.SSHCommandRunner.make_runner_list(
        ips, *ssh_credentials)

    def _check_dependencies(runner: command_runner.SSHCommandRunner) -> None:
        # Checks for global python3 installation.
        backend_utils.run_command_and_handle_ssh_failure(
            runner,
            'sudo python3 --version',
            failure_message=f'Python3 is not installed on {runner.ip}.')

        # Checks for global Ray installation (accessible by all users).
        backend_utils.run_command_and_handle_ssh_failure(
            runner,
            'sudo ray --version',
            failure_message=f'Ray is not installed on {runner.ip}.')

        # Checks for global Sky installation (accessible by all users). When
        # Sky's job submission code is ran on a user's account, Sky calls the
        # Ray cluster to prepare the user's job. Due to Ray's limitations,
        # this is ran under the admin's environment, which requires Sky to be
        # installed globally.
        # TODO(mluo): Make Sky admin only.
        backend_utils.run_command_and_handle_ssh_failure(
            runner,
            'sudo sky --help',
            failure_message=f'Sky is not installed on {runner.ip}.')

        # Patches global Ray.
        backend_utils.run_command_and_handle_ssh_failure(
            runner, ('sudo python3 -c "from sky.skylet.ray_patches '
                     'import patch; patch()"'),
            failure_message=f'Failed to patch ray on {runner.ip}.')

    subprocess_utils.run_in_parallel(_check_dependencies, runners)


def get_local_cluster_accelerators(
        ips: List[str], auth_config: Dict[str, str]) -> List[Dict[str, int]]:
    """Gets the custom accelerators for the local cluster.

    Loops through all cluster nodes to obtain a mapping of specific acclerator
    types to the count of accelerators.

    Args:
        ips: List of ips in the local cluster. 0-index corresponds to the head
          node's ip.
        auth_config: An authentication config that authenticates into the
        cluster.

    Returns:
        A list of dictionaries corresponding to accelerator counts for each
        node. Each dictionary maps accelerator type to the number of
        accelerators on the node. For example, in a two node cluster:
        [
         {'V100': 8,},
         {'K80': 2,},
        ]
    """
    ssh_user = auth_config['ssh_user']
    ssh_key = auth_config['ssh_private_key']
    remote_resource_dir = os.path.dirname(_SKY_GET_ACCELERATORS_SCRIPT_PATH)
    custom_resources = []

    # Ran on the remote cluster node to identify accelerator resources.
    # TODO(mluo): Add code to detect more types of GPUS and how much GPU
    # memory a GPU has.
    code = textwrap.dedent("""\
        import os

        all_accelerators = ['V100',
                            'P100',
                            'T4',
                            'P4',
                            'K80',
                            'A100',]
        accelerators_dict = {}
        for acc in all_accelerators:
            output_str = os.popen(f'lspci | grep \\'{acc}\\'').read()
            output_lst = output_str.split('\\n')
            count = 0
            for output in output_lst:
                count += int(acc in output)
            if count !=0:
                accelerators_dict[acc] = count

        print(accelerators_dict)
        """)

    ssh_credentials = (ssh_user, ssh_key, 'sky-admin-deploy')
    runners = command_runner.SSHCommandRunner.make_runner_list(
        ips, *ssh_credentials)

    def _gather_cluster_accelerators(
            runner: command_runner.SSHCommandRunner):  # -> Dict[str, int]:
        with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
            fp.write(code)
            fp.flush()
            runner.run(f'mkdir -p {remote_resource_dir}', stream_logs=False)
            runner.rsync(source=fp.name,
                         target=_SKY_GET_ACCELERATORS_SCRIPT_PATH,
                         up=True,
                         stream_logs=False)
            output = backend_utils.run_command_and_handle_ssh_failure(
                runner,
                f'python3 {_SKY_GET_ACCELERATORS_SCRIPT_PATH}',
                failure_message=f'Fail to fetch accelerators on {runner.ip}')
        node_accs = ast.literal_eval(output)
        return node_accs

    custom_resources = subprocess_utils.run_in_parallel(
        _gather_cluster_accelerators, runners)
    return custom_resources


def launch_ray_on_local_cluster(
        cluster_config: Dict[str, Dict[str, Any]],
        custom_resources: List[Dict[str, int]] = None) -> None:
    """Launches Ray on all nodes for local cluster.

    Launches Ray on the root user of all nodes and opens the Ray dashboard port
    on the non-head nodes. This ensures that Sky can coordinate and cancel jobs
    across nodes.

    Args:
        cluster_config: Dictionary representing the cluster config.
          Contains cluster-specific hyperparameters and the authentication
          config.
        custom_resources: List of dictionaries corresponding to accelerator
          counts for each node. Each dictionary maps accelerator type to the
          number of accelerators on the node.
    """
    local_cluster_config = cluster_config['cluster']
    ip_list = local_cluster_config['ips']
    if not isinstance(ip_list, list):
        ip_list = [ip_list]
    ip_list = [socket.gethostbyname(ip) for ip in ip_list]
    cluster_config['cluster']['ips'] = ip_list
    auth_config = cluster_config['auth']
    assert len(ip_list) >= 1, 'Must specify at least one Local IP'

    head_ip = ip_list[0]
    worker_ips = ip_list[1:]

    ssh_user = auth_config['ssh_user']
    ssh_key = auth_config['ssh_private_key']
    ssh_credentials = (ssh_user, ssh_key, 'sky-admin-deploy')
    head_runner = command_runner.SSHCommandRunner(head_ip, *ssh_credentials)
    worker_runners = []
    if worker_ips:
        worker_runners = command_runner.SSHCommandRunner.make_runner_list(
            worker_ips, *ssh_credentials)

    # Stops all running Ray instances on all nodes
    with console.status('[bold cyan]Stopping ray cluster'):

        def _stop_ray_workers(runner: command_runner.SSHCommandRunner):
            backend_utils.run_command_and_handle_ssh_failure(
                runner,
                'sudo ray stop -f',
                failure_message=f'Failed to stop ray on {runner.ip}.')

        subprocess_utils.run_in_parallel(_stop_ray_workers,
                                         [head_runner] + worker_runners)

    # Launching Ray on the head node.
    head_resources = json.dumps(custom_resources[0], separators=(',', ':'))
    head_cmd = ('sudo ray start --head --port=6379 '
                '--object-manager-port=8076 --dashboard-port 8265 '
                f'--resources={head_resources!r}')

    with console.status('[bold cyan]Launching ray cluster on head'):
        backend_utils.run_command_and_handle_ssh_failure(
            head_runner,
            head_cmd,
            failure_message='Failed to launch ray on head node.')

    if not worker_runners:
        return

    # Launches Ray on the worker nodes and links Ray dashboard from the head
    # to worker node.
    remote_ssh_key = f'~/.ssh/{os.path.basename(ssh_key)}'
    dashboard_remote_path = '~/.sky/dashboard_portforward.sh'
    worker_runners = [(runner, idx) for idx, runner in enumerate(worker_runners)
                     ]
    # Connect head node's Ray dashboard to worker nodes
    # Worker nodes need access to Ray dashboard to poll the
    # JobSubmissionClient (in subprocess_daemon.py) for completed,
    # failed, or cancelled jobs.
    ssh_options = command_runner.ssh_options_list(
        ssh_private_key=remote_ssh_key, ssh_control_name=None)
    ssh_options = ' '.join(ssh_options)
    port_cmd = (f'ssh -tt -L 8265:localhost:8265 '
                f'{ssh_options} {ssh_user}@{head_ip} '
                '\'while true; do sleep 86400; done\'')
    with console.status('[bold cyan]Waiting for workers.'):

        def _start_ray_workers(
                runner_tuple: Tuple[command_runner.SSHCommandRunner, int]):
            runner, idx = runner_tuple
            backend_utils.run_command_and_handle_ssh_failure(
                runner,
                'sudo ray stop -f',
                failure_message=f'Failed to stop ray on {runner.ip}.')

            worker_resources = json.dumps(custom_resources[idx + 1],
                                          separators=(',', ':'))
            worker_cmd = (f'sudo ray start --address={head_ip}:6379 '
                          '--object-manager-port=8076 --dashboard-port 8265 '
                          f'--resources={worker_resources!r}')
            backend_utils.run_command_and_handle_ssh_failure(
                runner,
                worker_cmd,
                failure_message=
                f'Failed to launch ray on worker node {runner.ip}.')

            # Connecting ray dashboard with worker node.
            runner.rsync(source=ssh_key,
                         target=remote_ssh_key,
                         up=True,
                         stream_logs=False)
            with tempfile.NamedTemporaryFile('w', prefix='sky_app_') as fp:
                fp.write(port_cmd)
                fp.flush()
                runner.rsync(source=fp.name,
                             target=dashboard_remote_path,
                             up=True,
                             stream_logs=False)
            # Kill existing dashboard connection and launch new one
            backend_utils.run_command_and_handle_ssh_failure(
                runner, f'chmod a+rwx {dashboard_remote_path};'
                'screen -S ray-dashboard -X quit;'
                f'screen -S ray-dashboard -dm {dashboard_remote_path}',
                failure_message=
                f'Failed to connect ray dashboard to worker node {runner.ip}.')

        subprocess_utils.run_in_parallel(_start_ray_workers, worker_runners)


def save_distributable_yaml(cluster_config: Dict[str, Dict[str, Any]]) -> None:
    """Generates a distributable yaml for the system admin to send to users.

    Args:
        cluster_config: Dictionary representing the cluster config.
          Contains cluster-specific hyperparameters and the authentication
          config.
    """
    auth_config = cluster_config['auth']
    head_ip = cluster_config['cluster']['ips'][0]
    ssh_user = auth_config['ssh_user']
    ssh_key = auth_config['ssh_private_key']
    ssh_credentials = (ssh_user, ssh_key, 'sky-admin-deploy')
    head_runner = command_runner.SSHCommandRunner(head_ip, *ssh_credentials)
    # Admin authentication must be censored out.
    cluster_config['auth']['ssh_user'] = AUTH_PLACEHOLDER
    cluster_config['auth']['ssh_private_key'] = AUTH_PLACEHOLDER
    cluster_config['python'] = backend_utils.run_command_and_handle_ssh_failure(
        head_runner,
        'sudo which python3',
        failure_message='Failed to obtain admin python path.').split()[0]

    cluster_name = cluster_config['cluster']['name']
    yaml_path = SKY_USER_LOCAL_CONFIG_PATH.format(cluster_name)
    abs_yaml_path = os.path.expanduser(yaml_path)
    os.makedirs(os.path.dirname(abs_yaml_path), exist_ok=True)
    common_utils.dump_yaml(abs_yaml_path, cluster_config)


# Currently, programmatic API doesn't check this.
def check_local_cloud_args(cloud: Optional[str] = None,
                           cluster_name: Optional[str] = None,
                           yaml_config: Optional[dict] = None) -> bool:
    """Checks if user-provided arguments satisfies local cloud specs.

    Args:
        cloud: Cloud type (AWS, GCP, Azure, or Local).
        cluster_name: Cluster name.
        yaml_config: User's task yaml loaded into a JSON dictionary.
    """
    yaml_cloud = None
    if yaml_config is not None and 'resources' in yaml_config:
        yaml_cloud = yaml_config['resources'].get('cloud')

    if (cluster_name is not None and check_if_local_cloud(cluster_name)):
        if cloud is not None and cloud != 'local':
            raise click.UsageError(f'Local cluster {cluster_name} is '
                                   f'not part of cloud: {cloud}.')
        if cloud is None and yaml_cloud is not None and yaml_cloud != 'local':
            raise ValueError(
                f'Detected Local cluster {cluster_name}. Must specify '
                '`cloud: local` or no cloud in YAML or CLI args.')
        return True
    else:
        if cloud == 'local' or yaml_cloud == 'local':
            if cluster_name is not None:
                raise click.UsageError(
                    f'Local cluster \'{cluster_name}\' does not exist. \n'
                    'See `sky status` for local cluster name(s).')
            else:
                raise click.UsageError(
                    'Specify -c [local_cluster] to launch on a local cluster.\n'
                    'See `sky status` for local cluster name(s).')

        return False
