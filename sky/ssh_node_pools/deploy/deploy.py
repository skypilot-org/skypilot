"""SSH-based Kubernetes Cluster Deployment Script"""
# pylint: disable=line-too-long
import base64
import concurrent.futures as cf
import os
import re
import shlex
import shutil
import tempfile
from typing import List, Optional

import colorama
import yaml

from sky import sky_logging
from sky.ssh_node_pools import constants
from sky.ssh_node_pools import utils as ssh_utils
from sky.ssh_node_pools.deploy import tunnel_utils
from sky.ssh_node_pools.deploy import utils as deploy_utils
from sky.utils import rich_utils
from sky.utils import ux_utils

RESET_ALL = colorama.Style.RESET_ALL

# Get the directory of this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

logger = sky_logging.init_logger(__name__)


def progress_message(message):
    """Show a progress message."""
    logger.info(f'{colorama.Fore.YELLOW}➜ {message}{RESET_ALL}')


def success_message(message):
    """Show a success message."""
    logger.info(f'{colorama.Fore.GREEN}✔ {message}{RESET_ALL}')


def force_update_status(message):
    """Force update rich spinner status."""
    rich_utils.force_update_status(ux_utils.spinner_message(message))


def run(cleanup: bool = False,
        infra: Optional[str] = None,
        kubeconfig_path: str = constants.DEFAULT_KUBECONFIG_PATH):
    """Deploy a Kubernetes cluster on SSH targets.

    This function reads ~/.sky/ssh_node_pools.yaml and uses it to deploy a
    Kubernetes cluster on the specified machines.

    Args:
        cleanup: Whether to clean up the cluster instead of deploying.
        infra: Name of the cluster in ssh_node_pools.yaml to use.
            If None, the first cluster in the file will be used.
        kubeconfig_path: Path to save the Kubernetes configuration file.
            If None, the default ~/.kube/config will be used.
    """
    deploy_utils.check_ssh_cluster_dependencies()
    action = 'Cleanup' if cleanup else 'Deployment'
    msg_str = f'Initializing SSH Node Pools {action}...'

    with rich_utils.safe_status(ux_utils.spinner_message(msg_str)):
        try:
            deploy_multiple_clusters(infra=infra,
                                     cleanup=cleanup,
                                     kubeconfig_path=kubeconfig_path)
        except Exception as e:  # pylint: disable=broad-except
            logger.error(str(e))
            with ux_utils.print_exception_no_traceback():
                raise RuntimeError(
                    'Failed to deploy SkyPilot on some Node Pools.') from e

    # Add empty line for ux-purposes.
    logger.info('')
    if cleanup:
        logger.info(
            ux_utils.finishing_message(
                '🎉 SSH Node Pools cleaned up successfully.'))
    else:
        logger.info(
            ux_utils.finishing_message(
                '🎉 SSH Node Pools set up successfully. ',
                follow_up_message=(
                    f'Run `{colorama.Style.BRIGHT}'
                    f'sky check ssh'
                    f'{colorama.Style.RESET_ALL}` to verify access, '
                    f'`{colorama.Style.BRIGHT}sky launch --infra ssh'
                    f'{colorama.Style.RESET_ALL}` to launch a cluster.')))


