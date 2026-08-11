"""Slurm adaptor for SkyPilot."""

import base64
import binascii
import ipaddress
import logging
import re
import shlex
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from sky.adaptors import common
from sky.utils import command_runner
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = logging.getLogger(__name__)

# ASCII Unit Separator (\x1f) to handle values with spaces
# and other special characters.
SEP = r'\x1f'

_INFO_NODES_CMD = (f'sinfo -h --Node -o '
                   f'"%N{SEP}%t{SEP}%G{SEP}%c{SEP}%m{SEP}%P"')
_ALL_NODE_DETAILS_CMD = 'scontrol show node -o'
_ALL_JOBS_INFO_CMD = (f'squeue -h --states=running,completing '
                      f'-o "%i{SEP}%j{SEP}%u{SEP}%N{SEP}%b"')
_PARTITIONS_INFO_CMD = 'scontrol show partitions -o'
_BATCH_OUTPUT_HEADER = 'SKYPILOT_SLURM_BATCH\n'

# Regex pattern to extract partition names from scontrol output
# Matches PartitionName=<name> and captures until the next field
_PARTITION_NAME_REGEX = re.compile(r'PartitionName=(.+?)(?:\s+\w+=|$)')

# Regex pattern to extract MAXTIME from scontrol output
# Matches MaxTime=<time> and captures the time
_MAXTIME_REGEX = re.compile(r'MaxTime=((?:\d+-)?\d{1,2}:\d{2}:\d{2}|UNLIMITED)')

# Regex pattern to extract DefaultTime from scontrol output
# Matches DefaultTime=<time>, DefaultTime=UNLIMITED, or DefaultTime=NONE.
_DEFAULT_TIME_REGEX = re.compile(
    r'DefaultTime=((?:\d+-)?\d{1,2}:\d{2}:\d{2}|UNLIMITED|NONE)')

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
    # The raw Slurm time string the partition assigns when --time is omitted
    # (e.g. '01:00:00', '2-00:00:00'). None if the partition has no
    # DefaultTime configured (NONE/UNLIMITED).
    default_time: Optional[str]


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


class JobGresInfo(NamedTuple):
    """Per-node GRES allocation of a running job from squeue."""
    job_id: str
    job_name: str
    # The user who submitted the job (squeue %u).
    user: str
    # The job's per-node GRES request (squeue %b, TRES_PER_NODE), e.g.
    # 'gres/gpu:h100:4'.
    gres_str: str


class SlurmInventorySnapshot(NamedTuple):
    """Cluster-wide Slurm inventory collected in one remote invocation."""
    node_infos: List[NodeInfo]
    node_details: Dict[str, Dict[str, str]]
    jobs: Optional[Dict[str, List[JobGresInfo]]]
    partitions: Optional[List[SlurmPartition]]


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


def _parse_default_time(line: str) -> Optional[str]:
    """Parse the DefaultTime a partition uses from the scontrol output.

    Returns the raw Slurm time string (e.g. '01:00:00', '2-00:00:00') so it
    can be passed straight through to ``--time``. Returns None when the
    partition has no DefaultTime configured (``NONE``/``UNLIMITED``).
    """
    match = _DEFAULT_TIME_REGEX.search(line)
    if not match:
        return None
    raw = match.group(1)
    if raw in ('NONE', 'UNLIMITED'):
        return None
    return raw


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


def _parse_info_nodes_output(stdout: str) -> List[NodeInfo]:
    nodes = []
    for line in stdout.splitlines():
        parts = line.split(SEP)
        if len(parts) != 6:
            raise RuntimeError(f'Unexpected output format from sinfo: {line!r}')
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


def _parse_all_node_details_output(stdout: str) -> Dict[str, Dict[str, str]]:
    details: Dict[str, Dict[str, str]] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        node_info = _parse_scontrol_node_output(line)
        node_name = node_info.get('NodeName')
        if node_name:
            details[node_name] = node_info
    return details


