"""Seeweb provisioner for SkyPilot / Ray autoscaler.

Prerequisites:
    pip install ecsapi
"""

import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from sky import sky_logging
from sky.adaptors import seeweb as seeweb_adaptor
from sky.provision import common
from sky.provision.common import ClusterInfo
from sky.provision.common import InstanceInfo
from sky.provision.common import ProvisionConfig
from sky.provision.common import ProvisionRecord
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

# --------------------------------------------------------------------------- #
# Useful constants
# --------------------------------------------------------------------------- #
_POLL_INTERVAL = 5  # sec
_MAX_BOOT_TIME = 1200  # sec
_NOTE_KEY = 'skypilot_cluster'  # we save cluster_name in .notes for filtering


# --------------------------------------------------------------------------- #
#  Class required by the Ray backend
# --------------------------------------------------------------------------- #
class SeewebNodeProvider:
    """Minimalist provisioner for Seeweb ECS."""

    def __init__(self, provider_config: ProvisionConfig, cluster_name: str):
        """provider_config: dict populated by template (plan, image, location,
                         remote_key_name, optional gpu…)
        cluster_name   : SkyPilot name on cloud (used in notes)
        """
        self.config = provider_config
        self.cluster_name = cluster_name
        self.ecs = seeweb_adaptor.client()

    # --------------------------------------------------------------------- #
    # 1. bootstrap_instances – no preprocessing needed here
    # --------------------------------------------------------------------- #

    # --------------------------------------------------------------------- #
    # 2. run_instances: restart or create until we reach count
    # --------------------------------------------------------------------- #
    def run_instances(self, config: Dict, count: int) -> None:
        existing = self._query_cluster_nodes()
        del config  # unused
        running = [
            s for s in existing if s.status in ('Booted', 'Running', 'RUNNING',
                                                'Booting', 'PoweringOn')
        ]

        # a) restart Off servers
        for srv in (s for s in existing if s.status == 'Booted'):
            specific_status = self.ecs.fetch_server_status(srv.name)
            if specific_status == 'SHUTOFF':
                logger.info(f'Powering on server {srv.name}')
                self._power_on(srv.name)
                running.append(srv)
            if len(running) >= count:
                break

        # b) create new VMs if missing
        while len(running) < count:
            self._create_server()
            running.append({})  # placeholder

    # --------------------------------------------------------------------- #
    # 3. terminate_instances
    # --------------------------------------------------------------------- #
    def terminate_instances(self) -> None:
        for srv in self._query_cluster_nodes():
            logger.info('Deleting server %s …', srv.name)
            self.ecs.delete_server(srv.name)  # DELETE /servers/{name}

    # --------------------------------------------------------------------- #
    # 4. stop_instances
    # --------------------------------------------------------------------- #
    def stop_instances(self) -> None:
        cluster_nodes = self._query_cluster_nodes()

        for srv in cluster_nodes:
            specific_status = self.ecs.fetch_server_status(srv.name)

            if specific_status == 'SHUTOFF':
                logger.info(f'\nServer {srv.name} is already stopped\n')
                continue
            elif srv.status in ('Booted', 'Running', 'RUNNING'):
                # Get specific status to check if server is not already SHUTOFF
                try:
                    specific_status = self.ecs.fetch_server_status(srv.name)
                    # Continue with power off only if
                    # specific_status is not SHUTOFF
                    # and general status is not STOPPED
                    if specific_status != 'SHUTOFF' and srv.status != 'STOPPED':
                        self._power_off(srv.name)
                except Exception:  # pylint: disable=broad-except
                    # Fallback: if we can't get specific
                    # status, use general status check
                    if srv.status != 'STOPPED':
                        self._power_off(srv.name)
            else:
                logger.info(f'\nServer {srv.name} has status'
                            f"'{srv.status}', skipping\n")
        # Wait for all servers to be actually stopped with forced refresh
        self._wait_for_stop_with_forced_refresh()

    # --------------------------------------------------------------------- #
    # 5. query_instances
    # --------------------------------------------------------------------- #
    def query_instances(self) -> Dict[str, str]:
        """Query instances status using both fetch_servers()
        and fetch_server_status().

        Seeweb has two different APIs:
        - fetch_servers() returns states like 'Booted', 'Booting'
        - fetch_server_status() returns states like 'SHUTOFF' (stopped)

        We need to use fetch_server_status() to get the correct stopped state.
        """
        instances = {}
        cluster_nodes = self._query_cluster_nodes()

        for server in cluster_nodes:
            # Always try to get the specific status first for more accuracy
            try:
                specific_status = self.ecs.fetch_server_status(server.name)
                instances[server.name] = specific_status
            except Exception:  # pylint: disable=broad-except
                # Fallback to general status if fetch_server_status fails
                general_status = server.status
                instances[server.name] = general_status

        return instances

    # --------------------------------------------------------------------- #
    # 6. wait_instances
    # --------------------------------------------------------------------- #
    def wait_instances(self, desired_state: str = 'Booted') -> None:
        deadline = time.time() + _MAX_BOOT_TIME

        while time.time() < deadline:
            cluster_nodes = self._query_cluster_nodes()

            # For SHUTOFF state, we need to use fetch_server_status()
            # to get the real status
            if desired_state == 'SHUTOFF':
                all_shutoff = True
                for server in cluster_nodes:
                    try:
                        specific_status = self.ecs.fetch_server_status(
                            server.name)
                        if specific_status != 'SHUTOFF':
                            all_shutoff = False
                    except Exception:  # pylint: disable=broad-except
                        all_shutoff = False

                if all_shutoff:
                    return
            else:
                # For other states, use the general status
                states = {srv.status for srv in cluster_nodes}

                if states <= {desired_state}:
                    # If all servers are Booted, wait
                    # for them to be truly stable
                    if desired_state == 'Booted':
                        if self._wait_for_all_servers_stable():
                            return
                        else:
                            time.sleep(_POLL_INTERVAL)
                            continue
                    return

            time.sleep(_POLL_INTERVAL)

        raise TimeoutError(
            f'Nodes are not all in state {desired_state} within timeout')

    def _wait_for_all_servers_stable(self, max_wait: int = 600) -> bool:
        """Waits for all cluster servers to be stable."""
        logger.info('Checking stability of all cluster servers...')

        start_time = time.time()
        while time.time() - start_time < max_wait:
            cluster_nodes = self._query_cluster_nodes()
            all_stable = True

            for node in cluster_nodes:
                if node.status == 'Booted':
                    # Check that server is reachable via ping
                    if not self._ping_server(node.ipv4):
                        logger.warning(f'Server {node.name} ({node.ipv4})'
                                       f'not reachable via ping')
                        all_stable = False
                        break

                    # Check that SSH is available
                    if not self._check_ssh_ready(node.ipv4):
                        logger.warning(
                            f'SSH not available on {node.name} ({node.ipv4})')
                        all_stable = False
                        break

                    logger.info(f'Server {node.name} ({node.ipv4}) is stable')

            if all_stable:
                logger.info('All servers are stable')
                # Safety sleep to allow for late reboots
                logger.info('Waiting 1 second to allow for late reboots...')
                time.sleep(1)
                return True

            logger.info('Waiting for all servers to be stable...')
            time.sleep(1)

        logger.error('Timeout waiting for server stability')
        return False

    def _ping_server(self, server_ip: str) -> bool:
        """Check that server is reachable via ping."""
        try:
            result = subprocess.run(['ping', '-c', '1', '-W', '5', server_ip],
                                    capture_output=True,
                                    timeout=10,
                                    check=False)
            return result.returncode == 0
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Error pinging {server_ip}: {e}')
            return False

    def _check_ssh_ready(self, server_ip: str) -> bool:
        """Check that SSH is available on the server."""
        try:
            result = subprocess.run([
                'ssh', '-o', 'ConnectTimeout=10', '-o',
                'StrictHostKeyChecking=no', '-i',
                '/root/.sky/clients/8f6f0399/ssh/sky-key',
                'ecuser@' + server_ip, 'echo "SSH ready"'
            ],
                                    capture_output=True,
                                    timeout=15,
                                    check=False)
            return result.returncode == 0
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Error checking SSH on {server_ip}: {e}')
            return False

    # ------------------------------------------------------------------ #
    # 7. open_ports / cleanup_ports – Seeweb doesn't have security groups
    # ------------------------------------------------------------------ #
    def open_ports(self, ports: List[int]):  # pylint: disable=unused-argument
        """Open ports using iptables on the server.

        Since Seeweb doesn't have security groups/firewall
        rules at the cloud level, we manage ports using
        iptables on the server itself.
        """
        if not ports:
            return

        # Get server IP to run iptables commands
        cluster_nodes = self._query_cluster_nodes()
        if not cluster_nodes:
            logger.warning('No cluster nodes found for port management')
            return

        for server in cluster_nodes:
            try:
                # Open ports using iptables on the server
                self._open_ports_on_server(server.ipv4, ports)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(
                    f'Failed to open ports on server {server.name}: {e}')

    def cleanup_ports(self):

        # Get server IP to run iptables commands
        cluster_nodes = self._query_cluster_nodes()
        if not cluster_nodes:
            return

        for server in cluster_nodes:
            try:
                # Cleanup ports using iptables on the server
                self._cleanup_ports_on_server(server.ipv4)
            except Exception as e:  # pylint: disable=broad-except
                logger.error(
                    f'Failed to cleanup ports on server {server.name}: {e}')

    def _open_ports_on_server(self, server_ip: str, ports: List[int]):
        """Open ports on a specific server using iptables via SSH."""
        try:

            # Convert ports to list of strings (not needed currently)
            # port_list = [str(port) for port in ports]

            # Check which ports are already open
            already_open = self._check_ports_status(server_ip, ports)
            ports_to_open = [port for port in ports if port not in already_open]

            if not ports_to_open:
                return

            # Create iptables rules to open ports
            iptables_cmds = []
            for port in ports_to_open:
                # Check if rule already exists to avoid duplicates
                iptables_cmds.append(
                    f'iptables -C INPUT -p tcp --dport {port} -j ACCEPT'
                    '2>/dev/null || '
                    f'iptables -A INPUT -p tcp --dport {port} -j ACCEPT')
                iptables_cmds.append(
                    f'iptables -C OUTPUT -p tcp --sport {port} -j ACCEPT'
                    '2>/dev/null || '
                    f'iptables -A OUTPUT -p tcp --sport {port} -j ACCEPT')

            # Join commands with semicolons
            iptables_cmd = '; '.join(iptables_cmds)

            # Execute iptables commands via SSH
            cmd = f'sudo {iptables_cmd}'
            result = subprocess.run([
                'ssh', '-o', 'ConnectTimeout=10', '-o',
                'StrictHostKeyChecking=no', '-i',
                '/root/.sky/clients/8f6f0399/ssh/sky-key',
                'ecuser@' + server_ip, cmd
            ],
                                    capture_output=True,
                                    timeout=30,
                                    check=False)

            if result.returncode != 0:
                logger.error(f'Failed to open ports on server {server_ip}:'
                             f'{result.stderr.decode()}')

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error opening ports on server {server_ip}: {e}')

    def _check_ports_status(self, server_ip: str,
                            ports: List[int]) -> List[int]:
        """Check which ports are already open on the server."""
        try:

            already_open = []
            for port in ports:
                # Check if INPUT rule exists for this port
                check_cmd = (
                    f'sudo iptables -C INPUT -p tcp --dport {port} -j ACCEPT')
                result = subprocess.run([
                    'ssh', '-o', 'ConnectTimeout=10', '-o',
                    'StrictHostKeyChecking=no', '-i',
                    '/root/.sky/clients/8f6f0399/ssh/sky-key',
                    'ecuser@' + server_ip, check_cmd
                ],
                                        capture_output=True,
                                        timeout=15,
                                        check=False)

                if result.returncode == 0:
                    already_open.append(port)

            return already_open

        except Exception as e:  # pylint: disable=broad-except
            logger.debug(
                f'Error checking port status on server {server_ip}: {e}')
            return []

    def _cleanup_ports_on_server(self, server_ip: str):
        """Cleanup iptables rules for opened ports."""
        try:

            # More sophisticated cleanup: remove only the rules we added
            # First, save current iptables rules (not used currently)
            # save_cmd = 'sudo iptables-save > /tmp/iptables_backup'

            # Remove custom rules (this is a conservative approach)
            # We'll remove rules that match our pattern but keep essential ones
            cleanup_cmd = """
            # Remove custom INPUT rules for specific ports (keep SSH port 22)
            sudo iptables -D INPUT -p tcp --dport 22 -j ACCEPT 2>/dev/null || true
            sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
            
            # Remove all other custom INPUT rules
            sudo iptables -D INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
            sudo iptables -D INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
            
            # Remove custom OUTPUT rules for specific ports
            sudo iptables -D OUTPUT -p tcp --sport 80 -j ACCEPT 2>/dev/null || true
            sudo iptables -D OUTPUT -p tcp --sport 443 -j ACCEPT 2>/dev/null || true
            
            # Add default policies back
            sudo iptables -P INPUT ACCEPT
            sudo iptables -P OUTPUT ACCEPT
            sudo iptables -P FORWARD ACCEPT
            """

            # Execute cleanup commands via SSH
            result = subprocess.run([
                'ssh', '-o', 'ConnectTimeout=10', '-o',
                'StrictHostKeyChecking=no', '-i',
                '/root/.sky/clients/8f6f0399/ssh/sky-key',
                'ecuser@' + server_ip, cleanup_cmd
            ],
                                    capture_output=True,
                                    timeout=60,
                                    check=False)

            if result.returncode == 0:
                logger.info(
                    f'Successfully cleaned up ports on server {server_ip}')
            else:
                logger.warning(f'Failed to cleanup ports on server {server_ip}:'
                               f'{result.stderr.decode()}')

        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error cleaning up ports on server {server_ip}: {e}')

    # ======================  private helpers  ========================= #
    def _query_cluster_nodes(self):
        """List servers with notes == cluster_name."""
        return [
            s for s in self.ecs.fetch_servers() if s.notes == self.cluster_name
        ]

    def _create_server(self):
        """POST /servers with complete payload."""
        payload = {
            'plan': self.config.node_config.get('plan'),  # e.g. eCS4
            'image': self.config.node_config.get('image'),  # e.g. ubuntu-2204
            'location': self.config.node_config.get('location'),  # e.g. it-mi2
            'notes': self.cluster_name,
            'ssh_key': self.config.authentication_config.get('remote_key_name'
                                                            ),  # remote key
        }

        # Optional GPU
        if 'gpu' in self.config.node_config:
            payload.update({
                'gpu': self.config.node_config.get('gpu'),
                'gpu_label': self.config.node_config.get('gpu_label', ''),
            })

        # Build the request object expected by ecsapi
        server_create_request_cls = (
            seeweb_adaptor.ecsapi.ServerCreateRequest  # type: ignore
        )
        create_request = server_create_request_cls(**payload)

        logger.info('Creating Seeweb server %s', payload)

        # POST /servers – returns (response, action_id)
        _, action_id = self.ecs.create_server(create_request,
                                              check_if_can_create=False)
        self.ecs.watch_action(action_id, max_retry=360, fetch_every=5)

    def _power_on(self, server_id: str):
        try:
            self.ecs.turn_on_server(server_id)
        except Exception as e:
            logger.error(f'Error in _power_on for {server_id}: {e}')
            raise

    def _power_off(self, server_id: str):
        try:
            self.ecs.turn_off_server(server_id)
        except Exception as e:
            logger.error(f'\n\nError in _power_off for {server_id}: {e}')
            raise

    def _wait_action(self, action_id: int):
        """Poll action until it completes."""
        while True:
            action = self.ecs.fetch_action(action_id)
            if action['status'] in ('completed', 'ok', 'no_content'):
                return
            if action['status'] == 'error':
                raise RuntimeError(f'Seeweb action {action_id} failed')
            time.sleep(_POLL_INTERVAL)

    def _wait_for_stop_with_forced_refresh(self, max_wait: int = 300) -> None:
        """Wait for servers to be stopped with
        aggressive polling and forced refresh."""
        start_time = time.time()
        poll_interval = 1  # 1 second for aggressive polling

        while time.time() - start_time < max_wait:
            # Force refresh by re-fetching cluster nodes
            cluster_nodes = self._query_cluster_nodes()

            all_stopped = True
            for server in cluster_nodes:
                try:
                    # Always use fetch_server_status() for accurate status
                    specific_status = self.ecs.fetch_server_status(server.name)

                    if specific_status != 'SHUTOFF':
                        all_stopped = False

                except Exception:  # pylint: disable=broad-except
                    all_stopped = False

            if all_stopped:
                return

            time.sleep(poll_interval)

        raise TimeoutError(f'Servers not stopped within {max_wait} seconds')


