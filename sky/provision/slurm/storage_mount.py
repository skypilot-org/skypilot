"""Runtime side of the Slurm storage-mount keeper.

A Slurm cluster's sbatch script runs a keeper loop that launches every mount
spec written to ``<sky_cluster_home_dir>/.sky/storage_mounts/`` as a
persistent ``srun`` step, so the FUSE daemons a mount spawns are owned by the
allocation instead of an ephemeral step that ``proctrack/cgroup`` reaps (the
``Transport endpoint is not connected`` failure). This module is the runtime's
half of that handshake: it writes a spec, waits for every node to report
success, and collects the per-node mount logs.

File layout under the storage-mounts directory (all on the shared cluster
home, visible to every node):

* ``keeper_ready``               touched by the sbatch keeper loop at startup
* ``spec-<gen>.sh``              mount script written by the runtime (0600)
* ``started-<gen>``              keeper writes the srun client PID after launch
* ``done-<gen>/<node>``          per-node success marker
* ``failed-<gen>/<node>``        per-node failure marker (contains exit code)
* ``failed-<gen>/_step``         keeper writes srun's rc if the step exits
* ``paths``                      append-only list of expanded mount paths,
                                 read by teardown to unmount everything
* ``logs/storage_mounts-<node>-<gen>.log``  per-node mount output
"""

import os
import shlex
import tempfile
import time
from typing import Dict, List, Optional, Tuple
import uuid

from sky import exceptions
from sky import sky_logging
from sky.utils import command_runner
from sky.utils import subprocess_utils

logger = sky_logging.init_logger(__name__)

STORAGE_MOUNTS_DIR_NAME = 'storage_mounts'
KEEPER_READY_MARKER = 'keeper_ready'
PATHS_FILE_NAME = 'paths'
LOGS_DIR_NAME = 'logs'
SPEC_PREFIX = 'spec-'
STARTED_PREFIX = 'started-'
DONE_PREFIX = 'done-'
FAILED_PREFIX = 'failed-'
# Marker written by the keeper when the srun client itself exits non-zero
# before every node reported a result.
STEP_FAILED_MARKER = '_step'

# How long to wait for the keeper loop to pick up a written spec. The loop
# scans every 2 seconds, so this only expires when the keeper is dead.
KEEPER_PICKUP_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 1
# Grace after the srun client disappears before declaring the step dead:
# the failure markers are written just before/after the client exits.
STEP_EXIT_GRACE_SECONDS = 3

# Output token prefix for poll commands; lets the parser ignore SSH noise.
_POLL_PREFIX = 'SKYSM:'


def storage_mounts_dir(sky_cluster_home_dir: str) -> str:
    """Returns the storage-mounts dir under the shared cluster home."""
    return f'{sky_cluster_home_dir}/.sky/{STORAGE_MOUNTS_DIR_NAME}'


def _spec_path(mount_dir: str, generation: str) -> str:
    return f'{mount_dir}/{SPEC_PREFIX}{generation}.sh'


def _started_marker(mount_dir: str, generation: str) -> str:
    return f'{mount_dir}/{STARTED_PREFIX}{generation}'


def _done_marker(mount_dir: str, generation: str, node: str) -> str:
    return f'{mount_dir}/{DONE_PREFIX}{generation}/{node}'


def _failed_marker(mount_dir: str, generation: str, node: str) -> str:
    return f'{mount_dir}/{FAILED_PREFIX}{generation}/{node}'


def _step_failed_marker(mount_dir: str, generation: str) -> str:
    return f'{mount_dir}/{FAILED_PREFIX}{generation}/{STEP_FAILED_MARKER}'


def _node_log_path(mount_dir: str, generation: str, node: str) -> str:
    return f'{mount_dir}/{LOGS_DIR_NAME}/storage_mounts-{node}-{generation}.log'


def _sbatch_log_path(runner: command_runner.SlurmCommandRunner) -> str:
    """Best-effort sbatch log path, derived from the runner's cluster paths."""
    # sky_dir is <sky_base_dir>/.sky_clusters/<cluster_name_on_cloud> and the
    # sbatch log is <sky_base_dir>/.sky_provision/slurm-<job_id>.out.
    sky_base_dir = os.path.dirname(os.path.dirname(runner.sky_dir))
    return (f'{sky_base_dir}/.sky_provision/'
            f'slurm-{runner.job_id}.out')


