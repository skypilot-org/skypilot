"""Slurm adaptor for SkyPilot."""

import logging
import re
from typing import List, Optional, Tuple

from sky.utils import command_runner
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = logging.getLogger(__name__)


class SlurmClient:
    """Client for Slurm control plane operations."""

    def __init__(
        self,
        ssh_host: str,
        ssh_port: int,
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

        rc, stdout, stderr = self._runner.run(cmd, require_outputs=True)
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
        rc, stdout, stderr = self._runner.run(cmd, require_outputs=True)
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
        rc, stdout, stderr = self._runner.run(cmd, require_outputs=True)
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
        rc, stdout, stderr = self._runner.run(cmd, require_outputs=True)
        subprocess_utils.handle_returncode(
            rc, cmd, 'Failed to get Slurm node information.', stderr=stderr)
        return stdout.splitlines()

    @timeline.event
    def get_job_nodes(self, job_id: str) -> Tuple[List[str], List[str]]:
        """Get the list of nodes and their IPs for a given job ID.

        The ordering is guaranteed to be stable for the lifetime of the job.

        Args:
            job_id: The Slurm job ID.

        Returns:
            A tuple of (nodes, node_ips) where nodes is a list of node names
            and node_ips is a list of corresponding IP addresses.

        Raises:
            CommandError: If the squeue or scontrol command fails.
            RuntimeError: If no nodes are found for the job.
        """
        cmd = (
            f'squeue -j {job_id} -o %N -h | tr \',\' \'\\n\' | '
            f'while read node; do '
            # TODO(kevin): Use json output for more robust parsing.
            f'ip=$(scontrol show node=$node | grep NodeAddr= | '
            f'awk -F= \'{{print $2}}\' | awk \'{{print $1}}\'); '
            f'echo "$node $ip"; '
            f'done')
        rc, stdout, stderr = self._runner.run(cmd, require_outputs=True)
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
        rc, stdout, stderr = self._runner.run(cmd, require_outputs=True)
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