# =============================================================================
# Standalone functions required by the provisioning interface
# Following Lambda Cloud pattern
# =============================================================================


def run_instances(region: str, cluster_name_on_cloud: str,
                  config: ProvisionConfig) -> ProvisionRecord:
    """Run instances for Seeweb cluster."""
    provider = SeewebNodeProvider(config, cluster_name_on_cloud)
    provider.run_instances(config.node_config, config.count)

    # Find the head node (for now we take the first server of the cluster)
    cluster_nodes = getattr(provider, '_query_cluster_nodes')()
    if not cluster_nodes:
        raise RuntimeError(
            f'No nodes found for cluster {cluster_name_on_cloud}')

    head_node = cluster_nodes[0]

    return ProvisionRecord(
        provider_name='Seeweb',
        region=region,
        zone=None,  # Seeweb doesn't use zones
        cluster_name=cluster_name_on_cloud,
        head_instance_id=head_node.name,
        resumed_instance_ids=[],  # Empty for now
        created_instance_ids=[node.name for node in cluster_nodes],
    )


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Stop instances for Seeweb cluster."""
    del worker_only  # unused - Seeweb doesn't distinguish between head/worker
    assert provider_config is not None

    # Convert Dict to ProvisionConfig for SeewebNodeProvider
    config = common.ProvisionConfig(
        provider_config=provider_config,
        authentication_config={},
        docker_config={},
        node_config=provider_config,
        count=1,  # Not used for stop operation
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )
    provider = SeewebNodeProvider(config, cluster_name_on_cloud)
    provider.stop_instances()


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """Terminate instances for Seeweb cluster."""
    del worker_only  # unused - Seeweb doesn't distinguish between head/worker
    assert provider_config is not None
    # Convert Dict to ProvisionConfig for SeewebNodeProvider
    config = common.ProvisionConfig(
        provider_config=provider_config,
        authentication_config={},
        docker_config={},
        node_config=provider_config,
        count=1,  # Not used for terminate operation
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )
    provider = SeewebNodeProvider(config, cluster_name_on_cloud)
    provider.terminate_instances()


def wait_instances(
    region: str,
    cluster_name_on_cloud: str,
    state: Optional[status_lib.ClusterStatus],
) -> None:
    del region  # unused
    # Map ClusterStatus to Seeweb string
    if state == status_lib.ClusterStatus.UP:
        seeweb_state = 'Booted'
    elif state == status_lib.ClusterStatus.STOPPED:
        seeweb_state = 'SHUTOFF'
    elif state is None:
        seeweb_state = 'Terminated'  # For termination
    else:
        seeweb_state = 'Booted'  # Default fallback

    # Create Seeweb client directly and wait
    client = seeweb_adaptor.client()
    deadline = time.time() + _MAX_BOOT_TIME
    while time.time() < deadline:
        cluster_nodes = [
            s for s in client.fetch_servers()
            if s.notes == cluster_name_on_cloud
        ]
        if not cluster_nodes:
            time.sleep(_POLL_INTERVAL)
            continue

        states = {srv.status for srv in cluster_nodes}
        if states <= {seeweb_state}:
            # If all servers are Booted, wait for them to be truly stable
            if seeweb_state == 'Booted':
                if _wait_for_all_servers_stable_standalone(cluster_nodes):
                    return
                else:
                    time.sleep(_POLL_INTERVAL)
                    continue
            return
        time.sleep(_POLL_INTERVAL)

    raise TimeoutError(
        f'Nodes are not all in state {seeweb_state} within timeout')


def _wait_for_all_servers_stable_standalone(cluster_nodes,
                                            max_wait: int = 300) -> bool:
    """Waits for all cluster servers to be stable (standalone version)."""
    start_time = time.time()
    while time.time() - start_time < max_wait:
        all_stable = True

        for node in cluster_nodes:
            if node.status == 'Booted':
                # Check that server is reachable via ping
                if not _ping_server_standalone(node.ipv4):
                    all_stable = False
                    break

                # Check that SSH is available
                if not _check_ssh_ready_standalone(node.ipv4):
                    all_stable = False
                    break

        if all_stable:
            # Safety sleep to allow for late reboots
            time.sleep(1)
            return True

        time.sleep(1)

    return False


def _ping_server_standalone(server_ip: str) -> bool:
    """Check that server is reachable via ping (standalone version)."""
    try:
        result = subprocess.run(['ping', '-c', '1', '-W', '5', server_ip],
                                capture_output=True,
                                timeout=10,
                                check=False)
        return result.returncode == 0
    except Exception as e:  # pylint: disable=broad-except
        print(f'Error pinging {server_ip}: {e}')
        return False


def _check_ssh_ready_standalone(server_ip: str) -> bool:
    """Check that SSH is available on the server (standalone version)."""
    try:
        result = subprocess.run([
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            '-i', '/root/.sky/clients/8f6f0399/ssh/sky-key',
            'ecuser@' + server_ip, 'echo "SSH ready"'
        ],
                                capture_output=True,
                                timeout=15,
                                check=False)
        return result.returncode == 0
    except Exception:  # pylint: disable=broad-except
        return False


def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
) -> Dict[str, Tuple[Optional['status_lib.ClusterStatus'], Optional[str]]]:
    """Query instances status for Seeweb cluster."""
    del cluster_name  # unused
    # Use the provided provider_config or default to empty dict
    if provider_config is None:
        provider_config = {}

    # Convert Dict to ProvisionConfig for SeewebNodeProvider
    config = common.ProvisionConfig(
        provider_config=provider_config,
        authentication_config={},
        docker_config={},
        node_config=provider_config,
        count=1,  # Not used for query operation
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )
    provider = SeewebNodeProvider(config, cluster_name_on_cloud)
    seeweb_instances = provider.query_instances()

    # Map Seeweb status to SkyPilot status
    status_map = {
        'Booted':
            status_lib.ClusterStatus.UP,  # Seeweb uses "Booted" for running
        'Running': status_lib.ClusterStatus.UP,  # Alternative running state
        'RUNNING': status_lib.ClusterStatus.UP,  # All caps version
        'Booting': status_lib.ClusterStatus.INIT,
        'PoweringOn': status_lib.ClusterStatus.INIT,
        'Off': status_lib.ClusterStatus.STOPPED,
        'Stopped': status_lib.ClusterStatus.STOPPED,
        'SHUTOFF':
            status_lib.ClusterStatus.STOPPED,  # Add missing SHUTOFF status
        'PoweringOff': status_lib.ClusterStatus.
                       STOPPED,  # Fixed: should be STOPPED, not INIT
    }

    result: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                            Optional[str]]] = {}
    for name, seeweb_status in seeweb_instances.items():
        if non_terminated_only and seeweb_status in ('Terminated', 'Deleted'):
            continue
        mapped_status = status_map.get(seeweb_status,
                                       status_lib.ClusterStatus.INIT)
        # Return tuple of (status, reason) where reason is None for Seeweb
        result[name] = (mapped_status, None)

    return result


# Signature should not include provider_name; router strips it before calling
def get_cluster_info(
    region: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
) -> 'ClusterInfo':
    del region  # unused
    # Use Seeweb client to get cluster instances
    client = seeweb_adaptor.client()
    cluster_nodes = [
        s for s in client.fetch_servers() if s.notes == cluster_name_on_cloud
    ]

    if not cluster_nodes:
        raise RuntimeError(
            f'No instances found for cluster {cluster_name_on_cloud}')

    instances = {}
    head_instance = None

    for node in cluster_nodes:
        # For Seeweb, we take the first node as head
        if head_instance is None:
            head_instance = node.name

        # Get server IP (Seeweb uses 'ipv4' attribute)
        external_ip = node.ipv4
        internal_ip = external_ip  # For Seeweb, internal IP = external IP

        instances[node.name] = [
            InstanceInfo(
                instance_id=node.name,
                internal_ip=internal_ip,
                external_ip=external_ip,
                ssh_port=22,
                tags={},
            )
        ]

    return ClusterInfo(
        instances=instances,
        head_instance_id=head_instance,
        provider_name='Seeweb',
        provider_config=provider_config,
    )


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:

    # Convert Dict to ProvisionConfig for SeewebNodeProvider
    # use module-level import of common
    config = common.ProvisionConfig(
        provider_config=provider_config or {},
        authentication_config={},
        docker_config={},
        node_config=provider_config or {},
        count=1,  # Not used for port operation
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )

    try:
        provider = SeewebNodeProvider(config, cluster_name_on_cloud)
        # Convert port strings to integers for the provider
        port_ints = [int(port) for port in ports]
        provider.open_ports(port_ints)
    except Exception as e:
        logger.error(f'Failed to open ports {ports} for cluster'
                     f'{cluster_name_on_cloud}: {e}')
        raise


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Cleanup ports for Seeweb cluster.

    Since Seeweb doesn't support security groups/firewall
    rules at the cloud level,
    we manage ports using iptables on the server itself.
    """

    # Convert Dict to ProvisionConfig for SeewebNodeProvider
    # use module-level import of common
    del ports  # unused
    config = common.ProvisionConfig(
        provider_config=provider_config or {},
        authentication_config={},
        docker_config={},
        node_config=provider_config or {},
        count=1,  # Not used for port operation
        tags={},
        resume_stopped_nodes=False,
        ports_to_open_on_launch=None,
    )

    try:
        provider = SeewebNodeProvider(config, cluster_name_on_cloud)
        provider.cleanup_ports()
    except Exception as e:
        logger.error(
            f'Failed to cleanup ports for cluster {cluster_name_on_cloud}: {e}')
        raise
