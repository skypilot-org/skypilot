"""PBS Pro / OpenPBS adaptor for SkyPilot.

Talks to a PBS server through its command line tools (qstat, qsub, qdel,
qselect, pbsnodes) on a login node reached over SSH. Only the plain text
``-f`` output formats are parsed: they are stable across PBS Pro and OpenPBS
releases, unlike ``-F json``, which some releases emit with unescaped
values.
"""

import logging
import re
import shlex
import subprocess
from typing import (Any, Dict, FrozenSet, List, NamedTuple, Optional, Sequence,
                    Tuple, Union)

from sky.utils import command_runner
from sky.utils import subprocess_utils
from sky.utils import timeline

logger = logging.getLogger(__name__)

# PBS job states, see the qstat man page. ``B`` (begun) only applies to job
# arrays and ``U`` is a user-suspended job on some releases.
PENDING_JOB_STATES: FrozenSet[str] = frozenset({'Q', 'H', 'W', 'T', 'M'})
RUNNING_JOB_STATES: FrozenSet[str] = frozenset({'R', 'B', 'S', 'E', 'U'})
TERMINAL_JOB_STATES: FrozenSet[str] = frozenset({'F', 'X'})

# Node ``state`` tokens (comma separated in pbsnodes) that mean the node
# cannot run jobs right now.
NODE_UNAVAILABLE_STATES = frozenset({
    'offline',
    'down',
    'state-unknown',
    'stale',
    'maintenance',
    'provisioning',
    'wait-provisioning',
    'initializing',
    'unresolvable',
})

# PBS client binaries are not always on the non-login PATH of SSH sessions
# (e.g. inside the job's own sshd), so always try the default install root.
_PBS_PATH_PREFIX = 'export PATH="$PATH:/opt/pbs/bin"; '

_BATCH_BEGIN = 'SKYPILOT_PBS_BATCH_BEGIN'
_BATCH_END = 'SKYPILOT_PBS_BATCH_END'
_BATCH_END_REGEX = re.compile(rf'^{_BATCH_END} (\d+)$', re.MULTILINE)

# qstat/qselect messages for a job the server no longer knows about.
_JOB_NOT_FOUND_REGEX = re.compile(
    r'(?:Unknown Job Id|Job has finished|Unknown queue destination)',
    re.IGNORECASE)
_NO_NODES_REGEX = re.compile(r'no node', re.IGNORECASE)

# Wall-clock bound for the inventory queries (SSH connect included), so that
# a login node that accepts the connection but never answers cannot hang the
# aggregate across clusters forever.
_INVENTORY_TIMEOUT_SECONDS = 60

_SIZE_UNITS_IN_BYTES = {
    'b': 1,
    'w': 8,
    'kb': 1024,
    'kw': 8 * 1024,
    'mb': 1024**2,
    'mw': 8 * 1024**2,
    'gb': 1024**3,
    'gw': 8 * 1024**3,
    'tb': 1024**4,
    'tw': 8 * 1024**4,
    'pb': 1024**5,
    'pw': 8 * 1024**5,
}
_SIZE_REGEX = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$')


class PbsQueue(NamedTuple):
    """A PBS queue, the SkyPilot zone of a PBS cluster."""
    name: str
    is_default: bool
    # 'Execution' or 'Route'. Jobs can only run in execution queues.
    queue_type: str
    enabled: bool
    started: bool
    # Raw ``resources_max.walltime`` / ``resources_default.walltime`` values
    # (e.g. '24:00:00'), None when unset.
    max_walltime: Optional[str]
    default_walltime: Optional[str]
    # Accepts jobs only from a routing queue, never from qsub directly.
    from_route_only: bool = False
    # ``default_chunk.Qlist``: sites that tie nodes to queues with a Qlist
    # resource stamp it on every chunk of the queue's jobs.
    qlist: Optional[str] = None
    # Destinations of a routing queue.
    route_destinations: Tuple[str, ...] = ()


