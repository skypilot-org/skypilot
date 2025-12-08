"""Utility functions for deploying local Kubernetes kind clusters."""
import os
import random
import shlex
import subprocess
import tempfile
import textwrap
from typing import Optional, Tuple

from sky import check as sky_check
from sky import sky_logging
from sky.backends import backend_utils
from sky.clouds import cloud as sky_cloud
from sky.provision.kubernetes import utils as kubernetes_utils
from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import log_utils
from sky.utils import rich_utils
from sky.utils import subprocess_utils
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

# Default path for Kubernetes configuration file
DEFAULT_KUBECONFIG_PATH = os.path.expanduser('~/.kube/config')
DEFAULT_LOCAL_CLUSTER_NAME = 'skypilot'
LOCAL_CLUSTER_PORT_RANGE = 100
LOCAL_CLUSTER_INTERNAL_PORT_START = 30000
LOCAL_CLUSTER_INTERNAL_PORT_END = 30099


def generate_kind_config(port_start: int,
                         num_nodes: int = 1,
                         gpus: bool = False) -> str:
    """Generate a kind cluster config with ports mapped from host to container

    Port range will be [port_start, port_start + LOCAL_CLUSTER_PORT_RANGE)
    Internally, this will map to ports 30000 - 30099

    Args:
        path: Path to generate the config file at
        port_start: Port range start for mappings
        num_nodes: Number of nodes in the cluster
        gpus: If true, initialize kind cluster with GPU support

    Returns:
        The kind cluster config
    """
    internal_start = LOCAL_CLUSTER_INTERNAL_PORT_START
    internal_end = LOCAL_CLUSTER_INTERNAL_PORT_END

    config = textwrap.dedent(f"""
    apiVersion: kind.x-k8s.io/v1alpha4
    kind: Cluster
    kubeadmConfigPatches:
    - |
      kind: ClusterConfiguration
      apiServer:
        extraArgs:
          "service-node-port-range": {internal_start}-{internal_end}
    nodes:
    - role: control-plane
      kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    """)
    if gpus:
        config += textwrap.indent(
            textwrap.dedent("""
        extraMounts:
        - hostPath: /dev/null
          containerPath: /var/run/nvidia-container-devices/all"""), ' ' * 2)
    config += textwrap.indent(textwrap.dedent("""
      extraPortMappings:"""), ' ' * 2)
    for offset in range(LOCAL_CLUSTER_PORT_RANGE):
        config += textwrap.indent(
            textwrap.dedent(f"""
        - containerPort: {internal_start + offset}
          hostPort: {port_start + offset}
          listenAddress: "0.0.0.0"
          protocol: tcp
        """), ' ' * 2)
    if num_nodes > 1:
        config += '- role: worker\n' * (num_nodes - 1)
    return config


def _get_port_range(name: str, port_start: Optional[int]) -> Tuple[int, int]:
    is_default = name == DEFAULT_LOCAL_CLUSTER_NAME
    if port_start is None:
        if is_default:
            port_start = LOCAL_CLUSTER_INTERNAL_PORT_START
        else:
            port_start = random.randint(301, 399) * 100
    port_end = port_start + LOCAL_CLUSTER_PORT_RANGE - 1

    port_range = f'Current port range: {port_start}-{port_end}'
    if is_default and port_start != LOCAL_CLUSTER_INTERNAL_PORT_START:
        raise ValueError('Default local cluster `skypilot` should have '
                         f'port range from 30000 to 30099. {port_range}.')
    if not is_default and port_start == LOCAL_CLUSTER_INTERNAL_PORT_START:
        raise ValueError('Port range 30000 to 30099 is reserved for '
                         f'default local cluster `skypilot`. {port_range}.')
    if port_start % 100 != 0:
        raise ValueError('Local cluster port start must be a multiple of 100. '
                         f'{port_range}.')

    return port_start, port_end


