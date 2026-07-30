"""Slurm adaptor for SkyPilot."""

import ipaddress
import json
import logging
import re
import shlex
from typing import (Any, Callable, Dict, List, NamedTuple, Optional, Tuple,
                    TypeVar)

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

# Regex pattern to extract DefaultTime from scontrol output
# Matches DefaultTime=<time>, DefaultTime=UNLIMITED, or DefaultTime=NONE.
_DEFAULT_TIME_REGEX = re.compile(
    r'DefaultTime=((?:\d+-)?\d{1,2}:\d{2}:\d{2}|UNLIMITED|NONE)')

_IMPORT_ERROR_MESSAGE = ('Failed to import dependencies for Slurm. '
                         'Try running: pip install "skypilot[slurm]"')
hostlist = common.LazyImport('hostlist',
                             import_error_message=_IMPORT_ERROR_MESSAGE)

_UNRESOLVED_HOSTNAME_MARKER = 'UNRESOLVED'

# getopt marker in stderr when the Slurm CLI does not know an option, e.g. on
# versions predating `--json` output.
_UNRECOGNIZED_OPTION_MARKER = 'unrecognized option'

_JSON_OPTION = '--json'

# squeue marker in stderr when slurmctld no longer knows the job ID.
_INVALID_JOB_ID_MARKER = 'invalid job id'

# How much of a malformed JSON payload to include in error messages.
_JSON_SNIPPET_LEN = 500

_T = TypeVar('_T')

# Slurm's sentinel for an unset uint32, e.g. a CPU load slurmd has not
# reported yet. Fields typed as a plain number in the JSON schema carry it
# through verbatim.
_NO_VAL32 = 0xfffffffe

# Slurm reports CPULoad as the load average scaled by this factor.
_CPU_LOAD_SCALE = 100.0


class _JsonOutputUnsupportedError(Exception):
    """The Slurm CLI does not support `--json` output."""


class SlurmPartition(NamedTuple):
    """Information about the Slurm partitions."""
    name: str
    is_default: bool
    # The maximum time a job can run in seconds.
    # None if the maximum time is unlimited.
    maxtime: Optional[int]
    # The time in seconds the partition assigns when --time is omitted.
    # None if the partition has no DefaultTime configured (NONE/UNLIMITED).
    default_time: Optional[int]


class NodeDetails(NamedTuple):
    """Per-node counters that sinfo's format codes cannot express.

    Every field is None when Slurm does not report the counter, e.g. on a node
    slurmd has not reported in for.
    """
    # CPUs allocated to jobs by the scheduler, and the node's total.
    alloc_cpus: Optional[int]
    total_cpus: Optional[int]
    # Memory in MB: allocated to jobs by the scheduler, the node's total, and
    # the free memory slurmd last sampled from the OS.
    alloc_memory_mb: Optional[int]
    real_memory_mb: Optional[int]
    free_memory_mb: Optional[int]
    # Load average slurmd last sampled from the OS.
    cpu_load: Optional[float]


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


def _parse_duration(raw: str) -> Optional[int]:
    """Convert a Slurm '[days-]hours:minutes:seconds' duration into seconds.

    Example: '2-12:30:05' => (2*86400) + (12*3600) + (30*60) + 5. Returns None
    for the durations Slurm spells out (``UNLIMITED``/``NONE``).
    """
    if raw in ('NONE', 'UNLIMITED'):
        return None

    days = 0
    time_part = raw
    if '-' in raw:
        days_part, time_part = raw.split('-', 1)
        days = int(days_part)

    h, m, s = map(int, time_part.split(':'))
    return days * 86400 + h * 3600 + m * 60 + s


def _parse_maxtime(line: str) -> Optional[int]:
    """Parse the maximum time a job can run from the scontrol output."""
    maxtime_match = _MAXTIME_REGEX.search(line)
    if not maxtime_match:
        return None
    return _parse_duration(maxtime_match.group(1).strip())


def _parse_default_time(line: str) -> Optional[int]:
    """Parse the DefaultTime a partition uses from the scontrol output."""
    match = _DEFAULT_TIME_REGEX.search(line)
    if not match:
        return None
    return _parse_duration(match.group(1))


