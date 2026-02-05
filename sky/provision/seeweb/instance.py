"""Seeweb provisioner for SkyPilot / Ray autoscaler.

Prerequisites:
    pip install ecsapi
"""

import os
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
from sky.utils import auth_utils
from sky.utils import command_runner  # Unified SSH helper
from sky.utils import common_utils
from sky.utils import status_lib

logger = sky_logging.init_logger(__name__)

# Singleton Seeweb client reused across the module
_seeweb_client = None


def _get_seeweb_client():
    """Return a singleton Seeweb ECS API client."""
    global _seeweb_client
    if _seeweb_client is None:
        # Initialize via adaptor's cached client
        _seeweb_client = seeweb_adaptor.client()
    return _seeweb_client


# --------------------------------------------------------------------------- #
# Useful constants
# --------------------------------------------------------------------------- #
_POLL_INTERVAL = 5  # sec
_MAX_BOOT_TIME = 1200  # sec
_ACTION_WATCH_MAX_RETRY = 360  # number of polls before giving up
_ACTION_WATCH_FETCH_EVERY = 5  # seconds between polls
_API_RETRY_MAX_RETRIES = 5
_API_RETRY_INITIAL_BACKOFF = 1


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
        # Reuse a singleton Seeweb client to avoid repeated authentications/API
        # object creations across different provider instances.
        self.ecs = _get_seeweb_client()

    def _get_ssh_user(self) -> str:
        # Prefer auth config; fallback to template default for Seeweb
        return (self.config.authentication_config.get('ssh_user') if self.config
                and self.config.authentication_config else None) or 'ecuser'

    def _get_private_key_path(self) -> str:
        # Prefer explicit path from auth config; otherwise use SkyPilot key
        key_path = None
        if self.config and self.config.authentication_config:
            key_path = self.config.authentication_config.get('ssh_private_key')
        if not key_path:
            key_path, _ = auth_utils.get_or_generate_keys()
        return os.path.expanduser(key_path)

    # ------------------------------------------------------------------ #
    # Helper: run a command on the VM via SSH using CommandRunner
    # ------------------------------------------------------------------ #
    def _run_remote(self,
                    server_ip: str,
                    cmd: str,
                    *,
                    timeout: int = 30,
                    stream_logs: bool = False) -> subprocess.CompletedProcess:
        """Execute *cmd* on the remote host.

        Uses sky.utils.command_runner.SSHCommandRunner for consistent SSH
        options across all providers.
        Returns a subprocess.CompletedProcess-like
        object with returncode, stdout, stderr.
        """
        runner = command_runner.SSHCommandRunner(
            node=(server_ip, 22),
            ssh_user=self._get_ssh_user(),
            ssh_private_key=self._get_private_key_path(),
        )
        rc, stdout, stderr = runner.run(cmd,
                                        stream_logs=stream_logs,
                                        require_outputs=True,
                                        connect_timeout=timeout)
        # Convert to simple namespace for compatibility
        proc = subprocess.CompletedProcess(args=cmd,
                                           returncode=rc,
                                           stdout=stdout.encode(),
                                           stderr=stderr.encode())
        return proc

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

            # Retry deletion with exponential backoff
            # to handle transient API errors
            common_utils.retry(self.ecs.delete_server,
                               max_retries=5,
                               initial_backoff=1)(srv.name)

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
                            f'{srv.status}, skipping\n')
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

                    # SSH readiness handled by provisioner.wait_for_ssh()

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
            ssh_user = self._get_ssh_user()
            private_key_path = self._get_private_key_path()
            result = subprocess.run([
                'ssh', '-o', 'ConnectTimeout=10', '-o',
                'StrictHostKeyChecking=no', '-o',
                f'UserKnownHostsFile={os.devnull}', '-o',
                f'GlobalKnownHostsFile={os.devnull}', '-o',
                'IdentitiesOnly=yes', '-i', private_key_path,
                f'{ssh_user}@{server_ip}', 'echo "SSH ready"'
            ],
                                    capture_output=True,
                                    timeout=15,
                                    check=False)
            return result.returncode == 0
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Error checking SSH on {server_ip}: {e}')
            return False

    # ------------------------------------------------------------------ #
    # 7. open_ports / cleanup_ports – Seeweb has all ports open by default
    # ------------------------------------------------------------------ #
    def open_ports(
        self,
        cluster_name_on_cloud: str,
        ports: List[str],
        provider_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """See sky/provision/__init__.py"""
        logger.debug(f'Skip opening ports {ports} for Seeweb instances, as all '
                     'ports are open by default.')
        del cluster_name_on_cloud, provider_config, ports

    def cleanup_ports(
        self,
        cluster_name_on_cloud: str,
        ports: List[str],
        provider_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        del cluster_name_on_cloud, ports, provider_config  # Unused.

    # ======================  private helpers  ========================= #
    def _query_cluster_nodes(self):
        """List servers with notes == cluster_name."""
        servers = common_utils.retry(
            self.ecs.fetch_servers,
            max_retries=_API_RETRY_MAX_RETRIES,
            initial_backoff=_API_RETRY_INITIAL_BACKOFF)()
        return [
            s for s in servers
            if s.notes and s.notes.startswith(self.cluster_name)
        ]

    def query_cluster_nodes(self):
        """Public wrapper for querying cluster nodes for this cluster."""
        return common_utils.retry(self._query_cluster_nodes,
                                  max_retries=_API_RETRY_MAX_RETRIES,
                                  initial_backoff=_API_RETRY_INITIAL_BACKOFF)()

    def _get_head_instance_id(self) -> Optional[str]:
        """Return head instance id for this cluster.

        Prefer notes == "{cluster}-head"; fallback to first node if none
        matches (legacy naming).
        """
        nodes = common_utils.retry(self._query_cluster_nodes,
                                   max_retries=_API_RETRY_MAX_RETRIES,
                                   initial_backoff=_API_RETRY_INITIAL_BACKOFF)()
        for node in nodes:
            try:
                if getattr(node, 'notes', None) == f'{self.cluster_name}-head':
                    return node.name
                if getattr(node, 'name', None) and node.name.endswith('-head'):
                    return node.name
            except Exception:  # pylint: disable=broad-except
                continue
        return nodes[0].name if nodes else None

    def get_head_instance_id(self) -> Optional[str]:
        """Public wrapper for getting head instance id."""
        return common_utils.retry(self._get_head_instance_id,
                                  max_retries=_API_RETRY_MAX_RETRIES,
                                  initial_backoff=_API_RETRY_INITIAL_BACKOFF)()

    def _create_server(self):
        """POST /servers with complete payload."""
        node_type = 'head'
        payload = {
            'plan': self.config.node_config.get('plan'),  # e.g. eCS4
            'image': self.config.node_config.get('image'),  # e.g. ubuntu-2204
            'location': self.config.node_config.get('location'),  # e.g. it-mi2
            'notes': f'{self.cluster_name}-{node_type}',
            'ssh_key': self.config.authentication_config.get('remote_key_name'
                                                            ),  # remote key
        }

        # Optional GPU
        if 'gpu' in self.config.node_config:
            payload.update({
                'gpu': self.config.node_config.get('gpu'),
                'gpu_label': self.config.node_config.get('gpu_label', ''),
            })

        # Add user_customize if present (Seeweb Cloud Script)
        if 'user_customize' in self.config.node_config:
            payload['user_customize'] = self.config.node_config[
                'user_customize']

        # Build the request object expected by ecsapi
        server_create_request_cls = (
            seeweb_adaptor.ecsapi.ServerCreateRequest  # type: ignore
        )
        create_request = server_create_request_cls(**payload)

        logger.info('Creating Seeweb server %s', payload)

        # POST /servers – returns (response, action_id)
        _, action_id = common_utils.retry(
            self.ecs.create_server,
            max_retries=_API_RETRY_MAX_RETRIES,
            initial_backoff=_API_RETRY_INITIAL_BACKOFF)(
                create_request, check_if_can_create=False)
        self.ecs.watch_action(action_id,
                              max_retry=_ACTION_WATCH_MAX_RETRY,
                              fetch_every=_ACTION_WATCH_FETCH_EVERY)

    def _power_on(self, server_id: str):
        try:
            common_utils.retry(
                self.ecs.turn_on_server,
                max_retries=_API_RETRY_MAX_RETRIES,
                initial_backoff=_API_RETRY_INITIAL_BACKOFF)(server_id)
        except seeweb_adaptor.SeewebError as e:
            logger.error(f'Error in _power_on for {server_id}: {e}')
            raise

    def _power_off(self, server_id: str):
        try:
            common_utils.retry(
                self.ecs.turn_off_server,
                max_retries=_API_RETRY_MAX_RETRIES,
                initial_backoff=_API_RETRY_INITIAL_BACKOFF)(server_id)
        except seeweb_adaptor.SeewebError as e:
            logger.error(f'\n\nError in _power_off for {server_id}: {e}')
            raise

    def _wait_action(self, action_id: int):
        """Poll action until it completes."""
        while True:
            action = common_utils.retry(
                self.ecs.fetch_action,
                max_retries=_API_RETRY_MAX_RETRIES,
                initial_backoff=_API_RETRY_INITIAL_BACKOFF)(action_id)
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
            cluster_nodes = common_utils.retry(
                self._query_cluster_nodes,
                max_retries=_API_RETRY_MAX_RETRIES,
                initial_backoff=_API_RETRY_INITIAL_BACKOFF)()

            all_stopped = True
            for server in cluster_nodes:
                try:
                    # Always use fetch_server_status() for accurate status
                    specific_status = common_utils.retry(
                        self.ecs.fetch_server_status,
                        max_retries=_API_RETRY_MAX_RETRIES,
                        initial_backoff=_API_RETRY_INITIAL_BACKOFF)(server.name)

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
# =============================================================================


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: ProvisionConfig) -> ProvisionRecord:
    """Run instances for Seeweb cluster."""
    del cluster_name  # unused
    provider = SeewebNodeProvider(config, cluster_name_on_cloud)
    provider.run_instances(config.node_config, config.count)

    # Find the head node using notes convention
    cluster_nodes = provider.query_cluster_nodes()
    if not cluster_nodes:
        raise RuntimeError(
            f'No nodes found for cluster {cluster_name_on_cloud}')
    head_node_id = provider.get_head_instance_id()
    assert head_node_id is not None, 'head_instance_id should not be None'

    return ProvisionRecord(
        provider_name='Seeweb',
        region=region,
        zone=None,  # Seeweb doesn't use zones
        cluster_name=cluster_name_on_cloud,
        head_instance_id=head_node_id,
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
    client = _get_seeweb_client()
    deadline = time.time() + _MAX_BOOT_TIME
    while time.time() < deadline:
        cluster_nodes = [
            s for s in client.fetch_servers()
            if s.notes and s.notes.startswith(cluster_name_on_cloud)
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

                # Do not check SSH here; handled by provisioner.wait_for_ssh().

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
        logger.error(f'Error pinging {server_ip}: {e}')
        return False


def _check_ssh_ready_standalone(server_ip: str) -> bool:
    """Check that SSH is available on the server (standalone version)."""
    try:
        private_key_path, _ = auth_utils.get_or_generate_keys()
        private_key_path = os.path.expanduser(private_key_path)
        ssh_user = 'ecuser'
        result = subprocess.run([
            'ssh', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
            '-o', f'UserKnownHostsFile={os.devnull}', '-o',
            f'GlobalKnownHostsFile={os.devnull}', '-o', 'IdentitiesOnly=yes',
            '-i', private_key_path, f'{ssh_user}@{server_ip}',
            'echo "SSH ready"'
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
    client = _get_seeweb_client()
    cluster_nodes = [
        s for s in client.fetch_servers()
        if s.notes and s.notes.startswith(cluster_name_on_cloud)
    ]

    if not cluster_nodes:
        raise RuntimeError(
            f'No instances found for cluster {cluster_name_on_cloud}')

    instances = {}
    head_instance = None
    for node in cluster_nodes:
        if getattr(node, 'notes', None) == f'{cluster_name_on_cloud}-head':
            head_instance = node.name
            break
    if head_instance is None:
        head_instance = cluster_nodes[0].name

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
    del provider_config  # Unused
    logger.debug(f'Seeweb: skipping open_ports for {cluster_name_on_cloud}'
                 f'ports={ports} all ports are open by default')
    return


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    del cluster_name_on_cloud, ports, provider_config  # Unused.
    return