def deploy_local_cluster(name: Optional[str], port_start: Optional[int],
                         gpus: bool):
    name = name or DEFAULT_LOCAL_CLUSTER_NAME
    port_start, port_end = _get_port_range(name, port_start)
    context_name = f'kind-{name}'
    cluster_created = False

    # Check if GPUs are available on the host
    local_gpus_available = backend_utils.check_local_gpus()
    gpus = gpus and local_gpus_available

    # Check if ~/.kube/config exists:
    if os.path.exists(os.path.expanduser('~/.kube/config')):
        curr_context = kubernetes_utils.get_current_kube_config_context_name()
        if curr_context is not None and curr_context != context_name:
            logger.info(
                f'Current context in kube config: {curr_context}'
                f'\nWill automatically switch to {context_name} after the '
                'local cluster is created.')
    message_str = 'Creating local cluster {}{}...'
    message_str = message_str.format(
        name,
        ' with GPU support (this may take up to 15 minutes)' if gpus else '')

    with tempfile.NamedTemporaryFile(mode='w+', suffix='.yaml',
                                     delete=True) as f:
        # Choose random port range to use on the host machine.
        # Port range is port_start - port_start + 99 (exactly 100 ports).
        logger.debug(f'Using host port range {port_start}-{port_end}')
        f.write(generate_kind_config(port_start, gpus=gpus))
        f.flush()

        path_to_package = os.path.dirname(__file__)
        up_script_path = os.path.join(path_to_package, 'create_cluster.sh')

        # Get directory of script and run it from there
        cwd = os.path.dirname(os.path.abspath(up_script_path))
        run_command = f'{up_script_path} {name} {f.name}'
        if gpus:
            run_command += ' --gpus'
        run_command = shlex.split(run_command)

        # Setup logging paths
        run_timestamp = sky_logging.get_run_timestamp()
        log_path = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp,
                                'local_up.log')
        logger.info(message_str)

        with rich_utils.safe_status(
                ux_utils.spinner_message(message_str,
                                         log_path=log_path,
                                         is_local=True)):
            returncode, _, stderr = log_lib.run_with_log(
                cmd=run_command,
                log_path=log_path,
                require_outputs=True,
                stream_logs=False,
                line_processor=log_utils.SkyLocalUpLineProcessor(
                    log_path=log_path, is_local=True),
                cwd=cwd)

    # Kind always writes to stderr even if it succeeds.
    # If the failure happens after the cluster is created, we need
    # to strip all stderr of "No kind clusters found.", which is
    # printed when querying with kind get clusters.
    stderr = stderr.replace('No kind clusters found.\n', '')

    if returncode == 0:
        cluster_created = True
    elif returncode == 100:
        logger.info(
            ux_utils.finishing_message(
                f'Local cluster {name} already exists.\n',
                log_path=log_path,
                is_local=True,
                follow_up_message=
                'If you want to delete it instead, run: `sky local down --name {name}`'))  # pylint: disable=line-too-long
    else:
        with ux_utils.print_exception_no_traceback():
            log_hint = ux_utils.log_path_hint(log_path, is_local=True)
            raise RuntimeError('Failed to create local cluster. '
                               f'Full log: {log_hint}'
                               f'\nError: {stderr}')
    # Run sky check
    with rich_utils.safe_status('[bold cyan]Running sky check...'):
        sky_check.check_capability(sky_cloud.CloudCapability.COMPUTE,
                                   quiet=True,
                                   clouds=['kubernetes'])
    if cluster_created:
        # Prepare completion message which shows CPU and GPU count
        # Get number of CPUs
        p = subprocess_utils.run(
            'kubectl get nodes -o jsonpath=\'{.items[0].status.capacity.cpu}\'',
            capture_output=True)
        num_cpus = int(p.stdout.decode('utf-8'))

        # GPU count/type parsing
        gpu_message = ''
        gpu_hint = ''
        if gpus:
            # Get GPU model by querying the node labels
            label_name_escaped = 'skypilot.co/accelerator'.replace('.', '\\.')
            gpu_type_cmd = f'kubectl get node {name}-control-plane -o jsonpath=\"{{.metadata.labels[\'{label_name_escaped}\']}}\"'  # pylint: disable=line-too-long
            try:
                # Run the command and capture the output
                gpu_count_output = subprocess.check_output(gpu_type_cmd,
                                                           shell=True,
                                                           text=True)
                gpu_type_str = gpu_count_output.strip() + ' '
            except subprocess.CalledProcessError as e:
                output = str(e.output.decode('utf-8'))
                logger.warning(f'Failed to get GPU type: {output}')
                gpu_type_str = ''

            # Get number of GPUs (sum of nvidia.com/gpu resources)
            gpu_count_command = 'kubectl get nodes -o=jsonpath=\'{range .items[*]}{.status.allocatable.nvidia\\.com/gpu}{\"\\n\"}{end}\' | awk \'{sum += $1} END {print sum}\''  # pylint: disable=line-too-long
            try:
                # Run the command and capture the output
                gpu_count_output = subprocess.check_output(gpu_count_command,
                                                           shell=True,
                                                           text=True)
                gpu_count = gpu_count_output.strip(
                )  # Remove any extra whitespace
                gpu_message = f' and {gpu_count} {gpu_type_str}GPUs'
            except subprocess.CalledProcessError as e:
                output = str(e.output.decode('utf-8'))
                logger.warning(f'Failed to get GPU count: {output}')
                gpu_message = f' with {gpu_type_str}GPU support'

            gpu_hint = (
                '\nHint: To see the list of GPUs in the cluster, '
                'run \'sky show-gpus --cloud kubernetes\'') if gpus else ''

        if num_cpus < 2:
            logger.info('Warning: Local cluster has less than 2 CPUs. '
                        'This may cause issues with running tasks.')
        logger.info(
            ux_utils.finishing_message(
                message=(
                    f'Local Kubernetes cluster {name} created successfully '
                    f'with {num_cpus} CPUs{gpu_message} on host port range '
                    f'{port_start}-{port_end}.'),
                log_path=log_path,
                is_local=True,
                follow_up_message=(
                    '\n`sky launch` can now run tasks locally.\n'
                    'Hint: To change the number of CPUs, change your docker '
                    'runtime settings. See https://kind.sigs.k8s.io/docs/user/quick-start/#settings-for-docker-desktop for more info.'  # pylint: disable=line-too-long
                    f'{gpu_hint}')))


def teardown_local_cluster(name: Optional[str] = None):
    name = name or DEFAULT_LOCAL_CLUSTER_NAME
    cluster_removed = False

    path_to_package = os.path.dirname(__file__)
    down_script_path = os.path.join(path_to_package, 'delete_cluster.sh')

    cwd = os.path.dirname(os.path.abspath(down_script_path))
    run_command = f'{down_script_path} {name}'
    run_command = shlex.split(run_command)

    # Setup logging paths
    run_timestamp = sky_logging.get_run_timestamp()
    log_path = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp,
                            'local_down.log')

    with rich_utils.safe_status(
            ux_utils.spinner_message(f'Removing local cluster {name}',
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
            logger.info(
                ux_utils.error_message(f'Local cluster {name} does not exist.'))
        else:
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(f'Failed to down local cluster {name}. '
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
            ux_utils.finishing_message(f'Local cluster {name} removed.',
                                       log_path=log_path,
                                       is_local=True))