def _parse_all_jobs_info_output(stdout: str) -> Dict[str, List[JobGresInfo]]:
    nodes_to_jobs: Dict[str, List[JobGresInfo]] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(SEP)
        if len(parts) != 5:
            continue
        job_id, job_name, user, nodelist_str, gres_str = parts
        if not gres_str or gres_str == 'N/A':
            continue

        job_info = JobGresInfo(job_id=job_id,
                               job_name=job_name,
                               user=user,
                               gres_str=gres_str)
        for node in hostlist.expand_hostlist(nodelist_str):
            nodes_to_jobs.setdefault(node, []).append(job_info)
    return nodes_to_jobs


def _parse_partitions_info_output(stdout: str) -> List[SlurmPartition]:
    partitions = []
    for line in stdout.strip().splitlines():
        is_default = False
        match = _PARTITION_NAME_REGEX.search(line)
        if 'Default=YES' in line:
            is_default = True
        maxtime = _parse_maxtime(line)
        default_time = _parse_default_time(line)
        if match:
            partition = match.group(1).strip()
            if partition:
                partitions.append(
                    SlurmPartition(name=partition,
                                   is_default=is_default,
                                   maxtime=maxtime,
                                   default_time=default_time))
    return partitions


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
        slurm_user: Optional[str] = None,
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
            slurm_user: Unix user to run remote Slurm commands as. None runs
                commands as the SSH user.
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
            self._runner = command_runner.SlurmLoginNodeCommandRunner(
                (ssh_host, ssh_port),
                ssh_user,
                ssh_key,
                ssh_proxy_command=ssh_proxy_command,
                ssh_proxy_jump=ssh_proxy_jump,
                enable_interactive_auth=True,
                disable_identities_only=not identities_only,
                slurm_user=slurm_user,
            )

    def _run_slurm_cmd(self, cmd: str) -> Tuple[int, str, str]:
        return self._runner.run(cmd,
                                require_outputs=True,
                                separate_stderr=True,
                                stream_logs=False)

    def _run_slurm_cmds(self,
                        commands: Sequence[str]) -> List[Tuple[int, str, str]]:
        """Run independent commands concurrently in one remote invocation."""
        if not commands:
            return []

        script_lines = [
            'snapshot_dir=$(mktemp -d) || exit $?',
            'cleanup_snapshot() { rm -rf -- "${snapshot_dir:?}"; }',
            'trap cleanup_snapshot EXIT',
        ]
        for index, command in enumerate(commands):
            stdout_path = f'"${{snapshot_dir}}/{index}.stdout"'
            stderr_path = f'"${{snapshot_dir}}/{index}.stderr"'
            returncode_path = f'"${{snapshot_dir}}/{index}.returncode"'
            script_lines.append(
                f'( ( {command} ) > {stdout_path} 2> {stderr_path}; '
                f'printf \'%s\\n\' "$?" > {returncode_path} ) &')
        batch_output_header = _BATCH_OUTPUT_HEADER.rstrip('\n')
        script_lines.extend([
            'wait',
            f'printf \'%s\\n\' {shlex.quote(batch_output_header)}',
        ])
        for index in range(len(commands)):
            stdout_path = f'"${{snapshot_dir}}/{index}.stdout"'
            stderr_path = f'"${{snapshot_dir}}/{index}.stderr"'
            encoded_stdout_path = f'"${{snapshot_dir}}/{index}.stdout.b64"'
            encoded_stderr_path = f'"${{snapshot_dir}}/{index}.stderr.b64"'
            returncode_path = f'"${{snapshot_dir}}/{index}.returncode"'
            script_lines.extend([
                f'returncode=$(cat {returncode_path}) || exit $?',
                # Keep the framed transport ASCII-only because command runners
                # decode and filter subprocess output as text.
                f'base64 < {stdout_path} > {encoded_stdout_path} || exit $?',
                f'base64 < {stderr_path} > {encoded_stderr_path} || exit $?',
                f'stdout_size=$(wc -c < {encoded_stdout_path}) || exit $?',
                f'stderr_size=$(wc -c < {encoded_stderr_path}) || exit $?',
                'printf \'%s %s %s\\n\' "$returncode" "$stdout_size" '
                '"$stderr_size"',
                f'cat {encoded_stdout_path} || exit $?',
                f'cat {encoded_stderr_path} || exit $?',
            ])

        script = '\n'.join(script_lines)
        rc, stdout, stderr = self._run_slurm_cmd(script)
        subprocess_utils.handle_returncode(
            rc,
            'concurrent Slurm inventory commands',
            'Failed to run concurrent Slurm inventory commands.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

        try:
            output_bytes = stdout.encode('ascii')
        except UnicodeEncodeError as e:
            raise RuntimeError('Unexpected output from concurrent Slurm '
                               'inventory commands: non-ASCII transport '
                               'output.') from e
        header_bytes = _BATCH_OUTPUT_HEADER.encode('ascii')
        if not output_bytes.startswith(header_bytes):
            raise RuntimeError('Unexpected output from concurrent Slurm '
                               'inventory commands: missing header.')
        offset = len(header_bytes)
        results = []
        for _ in commands:
            header_end = output_bytes.find(b'\n', offset)
            if header_end == -1:
                raise RuntimeError('Unexpected output from concurrent Slurm '
                                   'inventory commands: missing frame header.')
            frame_header_bytes = output_bytes[offset:header_end]
            try:
                frame_header = frame_header_bytes.decode('ascii')
                returncode_str, stdout_size_str, stderr_size_str = (
                    frame_header.split())
                returncode = int(returncode_str)
                stdout_size = int(stdout_size_str)
                stderr_size = int(stderr_size_str)
                if stdout_size < 0 or stderr_size < 0:
                    raise ValueError('negative output size')
            except (UnicodeDecodeError, ValueError) as e:
                raise RuntimeError(
                    'Unexpected output from concurrent Slurm inventory '
                    f'commands: invalid frame header '
                    f'{frame_header_bytes!r}.') from e
            offset = header_end + 1
            frame_end = offset + stdout_size + stderr_size
            if frame_end > len(output_bytes):
                raise RuntimeError('Unexpected output from concurrent Slurm '
                                   'inventory commands: truncated frame.')
            stdout_end = offset + stdout_size
            encoded_stdout = output_bytes[offset:stdout_end]
            encoded_stderr = output_bytes[stdout_end:frame_end]
            try:
                command_stdout_bytes = base64.b64decode(b''.join(
                    encoded_stdout.split()),
                                                        validate=True)
                command_stderr_bytes = base64.b64decode(b''.join(
                    encoded_stderr.split()),
                                                        validate=True)
            except binascii.Error as e:
                raise RuntimeError('Unexpected output from concurrent Slurm '
                                   'inventory commands: invalid encoded '
                                   'output.') from e
            command_stdout = command_stdout_bytes.decode('utf-8',
                                                         errors='replace')
            command_stderr = command_stderr_bytes.decode('utf-8',
                                                         errors='replace')
            results.append((returncode, command_stdout, command_stderr))
            offset = frame_end
        if offset != len(output_bytes):
            raise RuntimeError('Unexpected output from concurrent Slurm '
                               'inventory commands: trailing data.')
        return results

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
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

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
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)
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
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
        return stdout

    def info_nodes(self) -> List[NodeInfo]:
        """Get Slurm node information.

        Returns node names, states, GRES (generic resources like GPUs),
        CPUs, memory (MB), and partitions.
        """
        cmd = _INFO_NODES_CMD
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get Slurm node information.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

        return _parse_info_nodes_output(stdout)

    def get_node_inventory(
            self) -> Tuple[List[NodeInfo], Dict[str, Dict[str, str]]]:
        """Get node information and details in one remote invocation."""
        outputs = self._run_slurm_cmds([_INFO_NODES_CMD, _ALL_NODE_DETAILS_CMD])
        node_returncode, node_stdout, node_stderr = outputs[0]
        details_returncode, details_stdout, details_stderr = outputs[1]
        subprocess_utils.handle_returncode(
            node_returncode,
            _INFO_NODES_CMD,
            'Failed to get Slurm node information.',
            stderr=f'{node_stdout}\n{node_stderr}',
            stream_logs=False)
        node_infos = _parse_info_nodes_output(node_stdout)

        node_details: Dict[str, Dict[str, str]] = {}
        if details_returncode != 0:
            logger.debug('Failed to get detailed Slurm node information: %s',
                         details_stderr)
        else:
            try:
                node_details = _parse_all_node_details_output(details_stdout)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(
                    'Failed to parse detailed Slurm node '
                    'information: %s', e)
        return node_infos, node_details

    def get_inventory_snapshot(self) -> SlurmInventorySnapshot:
        """Get cluster-wide nodes, jobs, and partitions in one SSH call.

        Node information is required. Node details, running jobs, and
        partitions are best-effort and use empty or None values when their
        individual Slurm commands fail.
        """
        outputs = self._run_slurm_cmds([
            _INFO_NODES_CMD,
            _ALL_NODE_DETAILS_CMD,
            _ALL_JOBS_INFO_CMD,
            _PARTITIONS_INFO_CMD,
        ])
        node_returncode, node_stdout, node_stderr = outputs[0]
        details_returncode, details_stdout, details_stderr = outputs[1]
        jobs_returncode, jobs_stdout, jobs_stderr = outputs[2]
        partitions_returncode, partitions_stdout, partitions_stderr = outputs[3]
        subprocess_utils.handle_returncode(
            node_returncode,
            _INFO_NODES_CMD,
            'Failed to get Slurm node information.',
            stderr=f'{node_stdout}\n{node_stderr}',
            stream_logs=False)
        node_infos = _parse_info_nodes_output(node_stdout)

        node_details: Dict[str, Dict[str, str]] = {}
        if details_returncode != 0:
            logger.debug('Failed to get detailed Slurm node information: %s',
                         details_stderr)
        else:
            try:
                node_details = _parse_all_node_details_output(details_stdout)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(
                    'Failed to parse detailed Slurm node '
                    'information: %s', e)

        jobs = None
        if jobs_returncode != 0:
            logger.debug('Failed to get running Slurm jobs: %s', jobs_stderr)
        else:
            try:
                jobs = _parse_all_jobs_info_output(jobs_stdout)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug('Failed to parse running Slurm jobs: %s', e)

        partitions = None
        if partitions_returncode != 0:
            logger.debug('Failed to get Slurm partitions: %s',
                         partitions_stderr)
        else:
            try:
                partitions = _parse_partitions_info_output(partitions_stdout)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug('Failed to parse Slurm partitions: %s', e)

        return SlurmInventorySnapshot(node_infos=node_infos,
                                      node_details=node_details,
                                      jobs=jobs,
                                      partitions=partitions)

    def node_details(self, node_name: str) -> Dict[str, str]:
        """Get detailed Slurm node information.

        Returns:
            A dictionary of node attributes.
        """
        cmd = f'scontrol show node {node_name}'
        rc, node_details, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get detailed node information for {node_name}.',
            stderr=f'{node_details}\n{stderr}',
            stream_logs=False)
        node_info = _parse_scontrol_node_output(node_details)
        return node_info

    def get_all_node_details(self) -> Dict[str, Dict[str, str]]:
        """Get detailed attributes for every node in a single scontrol call.

        Uses ``scontrol show node -o`` (one line per node) so per-node
        attributes that sinfo's format codes cannot express (CPUAlloc,
        AllocMem, FreeMem, CPULoad, GresUsed, ...) are available without a
        round-trip per node.

        Returns:
            A dictionary mapping node name to its attribute dictionary.
        """
        cmd = _ALL_NODE_DETAILS_CMD
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get detailed node information.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
        return _parse_all_node_details_output(stdout)

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
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
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
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

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

    def get_all_jobs_info(self) -> Dict[str, List[JobGresInfo]]:
        """Get id, name, user and GRES of all running jobs, grouped by node.

        Like ``get_all_jobs_gres`` but keeps the job identity, so callers can
        attribute per-node GPU allocations to specific jobs. A multi-node job
        appears in the list of every node it runs on, each time with its
        per-node GRES request (squeue's ``%b``, TRES_PER_NODE). Jobs without
        GRES (``%b`` empty or ``N/A``) are skipped.

        Returns:
            Dict mapping node_name -> list of JobGresInfo for jobs on that
            node.
        """
        cmd = _ALL_JOBS_INFO_CMD
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to get all jobs info.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

        return _parse_all_jobs_info_output(stdout)

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
        # Fall back to the command without --only-job-state for older
        # Slurm versions (< 21.08) that don't support this flag.
        cmd = f'squeue -h --only-job-state --jobs {job_id} -o "%T"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        if rc != 0 and 'unrecognized option' in stderr:
            cmd = f'squeue -h --jobs {job_id} -o "%T"'
            rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get job state for job {job_id}.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

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
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

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
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

        output = stdout.strip()
        if not output:
            return None

        return output if output != 'None' else None

    def get_pending_job_count(self,
                              partition: str,
                              exclude_job_id: Optional[str] = None) -> int:
        """Count pending jobs in a partition, excluding our own job.

        Args:
            partition: The Slurm partition to query.
            exclude_job_id: Optional job ID to exclude from the count.

        Returns:
            The number of pending jobs, or -1 if the query fails.
        """
        cmd = f'squeue -h -p {partition} --states=pending -o "%i"'
        rc, stdout, _ = self._run_slurm_cmd(cmd)
        if rc != 0:
            return -1
        job_ids = [j.strip() for j in stdout.strip().splitlines() if j.strip()]
        if exclude_job_id:
            job_ids = [j for j in job_ids if j != exclude_job_id]
        return len(job_ids)

    def check_job_has_nodes(self, job_id: str) -> bool:
        """Check if a Slurm job has nodes allocated."""
        cmd = f'squeue -h --jobs {job_id} -o "%N"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        if rc != 0:
            logger.debug(f'Failed to check nodes for job {job_id}: '
                         f'{stdout}\n{stderr}')
            return False
        return bool(stdout.strip())

    @timeline.event
    def get_job_nodes(self, job_id: str) -> Tuple[List[str], List[str]]:
        """Get the list of nodes and their IPs for a given job ID.

        The ordering is guaranteed to be stable for the lifetime of the job.

        Args:
            job_id: The Slurm job ID.

        Returns:
            A tuple of (nodes, node_ips) where nodes is a list of node names
            and node_ips is a list of corresponding IP addresses.
        """

        cmd = (
            # Use scontrol show hostnames to expand both compact Slurm
            # hostlist notation (e.g. ml-16-node-[001-002]) and
            # comma-separated nodes into individual node names.
            # TODO(kevin): Use json output for more robust parsing.
            f'nodelist=$(squeue -h --jobs {job_id} -o "%N"); '
            f'scontrol show hostnames $nodelist | while read -r node; do '
            f'node_addr=$(scontrol show node=$node | grep NodeAddr= | '
            f'awk -F= \'{{print $2}}\' | awk \'{{print $1}}\'); '
            f'echo "$node $node_addr"; '
            f'done')
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            f'Failed to get nodes for job {job_id}.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
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
                stderr=f'{resolve_stdout}\n{stderr}',
                stream_logs=False)

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
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

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
        cmd = _PARTITIONS_INFO_CMD
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to get Slurm partitions.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

        return _parse_partitions_info_output(stdout)

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

    def get_select_type_parameters(self) -> Optional[str]:
        """Get SelectTypeParameters from Slurm configuration.

        See: https://slurm.schedmd.com/slurm.conf.html#OPT_SelectTypeParameters

        Returns:
            The raw value (e.g., 'CR_CPU', 'CR_CPU_Memory', 'CR_Core_Memory'),
            or None if it cannot be determined.
        """
        cmd = 'scontrol show config | grep -i "^SelectTypeParameters"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        if rc != 0:
            logger.warning(f'Failed to get SelectTypeParameters: {stderr}')
            return None

        # Parse output like "SelectTypeParameters     = CR_CPU_Memory"
        # When unset, Slurm defaults to CR_CORE_MEMORY for select/cons_tres,
        # so this field always has a value.
        match = re.search(r'SelectTypeParameters\s*=\s*(\S+)', stdout)
        if match:
            return match.group(1)
        return None

    def check_pyxis_enabled(self) -> bool:
        """Check if the Pyxis SPANK plugin is installed.

        Pyxis registers --container-* flags tagged with [pyxis] in srun
        help output. This is a reliable way to detect the plugin without
        requiring a job allocation.

        Returns:
            True if Pyxis is installed, False otherwise.
        """
        cmd = 'srun --help 2>&1 | grep -q \'\\[pyxis\\]\''
        rc, _, _ = self._run_slurm_cmd(cmd)
        return rc == 0

    def get_env(self) -> Dict[str, str]:
        """Fetch environment variables from the remote host.

        Returns:
            Dictionary of environment variable name -> value.
        """
        rc, stdout, stderr = self._run_slurm_cmd('env')
        if rc != 0:
            logger.warning(f'Failed to fetch remote env: {stderr}')
            return {}
        env: Dict[str, str] = {}
        for line in stdout.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                env[key] = value
        return env

    def get_remote_home_dir(self) -> str:
        """Returns the remote user's home directory."""
        return self._runner.get_remote_home_dir()

    def check_file_exists(self, path: str) -> bool:
        """Check if a file exists on the remote host."""
        cmd = f'test -f {shlex.quote(path)}'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        if rc not in (0, 1):
            subprocess_utils.handle_returncode(
                rc,
                cmd,
                f'Failed to check for file: {path}',
                stderr=f'{stdout}\n{stderr}')
        return rc == 0

    def check_fuse_enabled(self) -> bool:
        """Check if FUSE is available on the cluster.

        FUSE is required for mounting object stores (e.g., via goofys or
        rclone). We check for /dev/fuse which is the device node that FUSE
        requires.

        We first try to check on a compute node via srun, since that is
        where mounts actually happen. If srun cannot allocate resources
        (cluster is full, etc.), we fall back to checking the login node.

        Returns:
            True if FUSE is available, False otherwise.
        """
        # Try checking on a compute node first. We use a wrapper that
        # prints a marker so we can distinguish "command ran and /dev/fuse
        # is missing" from "srun itself failed to allocate".
        srun_cmd = ('srun --immediate=10 --time=00:00:30 '
                    'bash -c \'test -e /dev/fuse '
                    '&& echo FUSE_OK || echo FUSE_MISSING\'')
        rc, stdout, _ = self._run_slurm_cmd(srun_cmd)
        stdout = stdout.strip()
        if rc == 0 and 'FUSE_OK' in stdout:
            return True
        if rc == 0 and 'FUSE_MISSING' in stdout:
            return False

        # srun failed (no resources, misconfigured, etc.).
        # Fall back to checking the login node.
        logger.debug('srun FUSE check failed, falling back to login node')
        cmd = 'test -e /dev/fuse'
        rc, _, _ = self._run_slurm_cmd(cmd)
        return rc == 0

    def check_dir_shared_fs(self, path: str) -> Optional[str]:
        """Check the filesystem type of a directory.

        Args:
            path: The directory path to check. Must be an absolute path
                (no shell variables or ~).

        Returns:
            The filesystem type string (e.g., 'nfs', 'ext2/ext3'),
            or None if the check could not be performed.
        """
        cmd = f'stat -f -c %T {shlex.quote(path)}'
        rc, stdout, _ = self._run_slurm_cmd(cmd)
        if rc != 0:
            return None
        return stdout.strip().lower()

    def check_homedir_shared_fs(self) -> Optional[str]:
        """Check the filesystem type of the home directory."""
        return self.check_dir_shared_fs('~')
