"""Slurm adaptor for SkyPilot."""

import logging
import re
from typing import Dict, List, Optional, Tuple, Union

from sky.utils import command_runner
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = logging.getLogger(__name__)


class SlurmClient:
    """Client for Slurm control plane operations."""

    def __init__(
        self,
        ssh_host: str,
        ssh_port: Union[str, int],
        ssh_user: str,
        ssh_key: str,
        ssh_proxy_command: Optional[str] = None,
    ):
        """Initialize SlurmClient.

        Args:
            ssh_host: Hostname of the Slurm controller.
            ssh_port: SSH port on the controller.
            ssh_user: SSH username.
            ssh_key: Path to SSH private key.
            ssh_proxy_command: Optional SSH proxy command.
        """
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.ssh_proxy_command = ssh_proxy_command

        # Internal runner for executing Slurm CLI commands
        # on the controller node.
        self._runner = command_runner.SSHCommandRunner(
            (ssh_host, ssh_port),
            ssh_user,
            ssh_key,
            ssh_proxy_command=ssh_proxy_command,
        )

    def query_jobs(
        self,
        state_filters: Optional[List[str]] = None,
        job_name: Optional[str] = None,
    ) -> List[str]:
        """Query Slurm jobs by state and optional name.

        Args:
            state_filters: List of job states to filter by (e.g., ['running', 'pending']).
                           If None, returns jobs in all states.
            job_name: Optional job name to filter by.

        Returns:
            List of job IDs matching the filters.

        Raises:
            CommandError: If the squeue command fails.
        """
        if state_filters is None:
            state_filters = [
                'pending', 'running', 'completed', 'cancelled', 'failed'
            ]

        job_state_str = ','.join(state_filters)

        # Build squeue command
        # -u: filter by user
        # -t: filter by state
        # -h: no header
        # -n: filter by job name (if provided)
        # -o: output format (job ID only)
        cmd = f'squeue -u {self.ssh_user} -t {job_state_str} -h -o "%i"'
        if job_name is not None:
            cmd += f' -n {job_name}'

        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to query Slurm jobs.',
                                           stderr=stderr)

        job_ids = stdout.strip().splitlines()
        return job_ids

    def cancel_job(self, job_name: str) -> None:
        """Cancel a Slurm job by name.

        Args:
            job_name: Name of the job to cancel.

        Raises:
            CommandError: If the scancel command fails.
        """
        cmd = f'scancel -n {job_name}'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           f'Failed to cancel job {job_name}.',
                                           stderr=stderr)
        logger.debug(f'Successfully cancelled job {job_name}: {stdout}')

    def info(self) -> str:
        """Get Slurm cluster information using sinfo.

        This is useful for checking if the cluster is accessible and
        retrieving node information.

        Returns:
            The stdout output from sinfo.

        Raises:
            CommandError: If the sinfo command fails.
        """
        cmd = 'sinfo'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(
            rc, cmd, 'Failed to get Slurm cluster information.', stderr=stderr)
        return stdout

    def info_nodes(self) -> List[str]:
        """Get Slurm node information.

        Returns node names, states, and GRES (generic resources like GPUs).

        Returns:
            A list of node info lines from 'sinfo -N -h -o "%N %t %G"'

        Raises:
            CommandError: If the sinfo command fails.
        """
        cmd = 'sinfo -N -h -o "%N %t %G"'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(
            rc, cmd, 'Failed to get Slurm node information.', stderr=stderr)
        return stdout.splitlines()

    def node_details(self, node_name: str) -> Dict[str, str]:
        """Get detailed Slurm node information.

        Returns:
            A dictionary of node info from 'scontrol show node {node_name}'
        """

        # Helper function to parse scontrol output (can be moved to utils if needed)
        def _parse_scontrol_node_output(output: str) -> Dict[str, str]:
            """Parses the key=value output of 'scontrol show node'."""
            node_info = {}
            # Split by space, handling values that might contain spaces if quoted
            # This is a simplified parser; scontrol output can be complex.
            parts = output.split()
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    # Simple quote removal, might need refinement
                    value = value.strip('\'"')
                    node_info[key] = value
            return node_info

        cmd = ['scontrol', 'show', 'node', node_name]
        rc, node_details, _ = self._runner.run(cmd,
                                               require_outputs=True,
                                               stream_logs=False)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get detailed node information for {node_name}.',
            stderr=node_details)
        node_info = _parse_scontrol_node_output(node_details)
        return node_info

    def info_partitions(self) -> List[str]:
        """Get Slurm partition information.

        Returns:
            A list of partition info lines from 'sinfo -p -h -o "%P %t"'
        """
        cmd = ['sinfo', '-N', '-h', '-o', '%N %P']
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get Slurm partition information.',
            stderr=stderr)
        return stdout.splitlines()

    def get_node_jobs(self, node_name: str) -> List[str]:
        """Get the list of jobs for a given node name.

        Returns:
            A list of job info lines from 'squeue -w {node_name} -h -o "%i"'
        """
        cmd = ['squeue', '-w', node_name, '-h', '-o', '%b']
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(
            rc, cmd, f'Failed to get jobs for node {node_name}.', stderr=stderr)
        return stdout.splitlines()

    def get_job_state(self, job_id: str) -> Optional[str]:
        """Get the state of a Slurm job.

        Args:
            job_id: The Slurm job ID.

        Returns:
            The job state (e.g., 'PENDING', 'RUNNING', 'COMPLETED', etc.),
            or None if the job is not found.

        Raises:
            CommandError: If the squeue command fails.
        """
        cmd = f'squeue -j {job_id} -h -o "%T"'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        if rc != 0:
            # Job may not exist
            return None
        
        state = stdout.strip()
        return state if state else None

    @timeline.event
    def wait_for_job_nodes(self, job_id: str, timeout: int = 300) -> None:
        """Wait for a Slurm job to have nodes allocated.

        Args:
            job_id: The Slurm job ID.
            timeout: Maximum time to wait in seconds (default: 300).

        Raises:
            TimeoutError: If the job doesn't get nodes allocated within timeout.
            RuntimeError: If the job fails or is cancelled.
        """
        import time
        start_time = time.time()
        last_state = None
        
        while time.time() - start_time < timeout:
            state = self.get_job_state(job_id)
            
            if state != last_state:
                logger.debug(f'Job {job_id} state: {state}')
                last_state = state
            
            if state is None:
                raise RuntimeError(
                    f'Job {job_id} not found. It may have been cancelled or failed.')
            
            if state in ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT'):
                raise RuntimeError(
                    f'Job {job_id} terminated with state {state} before nodes were allocated.')
            
            # Check if nodes are allocated by trying to get node list
            cmd = f'squeue -j {job_id} -o %N -h'
            rc, stdout, stderr = self._runner.run(cmd,
                                                  require_outputs=True,
                                                  stream_logs=False)
            
            if rc == 0 and stdout.strip():
                # Nodes are allocated
                logger.debug(f'Job {job_id} has nodes allocated: {stdout.strip()}')
                return
            
            # Wait before checking again
            time.sleep(2)
        
        raise TimeoutError(
            f'Job {job_id} did not get nodes allocated within {timeout} seconds. '
            f'Last state: {last_state}')

    @timeline.event
    def get_job_nodes(self, job_id: str, wait: bool = True) -> Tuple[List[str], List[str]]:
        """Get the list of nodes and their IPs for a given job ID.

        The ordering is guaranteed to be stable for the lifetime of the job.

        Args:
            job_id: The Slurm job ID.
            wait: If True, wait for nodes to be allocated before returning.

        Returns:
            A tuple of (nodes, node_ips) where nodes is a list of node names
            and node_ips is a list of corresponding IP addresses.

        Raises:
            CommandError: If the squeue or scontrol command fails.
            RuntimeError: If no nodes are found for the job.
            TimeoutError: If waiting for nodes times out.
        """
        # Wait for nodes to be allocated if requested
        if wait:
            self.wait_for_job_nodes(job_id)
        
        cmd = (
            f'squeue -j {job_id} -o %N -h | tr \',\' \'\\n\' | '
            f'while read node; do '
            # TODO(kevin): Use json output for more robust parsing.
            f'ip=$(scontrol show node=$node | grep NodeAddr= | '
            f'awk -F= \'{{print $2}}\' | awk \'{{print $1}}\'); '
            f'echo "$node $ip"; '
            f'done')
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(
            rc, cmd, f'Failed to get nodes for job {job_id}.', stderr=stderr)
        logger.debug(f'Successfully got nodes for job {job_id}: {stdout}')

        node_info = {}
        for line in stdout.strip().splitlines():
            line = line.strip()
            if line:
                parts = line.split()
                if len(parts) >= 2:
                    node_name = parts[0]
                    node_ip = parts[1]
                    node_info[node_name] = node_ip

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

        Raises:
            CommandError: If job submission fails.
            RuntimeError: If job ID cannot be parsed from sbatch output.
        """
        cmd = f'sbatch --partition={partition} {script_path}'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to submit Slurm job.',
                                           stderr=stderr)

        # Parse job ID from sbatch output (format: "Submitted batch job 12345")
        job_id_match = re.search(r'Submitted batch job (\d+)', stdout)
        if not job_id_match:
            raise RuntimeError(
                f'Failed to parse job ID from sbatch output: {stdout}')

        job_id = job_id_match.group(1).strip()
        logger.debug(
            f'Successfully submitted Slurm job {job_id} with name {job_name}: {stdout}'
        )

        return job_id