def execute_storage_mounts(
    runners: List[command_runner.SlurmCommandRunner],
    mount_specs: List[Tuple[str, str, str, Optional[str]]],
    log_path: str,
) -> bool:
    """Mounts storages through the allocation-owned keeper step.

    Args:
        runners: The cluster's command runners (head first).
        mount_specs: ``(dst, mount_cmd, action_message, src_print)`` tuples
            from ``mounting_utils.resolve_mount_commands``.
        log_path: Local path of the storage_mounts.log to append node logs to.

    Returns:
        False when the cluster's sbatch script predates the keeper (the
        caller should fall back to mounting through ephemeral srun steps);
        True otherwise. Raises on mount failure.
    """
    head_runner = runners[0]
    mount_dir = storage_mounts_dir(head_runner.sky_dir)
    if not _poll_markers(head_runner, [f'{mount_dir}/{KEEPER_READY_MARKER}']):
        logger.warning(
            'The Slurm batch script of this cluster predates the '
            'storage-mount keeper; mounting through an ephemeral srun step. '
            'On Slurm clusters configured with ProctrackType=proctrack/cgroup '
            'the FUSE daemon is killed when that step exits, so the mount '
            'may not survive; tearing the cluster down (sky down) and '
            'relaunching it picks up the fix.')
        return False

    # The random suffix makes collisions impossible without having to
    # reason about launch serialization across API-server replicas.
    generation = f'{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}'
    spec_script = _build_spec_script(mount_specs, mount_dir)
    _write_spec(head_runner, mount_dir, generation, spec_script)

    _wait_for_keeper_pickup(head_runner, mount_dir, generation)
    # The PID lives in the batch script's process (the allocation's first
    # node, which is the head node the runner's srun targets), so kill -0
    # from the head runner can see it.
    _, stdout, _ = _run(head_runner,
                        f'cat {_started_marker(mount_dir, generation)}',
                        'Failed to read the storage-mount keeper PID.')
    srun_pid = stdout.strip().splitlines()[-1] if stdout.strip() else ''
    if not srun_pid.isdigit():
        raise RuntimeError(
            f'The storage-mount keeper reported an invalid srun PID '
            f'{srun_pid!r} on cluster node {head_runner.slurm_node}.')

    node_names = [runner.slurm_node for runner in runners]
    _wait_for_mounts(head_runner, mount_dir, generation, node_names, srun_pid,
                     log_path)
    _collect_node_logs(head_runner, mount_dir, generation, node_names, log_path)
    return True


def _build_spec_script(
    mount_specs: List[Tuple[str, str, str, Optional[str]]],
    mount_dir: str,
) -> str:
    """Builds the mount spec executed on every node by the keeper step."""
    paths_file = f'{mount_dir}/{PATHS_FILE_NAME}'
    lines = ['set -e']
    for dst, mount_cmd, action_message, src_print in mount_specs:
        lines.append(
            f'echo {shlex.quote(f"{action_message} {src_print} -> {dst}")}')
        # The subshell scopes each mount's EXIT-trap cleanup: several mounts
        # share this one shell, and a bare trap would be overwritten by the
        # next mount line.
        lines.append(f'( {mount_cmd} )')
        # Record the expanded mount path (without a trailing slash, which
        # fusermount does not match) so teardown (sky down / sky stop) can
        # unmount it; each node appends its own copy.
        lines.append(f'p="$(eval echo {dst})"; '
                     f'echo "${{p%/}}" >> {shlex.quote(paths_file)}')
    return '\n'.join(lines) + '\n'