def deploy_multiple_clusters(
        infra: Optional[str],
        ssh_node_pools_file: str = constants.DEFAULT_SSH_NODE_POOLS_PATH,
        kubeconfig_path: str = constants.DEFAULT_KUBECONFIG_PATH,
        cleanup: bool = True):

    kubeconfig_path = kubeconfig_path or constants.DEFAULT_KUBECONFIG_PATH
    kubeconfig_path = os.path.expanduser(kubeconfig_path)

    failed_clusters = []
    successful_clusters = []

    # Using YAML configuration
    targets = ssh_utils.load_ssh_targets(ssh_node_pools_file)
    clusters_config = ssh_utils.get_cluster_config(
        targets, infra, file_path=ssh_node_pools_file)

    # Print information about clusters being processed
    num_clusters = len(clusters_config)
    cluster_names = list(clusters_config.keys())
    cluster_info = f'Found {num_clusters} Node Pool{"s" if num_clusters > 1 else ""}: {", ".join(cluster_names)}'
    logger.info(f'{colorama.Fore.CYAN}{cluster_info}{RESET_ALL}')

    # Process each cluster
    for cluster_name, cluster_config in clusters_config.items():
        try:
            action = 'Cleaning up' if cleanup else 'Deploying'
            force_update_status(f'{action} Node Pool: {cluster_name}')
            hosts_info = ssh_utils.prepare_hosts_info(cluster_name,
                                                      cluster_config)

            if not hosts_info:
                logger.warning(
                    f'{colorama.Fore.RED}Error: No valid hosts found '
                    f'for cluster {cluster_name!r}. Skipping.{RESET_ALL}')
                continue

            context_name = f'ssh-{cluster_name}'

            # Check cluster history
            os.makedirs(constants.NODE_POOLS_INFO_DIR, exist_ok=True)
            history_yaml_file = os.path.join(constants.NODE_POOLS_INFO_DIR,
                                             f'{context_name}-history.yaml')

            history = None
            if os.path.exists(history_yaml_file):
                logger.debug(f'Loading history from {history_yaml_file}')
                with open(history_yaml_file, 'r', encoding='utf-8') as f:
                    history = yaml.safe_load(f)
            else:
                logger.debug(f'No history found for {context_name}.')

            history_workers_info = None
            history_worker_nodes = None
            history_use_ssh_config = None
            # Do not support changing anything besides hosts for now
            if history is not None:
                for key in ['user', 'identity_file', 'password']:
                    if not cleanup and history.get(key) != cluster_config.get(
                            key):
                        raise ValueError(
                            f'Cluster configuration has changed for field {key!r}. '
                            f'Previous value: {history.get(key)}, '
                            f'Current value: {cluster_config.get(key)}')
                history_hosts_info = ssh_utils.prepare_hosts_info(
                    cluster_name, history)
                if not cleanup and history_hosts_info[0] != hosts_info[0]:
                    raise ValueError(
                        f'Cluster configuration has changed for master node. '
                        f'Previous value: {history_hosts_info[0]}, '
                        f'Current value: {hosts_info[0]}')
                history_workers_info = history_hosts_info[1:] if len(
                    history_hosts_info) > 1 else []
                history_worker_nodes = [h['ip'] for h in history_workers_info]
                history_use_ssh_config = [
                    h.get('use_ssh_config', False) for h in history_workers_info
                ]

            # Use the first host as the head node and the rest as worker nodes
            head_host = hosts_info[0]
            worker_hosts = hosts_info[1:] if len(hosts_info) > 1 else []

            head_node = head_host['ip']
            worker_nodes = [h['ip'] for h in worker_hosts]
            ssh_user = head_host['user']
            ssh_key = head_host['identity_file']
            head_use_ssh_config = head_host.get('use_ssh_config', False)
            worker_use_ssh_config = [
                h.get('use_ssh_config', False) for h in worker_hosts
            ]
            password = head_host['password']

            # Deploy this cluster
            unsuccessful_workers = deploy_single_cluster(
                cluster_name,
                head_node,
                worker_nodes,
                ssh_user,
                ssh_key,
                context_name,
                password,
                head_use_ssh_config,
                worker_use_ssh_config,
                kubeconfig_path,
                cleanup,
                worker_hosts=worker_hosts,
                history_worker_nodes=history_worker_nodes,
                history_workers_info=history_workers_info,
                history_use_ssh_config=history_use_ssh_config)

            if not cleanup:
                successful_hosts = []
                for host in cluster_config['hosts']:
                    if isinstance(host, str):
                        host_node = host
                    else:
                        host_node = host['ip']
                    if host_node not in unsuccessful_workers:
                        successful_hosts.append(host)
                cluster_config['hosts'] = successful_hosts
                with open(history_yaml_file, 'w', encoding='utf-8') as f:
                    logger.debug(f'Writing history to {history_yaml_file}')
                    yaml.dump(cluster_config, f)

            action = 'cleanup' if cleanup else 'deployment'
            logger.info(
                f'{colorama.Fore.CYAN}Completed {action} for cluster: {cluster_name}{colorama.Style.RESET_ALL}'
            )
            successful_clusters.append(cluster_name)
        except Exception as e:  # pylint: disable=broad-except
            reason = str(e)
            failed_clusters.append((cluster_name, reason))
            action = 'cleaning' if cleanup else 'deploying'
            logger.debug(
                f'Error {action} SSH Node Pool `{cluster_name}`: {reason}')

    if failed_clusters:
        action = 'clean' if cleanup else 'deploy'
        msg = f'{colorama.Fore.GREEN}Successfully {action}ed {len(successful_clusters)} cluster(s) ({", ".join(successful_clusters)}). {RESET_ALL}'
        msg += f'{colorama.Fore.RED}Failed to {action} {len(failed_clusters)} cluster(s): {RESET_ALL}'
        for cluster_name, reason in failed_clusters:
            msg += f'\n  {cluster_name}: {reason}'
        raise RuntimeError(msg)