def _parse_optional_number(value: Optional[str]) -> Optional[float]:
    """Parse a number from an scontrol attribute value; None on N/A."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    """Parse an int from an scontrol attribute value; None on N/A."""
    number = _parse_optional_number(value)
    return None if number is None else int(number)


def _json_optional_int(value: Any) -> Optional[int]:
    """Read a Slurm JSON number, which is optional-typed or plain.

    Optional numbers are wrapped as ``{'set': bool, 'infinite': bool,
    'number': N}``; plain ones carry ``_NO_VAL32`` when unset.
    """
    if isinstance(value, dict):
        if not value.get('set') or value.get('infinite'):
            return None
        value = value.get('number')
    if not isinstance(value, int) or value == _NO_VAL32:
        return None
    return value


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

        # Whether the cluster's Slurm CLI supports `--json` output, keyed by
        # program name. `--json` ships in a different release per program
        # (squeue since 21.08, scontrol since 23.02), so the verdict is probed
        # and cached per program. A program is absent until the first command
        # that tries it.
        self._json_output_supported: Dict[str, bool] = {}

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
        cmd = (f'sinfo -h --Node -o '
               f'"%N{SEP}%t{SEP}%G{SEP}%c{SEP}%m{SEP}%P"')
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get Slurm node information.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

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

    def _get_all_node_details_json(self) -> Dict[str, NodeDetails]:
        """get_all_node_details() using Slurm's `--json` output.

        `scontrol --json` ships since Slurm 23.02. Older versions reject the
        option, and get_all_node_details() falls back to the text output.
        """
        cmd = 'scontrol show node --json'
        rc, stdout, stderr = self._run_slurm_json_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get detailed node information.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

        payload = self._load_slurm_json(cmd, stdout)
        details: Dict[str, NodeDetails] = {}
        for node in self._get_json_field(cmd, payload, 'nodes'):
            cpu_load = _json_optional_int(node.get('cpu_load'))
            details[self._get_json_field(cmd, node, 'name')] = NodeDetails(
                alloc_cpus=_json_optional_int(node.get('alloc_cpus')),
                total_cpus=_json_optional_int(node.get('cpus')),
                alloc_memory_mb=_json_optional_int(node.get('alloc_memory')),
                real_memory_mb=_json_optional_int(node.get('real_memory')),
                free_memory_mb=_json_optional_int(node.get('free_mem')),
                cpu_load=(None if cpu_load is None else cpu_load /
                          _CPU_LOAD_SCALE),
            )
        return details

    def _get_all_node_details_text(self) -> Dict[str, NodeDetails]:
        """get_all_node_details() for Slurm versions without `--json`."""
        cmd = 'scontrol show node -o'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get detailed node information.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
        details: Dict[str, NodeDetails] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            node_info = _parse_scontrol_node_output(line)
            node_name = node_info.get('NodeName')
            if node_name:
                details[node_name] = NodeDetails(
                    alloc_cpus=_parse_optional_int(node_info.get('CPUAlloc')),
                    total_cpus=_parse_optional_int(node_info.get('CPUTot')),
                    alloc_memory_mb=_parse_optional_int(
                        node_info.get('AllocMem')),
                    real_memory_mb=_parse_optional_int(
                        node_info.get('RealMemory')),
                    free_memory_mb=_parse_optional_int(
                        node_info.get('FreeMem')),
                    cpu_load=_parse_optional_number(node_info.get('CPULoad')),
                )
        return details

    def get_all_node_details(self) -> Dict[str, NodeDetails]:
        """Get per-node counters for every node in a single scontrol call.

        These are the counters sinfo's format codes cannot express, fetched
        without a round-trip per node.

        Returns:
            A dictionary mapping node name to its counters.
        """
        return self._with_json_output(['scontrol'],
                                      self._get_all_node_details_json,
                                      self._get_all_node_details_text)

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
        cmd = (f'squeue -h --states=running,completing '
               f'-o "%i{SEP}%j{SEP}%u{SEP}%N{SEP}%b"')
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to get all jobs info.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

        nodes_to_jobs: Dict[str, List[JobGresInfo]] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(SEP)
            if len(parts) != 5:
                # We should never reach here, but just in case.
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

    def _resolve_hostnames(
            self, nodes_to_resolve: List[Tuple[str, str]]) -> Dict[str, str]:
        """Resolve node addresses that are hostnames into IPs.

        The resolution runs on the login node, where the cluster-internal DNS
        is reachable.

        Args:
            nodes_to_resolve: List of (node name, hostname) pairs.

        Returns:
            A dictionary mapping node name to the resolved IP.
        """
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
            raise RuntimeError(f'Failed to resolve hostnames for: {unresolved}')

        node_to_ip = {}
        for node_name, hostname in nodes_to_resolve:
            if hostname not in hostname_to_ip:
                raise RuntimeError(
                    f'Failed to resolve {hostname} for node {node_name}')
            node_to_ip[node_name] = hostname_to_ip[hostname]
        return node_to_ip

    def _with_json_output(self, programs: List[str], json_impl: Callable[[],
                                                                         _T],
                          text_impl: Callable[[], _T]) -> _T:
        """Run a `--json` implementation, falling back to the text one.

        Args:
            programs: The Slurm programs that `json_impl` runs with `--json`.
            json_impl: The implementation parsing `--json` output.
            text_impl: The implementation parsing the text output, used when
                the cluster's Slurm is too old for `--json`.
        """
        if all(self._json_output_supported.get(p, True) for p in programs):
            try:
                return json_impl()
            except _JsonOutputUnsupportedError as e:
                logger.debug(f'Falling back to parsing the text output: {e}')
        return text_impl()

    def _run_slurm_json_cmd(self, cmd: str) -> Tuple[int, str, str]:
        """Run a Slurm command with `--json`.

        Raises:
            _JsonOutputUnsupportedError: The Slurm CLI does not know one of the
                command's options, so the caller has to use the text output.
        """
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        if rc != 0 and _UNRECOGNIZED_OPTION_MARKER in stderr:
            if _JSON_OPTION in stderr:
                # Any other option is specific to this command, so it must not
                # disqualify the program's `--json` support as a whole.
                self._json_output_supported[shlex.split(cmd)[0]] = False
            raise _JsonOutputUnsupportedError(
                f'{cmd!r} failed: {stderr.strip()}')
        return rc, stdout, stderr

    def _load_slurm_json(self, cmd: str, stdout: str) -> Dict[str, Any]:
        """Parse the JSON payload of a Slurm `--json` command.

        Raises:
            _JsonOutputUnsupportedError: The command exited successfully but
                printed its text output, i.e. it ignored `--json`, and it has
                never produced JSON before.
            RuntimeError: The payload is not a JSON object.
        """
        program = shlex.split(cmd)[0]
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as e:
            if not self._json_output_supported.get(program):
                # Some subcommands (e.g. `scontrol show config`) ignore
                # `--json` and exit 0, so a successful exit is not proof that
                # the output is JSON.
                self._json_output_supported[program] = False
                raise _JsonOutputUnsupportedError(
                    f'{cmd!r} did not return JSON output: '
                    f'{stdout[:_JSON_SNIPPET_LEN]!r}') from e
            raise RuntimeError(
                f'Failed to parse the JSON output of {cmd!r}: {e}. '
                f'Output: {stdout[:_JSON_SNIPPET_LEN]!r}') from e
        if not isinstance(payload, dict):
            raise RuntimeError(
                f'Unexpected JSON output of {cmd!r}: expected an object, got '
                f'{type(payload).__name__}. '
                f'Output: {stdout[:_JSON_SNIPPET_LEN]!r}')
        self._json_output_supported[program] = True
        return payload

    def _get_json_field(self, cmd: str, payload: Dict[str, Any],
                        field: str) -> Any:
        """Read a field of a Slurm JSON payload, raising if it is absent."""
        if field not in payload:
            raise RuntimeError(
                f'Unexpected JSON output of {cmd!r}: no {field!r} field. '
                f'Output: {json.dumps(payload)[:_JSON_SNIPPET_LEN]!r}')
        return payload[field]

    def _get_job_nodes_json(self, job_id: str) -> Tuple[List[str], List[str]]:
        """get_job_nodes() implementation using Slurm's `--json` output.

        `squeue --json` ships since Slurm 21.08 and `scontrol --json` since
        23.02, so 23.02 is the floor for this path. Older versions reject the
        option, and get_job_nodes() falls back to parsing the text output.
        """
        no_nodes_error = (
            f'No nodes found for job {job_id}. '
            f'The job may have terminated or the output was empty.')

        squeue_cmd = f'squeue --jobs {job_id} --json'
        rc, stdout, stderr = self._run_slurm_json_cmd(squeue_cmd)
        if rc != 0 and _INVALID_JOB_ID_MARKER in stderr.lower():
            # slurmctld no longer knows about the job.
            raise RuntimeError(no_nodes_error)
        subprocess_utils.handle_returncode(
            rc,
            squeue_cmd,
            f'Failed to get nodes for job {job_id}.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

        payload = self._load_slurm_json(squeue_cmd, stdout)
        jobs = self._get_json_field(squeue_cmd, payload, 'jobs')
        if not jobs:
            raise RuntimeError(no_nodes_error)
        # A compact Slurm hostlist expression, e.g. 'ml-16-node-[001-002]'.
        # Empty while the job is still pending.
        nodelist = self._get_json_field(squeue_cmd, jobs[0], 'nodes')
        if not nodelist:
            raise RuntimeError(no_nodes_error)

        # Expand client-side so the ordering is ours, independent of the order
        # scontrol returns the nodes in.
        nodes = hostlist.expand_hostlist(nodelist)

        # scontrol takes the hostlist expression directly, so no per-node
        # round-trip is needed.
        scontrol_cmd = f'scontrol show node {shlex.quote(nodelist)} --json'
        rc, stdout, stderr = self._run_slurm_json_cmd(scontrol_cmd)
        subprocess_utils.handle_returncode(
            rc,
            scontrol_cmd,
            f'Failed to get node addresses for job {job_id}.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

        payload = self._load_slurm_json(scontrol_cmd, stdout)
        node_addrs: Dict[str, str] = {}
        for node in self._get_json_field(scontrol_cmd, payload, 'nodes'):
            name = self._get_json_field(scontrol_cmd, node, 'name')
            node_addrs[name] = self._get_json_field(scontrol_cmd, node,
                                                    'address')

        # scontrol silently omits nodes it does not know, so a node that was
        # removed from the cluster would otherwise disappear from the result.
        missing = [node for node in nodes if node not in node_addrs]
        if missing:
            raise RuntimeError(
                f'Slurm did not report an address for the following nodes of '
                f'job {job_id}: {missing}')

        node_info: Dict[str, str] = {}
        nodes_to_resolve: List[Tuple[str, str]] = []
        for node in nodes:
            address = node_addrs[node]
            try:
                ipaddress.ip_address(address)
                node_info[node] = address  # Already an IP
            except ValueError:
                nodes_to_resolve.append((node, address))

        if nodes_to_resolve:
            node_info.update(self._resolve_hostnames(nodes_to_resolve))

        return nodes, [node_info[node] for node in nodes]

    def _get_job_nodes_text(self, job_id: str) -> Tuple[List[str], List[str]]:
        """get_job_nodes() implementation for Slurm versions without `--json`.
        """
        cmd = (
            # Use scontrol show hostnames to expand both compact Slurm
            # hostlist notation (e.g. ml-16-node-[001-002]) and
            # comma-separated nodes into individual node names.
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
            node_info.update(self._resolve_hostnames(nodes_to_resolve))

        nodes = list(node_info.keys())
        node_ips = [node_info[node] for node in nodes]
        if not nodes:
            raise RuntimeError(
                f'No nodes found for job {job_id}. '
                f'The job may have terminated or the output was empty.')
        assert (len(nodes) == len(node_ips)
               ), f'Number of nodes and IPs do not match: {nodes} != {node_ips}'

        return nodes, node_ips

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
        return self._with_json_output(['squeue', 'scontrol'],
                                      lambda: self._get_job_nodes_json(job_id),
                                      lambda: self._get_job_nodes_text(job_id))

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

    def _get_default_partition_name(self, names: List[str]) -> Optional[str]:
        """Ask sinfo which partition is the default, marked with a '*'.

        `scontrol show partitions --json` does not serialize the partition
        flags, so `Default=YES` is not available there on any Slurm version
        released so far.

        Args:
            names: The cluster's partition names, used to tell a default
                marker apart from a name that ends with a '*' itself.
        """
        # --all so that a hidden partition is listed too.
        cmd = 'sinfo -h -a -o "%P"'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get the default Slurm partition.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

        known = set(names)
        for line in stdout.splitlines():
            name = line.strip()
            if name.endswith('*') and name not in known and name[:-1] in known:
                return name[:-1]
        return None

    def _get_partitions_info_json(self) -> List[SlurmPartition]:
        """get_partitions_info() using Slurm's `--json` output.

        `scontrol --json` ships since Slurm 23.02. Older versions reject the
        option, and get_partitions_info() falls back to the text output.
        """
        cmd = 'scontrol show partitions --json'
        rc, stdout, stderr = self._run_slurm_json_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to get Slurm partitions.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

        payload = self._load_slurm_json(cmd, stdout)
        names: List[str] = []
        # Slurm reports both times in minutes.
        times: List[Tuple[Optional[int], Optional[int]]] = []
        for partition in self._get_json_field(cmd, payload, 'partitions'):
            names.append(self._get_json_field(cmd, partition, 'name'))
            maximums = self._get_json_field(cmd, partition, 'maximums')
            defaults = self._get_json_field(cmd, partition, 'defaults')
            times.append((_json_optional_int(maximums.get('time')),
                          _json_optional_int(defaults.get('time'))))

        default_partition = self._get_default_partition_name(names)
        return [
            SlurmPartition(
                name=name,
                is_default=name == default_partition,
                maxtime=None if maxtime is None else maxtime * 60,
                default_time=None if default_time is None else default_time *
                60,
            ) for name, (maxtime, default_time) in zip(names, times)
        ]

    def _get_partitions_info_text(self) -> List[SlurmPartition]:
        """get_partitions_info() for Slurm versions without `--json`."""
        cmd = 'scontrol show partitions -o'
        rc, stdout, stderr = self._run_slurm_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to get Slurm partitions.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)

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

    def get_partitions_info(self) -> List[SlurmPartition]:
        """Get the partitions information for the Slurm cluster.

        Returns:
            List of SlurmPartition objects.
        """
        return self._with_json_output(['scontrol'],
                                      self._get_partitions_info_json,
                                      self._get_partitions_info_text)

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