def _write_spec(head_runner: command_runner.SlurmCommandRunner, mount_dir: str,
                generation: str, spec_script: str) -> None:
    """Writes the spec to shared storage atomically with 0600 permissions.

    The spec embeds storage credentials (e.g. an rclone config with access
    keys), so it is never world-readable, and the rename makes the keeper's
    glob see only fully written specs.
    """
    spec_file = _spec_path(mount_dir, generation)
    tmp_file = f'{spec_file}.tmp'
    mkdir_cmd = (f'mkdir -p '
                 f'{shlex.quote(f"{mount_dir}/{LOGS_DIR_NAME}")} '
                 f'{shlex.quote(f"{mount_dir}/{DONE_PREFIX}{generation}")} '
                 f'{shlex.quote(f"{mount_dir}/{FAILED_PREFIX}{generation}")}')
    _run(head_runner, mkdir_cmd,
         'Failed to create the Slurm storage-mounts directory.')
    # The heredoc delimiter is quoted, so the spec content is written
    # literally (no expansion by the transmitting shells).
    write_cmd = (f'umask 077 && cat > {shlex.quote(tmp_file)} '
                 f'<<\'SKY_STORAGE_MOUNT_SPEC\'\n'
                 f'{spec_script}'
                 'SKY_STORAGE_MOUNT_SPEC\n'
                 f'chmod 600 {shlex.quote(tmp_file)} && '
                 f'mv -f {shlex.quote(tmp_file)} {shlex.quote(spec_file)}')
    if not head_runner.is_command_length_over_limit(write_cmd):
        _run(head_runner, write_cmd,
             'Failed to write the Slurm storage-mount spec.')
        return
    # Over the inline limit (many / large mount commands): upload the spec
    # with rsync instead. It lands in a 0700-owned directory tree before
    # the chmod tightens the file itself.
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
        f.write(spec_script)
        local_spec = f.name
    try:
        head_runner.rsync_driver(local_spec,
                                 tmp_file,
                                 up=True,
                                 stream_logs=False)
    finally:
        os.unlink(local_spec)
    _run(
        head_runner, f'chmod 600 {shlex.quote(tmp_file)} && '
        f'mv -f {shlex.quote(tmp_file)} {shlex.quote(spec_file)}',
        'Failed to publish the Slurm storage-mount spec.')


def _wait_for_keeper_pickup(head_runner: command_runner.SlurmCommandRunner,
                            mount_dir: str, generation: str) -> None:
    deadline = time.time() + KEEPER_PICKUP_TIMEOUT_SECONDS
    started = _started_marker(mount_dir, generation)
    while not _poll_markers(head_runner, [started]):
        if time.time() >= deadline:
            raise RuntimeError(
                'The Slurm storage-mount keeper did not pick up the mount '
                f'spec within {KEEPER_PICKUP_TIMEOUT_SECONDS}s '
                f'({_spec_path(mount_dir, generation)}). The keeper step may '
                'have failed; check the Slurm batch script logs: '
                f'{_sbatch_log_path(head_runner)}')
        time.sleep(POLL_INTERVAL_SECONDS)


def _wait_for_mounts(head_runner: command_runner.SlurmCommandRunner,
                     mount_dir: str, generation: str, node_names: List[str],
                     srun_pid: str, log_path: str) -> None:
    """Waits until every node reports done, or raises on any failure."""
    done_markers = [
        _done_marker(mount_dir, generation, node) for node in node_names
    ]
    failed_markers = (
        [_step_failed_marker(mount_dir, generation)] +
        [_failed_marker(mount_dir, generation, node) for node in node_names])
    poll_cmd = _build_poll_cmd(done_markers + failed_markers, srun_pid)

    def _poll() -> Dict[str, bool]:
        # One srun per poll interval checks every marker and the srun PID.
        # Markers are checked by name (test -e), never by listing the
        # directory: NFS attribute caching can serve a stale directory list
        # long after a marker was created on another node.
        _, stdout, _ = _run_quiet(head_runner, poll_cmd)
        return _parse_poll(stdout)

    while True:
        present = _poll()
        if all(present.get(path) for path in done_markers):
            return
        failed = [path for path in failed_markers if present.get(path)]
        if failed:
            _raise_mount_failure(head_runner, mount_dir, generation, node_names,
                                 failed, log_path)
        if not present.get('pid_alive'):
            # The srun client exited; give the failure markers a moment to
            # appear (they are written right around the client's exit), then
            # fail loudly with whatever is known.
            time.sleep(STEP_EXIT_GRACE_SECONDS)
            present = _poll()
            if all(present.get(path) for path in done_markers):
                return
            failed = [path for path in failed_markers if present.get(path)]
            _raise_mount_failure(head_runner,
                                 mount_dir,
                                 generation,
                                 node_names,
                                 failed,
                                 log_path,
                                 step_dead=True)
        time.sleep(POLL_INTERVAL_SECONDS)