def deploy_single_cluster(cluster_name,
                          head_node,
                          worker_nodes,
                          ssh_user,
                          ssh_key,
                          context_name,
                          password,
                          head_use_ssh_config,
                          worker_use_ssh_config,
                          kubeconfig_path,
                          cleanup,
                          worker_hosts=None,
                          history_worker_nodes=None,
                          history_workers_info=None,
                          history_use_ssh_config=None) -> List[str]:
    """Deploy or clean up a single Kubernetes cluster.

    Returns: List of unsuccessful worker nodes.
    """
    history_yaml_file = os.path.join(constants.NODE_POOLS_INFO_DIR,
                                     f'{context_name}-history.yaml')
    cert_file_path = os.path.join(constants.NODE_POOLS_INFO_DIR,
                                  f'{context_name}-cert.pem')
    key_file_path = os.path.join(constants.NODE_POOLS_INFO_DIR,
                                 f'{context_name}-key.pem')
    tunnel_log_file_path = os.path.join(constants.NODE_POOLS_INFO_DIR,
                                        f'{context_name}-tunnel.log')

    # Generate the askpass block if password is provided
    askpass_block = create_askpass_script(password)

    # Token for k3s
    # TODO (kyuds): make this configurable?
    k3s_token = constants.K3S_TOKEN

    # Pre-flight checks
    logger.info(f'Checking SSH connection to head node ({head_node})...')
    result = deploy_utils.run_remote(
        head_node,
        f'echo \'SSH connection successful ({head_node})\'',
        ssh_user,
        ssh_key,
        use_ssh_config=head_use_ssh_config)
    if result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to SSH to head node ({head_node}). '
                f'Please check the SSH configuration and logs for more details.'
            )
    elif result.startswith('SSH connection successful'):
        success_message(f'SSH connection established to head node {head_node}.')

    # Checking history
    history_exists = (history_worker_nodes is not None and
                      history_workers_info is not None and
                      history_use_ssh_config is not None)

    # Cleanup history worker nodes
    worker_nodes_to_cleanup = []
    remove_worker_cmds = []
    if history_exists:
        for history_node, history_info, use_ssh_config in zip(
                history_worker_nodes, history_workers_info,
                history_use_ssh_config):
            if worker_hosts is not None and history_info not in worker_hosts:
                logger.debug(
                    f'Worker node {history_node} not found in YAML config. '
                    'Removing from history...')
                worker_nodes_to_cleanup.append(
                    dict(
                        node=history_node,
                        user=ssh_user
                        if history_info is None else history_info['user'],
                        ssh_key=ssh_key if history_info is None else
                        history_info['identity_file'],
                        askpass_block=(askpass_block if history_info is None
                                       else create_askpass_script(
                                           history_info['password'])),
                        use_ssh_config=use_ssh_config,
                    ))
                remove_worker_cmds.append(
                    f'kubectl delete node -l skypilot-ip={history_node}')
        # If this is a create operation and there exists some stale log,
        # cleanup the log for a new file to store new logs.
        if not cleanup and os.path.exists(tunnel_log_file_path):
            os.remove(tunnel_log_file_path)

    # If --cleanup flag is set, uninstall k3s and exit
    if cleanup:
        # Pickup all nodes
        worker_nodes_to_cleanup.clear()
        for node, info, use_ssh_config in zip(worker_nodes, worker_hosts,
                                              worker_use_ssh_config):
            worker_nodes_to_cleanup.append(
                dict(
                    node=node,
                    user=ssh_user if info is None else info['user'],
                    ssh_key=ssh_key if info is None else info['identity_file'],
                    askpass_block=(askpass_block if info is None else
                                   create_askpass_script(info['password'])),
                    use_ssh_config=use_ssh_config,
                ))

        # Clean up head node
        cleanup_node(head_node,
                     ssh_user,
                     ssh_key,
                     askpass_block,
                     use_ssh_config=head_use_ssh_config,
                     is_worker=False)
    # Clean up worker nodes
    force_update_status(f'Cleaning up worker nodes [{cluster_name}]')
    with cf.ThreadPoolExecutor() as executor:
        executor.map(lambda kwargs: cleanup_node(**kwargs),
                     worker_nodes_to_cleanup)

    with cf.ThreadPoolExecutor() as executor:
        executor.map(lambda cmd: deploy_utils.run_command(cmd, shell=True),
                     remove_worker_cmds)

    if cleanup:
        # Remove the context from local kubeconfig if it exists
        if os.path.isfile(kubeconfig_path):
            logger.debug(
                f'Removing context {context_name!r} from local kubeconfig...')
            deploy_utils.run_command(
                ['kubectl', 'config', 'delete-context', context_name],
                shell=False,
                silent=True)
            deploy_utils.run_command(
                ['kubectl', 'config', 'delete-cluster', context_name],
                shell=False,
                silent=True)
            deploy_utils.run_command(
                ['kubectl', 'config', 'delete-user', context_name],
                shell=False,
                silent=True)

            # Update the current context to the first available context
            contexts = deploy_utils.run_command([
                'kubectl', 'config', 'view', '-o',
                'jsonpath=\'{.contexts[0].name}\''
            ],
                                                shell=False,
                                                silent=True)
            if contexts:
                deploy_utils.run_command(
                    ['kubectl', 'config', 'use-context', contexts],
                    shell=False,
                    silent=True)
            else:
                # If no context is available, simply unset the current context
                deploy_utils.run_command(
                    ['kubectl', 'config', 'unset', 'current-context'],
                    shell=False,
                    silent=True)

            logger.debug(
                f'Context {context_name!r} removed from local kubeconfig.')

        for file in [history_yaml_file, cert_file_path, key_file_path]:
            if os.path.exists(file):
                os.remove(file)

        # Clean up SSH tunnel after clean up kubeconfig, because the kubectl
        # will restart the ssh tunnel if it's not running.
        tunnel_utils.cleanup_kubectl_ssh_tunnel(cluster_name, context_name)

        success_message(f'Node Pool `{cluster_name}` cleaned up successfully.')
        return []

    logger.debug('Checking TCP Forwarding Options...')
    cmd = (
        'if [ "$(sudo sshd -T | grep allowtcpforwarding)" = "allowtcpforwarding yes" ]; then '
        f'echo "TCP Forwarding already enabled on head node ({head_node})."; '
        'else '
        'sudo sed -i \'s/^#\\?\\s*AllowTcpForwarding.*/AllowTcpForwarding yes/\' '
        '/etc/ssh/sshd_config && sudo systemctl restart sshd && '
        f'echo "Successfully enabled TCP Forwarding on head node ({head_node})."; '
        'fi')
    result = deploy_utils.run_remote(head_node,
                                     shlex.quote(cmd),
                                     ssh_user,
                                     ssh_key,
                                     use_ssh_config=head_use_ssh_config,
                                     use_shell=True)
    if result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to setup TCP forwarding on head node ({head_node}). '
                f'Please check the SSH configuration.')

    # Get effective IP for master node if using SSH config - needed for workers to connect
    if head_use_ssh_config:
        effective_master_ip = deploy_utils.get_effective_host_ip(head_node)
        logger.info(f'{colorama.Fore.GREEN}Resolved head node {head_node} '
                    f'to {effective_master_ip} from SSH config{RESET_ALL}')
    else:
        effective_master_ip = head_node

    # Step 1: Install k3s on the head node
    # Check if head node has a GPU
    install_gpu = False
    force_update_status(
        f'Deploying SkyPilot runtime on head node ({head_node}).')
    cmd = f"""
        {askpass_block}
        curl -sfL https://get.k3s.io | K3S_TOKEN={k3s_token} K3S_NODE_NAME={head_node} sudo -E -A sh - &&
        mkdir -p ~/.kube &&
        sudo -A cp /etc/rancher/k3s/k3s.yaml ~/.kube/config &&
        sudo -A chown $(id -u):$(id -g) ~/.kube/config &&
        for i in {{1..3}}; do
            if kubectl wait --for=condition=ready node --all --timeout=2m --kubeconfig ~/.kube/config; then
                break
            else
                echo 'Waiting for nodes to be ready...'
                sleep 5
            fi
        done
        if [ $i -eq 3 ]; then
            echo 'Failed to wait for nodes to be ready after 3 attempts'
            exit 1
        fi
    """
    result = deploy_utils.run_remote(head_node,
                                     cmd,
                                     ssh_user,
                                     ssh_key,
                                     use_ssh_config=head_use_ssh_config)
    if result is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(
                f'Failed to deploy K3s on head node ({head_node}).')
    success_message(
        f'SkyPilot runtime successfully deployed on head node ({head_node}).')

    # Check if head node has a GPU
    install_gpu = False
    if deploy_utils.check_gpu(head_node,
                              ssh_user,
                              ssh_key,
                              use_ssh_config=head_use_ssh_config,
                              is_head=True):
        install_gpu = True

    # Fetch the head node's internal IP (this will be passed to worker nodes)
    master_addr = deploy_utils.run_remote(head_node,
                                          'hostname -I | awk \'{print $1}\'',
                                          ssh_user,
                                          ssh_key,
                                          use_ssh_config=head_use_ssh_config)
    if master_addr is None:
        with ux_utils.print_exception_no_traceback():
            raise RuntimeError(f'Failed to SSH to head node ({head_node}). '
                               f'Please check the SSH configuration.')
    logger.debug(f'Master node internal IP: {master_addr}')

    # Step 2: Install k3s on worker nodes and join them to the master node
    def deploy_worker(args):
        (i, node, worker_hosts, history_workers_info, ssh_user, ssh_key,
         askpass_block, worker_use_ssh_config, master_addr, k3s_token) = args

        # If using YAML config with specific worker info
        if worker_hosts and i < len(worker_hosts):
            if history_workers_info is not None and worker_hosts[
                    i] in history_workers_info:
                logger.info(
                    f'{colorama.Style.DIM}✔ SkyPilot runtime already deployed on worker node {node}. '
                    f'Skipping...{RESET_ALL}')
                return node, True, False
            worker_user = worker_hosts[i]['user']
            worker_key = worker_hosts[i]['identity_file']
            worker_password = worker_hosts[i]['password']
            worker_askpass = create_askpass_script(worker_password)
            worker_config = worker_use_ssh_config[i]
        else:
            worker_user = ssh_user
            worker_key = ssh_key
            worker_askpass = askpass_block
            worker_config = worker_use_ssh_config[i]

        return start_agent_node(node,
                                master_addr,
                                k3s_token,
                                worker_user,
                                worker_key,
                                worker_askpass,
                                use_ssh_config=worker_config)

    unsuccessful_workers = []

    # Deploy workers in parallel using thread pool
    force_update_status(
        f'Deploying SkyPilot runtime on worker nodes [{cluster_name}]')
    with cf.ThreadPoolExecutor() as executor:
        futures = []
        for i, node in enumerate(worker_nodes):
            args = (i, node, worker_hosts, history_workers_info, ssh_user,
                    ssh_key, askpass_block, worker_use_ssh_config, master_addr,
                    k3s_token)
            futures.append(executor.submit(deploy_worker, args))

        # Check if worker node has a GPU
        for future in cf.as_completed(futures):
            node, suc, has_gpu = future.result()
            install_gpu = install_gpu or has_gpu
            if not suc:
                unsuccessful_workers.append(node)

    # Step 3: Configure local kubectl to connect to the cluster
    force_update_status(f'Setting up SkyPilot configuration [{cluster_name}]')

    # Create temporary directory for kubeconfig operations
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_kubeconfig = os.path.join(temp_dir, 'kubeconfig')

        # Get the kubeconfig from remote server
        if head_use_ssh_config:
            scp_cmd = ['scp', head_node + ':~/.kube/config', temp_kubeconfig]
        else:
            scp_cmd = [
                'scp', '-o', 'StrictHostKeyChecking=no', '-o',
                'IdentitiesOnly=yes', '-i', ssh_key,
                f'{ssh_user}@{head_node}:~/.kube/config', temp_kubeconfig
            ]
        deploy_utils.run_command(scp_cmd, shell=False)

        # Create the directory for the kubeconfig file if it doesn't exist
        deploy_utils.ensure_directory_exists(kubeconfig_path)

        # Create empty kubeconfig if it doesn't exist
        if not os.path.isfile(kubeconfig_path):
            open(kubeconfig_path, 'a', encoding='utf-8').close()

        # Modify the temporary kubeconfig to update server address and context name
        modified_config = os.path.join(temp_dir, 'modified_config')
        with open(temp_kubeconfig, 'r', encoding='utf-8') as f_in:
            with open(modified_config, 'w', encoding='utf-8') as f_out:
                in_cluster = False
                in_user = False
                client_cert_data = None
                client_key_data = None

                for line in f_in:
                    if 'clusters:' in line:
                        in_cluster = True
                        in_user = False
                    elif 'users:' in line:
                        in_cluster = False
                        in_user = True
                    elif 'contexts:' in line:
                        in_cluster = False
                        in_user = False

                    # Skip certificate authority data in cluster section
                    if in_cluster and 'certificate-authority-data:' in line:
                        continue
                    # Skip client certificate data in user section but extract it
                    elif in_user and 'client-certificate-data:' in line:
                        client_cert_data = line.split(':', 1)[1].strip()
                        continue
                    # Skip client key data in user section but extract it
                    elif in_user and 'client-key-data:' in line:
                        client_key_data = line.split(':', 1)[1].strip()
                        continue
                    elif in_cluster and 'server:' in line:
                        # Initially just set to the effective master IP
                        # (will be changed to localhost by setup_kubectl_ssh_tunnel later)
                        f_out.write(
                            f'    server: https://{effective_master_ip}:6443\n')
                        f_out.write('    insecure-skip-tls-verify: true\n')
                        continue

                    # Replace default context names with user-provided context name
                    line = line.replace('name: default',
                                        f'name: {context_name}')
                    line = line.replace('cluster: default',
                                        f'cluster: {context_name}')
                    line = line.replace('user: default',
                                        f'user: {context_name}')
                    line = line.replace('current-context: default',
                                        f'current-context: {context_name}')

                    f_out.write(line)

                # Save certificate data if available

                if client_cert_data:
                    # Decode base64 data and save as PEM
                    try:
                        # Clean up the certificate data by removing whitespace
                        clean_cert_data = ''.join(client_cert_data.split())
                        cert_pem = base64.b64decode(clean_cert_data).decode(
                            'utf-8')

                        # Check if the data already looks like a PEM file
                        has_begin = '-----BEGIN CERTIFICATE-----' in cert_pem
                        has_end = '-----END CERTIFICATE-----' in cert_pem

                        if not has_begin or not has_end:
                            logger.debug(
                                'Warning: Certificate data missing PEM markers, attempting to fix...'
                            )
                            # Add PEM markers if missing
                            if not has_begin:
                                cert_pem = f'-----BEGIN CERTIFICATE-----\n{cert_pem}'
                            if not has_end:
                                cert_pem = f'{cert_pem}\n-----END CERTIFICATE-----'

                        # Write the certificate
                        with open(cert_file_path, 'w',
                                  encoding='utf-8') as cert_file:
                            cert_file.write(cert_pem)

                        # Verify the file was written correctly
                        if os.path.getsize(cert_file_path) > 0:
                            logger.debug(
                                f'Successfully saved certificate data ({len(cert_pem)} bytes)'
                            )

                            # Quick validation of PEM format
                            with open(cert_file_path, 'r',
                                      encoding='utf-8') as f:
                                content = f.readlines()
                                first_line = content[0].strip(
                                ) if content else ''
                                last_line = content[-1].strip(
                                ) if content else ''

                            if not first_line.startswith(
                                    '-----BEGIN') or not last_line.startswith(
                                        '-----END'):
                                logger.debug(
                                    'Warning: Certificate may not be in proper PEM format'
                                )
                        else:
                            logger.error(
                                f'{colorama.Fore.RED}Error: '
                                f'Certificate file is empty{RESET_ALL}')
                    except Exception as e:  # pylint: disable=broad-except
                        logger.error(f'{colorama.Fore.RED}'
                                     f'Error processing certificate data: {e}'
                                     f'{RESET_ALL}')

                if client_key_data:
                    # Decode base64 data and save as PEM
                    try:
                        # Clean up the key data by removing whitespace
                        clean_key_data = ''.join(client_key_data.split())
                        key_pem = base64.b64decode(clean_key_data).decode(
                            'utf-8')

                        # Check if the data already looks like a PEM file

                        # Check for EC key format
                        if 'EC PRIVATE KEY' in key_pem:
                            # Handle EC KEY format directly
                            match_ec = re.search(
                                r'-----BEGIN EC PRIVATE KEY-----(.*?)-----END EC PRIVATE KEY-----',
                                key_pem, re.DOTALL)
                            if match_ec:
                                # Extract and properly format EC key
                                key_content = match_ec.group(1).strip()
                                key_pem = f'-----BEGIN EC PRIVATE KEY-----\n{key_content}\n-----END EC PRIVATE KEY-----'
                            else:
                                # Extract content and assume EC format
                                key_content = re.sub(r'-----BEGIN.*?-----', '',
                                                     key_pem)
                                key_content = re.sub(r'-----END.*?-----.*', '',
                                                     key_content).strip()
                                key_pem = f'-----BEGIN EC PRIVATE KEY-----\n{key_content}\n-----END EC PRIVATE KEY-----'
                        else:
                            # Handle regular private key format
                            has_begin = any(marker in key_pem for marker in [
                                '-----BEGIN PRIVATE KEY-----',
                                '-----BEGIN RSA PRIVATE KEY-----'
                            ])
                            has_end = any(marker in key_pem for marker in [
                                '-----END PRIVATE KEY-----',
                                '-----END RSA PRIVATE KEY-----'
                            ])

                            if not has_begin or not has_end:
                                logger.debug(
                                    'Warning: Key data missing PEM markers, attempting to fix...'
                                )
                                # Add PEM markers if missing
                                if not has_begin:
                                    key_pem = f'-----BEGIN PRIVATE KEY-----\n{key_pem}'
                                if not has_end:
                                    key_pem = f'{key_pem}\n-----END PRIVATE KEY-----'
                                    # Remove any trailing characters after END marker
                                    key_pem = re.sub(
                                        r'(-----END PRIVATE KEY-----).*', r'\1',
                                        key_pem)

                        # Write the key
                        with open(key_file_path, 'w',
                                  encoding='utf-8') as key_file:
                            key_file.write(key_pem)

                        # Verify the file was written correctly
                        if os.path.getsize(key_file_path) > 0:
                            logger.debug(
                                f'Successfully saved key data ({len(key_pem)} bytes)'
                            )

                            # Quick validation of PEM format
                            with open(key_file_path, 'r',
                                      encoding='utf-8') as f:
                                content = f.readlines()
                                first_line = content[0].strip(
                                ) if content else ''
                                last_line = content[-1].strip(
                                ) if content else ''

                            if not first_line.startswith(
                                    '-----BEGIN') or not last_line.startswith(
                                        '-----END'):
                                logger.debug(
                                    'Warning: Key may not be in proper PEM format'
                                )
                        else:
                            logger.error(f'{colorama.Fore.RED}Error: '
                                         f'Key file is empty{RESET_ALL}')
                    except Exception as e:  # pylint: disable=broad-except
                        logger.error(f'{colorama.Fore.RED}'
                                     f'Error processing key data: {e}'
                                     f'{RESET_ALL}')

        # First check if context name exists and delete it if it does
        # TODO(romilb): Should we throw an error here instead?
        deploy_utils.run_command(
            ['kubectl', 'config', 'delete-context', context_name],
            shell=False,
            silent=True)
        deploy_utils.run_command(
            ['kubectl', 'config', 'delete-cluster', context_name],
            shell=False,
            silent=True)
        deploy_utils.run_command(
            ['kubectl', 'config', 'delete-user', context_name],
            shell=False,
            silent=True)

        # Merge the configurations using kubectl
        merged_config = os.path.join(temp_dir, 'merged_config')
        os.environ['KUBECONFIG'] = f'{kubeconfig_path}:{modified_config}'
        with open(merged_config, 'w', encoding='utf-8') as merged_file:
            kubectl_cmd = ['kubectl', 'config', 'view', '--flatten']
            result = deploy_utils.run_command(kubectl_cmd, shell=False)
            if result:
                merged_file.write(result)

        # Replace the kubeconfig with the merged config
        shutil.move(merged_config, kubeconfig_path)

        # Set the new context as the current context
        deploy_utils.run_command(
            ['kubectl', 'config', 'use-context', context_name],
            shell=False,
            silent=True)

    # Always set up SSH tunnel since we assume only port 22 is accessible
    tunnel_utils.setup_kubectl_ssh_tunnel(head_node,
                                          ssh_user,
                                          ssh_key,
                                          context_name,
                                          use_ssh_config=head_use_ssh_config)

    logger.debug(f'kubectl configured with new context \'{context_name}\'.')
    success_message(f'SkyPilot runtime is up [{cluster_name}].')

    # Install GPU operator if a GPU was detected on any node
    if install_gpu:
        force_update_status(f'Configuring NVIDIA GPUs [{cluster_name}]')
        cmd = f"""
            {askpass_block}
            curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 &&
            chmod 700 get_helm.sh &&
            ./get_helm.sh &&
            helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update &&
            kubectl create namespace gpu-operator --kubeconfig ~/.kube/config || true &&
            sudo -A ln -s /sbin/ldconfig /sbin/ldconfig.real || true &&
            helm install gpu-operator -n gpu-operator --create-namespace nvidia/gpu-operator \\
            --set 'toolkit.env[0].name=CONTAINERD_CONFIG' \\
            --set 'toolkit.env[0].value=/var/lib/rancher/k3s/agent/etc/containerd/config.toml' \\
            --set 'toolkit.env[1].name=CONTAINERD_SOCKET' \\
            --set 'toolkit.env[1].value=/run/k3s/containerd/containerd.sock' \\
            --set 'toolkit.env[2].name=CONTAINERD_RUNTIME_CLASS' \\
            --set 'toolkit.env[2].value=nvidia' &&
            echo 'Waiting for GPU operator installation...' &&
            while ! kubectl describe nodes --kubeconfig ~/.kube/config | grep -q 'nvidia.com/gpu:' || ! kubectl describe nodes --kubeconfig ~/.kube/config | grep -q 'nvidia.com/gpu.product'; do
                echo 'Waiting for GPU operator...'
                sleep 5
            done
            echo 'GPU operator installed successfully.'
        """
        result = deploy_utils.run_remote(head_node,
                                         cmd,
                                         ssh_user,
                                         ssh_key,
                                         use_ssh_config=head_use_ssh_config)
        if result is None:
            logger.error(f'{colorama.Fore.RED}Failed to install GPU Operator.'
                         f'{RESET_ALL}')
        else:
            success_message('GPU Operator installed.')
    else:
        logger.debug('No GPUs detected. Skipping GPU Operator installation.')

    # The env var KUBECONFIG ensures sky check uses the right kubeconfig
    os.environ['KUBECONFIG'] = kubeconfig_path
    deploy_utils.run_command(['sky', 'check', 'ssh'], shell=False)

    success_message('SkyPilot configured successfully.')

    if unsuccessful_workers:
        quoted_unsuccessful_workers = [
            f'"{worker}"' for worker in unsuccessful_workers
        ]

        logger.info(f'{colorama.Fore.YELLOW}'
                    'Failed to deploy Kubernetes on the following nodes: '
                    f'{", ".join(quoted_unsuccessful_workers)}. Please check '
                    f'the logs for more details.{RESET_ALL}')
    else:
        success_message(f'Node Pool `{cluster_name}` deployed successfully.')

    return unsuccessful_workers