class NodeInfo(NamedTuple):
    """A PBS execution host as reported by pbsnodes."""
    node: str
    # Comma separated pbsnodes state, e.g. 'free', 'job-busy', 'offline,down'.
    state: str
    cpus: int
    memory_gb: float
    gpus: int
    # Value of the node's GPU type resource, if the site declares one, and
    # the name of that resource.
    gpu_type: Optional[str]
    gpu_type_resource: Optional[str]
    # Queue the node is dedicated to, None when it serves every queue.
    queue: Optional[str]
    assigned_cpus: int
    assigned_gpus: int
    # Job ids running on the node.
    jobs: List[str]
    # Raw ``resources_available.Qlist``: comma separated queue tags the
    # node serves, on sites that use one; None otherwise.
    qlist: Optional[str] = None


class JobInfo(NamedTuple):
    """A PBS job as reported by qstat -f."""
    job_id: str
    name: str
    # Single letter PBS job state.
    state: str
    queue: str
    # Unique execution hosts in exec_host order; empty until the job runs.
    exec_hosts: List[str]
    # The scheduler's comment, e.g. why the job is not running yet.
    comment: Optional[str]
    exit_status: Optional[int]
    owner: Optional[str]


class PbsInventorySnapshot(NamedTuple):
    """Cluster-wide inventory collected in one remote invocation."""
    nodes: List[NodeInfo]
    queues: Optional[List[PbsQueue]]


def parse_size_to_gb(value: Optional[str]) -> float:
    """Parse a PBS size string ('128gb', '263782400kb', '4096mb') to GB."""
    if value is None:
        return 0.0
    match = _SIZE_REGEX.match(str(value))
    if match is None:
        raise ValueError(f'Invalid PBS size value: {value!r}')
    number = float(match.group(1))
    unit = match.group(2).lower() or 'b'
    if unit not in _SIZE_UNITS_IN_BYTES:
        raise ValueError(f'Invalid PBS size unit in {value!r}')
    return number * _SIZE_UNITS_IN_BYTES[unit] / 1024**3


def parse_exec_host(exec_host: Optional[str]) -> List[str]:
    """Unique hosts of a PBS exec_host string, in order.

    ``exec_host`` looks like ``node1/0*8+node1/8*8+node2/0*16``: one entry
    per chunk, ``host/first_cpu[*ncpus]``, joined by ``+``. The first host is
    the mother superior, where the batch script runs.
    """
    hosts: List[str] = []
    if not exec_host:
        return hosts
    for chunk in exec_host.split('+'):
        chunk = chunk.strip()
        if not chunk:
            continue
        host = chunk.split('/', 1)[0]
        # exec_host never carries a port, but exec_vnode style names can be
        # parenthesized when sites alias the two; strip defensively.
        host = host.strip('()')
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def parse_attribute_blocks(text: str) -> List[Tuple[str, Dict[str, str]]]:
    """Parse the ``-f`` output of qstat/pbsnodes into (header, attributes).

    The format is shared by ``qstat -f``, ``qstat -Q -f``, ``qstat -B -f``
    and ``pbsnodes -a``: an unindented header line starts an object, its
    attributes follow as indented ``name = value`` lines, and a value too
    long for one line continues on lines that start with a tab.
    """
    blocks: List[Tuple[str, Dict[str, str]]] = []
    # Attributes of the block being read; meaningful once blocks is non-empty.
    current_attrs: Dict[str, str] = {}
    last_key: Optional[str] = None
    for line in text.splitlines():
        if not line.strip():
            last_key = None
            continue
        if line.startswith('\t') and blocks and last_key:
            # Continuation of the previous value; PBS breaks long values
            # with "\n\t" and nothing else.
            current_attrs[last_key] += line[1:].strip()
            continue
        if line[0] not in (' ', '\t'):
            header = line.strip()
            current_attrs = {}
            blocks.append((header, current_attrs))
            last_key = None
            continue
        if not blocks or ' = ' not in line:
            continue
        key, value = line.strip().split(' = ', 1)
        current_attrs[key.strip()] = value.strip()
        last_key = key.strip()
    return blocks


