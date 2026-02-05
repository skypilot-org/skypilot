"""Slurm adaptor for SkyPilot."""

import ipaddress
import logging
import re
import time
from typing import Dict, List, NamedTuple, Optional, Tuple

from sky.adaptors import common
from sky.utils import command_runner
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = logging.getLogger(__name__)

# ASCII Unit Separator (\x1f) to handle values with spaces
# and other special characters.
SEP = r'\x1f'

# Regex pattern to extract partition names from scontrol output
# Matches PartitionName=<name> and captures until the next field
_PARTITION_NAME_REGEX = re.compile(r'PartitionName=(.+?)(?:\s+\w+=|$)')

# Regex pattern to extract MAXTIME from scontrol output
# Matches MaxTime=<time> and captures the time
_MAXTIME_REGEX = re.compile(r'MaxTime=((?:\d+-)?\d{1,2}:\d{2}:\d{2}|UNLIMITED)')

# Default timeout for waiting for job nodes to be allocated, in seconds.
_SLURM_DEFAULT_PROVISION_TIMEOUT = 10

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Slurm. '
                         'Try running: pip install "skypilot[slurm]"')
hostlist = common.LazyImport('hostlist',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_UNRESOLVED_HOSTNAME_MARKER = 'UNRESOLVED'


class SlurmPartition(NamedTuple):
    """Information about the Slurm partitions."""
    name: str
    is_default: bool
    # The maximum time a job can run in seconds.
    # None if the maximum time is unlimited.
    maxtime: Optional[int]


# TODO(kevin): Add more API types for other client functions.
class NodeInfo(NamedTuple):
    """Information about a Slurm node from sinfo."""
    node: str
    state: str
    gres: str
    cpus: int
    memory_gb: float
    # The default partition contains a '*' at the end of the name.
    # It is the caller's responsibility to strip the '*' if needed.
    partition: str


def _parse_maxtime(line: str) -> Optional[int]:
    """Parse the maximum time a job can run from the scontrol output."""
    maxtime_match = _MAXTIME_REGEX.search(line)
    if not maxtime_match:
        return None
    maxtime_str = maxtime_match.group(1).strip()
    if maxtime_str == 'UNLIMITED':
        return None

    # Convert maxTime from '[days-]hours:minutes:seconds' to seconds.
    # Example: "2-12:30:05" => (2*86400) + (12*3600) + (30*60) + 5
    days = 0
    time_part = maxtime_str
    if '-' in maxtime_str:
        days_part, time_part = maxtime_str.split('-', 1)
        days = int(days_part)

    h, m, s = map(int, time_part.split(':'))
    return days * 86400 + h * 3600 + m * 60 + s


class SlurmClient:
    """Client for Slurm control plane operations."""

    def __init__(
        self,
        ssh_host: Optional[str] = None,
        ssh_port: Optional[int] = None,
        ssh_user: Optional[str] = None,
        ssh_key: Optional[str] = None,
        ssh_proxy_command: Optional[str] = None,
        ssh_proxy_jump: Optional[str] = None,
        is_inside_slurm_cluster: bool = False,
        identities_only: Optional[bool] = None,
    ):
        """Initialize SlurmClient.

        Args:
            ssh_host: Hostname of the Slurm controller.
            ssh_port: SSH port on the controller.
            ssh_user: SSH username.
            ssh_key: Path to SSH private key, or None for keyless SSH.
            ssh_proxy_command: Optional SSH proxy command.
            ssh_proxy_jump: Optional SSH proxy jump destination.
            is_inside_slurm_cluster: If True, uses local execution mode (for
            when running on the Slurm cluster itself). Defaults to False.
            identities_only: If True, only use the specified identity file and
                don't try ssh-agent keys. If None, defaults to False (allows
                ssh-agent fallback for backward compatibility).
        """
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.ssh_proxy_command = ssh_proxy_command
        self.ssh_proxy_jump = ssh_proxy_jump

        self._runner: command_runner.CommandRunner

        if is_inside_slurm_cluster:
            # Local execution mode - for running on the Slurm cluster itself
            # (e.g., autodown from skylet).
            self._runner = command_runner.LocalProcessCommandRunner()
        else:
            # Remote execution via SSH
            assert ssh_host is not None
            assert ssh_port is not None
            assert ssh_user is not None
            # If user has IdentitiesOnly=yes in their config, respect it by
            # NOT disabling IdentitiesOnly. Otherwise, allow ssh-agent fallback.
            self._runner = command_runner.SSHCommandRunner(
                (ssh_host, ssh_port),
                ssh_user,
                ssh_key,
                ssh_proxy_command=ssh_proxy_command,
                ssh_proxy_jump=ssh_proxy_jump,
                enable_interactive_auth=True,
                disable_identities_only=not identities_only,
            )

    def _run_slurm_cmd(self, cmd: str) -> Tuple[int, str, str]:
        return self._runner.run(cmd,
                                require_outputs=True,
                                separate_stderr=True,
                                stream_logs=False)

    def query_jobs(
        self,
        job_name: Optional[str] = None,
        state_filters: Optional[List[str]] = None,
    ) -> List[str]:
        """Query Slurm jobs by state and optional name.

        Args:
            job_name: Optional job name to filter by.
            state_filters: List of job states to filter by
                (e.g., ['running', 'pending']). If None, returns all jobs.

        Returns:
            List of job IDs matching the filters.
        """
        cmd = 'squeue --me -h -o "%i"'
        if state_filters is not None:
            state_filters_str = ','.join(state_filters)
            cmd += f' --states {state_filters_str}'
        if job_name is not None:
            cmd += f' --name {job_name}'

        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to query Slurm jobs.',
                                           stderr=f'{stdout}\n{stderr}')

        job_ids = stdout.strip().splitlines()
        return job_ids

    def cancel_jobs_by_name(self,
                            job_name: str,
                            signal: Optional[str] = None,
                            full: bool = False) -> None:
        """Cancel Slurm job(s) by name.

        Args:
            job_name: Name of the job(s) to cancel.
            signal: Optional signal to send to the job(s).
            full: If True, signals the batch script and its children processes.
                By default, signals other than SIGKILL are not sent to the
                batch step (the shell script).
        """
        cmd = f'scancel --name {job_name}'
        if signal is not None:
            cmd += f' --signal {signal}'
        if full:
            cmd += ' --full'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           f'Failed to cancel job {job_name}.',
                                           stderr=f'{stdout}\n{stderr}')
        logger.debug(f'Successfully cancelled job {job_name}: {stdout}')

    def info(self) -> str:
        """Get Slurm cluster information.

        This is useful for checking if the cluster is accessible and
        retrieving node information.

        Returns:
            The stdout output from sinfo.
        """
        cmd = 'sinfo'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get Slurm cluster information.',
            stderr=f'{stdout}\n{stderr}')
        return stdout

    def info_nodes(self) -> List[NodeInfo]:
        """Get Slurm node information.

        Returns node names, states, GRES (generic resources like GPUs),
        CPUs, memory (MB), and partitions.
        """
        cmd = (f'sinfo -h --Node -o '
               f'"%N{SEP}%t{SEP}%G{SEP}%c{SEP}%m{SEP}%P"')
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get Slurm node information.',
            stderr=f'{stdout}\n{stderr}')

        nodes = []
        for line in stdout.splitlines():
            parts = line.split(SEP)
            if len(parts) != 6:
                raise RuntimeError(
                    f'Unexpected output format from sinfo: {line!r}')
            try:
                node_info = NodeInfo(node=parts[0],
                                     state=parts[1],
                                     gres=parts[2],
                                     cpus=int(parts[3]),
                                     memory_gb=int(parts[4]) / 1024.0,
                                     partition=parts[5])
                nodes.append(node_info)
            except ValueError as e:
                raise RuntimeError(
                    f'Failed to parse node info from line: {line!r}. '
                    f'Error: {e}') from e

        return nodes

    def node_details(self, node_name: str) -> Dict[str, str]:
        """Get detailed Slurm node information.

        Returns:
            A dictionary of node attributes.
        """

        def _parse_scontrol_node_output(output: str) -> Dict[str, str]:
            """Parses the key=value output of 'scontrol show node'."""
            node_info = {}
            # Split by space, handling values that might have spaces
            # if quoted. This is simplified; scontrol can be complex.
            parts = output.split()
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    # Simple quote removal, might need refinement
                    value = value.strip('\'"')
                    node_info[key] = value
            return node_info

        cmd = f'scontrol show node {node_name}'
        rc, node_details, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get detailed node information for {node_name}.',
            stderr=f'{node_details}\n{stderr}')
        node_info = _parse_scontrol_node_output(node_details)
        return node_info

    def get_jobs_gres(self, node_name: str) -> List[str]:
        """Get the list of jobs GRES for a given node name.

        Returns:
            A list of GRES specs (e.g., 'gres/gpu:h100:4')
            for jobs on the node.
        """
        cmd = f'squeue -h --nodelist {node_name} -o "%b"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get jobs for node {node_name}.',
            stderr=f'{stdout}\n{stderr}')
        return stdout.splitlines()

    def get_all_jobs_gres(self) -> Dict[str, List[str]]:
        """Get GRES allocation for all running jobs, grouped by node.

        Returns:
            Dict mapping node_name -> list of GRES strings for jobs on that
            node.
        """
        cmd = f'squeue -h --states=running,completing -o "%N{SEP}%b"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to get all jobs GRES.',
                                           stderr=f'{stdout}\n{stderr}')

        nodes_to_gres: Dict[str, List[str]] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(SEP)
            if len(parts) != 2:
                # We should never reach here, but just in case.
                continue
            nodelist_str, gres_str = parts
            if not gres_str or gres_str == 'N/A':
                continue

            for node in hostlist.expand_hostlist(nodelist_str):
                nodes_to_gres.setdefault(node, []).append(gres_str)

        return nodes_to_gres

    def get_job_state(self, job_id: str) -> Optional[str]:
        """Get the state of a Slurm job.

        Args:
            job_id: The Slurm job ID.

        Returns:
            The job state (e.g., 'PENDING', 'RUNNING', 'COMPLETED', etc.),
            or None if the job is not found.
        """
        # Use --only-job-state since we only need the job state.
        # This reduces the work required by slurmctld.
        cmd = f'squeue -h --only-job-state --jobs {job_id} -o "%T"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get job state for job {job_id}.',
            stderr=f'{stdout}\n{stderr}')

        state = stdout.strip()
        return state if state else None

    def get_jobs_state_by_name(self, job_name: str) -> List[str]:
        """Get the states of all Slurm jobs by name.
        """
        cmd = f'squeue -h --name {job_name} -o "%T"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get job state for job {job_name}.',
            stderr=f'{stdout}\n{stderr}')

        states = stdout.splitlines()
        return states

    @timeline.event
    def get_job_reason(self, job_id: str) -> Optional[str]:
        """Get the reason a job is in its current state

        Args:
            job_id: The Slurm job ID.
        """
        # Without --states all, squeue omits terminated jobs.
        cmd = f'squeue -h --jobs {job_id} --states all -o "%r"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get job reason for job {job_id}.',
            stderr=f'{stdout}\n{stderr}')

        output = stdout.strip()
        if not output:
            return None

        return output if output != 'None' else None

    @timeline.event
    def wait_for_job_nodes(self, job_id: str, timeout: int) -> None:
        """Wait for a Slurm job to have nodes allocated.

        Args:
            job_id: The Slurm job ID.
            timeout: Maximum time to wait in seconds.
        """
        start_time = time.time()
        last_state = None

        while time.time() - start_time < timeout:
            state = self.get_job_state(job_id)

            if state != last_state:
                logger.debug(f'Job {job_id} state: {state}')
                last_state = state

            if state is None:
                raise RuntimeError(f'Job {job_id} not found. It may have been '
                                   'cancelled or failed.')

            if state in ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT'):
                raise RuntimeError(
                    f'Job {job_id} terminated with state {state} '
                    'before nodes were allocated.')
            # TODO(kevin): Log reason for pending.

            # Check if nodes are allocated by trying to get node list
            cmd = f'squeue -h --jobs {job_id} -o "%N"'
            rc, stdout, stderr = self._run_slurm_cmd(cmd)

            if rc == 0 and stdout.strip():
                # Nodes are allocated
                logger.debug(
                    f'Job {job_id} has nodes allocated: {stdout.strip()}')
                return
            elif rc != 0:
                logger.debug(f'Failed to get nodes for job {job_id}: '
                             f'{stdout}\n{stderr}')

            # Wait before checking again
            time.sleep(2)

        raise TimeoutError(f'Job {job_id} did not get nodes allocated within '
                           f'{timeout} seconds. Last state: {last_state}')

    @timeline.event
    def get_job_nodes(
            self,
            job_id: str,
            wait: bool = True,
            timeout: Optional[int] = None) -> Tuple[List[str], List[str]]:
        """Get the list of nodes and their IPs for a given job ID.

        The ordering is guaranteed to be stable for the lifetime of the job.

        Args:
            job_id: The Slurm job ID.
            wait: If True, wait for nodes to be allocated before returning.
            timeout: Maximum time to wait in seconds. Only used when wait=True.

        Returns:
            A tuple of (nodes, node_ips) where nodes is a list of node names
            and node_ips is a list of corresponding IP addresses.
        """
        # Wait for nodes to be allocated if requested
        if wait:
            if timeout is None:
                timeout = _SLURM_DEFAULT_PROVISION_TIMEOUT
            self.wait_for_job_nodes(job_id, timeout=timeout)

        cmd = (
            f'squeue -h --jobs {job_id} -o "%N" | tr \',\' \'\\n\' | '
            f'while read node; do '
            # TODO(kevin): Use json output for more robust parsing.
            f'node_addr=$(scontrol show node=$node | grep NodeAddr= | '
            f'awk -F= \'{{print $2}}\' | awk \'{{print $1}}\'); '
            f'echo "$node $node_addr"; '
            f'done')
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get nodes for job {job_id}.',
            stderr=f'{stdout}\n{stderr}')
        logger.debug(f'Successfully got nodes for job {job_id}: {stdout}')

        node_info = {}
        nodes_to_resolve: List[Tuple[str, str]] = []

        for line in stdout.strip().splitlines():
            line = line.strip()
            if line:
                parts = line.split()
                if len(parts) >= 2:
                    node_name = parts[0]
                    node_addr = parts[1]
                    try:
                        ipaddress.ip_address(node_addr)
                        node_info[node_name] = node_addr  # Already an IP
                    except ValueError:
                        nodes_to_resolve.append((node_name, node_addr))

        if nodes_to_resolve:
            hostnames = [h for _, h in nodes_to_resolve]
            # The output of `getent ahostsv4` is as follows:
            # 10.0.0.0     STREAM worker-0
            # 10.0.0.0     DGRAM
            # 10.0.0.0     RAW
            resolve_ip_cmd = (
                f'for h in {" ".join(hostnames)}; do '
                f'ip=$(getent ahostsv4 "$h" | head -1 | awk \'{{print $1}}\'); '
                f'if [ -n "$ip" ]; then echo "$h $ip"; '
                f'else echo "$h {_UNRESOLVED_HOSTNAME_MARKER}"; fi; '
                f'done')
            rc, resolve_stdout, stderr = self._run_slurm_cmd(resolve_ip_cmd)
            subprocess_utils.handle_returncode(
                rc,
                resolve_ip_cmd,
                f'Failed to resolve hostnames for: {hostnames}',
                stderr=f'{resolve_stdout}\n{stderr}')

            hostname_to_ip = {}
            unresolved = []
            for line in resolve_stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    hostname = parts[0]
                    ip = parts[1]
                    if ip == _UNRESOLVED_HOSTNAME_MARKER:
                        unresolved.append(hostname)
                    else:
                        hostname_to_ip[hostname] = ip

            if unresolved:
                raise RuntimeError(
                    f'Failed to resolve hostnames for: {unresolved}')

            for node_name, hostname in nodes_to_resolve:
                if hostname not in hostname_to_ip:
                    raise RuntimeError(
                        f'Failed to resolve {hostname} for node {node_name}')
                node_info[node_name] = hostname_to_ip[hostname]

        nodes = list(node_info.keys())
        node_ips = [node_info[node] for node in nodes]
        if not nodes:
            raise RuntimeError(
                f'No nodes found for job {job_id}. '
                f'The job may have terminated or the output was empty.')
        assert (len(nodes) == len(node_ips)
               ), f'Number of nodes and IPs do not match: {nodes} != {node_ips}'

        return nodes, node_ips

    def submit_job(
        self,
        partition: str,
        job_name: str,
        script_path: str,
    ) -> str:
        """Submit a Slurm job script.

        Args:
            partition: Slurm partition to submit to.
            job_name: Name to give the job.
            script_path: Remote path where the script will be stored.

        Returns:
            The job ID of the submitted job.
        """
        cmd = f'sbatch --partition={partition} {script_path}'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to submit Slurm job.',
                                           stderr=f'{stdout}\n{stderr}')

        # Parse job ID from sbatch output (format: "Submitted batch job 12345")
        job_id_match = re.search(r'Submitted batch job (\d+)', stdout)
        if not job_id_match:
            raise RuntimeError(
                f'Failed to parse job ID from sbatch output: {stdout}')

        job_id = job_id_match.group(1).strip()
        logger.debug(f'Successfully submitted Slurm job {job_id} with name '
                     f'{job_name}: {stdout}')

        return job_id

    def get_partitions_info(self) -> List[SlurmPartition]:
        """Get the partitions information for the Slurm cluster.

        Returns:
            List of SlurmPartition objects.
        """
        cmd = 'scontrol show partitions -o'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to get Slurm partitions.',
                                           stderr=f'{stdout}\n{stderr}')

        partitions = []
        for line in stdout.strip().splitlines():
            is_default = False
            match = _PARTITION_NAME_REGEX.search(line)
            if 'Default=YES' in line:
                is_default = True
            maxtime = _parse_maxtime(line)
            if match:
                partition = match.group(1).strip()
                if partition:
                    partitions.append(
                        SlurmPartition(name=partition,
                                       is_default=is_default,
                                       maxtime=maxtime))
        return partitions

    def get_default_partition(self) -> Optional[str]:
        """Get the default partition name for the Slurm cluster.

        Returns:
            The default partition name, or None if it cannot be determined.
        """
        partitions = self.get_partitions_info()
        for partition in partitions:
            if partition.is_default:
                return partition.name
        return None

    def get_partitions(self) -> List[str]:
        """Get unique partition names in the Slurm cluster.

        Returns:
            List of partition names. The default partition will not have a '*'
            at the end of the name.
        """
        return [partition.name for partition in self.get_partitions_info()]

    def get_proctrack_type(self) -> Optional[str]:
        """Get the ProctrackType from Slurm configuration.

        Returns:
            The proctrack type (e.g., 'cgroup', 'linuxproc', 'pgid'),
            or None if it cannot be determined.
        """
        cmd = 'scontrol show config | grep -i "^ProctrackType"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        if rc != 0:
            logger.warning(f'Failed to get ProctrackType: {stderr}')
            return None

        # Parse output like "ProctrackType           = proctrack/cgroup"
        match = re.search(r'ProctrackType\s*=\s*proctrack/(\w+)', stdout)
        if match:
            return match.group(1)
        return None