def create_askpass_script(password):
    """Create an askpass script block for sudo with password."""
    if not password:
        return ''

    return f"""
# Create temporary askpass script
ASKPASS_SCRIPT=$(mktemp)
trap 'rm -f $ASKPASS_SCRIPT' EXIT INT TERM ERR QUIT
cat > $ASKPASS_SCRIPT << EOF
#!/bin/bash
echo {password}
EOF
chmod 700 $ASKPASS_SCRIPT
# Use askpass
export SUDO_ASKPASS=$ASKPASS_SCRIPT
"""


def cleanup_node(node,
                 user,
                 ssh_key,
                 askpass_block,
                 use_ssh_config=False,
                 is_worker=True):
    """Uninstall k3s and clean up the state on a node."""
    ntype = 'worker' if is_worker else 'head'
    force_update_status(f'Cleaning up {ntype} node ({node})...')
    script = f'k3s{"-agent" if is_worker else ""}-uninstall.sh'
    cmd = f"""
        {askpass_block}
        echo 'Uninstalling k3s...' &&
        sudo -A /usr/local/bin/{script} || true &&
        sudo -A rm -rf /etc/rancher /var/lib/rancher /var/lib/kubelet /etc/kubernetes ~/.kube
    """
    result = deploy_utils.run_remote(node,
                                     cmd,
                                     user,
                                     ssh_key,
                                     use_ssh_config=use_ssh_config)
    if result is None:
        logger.error(f'{colorama.Fore.RED}Failed to clean up {ntype} '
                     f'node ({node}).{RESET_ALL}')
    else:
        success_message(f'Node {node} cleaned up successfully.')