def _build_poll_cmd(poll_paths: List[str],
                    srun_pid: Optional[str] = None) -> str:
    """Builds the single command that checks every marker and the PID."""
    lines = [
        f'[ -e {shlex.quote(path)} ] && echo {_POLL_PREFIX}{path}'
        for path in poll_paths
    ]
    if srun_pid is not None:
        lines.append(f'kill -0 {int(srun_pid)} 2>/dev/null && '
                     f'echo {_POLL_PREFIX}pid_alive')
    # Keep the exit code 0 whether or not any marker matched, so a non-zero
    # rc always means the transport itself failed.
    lines.append('true')
    return '\n'.join(lines)


def _parse_poll(stdout: str) -> Dict[str, bool]:
    """Parses poll output into {marker path or 'pid_alive': present}."""
    result: Dict[str, bool] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(_POLL_PREFIX):
            result[line[len(_POLL_PREFIX):]] = True
    return result


def _poll_markers(head_runner: command_runner.SlurmCommandRunner,
                  paths: List[str]) -> Dict[str, bool]:
    """Checks which of the given shared-FS paths exist (one srun)."""
    _, stdout, _ = _run_quiet(head_runner, _build_poll_cmd(paths))
    return _parse_poll(stdout)


def _run_quiet(head_runner: command_runner.SlurmCommandRunner,
               cmd: str) -> Tuple[int, str, str]:
    """Runs a poll command; raises if the transport itself failed."""
    rc, stdout, stderr = head_runner.run_driver(cmd,
                                                require_outputs=True,
                                                stream_logs=False)
    if rc != 0:
        # The poll command always ends in `true`, so a non-zero rc means the
        # srun/SSH layer itself failed (e.g. the allocation is gone).
        raise RuntimeError(
            'Failed to poll the Slurm storage-mount markers on the head '
            f'node: rc={rc}, stderr={stderr.strip()!r}')
    return rc, stdout, stderr


