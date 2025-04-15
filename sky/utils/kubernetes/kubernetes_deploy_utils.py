"""Utility functions for deploying Kubernetes clusters."""
import os
import shlex
import subprocess
import tempfile
from typing import List, Optional

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


def deploy_remote_cluster(ip_list: List[str],
                          ssh_user: str,
                          ssh_key: str,
                          cleanup: bool,
                          context_name: Optional[str] = None,
                          password: Optional[str] = None):
    success = False
    path_to_package = os.path.dirname(__file__)
    up_script_path = os.path.join(path_to_package, 'deploy_remote_cluster.sh')
    # Get directory of script and run it from there
    cwd = os.path.dirname(os.path.abspath(up_script_path))

    # Create temporary files for the IPs and SSH key
    with tempfile.NamedTemporaryFile(mode='w') as ip_file, \
         tempfile.NamedTemporaryFile(mode='w') as key_file:

        # Write IPs and SSH key to temporary files
        ip_file.write('\n'.join(ip_list))
        ip_file.flush()

        key_file.write(ssh_key)
        key_file.flush()
        os.chmod(key_file.name, 0o600)

        deploy_command = (f'{up_script_path} {ip_file.name} '
                          f'{ssh_user} {key_file.name}')
        if context_name is not None:
            deploy_command += f' {context_name}'
        if password is not None:
            deploy_command += f' --password {password}'
        if cleanup:
            deploy_command += ' --cleanup'

        # Convert the command to a format suitable for subprocess
        deploy_command = shlex.split(deploy_command)

        # Setup logging paths
        run_timestamp = sky_logging.get_run_timestamp()
        log_path = os.path.join(constants.SKY_LOGS_DIRECTORY, run_timestamp,
                                'local_up.log')

        if cleanup:
            msg_str = 'Cleaning up remote cluster...'
        else:
            msg_str = 'Deploying remote cluster...'
        with rich_utils.safe_status(
                ux_utils.spinner_message(msg_str,
                                         log_path=log_path,
                                         is_local=True)):
            returncode, _, stderr = log_lib.run_with_log(
                cmd=deploy_command,
                log_path=log_path,
                require_outputs=True,
                stream_logs=False,
                line_processor=log_utils.SkyRemoteUpLineProcessor(
                    log_path=log_path, is_local=True),
                cwd=cwd)
        if returncode == 0:
            success = True
        else:
            with ux_utils.print_exception_no_traceback():
                log_hint = ux_utils.log_path_hint(log_path, is_local=True)
                raise RuntimeError('Failed to deploy remote cluster. '
                                   f'Full log: {log_hint}'
                                   f'\nError: {stderr}')

        if success:
            if cleanup:
                logger.info(
                    ux_utils.finishing_message(
                        '🎉 Remote cluster cleaned up successfully.',
                        log_path=log_path,
                        is_local=True))
            else:
                logger.info(
                    ux_utils.finishing_message(
                        '🎉 Remote cluster deployed successfully.',
                        log_path=log_path,
                        is_local=True))


def deploy_local_cluster(gpus: bool):
    cluster_created = False

    # Check if GPUs are available on the host
    local_gpus_available = backend_utils.check_local_gpus()
    gpus = gpus and local_gpus_available

    # Check if ~/.kube/config exists:
    if os.path.exists(os.path.expanduser('~/.kube/config')):
        curr_context = kubernetes_utils.get_current_kube_config_context_name()
        skypilot_context = 'kind-skypilot'
        if curr_context is not None and curr_context != skypilot_context:
            logger.info(
                f'Current context in kube config: {curr_context}'
                '\nWill automatically switch to kind-skypilot after the local '
                'cluster is created.')
    message_str = 'Creating local cluster{}...'
    message_str = message_str.format((' with GPU support (this may take up '
                                      'to 15 minutes)') if gpus else '')
    path_to_package = os.path.dirname(__file__)
    up_script_path = os.path.join(path_to_package, 'create_cluster.sh')

    # Get directory of script and run it from there
    cwd = os.path.dirname(os.path.abspath(up_script_path))
    run_command = up_script_path + ' --gpus' if gpus else up_script_path
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
            line_processor=log_utils.SkyLocalUpLineProcessor(log_path=log_path,
                                                             is_local=True),
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
                'Local cluster already exists.\n',
                log_path=log_path,
                is_local=True,
                follow_up_message=
                'If you want to delete it instead, run: sky local down'))
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
            gpu_type_cmd = f'kubectl get node skypilot-control-plane -o jsonpath=\"{{.metadata.labels[\'{label_name_escaped}\']}}\"'  # pylint: disable=line-too-long
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
                message=(f'Local Kubernetes cluster created successfully with '
                         f'{num_cpus} CPUs{gpu_message}.'),
                log_path=log_path,
                is_local=True,
                follow_up_message=(
                    '\n`sky launch` can now run tasks locally.\n'
                    'Hint: To change the number of CPUs, change your docker '
                    'runtime settings. See https://kind.sigs.k8s.io/docs/user/quick-start/#settings-for-docker-desktop for more info.'  # pylint: disable=line-too-long
                    f'{gpu_hint}')))