def _header_value(header: str, prefix: str) -> str:
    if header.startswith(prefix):
        return header[len(prefix):].strip()
    return header


def _parse_int(value: Optional[str], default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _parse_bool(value: Optional[str]) -> bool:
    return str(value).strip().lower() == 'true'


def parse_nodes_output(stdout: str, gpu_resource: str,
                       gpu_type_resources: Sequence[str]) -> List[NodeInfo]:
    """Parse ``pbsnodes -a`` output."""
    nodes = []
    for header, attrs in parse_attribute_blocks(stdout):
        node_name = header.split()[0]
        available_prefix = 'resources_available.'
        assigned_prefix = 'resources_assigned.'
        gpu_type = None
        gpu_type_resource = None
        for resource in gpu_type_resources:
            value = attrs.get(available_prefix + resource)
            if value:
                gpu_type = value
                gpu_type_resource = resource
                break
        queue = attrs.get('queue') or None
        jobs_str = attrs.get('jobs', '')
        jobs: List[str] = []
        for entry in jobs_str.split(','):
            entry = entry.strip()
            if not entry:
                continue
            job_id = entry.split('/', 1)[0]
            if job_id not in jobs:
                jobs.append(job_id)
        try:
            memory_gb = parse_size_to_gb(attrs.get(available_prefix + 'mem'))
        except ValueError:
            memory_gb = 0.0
        nodes.append(
            NodeInfo(
                node=node_name,
                state=attrs.get('state', 'state-unknown'),
                cpus=_parse_int(attrs.get(available_prefix + 'ncpus')),
                memory_gb=memory_gb,
                gpus=_parse_int(attrs.get(available_prefix + gpu_resource)),
                gpu_type=gpu_type,
                gpu_type_resource=gpu_type_resource,
                queue=queue,
                assigned_cpus=_parse_int(attrs.get(assigned_prefix + 'ncpus')),
                assigned_gpus=_parse_int(
                    attrs.get(assigned_prefix + gpu_resource)),
                jobs=jobs,
                qlist=attrs.get('resources_available.Qlist') or None,
            ))
    return nodes


def parse_queues_output(stdout: str,
                        default_queue: Optional[str]) -> List[PbsQueue]:
    """Parse ``qstat -Q -f`` output."""
    queues = []
    for header, attrs in parse_attribute_blocks(stdout):
        name = _header_value(header, 'Queue:')
        if not name:
            continue
        queues.append(
            PbsQueue(
                name=name,
                is_default=(name == default_queue),
                queue_type=attrs.get('queue_type', 'Execution'),
                enabled=_parse_bool(attrs.get('enabled', 'True')),
                started=_parse_bool(attrs.get('started', 'True')),
                max_walltime=attrs.get('resources_max.walltime'),
                default_walltime=attrs.get('resources_default.walltime'),
                from_route_only=_parse_bool(
                    attrs.get('from_route_only', 'False')),
                qlist=(attrs.get('default_chunk.Qlist') or
                       attrs.get('resources_default.Qlist') or None),
                route_destinations=tuple(
                    d.strip()
                    for d in attrs.get('route_destinations', '').split(',')
                    if d.strip()),
            ))
    return queues


def parse_jobs_output(stdout: str) -> List[JobInfo]:
    """Parse ``qstat -f`` output."""
    jobs = []
    for header, attrs in parse_attribute_blocks(stdout):
        job_id = _header_value(header, 'Job Id:')
        if not job_id:
            continue
        exit_status = attrs.get('Exit_status')
        comment = attrs.get('comment')
        jobs.append(
            JobInfo(
                job_id=job_id,
                name=attrs.get('Job_Name', ''),
                state=attrs.get('job_state', ''),
                queue=attrs.get('queue', ''),
                exec_hosts=parse_exec_host(attrs.get('exec_host')),
                comment=comment if comment else None,
                exit_status=(_parse_int(exit_status)
                             if exit_status is not None else None),
                owner=attrs.get('Job_Owner'),
            ))
    return jobs


def is_node_available(state: str) -> bool:
    """Whether a pbsnodes state string allows new jobs on the node."""
    tokens = {token.strip() for token in state.split(',') if token.strip()}
    return not tokens & NODE_UNAVAILABLE_STATES


class PbsClient:
    """Client for PBS server operations."""

    def __init__(
        self,
        ssh_host: Optional[str] = None,
        ssh_port: Optional[int] = None,
        ssh_user: Optional[str] = None,
        ssh_key: Optional[str] = None,
        ssh_proxy_command: Optional[str] = None,
        ssh_proxy_jump: Optional[str] = None,
        is_inside_pbs_cluster: bool = False,
        identities_only: Optional[bool] = None,
        ssh_control_path: Optional[str] = None,
    ):
        """Initialize PbsClient.

        Args:
            ssh_host: Hostname of the PBS login node.
            ssh_port: SSH port on the login node.
            ssh_user: SSH username.
            ssh_key: Path to the SSH private key, or None for keyless SSH.
            ssh_proxy_command: Optional SSH proxy command.
            ssh_proxy_jump: Optional SSH proxy jump destination.
            is_inside_pbs_cluster: Run the PBS commands locally, for code
                that already runs on an allocated node (e.g. autodown from
                skylet).
            identities_only: If True, only use the given identity file and
                do not fall back to ssh-agent keys.
            ssh_control_path: A user-managed ControlPath to reuse, for
                login nodes whose master connection the user opens by hand.
        """
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.ssh_key = ssh_key
        self.ssh_proxy_command = ssh_proxy_command
        self.ssh_proxy_jump = ssh_proxy_jump
        self.ssh_control_path = ssh_control_path

        self._runner: command_runner.CommandRunner
        if is_inside_pbs_cluster:
            self._runner = command_runner.LocalProcessCommandRunner()
        else:
            assert ssh_host is not None
            assert ssh_port is not None
            assert ssh_user is not None
            self._runner = command_runner.SSHCommandRunner(
                (ssh_host, ssh_port),
                ssh_user,
                ssh_key,
                ssh_proxy_command=ssh_proxy_command,
                ssh_proxy_jump=ssh_proxy_jump,
                enable_interactive_auth=True,
                disable_identities_only=not identities_only,
                ssh_control_path=ssh_control_path,
            )

    def _run_cmd(self,
                 cmd: Union[str, List[str]],
                 timeout: Optional[int] = None) -> Tuple[int, str, str]:
        if isinstance(cmd, list):
            cmd = shlex.join(cmd)
        cmd = _PBS_PATH_PREFIX + cmd
        kwargs: Dict[str, Any] = {}
        if timeout is not None:
            kwargs['timeout'] = timeout
        rc, stdout, stderr = self._runner.run(cmd,
                                              require_outputs=True,
                                              separate_stderr=True,
                                              stream_logs=False,
                                              **kwargs)
        if rc == 255 and self.ssh_control_path is not None:
            # Login nodes with a user-managed ControlPath are usually
            # password-only; the master connection has to be opened by hand.
            stderr = (f'{stderr}\nCould not log in to {self.ssh_host} '
                      'without a password. Open a shared connection first '
                      f'with: ssh -F ~/.pbs/config -fN <cluster>  (ControlPath '
                      f'{self.ssh_control_path})')
        return rc, stdout, stderr

    def _run_cmds(self,
                  commands: Sequence[str],
                  timeout: Optional[int] = None) -> List[Tuple[int, str]]:
        """Run several commands in one remote invocation.

        Each command's stdout is framed between marker lines so that
        anything the login shell prints before the first command (MOTD,
        module notices) cannot be mistaken for command output. stderr of all
        commands is folded into the raised error on transport failure only.
        """
        if not commands:
            return []
        script_lines = []
        for command in commands:
            script_lines.append(
                f'printf \'\\n%s\\n\' {_BATCH_BEGIN}; ( {command} ); '
                f'printf \'\\n%s %s\\n\' {_BATCH_END} "$?"')
        script = '\n'.join(script_lines)
        rc, stdout, stderr = self._run_cmd(script, timeout=timeout)
        subprocess_utils.handle_returncode(rc,
                                           'batched PBS commands',
                                           'Failed to run PBS commands.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)
        results: List[Tuple[int, str]] = []
        cursor = 0
        for _ in commands:
            begin = stdout.find(_BATCH_BEGIN, cursor)
            if begin == -1:
                raise RuntimeError('Unexpected output from batched PBS '
                                   f'commands: missing frame: {stdout!r}')
            begin += len(_BATCH_BEGIN)
            end_match = _BATCH_END_REGEX.search(stdout, begin)
            if end_match is None:
                raise RuntimeError('Unexpected output from batched PBS '
                                   f'commands: unterminated frame: {stdout!r}')
            frame = stdout[begin:end_match.start()]
            results.append((int(end_match.group(1)), frame.strip('\n')))
            cursor = end_match.end()
        return results

    def run_command(self,
                    cmd: str,
                    timeout: Optional[int] = None) -> Tuple[int, str, str]:
        """Runs one shell command on the login node and returns its output.

        Returns:
            (returncode, stdout, stderr). stdout is the command's own
            output, with whatever the login shell printed before it removed.
        """
        rc, stdout, stderr = self._run_cmd(
            f'printf \'\\n%s\\n\' {_BATCH_BEGIN}; ( {cmd} ); '
            f'printf \'\\n%s %s\\n\' {_BATCH_END} "$?"',
            timeout=timeout)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to run PBS command.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)
        results = self._parse_single_frame(stdout)
        return results[0], results[1], stderr

    @staticmethod
    def _parse_single_frame(stdout: str) -> Tuple[int, str]:
        begin = stdout.find(_BATCH_BEGIN)
        if begin == -1:
            raise RuntimeError(
                f'Unexpected output from PBS command: {stdout!r}')
        begin += len(_BATCH_BEGIN)
        end_match = _BATCH_END_REGEX.search(stdout, begin)
        if end_match is None:
            raise RuntimeError(
                f'Unexpected output from PBS command: {stdout!r}')
        return int(
            end_match.group(1)), stdout[begin:end_match.start()].strip('\n')

    # ------------------------------------------------------------------
    # Server / queue / node inventory
    # ------------------------------------------------------------------

    def info(self) -> str:
        """Returns ``qstat -B`` output; raises if the server is unreachable."""
        cmd = 'qstat -B'
        rc, stdout, stderr = self._run_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get PBS server information.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
        return stdout

    def get_server_attributes(self) -> Dict[str, str]:
        """Attributes of the PBS server (``qstat -B -f``)."""
        cmd = 'qstat -B -f'
        rc, stdout, stderr = self._run_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get PBS server attributes.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
        blocks = parse_attribute_blocks(stdout)
        return blocks[0][1] if blocks else {}

    def get_default_queue(self) -> Optional[str]:
        return self.get_server_attributes().get('default_queue') or None

    def get_queues_info(self) -> List[PbsQueue]:
        """Queues of the PBS server; the default queue is flagged."""
        outputs = self._run_cmds(['qstat -B -f', 'qstat -Q -f'],
                                 timeout=_INVENTORY_TIMEOUT_SECONDS)
        server_rc, server_stdout = outputs[0]
        queues_rc, queues_stdout = outputs[1]
        subprocess_utils.handle_returncode(queues_rc,
                                           'qstat -Q -f',
                                           'Failed to get PBS queues.',
                                           stderr=queues_stdout,
                                           stream_logs=False)
        default_queue = None
        if server_rc == 0:
            blocks = parse_attribute_blocks(server_stdout)
            if blocks:
                default_queue = blocks[0][1].get('default_queue') or None
        return parse_queues_output(queues_stdout, default_queue)

    def get_queues(self) -> List[str]:
        """Execution queue names; the default queue comes first."""
        default = []
        others = []
        for queue in self.get_queues_info():
            if queue.queue_type.lower() != 'execution':
                continue
            if queue.is_default:
                default.append(queue.name)
            else:
                others.append(queue.name)
        return default + sorted(others)

    def info_nodes(self, gpu_resource: str,
                   gpu_type_resources: Sequence[str]) -> List[NodeInfo]:
        """Execution hosts of the PBS server (``pbsnodes -a``)."""
        cmd = 'pbsnodes -a'
        rc, stdout, stderr = self._run_cmd(cmd,
                                           timeout=_INVENTORY_TIMEOUT_SECONDS)
        if rc != 0 and _NO_NODES_REGEX.search(f'{stdout}\n{stderr}'):
            return []
        subprocess_utils.handle_returncode(
            rc,
            cmd,
            'Failed to get PBS node information.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
        return parse_nodes_output(stdout, gpu_resource, gpu_type_resources)

    def get_inventory_snapshot(
            self, gpu_resource: str,
            gpu_type_resources: Sequence[str]) -> PbsInventorySnapshot:
        """Nodes and queues in one SSH call; queues are best-effort."""
        outputs = self._run_cmds(['pbsnodes -a', 'qstat -B -f', 'qstat -Q -f'],
                                 timeout=_INVENTORY_TIMEOUT_SECONDS)
        nodes_rc, nodes_stdout = outputs[0]
        server_rc, server_stdout = outputs[1]
        queues_rc, queues_stdout = outputs[2]
        nodes: List[NodeInfo] = []
        if nodes_rc != 0 and _NO_NODES_REGEX.search(nodes_stdout):
            nodes = []
        else:
            subprocess_utils.handle_returncode(
                nodes_rc,
                'pbsnodes -a',
                'Failed to get PBS node information.',
                stderr=nodes_stdout,
                stream_logs=False)
            nodes = parse_nodes_output(nodes_stdout, gpu_resource,
                                       gpu_type_resources)
        queues = None
        if queues_rc != 0:
            logger.debug('Failed to get PBS queues: %s', queues_stdout)
        else:
            default_queue = None
            if server_rc == 0:
                blocks = parse_attribute_blocks(server_stdout)
                if blocks:
                    default_queue = blocks[0][1].get('default_queue') or None
            try:
                queues = parse_queues_output(queues_stdout, default_queue)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug('Failed to parse PBS queues: %s', e)
        return PbsInventorySnapshot(nodes=nodes, queues=queues)

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def list_jobs_by_name(self,
                          job_name: str,
                          include_finished: bool = False) -> List[JobInfo]:
        """Jobs of the current user with the given name.

        Finished jobs are only visible when the server keeps job history
        (``job_history_enable``), and only with ``include_finished``.
        """
        history_flag = '-x' if include_finished else ''
        # Only the SSH user's own jobs: another user may well run a cluster
        # with the same name.
        select = (f'qselect -N {shlex.quote(job_name)} -u "$(id -un)" '
                  f'{history_flag}')
        cmd = (f'ids=$({select}) || exit $?; '
               f'if [ -n "$ids" ]; then qstat {history_flag} -f $ids; fi')
        rc, stdout, stderr = self._run_cmd(cmd)
        if rc != 0:
            if _JOB_NOT_FOUND_REGEX.search(f'{stdout}\n{stderr}'):
                # A job finished between qselect and qstat; what qstat
                # did print is still valid.
                pass
            else:
                subprocess_utils.handle_returncode(
                    rc,
                    cmd,
                    f'Failed to query PBS jobs named {job_name!r}.',
                    stderr=f'{stdout}\n{stderr}',
                    stream_logs=False)
        return [
            job for job in parse_jobs_output(stdout) if job.name == job_name
        ]

    def query_jobs(
        self,
        job_name: str,
        state_filters: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Ids of the current user's jobs named ``job_name`` in the states.

        Args:
            job_name: Job name to match exactly.
            state_filters: PBS state letters, e.g. ['Q', 'R']. None matches
                every state, finished ones included.
        """
        states = (frozenset(s.upper() for s in state_filters)
                  if state_filters is not None else None)
        include_finished = states is None or bool(states & TERMINAL_JOB_STATES)
        jobs = self.list_jobs_by_name(job_name,
                                      include_finished=include_finished)
        return [
            job.job_id
            for job in jobs
            if states is None or job.state.upper() in states
        ]

    def get_job(self, job_id: str) -> Optional[JobInfo]:
        """The job, or None if the server no longer knows it."""
        cmd = ['qstat', '-x', '-f', job_id]
        rc, stdout, stderr = self._run_cmd(cmd)
        if rc != 0:
            if _JOB_NOT_FOUND_REGEX.search(f'{stdout}\n{stderr}'):
                return None
            subprocess_utils.handle_returncode(
                rc,
                shlex.join(cmd),
                f'Failed to get PBS job {job_id}.',
                stderr=f'{stdout}\n{stderr}',
                stream_logs=False)
        jobs = parse_jobs_output(stdout)
        for job in jobs:
            if job.job_id == job_id:
                return job
        return jobs[0] if jobs else None

    def get_job_state(self, job_id: str) -> Optional[str]:
        """Single letter state of the job, or None if it is unknown."""
        job = self.get_job(job_id)
        return job.state if job is not None and job.state else None

    def get_jobs_state_by_name(self, job_name: str) -> List[str]:
        """States of every job (finished included) named ``job_name``."""
        return [
            job.state
            for job in self.list_jobs_by_name(job_name, include_finished=True)
        ]

    @timeline.event
    def get_job_reason(self, job_id: str) -> Optional[str]:
        """The scheduler's comment on the job, e.g. why it is still queued."""
        job = self.get_job(job_id)
        if job is None:
            return None
        return job.comment

    def get_pending_job_count(self,
                              queue: str,
                              exclude_job_id: Optional[str] = None) -> int:
        """Queued jobs in a queue, excluding our own; -1 if the query fails."""
        cmd = ['qselect', '-q', queue, '-s', 'Q']
        rc, stdout, _ = self._run_cmd(cmd)
        if rc != 0:
            return -1
        job_ids = [j.strip() for j in stdout.splitlines() if j.strip()]
        if exclude_job_id:
            job_ids = [j for j in job_ids if j != exclude_job_id]
        return len(job_ids)

    def check_job_has_nodes(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return job is not None and bool(job.exec_hosts)

    @timeline.event
    def get_job_nodes(self, job_id: str) -> List[str]:
        """Execution hosts of a job, in exec_host order (head first).

        Hostnames rather than addresses: they are what the login node and
        the nodes resolve, and some sites' nodes are IPv6-only.
        """
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError(f'PBS job {job_id} not found.')
        if not job.exec_hosts:
            raise RuntimeError(f'No nodes found for PBS job {job_id}. The job '
                               f'may not be running (state {job.state!r}).')
        return job.exec_hosts

    def submit_job(self, queue: str, job_name: str, script_path: str,
                   workdir: str) -> str:
        """Submit a job script with qsub and return its job id."""
        cmd = (f'cd {shlex.quote(workdir)} && ' + shlex.join(
            ['qsub', '-q', queue, '-N', job_name, '-r', 'n', script_path]))
        rc, stdout, stderr = self._run_cmd(cmd)
        subprocess_utils.handle_returncode(rc,
                                           cmd,
                                           'Failed to submit PBS job.',
                                           stderr=f'{stdout}\n{stderr}',
                                           stream_logs=False)
        # qsub prints the job id (e.g. 1234.pbsserver) and nothing else, but
        # a login shell may add noise before it: take the last token.
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines or not re.fullmatch(r'[\w.\-\[\]]+', lines[-1]):
            raise RuntimeError(
                f'Failed to parse the job id from qsub output: {stdout!r}')
        job_id = lines[-1]
        logger.debug(f'Submitted PBS job {job_id} ({job_name}) to queue '
                     f'{queue}: {stdout}')
        return job_id

    def delete_job(self, job_id: str, force: bool = False) -> None:
        """Delete a job with qdel; a job already gone is not an error."""
        cmd = ['qdel']
        if force:
            cmd += ['-W', 'force']
        cmd.append(job_id)
        rc, stdout, stderr = self._run_cmd(cmd)
        if rc != 0 and _JOB_NOT_FOUND_REGEX.search(f'{stdout}\n{stderr}'):
            logger.debug(f'PBS job {job_id} is already gone.')
            return
        subprocess_utils.handle_returncode(
            rc,
            shlex.join(cmd),
            f'Failed to delete PBS job {job_id}.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

    def cancel_jobs_by_name(self, job_name: str, force: bool = False) -> None:
        """Delete every non-finished job named ``job_name``."""
        for job in self.list_jobs_by_name(job_name):
            if job.state.upper() in TERMINAL_JOB_STATES:
                continue
            self.delete_job(job.job_id, force=force)

    def signal_job(self, job_id: str, signal: str) -> None:
        cmd = ['qsig', '-s', signal, job_id]
        rc, stdout, stderr = self._run_cmd(cmd)
        subprocess_utils.handle_returncode(
            rc,
            shlex.join(cmd),
            f'Failed to signal PBS job {job_id}.',
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)

    # ------------------------------------------------------------------
    # Login node environment
    # ------------------------------------------------------------------

    def get_env(self) -> Dict[str, str]:
        """Environment variables of the login node shell."""
        env: Dict[str, str] = {}
        try:
            rc, stdout, stderr = self._run_cmd('env')
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(
                'Failed to fetch remote env from %s; continuing '
                'with available user variables: %s', self.ssh_host, e)
            return env
        if rc != 0:
            logger.warning(
                'Failed to fetch remote env from %s (exit code %s); '
                'continuing with available user variables: %s', self.ssh_host,
                rc, stderr)
            return env
        for line in stdout.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                env[key] = value
        return env

    def get_remote_home_dir(self, timeout: Optional[int] = None) -> str:
        return self._runner.get_remote_home_dir(timeout=timeout)

    def check_fuse_enabled(self) -> bool:
        """Whether /dev/fuse exists on the login node."""
        rc, _, _ = self._run_cmd('test -e /dev/fuse')
        return rc == 0

    def check_dir_shared_fs(self, path: str) -> Optional[str]:
        """Filesystem type of a directory, lowercased, or None on failure."""
        cmd = ['stat', '-f', '-c', '%T', '--', path]
        rc, stdout, _ = self._run_cmd(cmd)
        if rc != 0:
            return None
        return stdout.strip().lower()

    def check_sshd_available(self) -> bool:
        """Whether an OpenSSH server binary exists on the login node.

        Compute nodes usually mirror the login node's base image, so this is
        a cheap early hint; the batch script checks the nodes for real.
        """
        rc, _, _ = self._run_cmd(
            'command -v sshd >/dev/null 2>&1 || test -x /usr/sbin/sshd')
        return rc == 0


# Re-exported so callers do not need to import subprocess for the
# TimeoutExpired raised by the inventory calls.
TimeoutExpired = subprocess.TimeoutExpired
