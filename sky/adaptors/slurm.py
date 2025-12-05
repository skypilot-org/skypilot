"""Slurm adaptor for SkyPilot."""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

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
        ssh_key: Optional[str],
        ssh_proxy_command: Optional[str] = None,
    ):
        """Initialize SlurmClient.

        Args:
            ssh_host: Hostname of the Slurm controller.
            ssh_port: SSH port on the controller.
            ssh_user: SSH username.
            ssh_key: Path to SSH private key, or None for keyless SSH.
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

        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to query Slurm jobs.',
                                           stderr=stderr)

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
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           f'Failed to cancel job {job_name}.',
                                           stderr=stderr)
        logger.debug(f'Successfully cancelled job {job_name}: {stdout}')

    def info(self) -> str:
        """Get Slurm cluster information.

        This is useful for checking if the cluster is accessible and
        retrieving node information.

        Returns:
            The stdout output from sinfo.
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

        Returns node names, states, GRES (generic resources like GPUs),
        and partition.

        Returns:
            A list of node info lines.
        """
        cmd = 'sinfo -h --Node -o "%N %t %G %P"'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        subprocess_utils.handle_returncode(
            rc, cmd, 'Failed to get Slurm node information.', stderr=stderr)
        return stdout.splitlines()

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
        """Get Slurm node-to-partition information.

        Returns:
            A list of node and partition info lines.
        """
        cmd = 'sinfo -h --Nodes -o "%N %P"'
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
            A list of job names for the current user on the node.
        """
        cmd = f'squeue --me -h --nodelist {node_name} -o "%b"'
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
        """
        # Use --only-job-state since we only need the job state.
        # This reduces the work required by slurmctld.
        cmd = f'squeue -h --only-job-state --jobs {job_id} -o "%T"'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        if rc != 0:
            # Job may not exist
            logger.debug(f'Failed to get job state for job {job_id}: {stderr}')
            return None

        state = stdout.strip()
        return state if state else None

    @timeline.event
    def wait_for_job_nodes(self, job_id: str, timeout: int = 300) -> None:
        """Wait for a Slurm job to have nodes allocated.

        Args:
            job_id: The Slurm job ID.
            timeout: Maximum time to wait in seconds (default: 300).
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
            rc, stdout, stderr = self._runner.run(cmd,
                                                  require_outputs=True,
                                                  stream_logs=False)

            if rc == 0 and stdout.strip():
                # Nodes are allocated
                logger.debug(
                    f'Job {job_id} has nodes allocated: {stdout.strip()}')
                return
            elif rc != 0:
                logger.debug(f'Failed to get nodes for job {job_id}: {stderr}')

            # Wait before checking again
            time.sleep(2)

        raise TimeoutError(f'Job {job_id} did not get nodes allocated within '
                           f'{timeout} seconds. Last state: {last_state}')

    @timeline.event
    def get_job_nodes(self,
                      job_id: str,
                      wait: bool = True) -> Tuple[List[str], List[str]]:
        """Get the list of nodes and their IPs for a given job ID.

        The ordering is guaranteed to be stable for the lifetime of the job.

        Args:
            job_id: The Slurm job ID.
            wait: If True, wait for nodes to be allocated before returning.

        Returns:
            A tuple of (nodes, node_ips) where nodes is a list of node names
            and node_ips is a list of corresponding IP addresses.
        """
        # Wait for nodes to be allocated if requested
        if wait:
            self.wait_for_job_nodes(job_id)

        cmd = (
            f'squeue -h --jobs {job_id} -o "%N" | tr \',\' \'\\n\' | '
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
        """
        cmd = f'sbatch --partition={partition} {script_path}'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
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

    def get_default_partition(self) -> Optional[str]:
        """Get the default partition for the Slurm cluster.

        Returns:
            The default partition name, or None if it cannot be determined.
        """
        cmd = 'scontrol show partition -o'
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              stream_logs=False)
        if rc != 0:
            logger.debug(f'Failed to get default partition: {stderr}')
            return None

        for line in stdout.strip().splitlines():
            if 'Default=YES' in line:
                # Extract partition name from PartitionName=<name>
                parts = line.split()
                for part in parts:
                    if part.startswith('PartitionName='):
                        return part.split('=', 1)[1]

        logger.debug('No default partition found')
        return None