def start_agent_node(node,
                     master_addr,
                     k3s_token,
                     user,
                     ssh_key,
                     askpass_block,
                     use_ssh_config=False):
    """Start a k3s agent node.
    Returns: if the start is successful, and whether the node has a GPU."""
    logger.info(f'Deploying worker node ({node}).')
    cmd = f"""
            {askpass_block}
            curl -sfL https://get.k3s.io | K3S_NODE_NAME={node} INSTALL_K3S_EXEC='agent --node-label skypilot-ip={node}' \
                K3S_URL=https://{master_addr}:6443 K3S_TOKEN={k3s_token} sudo -E -A sh -
        """
    result = deploy_utils.run_remote(node,
                                     cmd,
                                     user,
                                     ssh_key,
                                     use_ssh_config=use_ssh_config)
    if result is None:
        logger.error(f'{colorama.Fore.RED}✗ Failed to deploy K3s on worker '
                     f'node ({node}).{RESET_ALL}')
        return node, False, False
    success_message(
        f'SkyPilot runtime successfully deployed on worker node ({node}).')
    # Check if worker node has a GPU
    if deploy_utils.check_gpu(node,
                              user,
                              ssh_key,
                              use_ssh_config=use_ssh_config):
        logger.info(f'{colorama.Fore.YELLOW}GPU detected on worker node '
                    f'({node}).{RESET_ALL}')
        return node, True, True
    return node, True, False