def _raise_mount_failure(head_runner: command_runner.SlurmCommandRunner,
                         mount_dir: str,
                         generation: str,
                         node_names: List[str],
                         failed_paths: List[str],
                         log_path: str,
                         step_dead: bool = False) -> None:
    """Raises with the failing nodes' mount log tails recorded in log_path."""
    failed_nodes = []
    for node in node_names:
        if _failed_marker(mount_dir, generation, node) in failed_paths:
            failed_nodes.append(node)
    # One round trip collects every failed node's exit code and log tail:
    # each command is an SSH connection plus a step launch.
    details = _read_failure_details(head_runner, mount_dir, generation,
                                    failed_nodes)
    step_failed = _step_failed_marker(mount_dir, generation) in failed_paths

    detail_lines = []
    if step_failed:
        detail_lines.append(
            'The storage-mount keeper step exited before every node '
            'reported a result.')
    for node in failed_nodes:
        exit_code, log_tail = details.get(
            node, ('unknown', f'(no mount log found for node {node})'))
        detail_lines.append(f'=== Node {node} (exit code {exit_code}) ===')
        detail_lines.append(log_tail)
    detail = '\n'.join(detail_lines)

    log_path = os.path.expanduser(log_path)
    log_dir = os.path.dirname(log_path)
    os.makedirs(log_dir, exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(detail)
        f.write('\n')

    for node, (code, _) in details.items():
        if code.isdigit() and int(code) == exceptions.MOUNT_PATH_NON_EMPTY_CODE:
            raise RuntimeError(
                f'Mount path is non-empty on Slurm node {node}. It may be a '
                'standard unix path or may contain files from a previous '
                'task. To fix, change the mount path to an empty or '
                'non-existent path.')

    returncode = 1
    for code, _ in details.values():
        if code.isdigit():
            returncode = int(code)
            break
    raise exceptions.CommandError(
        returncode=returncode,
        command='to mount',
        error_msg=(
            'Storage mount failed on Slurm node(s) '
            f'{", ".join(failed_nodes) if failed_nodes else "unknown"}.' +
            (' The mount step exited early.' if step_dead else '')),
        detailed_reason=detail)


# Output token prefixes for the batched log-collection commands; lets the
# parser ignore SSH noise.
_NODE_LOG_PREFIX = 'SKYSMLOG:'
_FAILURE_PREFIX = 'SKYSMFAIL:'


def _read_failure_details(
        head_runner: command_runner.SlurmCommandRunner, mount_dir: str,
        generation: str, failed_nodes: List[str]) -> Dict[str, Tuple[str, str]]:
    """Reads each failed node's exit code and log tail in one round trip."""
    if not failed_nodes:
        return {}
    lines = []
    for node in failed_nodes:
        marker = shlex.quote(_failed_marker(mount_dir, generation, node))
        lines.append(f'code=$(cat {marker} 2>/dev/null || echo unknown) && '
                     f'echo {_FAILURE_PREFIX}{node}:$code')
        node_log = shlex.quote(_node_log_path(mount_dir, generation, node))
        lines.append(f'tail -n 100 {node_log} 2>/dev/null || '
                     f'echo "(no mount log found for node {node})"')
    stdout = _run_batched(head_runner, '\n'.join(lines))
    details: Dict[str, Tuple[str, str]] = {}
    current_node: Optional[str] = None
    for line in stdout.splitlines():
        if line.startswith(_FAILURE_PREFIX):
            marker = line[len(_FAILURE_PREFIX):]
            node, _, code = marker.partition(':')
            details[node] = (code, '')
            current_node = node
        elif current_node is not None:
            code, tail = details[current_node]
            details[current_node] = (code, tail + line + '\n')
    return details


def _read_node_logs(head_runner: command_runner.SlurmCommandRunner,
                    mount_dir: str, generation: str,
                    node_names: List[str]) -> Dict[str, str]:
    """Tails every node's mount log in one round trip."""
    lines = []
    for node in node_names:
        lines.append(f'echo {_NODE_LOG_PREFIX}{node}')
        node_log = shlex.quote(_node_log_path(mount_dir, generation, node))
        lines.append(f'tail -n 100 {node_log} 2>/dev/null || '
                     f'echo "(no mount log found for node {node})"')
    stdout = _run_batched(head_runner, '\n'.join(lines))
    logs: Dict[str, str] = {}
    current_node: Optional[str] = None
    for line in stdout.splitlines():
        if line.startswith(_NODE_LOG_PREFIX):
            current_node = line[len(_NODE_LOG_PREFIX):]
            logs[current_node] = ''
        elif current_node is not None:
            logs[current_node] += line + '\n'
    return logs


def _run_batched(head_runner: command_runner.SlurmCommandRunner,
                 cmd: str) -> str:
    """Runs a log-collection batch; best-effort (empty output on failure)."""
    # The batch is diagnostics: a transport failure degrades the error
    # message or the collected logs, never the mount result itself.
    try:
        rc, stdout, _ = head_runner.run_driver(cmd,
                                               require_outputs=True,
                                               stream_logs=False)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning('Failed to collect the Slurm storage-mount logs: %s', e)
        return ''
    if rc != 0:
        logger.warning('Failed to collect the Slurm storage-mount logs: '
                       f'rc={rc}')
        return ''
    return stdout


def _collect_node_logs(head_runner: command_runner.SlurmCommandRunner,
                       mount_dir: str, generation: str, node_names: List[str],
                       log_path: str) -> None:
    """Appends every node's step log to the local storage_mounts.log."""
    # log_path may be ~-relative (the backend passes self.log_dir); open()
    # does not expand it, unlike the remote shells it is also passed to.
    log_path = os.path.expanduser(log_path)
    log_dir = os.path.dirname(log_path)
    os.makedirs(log_dir, exist_ok=True)
    logs = _read_node_logs(head_runner, mount_dir, generation, node_names)
    with open(log_path, 'a', encoding='utf-8') as f:
        for node in node_names:
            f.write(f'=== storage mounts on node {node} ===\n')
            f.write(logs.get(node, f'(no mount log found for node {node})'))
            f.write('\n')


def _run(head_runner: command_runner.SlurmCommandRunner, cmd: str,
         failure_message: str) -> Tuple[int, str, str]:
    rc, stdout, stderr = head_runner.run_driver(cmd,
                                                require_outputs=True,
                                                stream_logs=False)
    subprocess_utils.handle_returncode(rc,
                                       cmd,
                                       failure_message,
                                       stderr=f'{stdout}\n{stderr}',
                                       stream_logs=False)
    return rc, stdout, stderr
