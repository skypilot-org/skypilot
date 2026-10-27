"""PBS Pro / OpenPBS instance provisioning.

A SkyPilot cluster on PBS is one long-running batch job. Its batch script
runs on the mother superior, prepares the shared cluster directory, and
starts a user-level ``sshd`` on every allocated node (``pbsdsh`` for the
other hosts).
SkyPilot then reaches each node over SSH through the login node, so every
process it starts is a descendant of a PBS task and lives inside the job.
"""

import hashlib
import os
import shlex
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import colorama

from sky import exceptions
from sky import global_user_state
from sky import sky_logging
from sky.adaptors import pbs
from sky.provision import common
from sky.provision import constants
from sky.provision.pbs import utils as pbs_utils
from sky.skylet import constants as skylet_constants
from sky.utils import command_runner
from sky.utils import common_utils
from sky.utils import env_options
from sky.utils import rich_utils
from sky.utils import status_lib
from sky.utils import subprocess_utils
from sky.utils import timeline
from sky.utils import ux_utils

logger = sky_logging.init_logger(__name__)

PROVISION_SCRIPTS_DIRECTORY_NAME = '.sky_provision'

POLL_INTERVAL_SECONDS = 2
# qdel sends TERM, waits the server's kill_delay (10s by default) and then
# KILL; give the job a little longer than that to leave the queue.
_JOB_TERMINATION_TIMEOUT_SECONDS = 60
# How long the node sshds get to come up once the job runs. Pulls nothing
# from the network, so this is generous.
_NODE_READY_TIMEOUT_SECONDS = 10 * 60
# How long to wait for SSH through the login node to reach every node.
_NODE_SSH_TIMEOUT_SECONDS = 5 * 60

# Lowest sshd port the node agent tries; the exact port is derived from the
# cluster name so that re-launches tend to land on the same port.
_SSHD_PORT_RANGE_START = 20000
_SSHD_PORT_RANGE_SIZE = 40000

# qsub options SkyPilot sets itself; user values for them are dropped.
_QSUB_PROTECTED_OPTIONS = frozenset({
    'N',
    'q',
    'o',
    'e',
    'j',
    'k',
    'r',
    'I',
    'J',
    'h',
    'S',
    'z',
    'select',
    'place',
})
# Keys of qsub_options that map to `-l <key>=<value>` instead of `-<key>`.
_QSUB_RESOURCE_KEYS = frozenset({'walltime'})

_TERMINAL_JOB_STATES = pbs.TERMINAL_JOB_STATES


def _pbs_log_path(base_dir: str, job_id: str) -> str:
    """The batch script's own log; it redirects its output here."""
    return f'{base_dir}/{PROVISION_SCRIPTS_DIRECTORY_NAME}/pbs-{job_id}.out'


def _pbs_spool_path(base_dir: str, cluster_name_on_cloud: str) -> str:
    """Where PBS copies the job's spooled output at the end of the job.

    Kept apart from the live log, which the script writes directly: the
    spool copy would otherwise overwrite it with an empty file.
    """
    return (f'{base_dir}/{PROVISION_SCRIPTS_DIRECTORY_NAME}/'
            f'{cluster_name_on_cloud}.spool')


def _provision_script_path(base_dir: str, cluster_name_on_cloud: str) -> str:
    # Under the shared base dir rather than /tmp: there can be several login
    # nodes, and consecutive SSH connections may land on different ones.
    return os.path.join(base_dir, PROVISION_SCRIPTS_DIRECTORY_NAME,
                        f'{cluster_name_on_cloud}.sh')


def _skypilot_runtime_dir(tmpdir: Optional[str],
                          cluster_name_on_cloud: str) -> str:
    """Node-local directory for the SkyPilot runtime (venv, sqlite)."""
    tmp = tmpdir if tmpdir is not None else '/tmp'
    return os.path.join(tmp, cluster_name_on_cloud)


def _preferred_sshd_port(cluster_name_on_cloud: str) -> int:
    digest = hashlib.sha256(cluster_name_on_cloud.encode()).hexdigest()
    return _SSHD_PORT_RANGE_START + int(digest[:8], 16) % _SSHD_PORT_RANGE_SIZE


def _validate_directive_value(key: str, value: str) -> None:
    # Defense in depth: the schema rejects newlines, but a newline here
    # would start a new line of the batch script.
    if '\n' in key or '\n' in value:
        raise ValueError(f'Newline characters are not allowed in qsub options: '
                         f'{key!r}={value!r}')


def _build_custom_qsub_directives(qsub_options: Dict[str, Any]) -> List[str]:
    """``#PBS`` lines for user-supplied ``qsub_options``.

    Keys are qsub flags without the dash (``A``, ``P``, ``W``, ``l``, ...).
    A string or number value gives ``#PBS -<key> <value>``; ``true`` gives a
    bare ``#PBS -<key>``; a list gives one line per element; ``null`` and
    ``false`` give nothing. ``walltime`` is a shorthand for
    ``-l walltime=...``. ``select`` and ``place`` entries of ``l`` and the
    flags SkyPilot manages are dropped with a warning.
    """
    lines: List[str] = []
    if not qsub_options:
        return lines
    normalized = {str(k).lstrip('-'): v for k, v in qsub_options.items()}
    conflicting = sorted(set(normalized) & _QSUB_PROTECTED_OPTIONS)
    if conflicting:
        logger.warning(f'{colorama.Fore.YELLOW}Ignoring protected qsub options '
                       f'managed by SkyPilot: {conflicting}. Remove them from '
                       f'pbs.qsub_options in ~/.sky/config.yaml.'
                       f'{colorama.Style.RESET_ALL}')
        for key in conflicting:
            del normalized[key]
    for key in sorted(normalized):
        value = normalized[key]
        if value is None or value is False:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is None or item is False:
                continue
            if key in _QSUB_RESOURCE_KEYS:
                str_value = str(item)
                _validate_directive_value(key, str_value)
                if key == 'walltime':
                    pbs_utils.validate_walltime(str_value)
                lines.append(f'#PBS -l {key}={str_value}')
                continue
            if item is True:
                _validate_directive_value(key, '')
                lines.append(f'#PBS -{key}')
                continue
            str_value = str(item)
            _validate_directive_value(key, str_value)
            if key == 'l':
                resource = str_value.split('=', 1)[0].strip()
                if resource in ('select', 'place'):
                    logger.warning(
                        f'{colorama.Fore.YELLOW}Ignoring qsub resource '
                        f'{str_value!r}: select and place are managed by '
                        f'SkyPilot.{colorama.Style.RESET_ALL}')
                    continue
                if resource == 'walltime':
                    pbs_utils.validate_walltime(str_value.split('=', 1)[1])
            lines.append(f'#PBS -{key} {str_value}')
    return lines


def _user_supplied_walltime(qsub_options: Dict[str, Any]) -> bool:
    if qsub_options.get('walltime') not in (None, False):
        return True
    resources = qsub_options.get('l')
    if resources is None or resources is False:
        return False
    if not isinstance(resources, list):
        resources = [resources]
    return any(str(r).split('=', 1)[0].strip() == 'walltime' for r in resources)


def _compute_walltime_directive(qsub_options: Dict[str, Any],
                                queue_info: Optional[pbs.PbsQueue],
                                queue: str) -> Optional[str]:
    """The auto-generated ``#PBS -l walltime=...`` line, if any.

    A user value wins. Otherwise the queue's ``resources_max.walltime`` is
    requested, so the allocation lives as long as the queue permits. With
    no limit, the directive is omitted and the queue or server default
    applies (unlimited when there is none).
    """
    if _user_supplied_walltime(qsub_options):
        return None
    if queue_info is not None and queue_info.max_walltime:
        return f'#PBS -l walltime={queue_info.max_walltime}'
    if queue_info is not None and queue_info.default_walltime:
        return None
    logger.debug(f'Queue {queue!r} sets no walltime limit or default; '
                 'submitting without walltime.')
    return None


def _build_qsub_directives(qsub_options: Dict[str, Any],
                           queue_info: Optional[pbs.PbsQueue],
                           queue: str) -> str:
    """Auto-generated plus user-supplied ``#PBS`` lines, newline-joined."""
    lines = []
    walltime = _compute_walltime_directive(qsub_options, queue_info, queue)
    if walltime is not None:
        lines.append(walltime)
    lines.extend(_build_custom_qsub_directives(qsub_options))
    return '\n'.join(lines)


def _wait_for_job_nodes(
    client: 'pbs.PbsClient',
    job_id: str,
    timeout: int,
    queue: str,
    on_pending: Callable[[str, Optional[str], Optional[int]], None],
) -> pbs.JobInfo:
    """Wait until the job has execution hosts.

    Args:
        timeout: Maximum seconds to wait; negative waits indefinitely.
        on_pending: Called with (state, reason, pending_count) while the
            job is queued.
    """
    start_time = time.time()
    last_state = None
    while timeout < 0 or time.time() - start_time < timeout:
        job = client.get_job(job_id)
        if job is None:
            raise RuntimeError(f'PBS job {job_id} not found. It may have been '
                               'deleted or rejected.')
        state = job.state.upper()
        if state != last_state:
            logger.debug(f'Job {job_id} state: {state}')
            last_state = state
        if state in _TERMINAL_JOB_STATES:
            raise RuntimeError(f'PBS job {job_id} terminated with state '
                               f'{state} (exit status {job.exit_status}) '
                               'before nodes were allocated.')
        if job.exec_hosts and state in pbs.RUNNING_JOB_STATES:
            logger.debug(f'Job {job_id} has nodes allocated: {job.exec_hosts}')
            return job
        if state in pbs.PENDING_JOB_STATES:
            try:
                pending_count: Optional[int] = client.get_pending_job_count(
                    queue, exclude_job_id=job_id)
                if pending_count is not None and pending_count < 0:
                    pending_count = None
                on_pending(state, job.comment, pending_count)
            except Exception as e:  # pylint: disable=broad-except
                logger.debug(f'Failed to get pending status for job '
                             f'{job_id}: {e}')
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f'PBS job {job_id} did not get nodes allocated within '
                       f'{timeout} seconds. Last state: {last_state}')


def _record_pending_reason(cluster_name: str, reason: Optional[str],
                           queue: Optional[str]) -> None:
    """Persist the scheduler comment as a launch-progress cluster event."""
    if not reason:
        return
    detail = f'pending: {reason}'
    if queue:
        detail += f'; queue: {queue}'
    try:
        global_user_state.add_cluster_event(
            cluster_name,
            new_status=None,
            reason=f'Launching ({detail})',
            event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
            nop_if_duplicate=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to record pending reason for {cluster_name}: '
                     f'{e}')


def _record_allocation(cluster_name: str, pbs_cluster: str,
                       job_id: str) -> None:
    """Persist which PBS job backs this cluster; survives teardown."""
    try:
        global_user_state.add_cluster_event(
            cluster_name,
            new_status=None,
            reason=f'PBS job {job_id} on {pbs_cluster}',
            event_type=global_user_state.ClusterEventType.DEBUG,
            nop_if_duplicate=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to record the PBS job of {cluster_name}: {e}')


def _record_nodes_allocated(cluster_name: str, pbs_cluster: str,
                            job_id: str) -> None:
    try:
        global_user_state.add_cluster_event(
            cluster_name,
            new_status=None,
            reason=(f'Launching (nodes allocated; PBS job {job_id} '
                    f'on {pbs_cluster})'),
            event_type=global_user_state.ClusterEventType.LAUNCH_PROGRESS,
            nop_if_duplicate=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        logger.debug(f'Failed to record the node allocation of '
                     f'{cluster_name}: {e}')


def _make_pending_callback(
    cluster_name: str,
    queue: Optional[str] = None,
) -> Callable[[str, Optional[str], Optional[int]], None]:
    """Spinner refresh plus event recording while the job is queued."""
    last_status_msg: Optional[str] = None
    last_recorded_reason: Optional[str] = None

    def _on_pending(state: str, reason: Optional[str],
                    pending_count: Optional[int]) -> None:
        nonlocal last_status_msg, last_recorded_reason
        del state  # unused
        parts = []
        if reason:
            parts.append(f'pending: {reason}')
        if pending_count is not None and pending_count > 0:
            word = 'other' if pending_count == 1 else 'others'
            parts.append(f'{pending_count} {word} queued')
        msg = f'Launching ({", ".join(parts)})' if parts else 'Launching'
        status_msg = ux_utils.spinner_message(msg, cluster_name=cluster_name)
        if status_msg != last_status_msg:
            rich_utils.force_update_status(status_msg)
            last_status_msg = status_msg
        if reason != last_recorded_reason:
            _record_pending_reason(cluster_name, reason, queue)
            last_recorded_reason = reason

    return _on_pending


def _run_on_login_node(
        login_node_runner: command_runner.CommandRunner,
        cmd: Union[str, List[str]],
        failure_message: str,
        tolerate_returncodes: Tuple[int, ...] = (),
) -> Tuple[int, str]:
    """Run a command on the login node; raise unless its exit is tolerated."""
    rc, stdout, stderr = login_node_runner.run(cmd,
                                               require_outputs=True,
                                               stream_logs=False)
    if rc not in tolerate_returncodes:
        subprocess_utils.handle_returncode(
            rc,
            cmd if isinstance(cmd, str) else shlex.join(cmd),
            failure_message,
            stderr=f'{stdout}\n{stderr}',
            stream_logs=False)
    return rc, stdout


def _run_on_login_node_best_effort(
    login_node_runner: command_runner.CommandRunner,
    cmd: Union[str, List[str]],
    failure_message: str,
) -> None:
    try:
        _run_on_login_node(login_node_runner, cmd, failure_message)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'{failure_message} '
                       f'{common_utils.format_exception(e, use_bracket=True)}')
        logger.debug('Full exception details:', exc_info=True)


def _make_pbs_client(provider_config: Dict[str, Any]) -> 'pbs.PbsClient':
    ssh_config = provider_config['ssh']
    return pbs.PbsClient(
        ssh_config['hostname'],
        int(ssh_config['port']),
        ssh_config['user'],
        ssh_config.get('private_key'),
        ssh_proxy_command=ssh_config.get('proxycommand'),
        ssh_proxy_jump=ssh_config.get('proxyjump'),
        identities_only=ssh_config.get('identities_only', False),
        ssh_control_path=ssh_config.get('control_path'),
    )


def _make_login_node_runner(
        provider_config: Dict[str, Any]) -> command_runner.SSHCommandRunner:
    return pbs_utils.make_login_node_runner(provider_config['ssh'])


def _make_client_and_login_runner(
    provider_config: Dict[str, Any],
    inside_pbs_cluster: bool,
) -> Tuple['pbs.PbsClient', command_runner.CommandRunner]:
    """The client and runner for control commands.

    On an allocated node (autodown from skylet) the PBS client commands and
    the shared filesystem are local, and the login node's SSH key is not
    available, so everything runs locally.
    """
    if inside_pbs_cluster:
        logger.debug('Running inside a PBS job, using local execution')
        return (pbs.PbsClient(is_inside_pbs_cluster=True),
                command_runner.LocalProcessCommandRunner())
    return (_make_pbs_client(provider_config),
            _make_login_node_runner(provider_config))


def _resolve_sky_base_dir(client: 'pbs.PbsClient',
                          provider_config: Dict[str, Any]) -> str:
    sky_base_dir = provider_config.get('sky_base_dir')
    if sky_base_dir is not None:
        if not isinstance(sky_base_dir, str) or not os.path.isabs(sky_base_dir):
            raise RuntimeError('PBS sky_base_dir must be an absolute path, '
                               f'got {sky_base_dir!r}.')
        return sky_base_dir
    pbs_cluster = pbs_utils.get_pbs_cluster_from_config(provider_config)
    return pbs_utils.resolve_sky_base_dir(pbs_cluster, client)


def _inside_cluster_runtime_dir() -> str:
    """The runtime directory of the node this code runs on (skylet)."""
    runtime_dir = os.environ.get(skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY)
    if runtime_dir is None:
        raise RuntimeError(
            f'{skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY} is not set; '
            'cannot resolve the SkyPilot runtime directory from inside the '
            'cluster.')
    return runtime_dir


def _node_agent_script(cluster_name_on_cloud: str, sky_cluster_home_dir: str,
                       tmpdir: Optional[str]) -> str:
    """The script ``pbsdsh`` runs on every allocated node.

    It picks the node-local SkyPilot runtime directory (under the
    configured ``tmpdir``, else the job's ``$TMPDIR``, else /tmp), starts a
    user-level sshd on a free port, publishes hostname, port and runtime
    directory to the shared cluster directory, and then waits. On job
    teardown (TERM from the MoM) it stops sshd, kills every process the
    user owns that carries this cluster's ``SKY_RUNTIME_DIR``, and removes
    the runtime directory.

    Sessions get ``PBS_JOBID`` and friends through sshd's ``SetEnv``: sites
    whose ``/etc/ssh/sshrc`` attaches sessions to their job (``pbs_attach``)
    need it, and so does a job's ``CUDA_VISIBLE_DEVICES``.
    """
    sshd_dir = pbs_utils.sshd_dir(sky_cluster_home_dir)
    host_key = f'{sshd_dir}/{pbs_utils.SSHD_HOST_KEY_FILENAME}'
    authorized_keys = f'{sshd_dir}/{pbs_utils.AUTHORIZED_KEYS_FILENAME}'
    port_file_prefix = f'{sshd_dir}/{pbs_utils.NODE_PORT_FILE_PREFIX}'
    port_file_suffix = pbs_utils.NODE_PORT_FILE_SUFFIX
    preferred_port = _preferred_sshd_port(cluster_name_on_cloud)
    runtime_env_var = skylet_constants.SKY_RUNTIME_DIR_ENV_VAR_KEY
    # The configured tmpdir is expanded by the node's shell ($USER, ...).
    tmp_base = f'"{tmpdir}"' if tmpdir else '"${TMPDIR:-/tmp}"'
    # pylint: disable=line-too-long
    return f"""\
#!/bin/bash
# SkyPilot PBS node agent for cluster {cluster_name_on_cloud}.
rank="${{1:-${{PBS_NODENUM:-0}}}}"
tmp_base={tmp_base}
runtime_dir="$tmp_base/{cluster_name_on_cloud}"
port_file="{port_file_prefix}${{rank}}{port_file_suffix}"
sshd_log="{sshd_dir}/sshd-rank${{rank}}.log"
SSHD_PID=
cleanup() {{
    trap '' TERM INT EXIT
    echo "[node-agent] rank $rank on $(hostname): cleaning up"
    if [ -n "$SSHD_PID" ]; then kill "$SSHD_PID" 2>/dev/null || true; fi
    # Stop every process of this cluster still running on the node (skylet,
    # job drivers, user tasks); they all carry the runtime dir in their env.
    for sig in TERM KILL; do
        for pid in $(pgrep -U "$(id -u)"); do
            [ "$pid" = "$$" ] && continue
            if tr '\\0' '\\n' 2>/dev/null < "/proc/$pid/environ" | grep -qx "{runtime_env_var}=$runtime_dir"; then
                kill -$sig "$pid" 2>/dev/null || true
            fi
        done
        [ "$sig" = TERM ] && sleep 1
    done
    rm -rf -- "$runtime_dir"
    rm -f -- "$port_file"
    exit 0
}}
trap cleanup TERM INT EXIT
mkdir -p "$runtime_dir/.sky"
printf '%s\\n' "$PBS_JOBID" > "$runtime_dir/.sky/{pbs_utils.PBS_MARKER_FILE}"
SSHD=$(command -v sshd 2>/dev/null || true)
[ -x "$SSHD" ] || SSHD=/usr/sbin/sshd
if [ ! -x "$SSHD" ]; then
    echo "[node-agent] ERROR: no sshd binary found on $(hostname); SkyPilot needs OpenSSH server on the compute nodes." >&2
    exit 1
fi
sftp_server=$(ls /usr/libexec/openssh/sftp-server /usr/lib/openssh/sftp-server /usr/lib/ssh/sftp-server 2>/dev/null | head -1)
# The session environment: the job id for site hooks that attach sessions
# to their job, the job's GPUs and scratch space, and the runtime dir.
session_env="PBS_JOBID=$PBS_JOBID PBS_NODENUM=$rank {runtime_env_var}=$runtime_dir"
[ -n "$TMPDIR" ] && session_env="$session_env TMPDIR=$TMPDIR"
[ -n "$CUDA_VISIBLE_DEVICES" ] && session_env="$session_env CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
setenv_opts=(-o "SetEnv=$session_env")
port={preferred_port}
for attempt in $(seq 1 30); do
    "$SSHD" -D -e -p "$port" -f /dev/null -h {shlex.quote(host_key)} \\
        -o PidFile=none -o UsePAM=no -o StrictModes=no \\
        -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \\
        -o PubkeyAuthentication=yes -o AuthorizedKeysFile={shlex.quote(authorized_keys)} \\
        -o X11Forwarding=no -o UseDNS=no -o AllowTcpForwarding=yes \\
        -o MaxStartups=100 -o MaxSessions=100 \\
        -o "Subsystem=sftp ${{sftp_server:-internal-sftp}}" \\
        -o AcceptEnv={skylet_constants.SKY_CLUSTER_NAME_ENV_VAR_KEY} "${{setenv_opts[@]}}" >> "$sshd_log" 2>&1 &
    SSHD_PID=$!
    sleep 2
    if kill -0 "$SSHD_PID" 2>/dev/null; then
        break
    fi
    SSHD_PID=
    if [ "${{#setenv_opts[@]}}" -gt 0 ] && grep -q 'SetEnv' "$sshd_log"; then
        # sshd predates SetEnv (OpenSSH 7.8); the runner exports the
        # runtime dir itself, only site hooks lose the job id.
        echo "[node-agent] sshd does not support SetEnv; sessions will not carry PBS_JOBID" >&2
        setenv_opts=()
        continue
    fi
    port=$(({_SSHD_PORT_RANGE_START} + RANDOM % {_SSHD_PORT_RANGE_SIZE}))
done
if [ -z "$SSHD_PID" ]; then
    echo "[node-agent] ERROR: failed to start sshd on $(hostname); see $sshd_log" >&2
    exit 1
fi
printf '%s %s %s\\n' "$(hostname)" "$port" "$runtime_dir" > "$port_file.tmp" && mv -f "$port_file.tmp" "$port_file"
echo "[node-agent] rank $rank: sshd listening on $(hostname):$port, runtime dir $runtime_dir"
wait "$SSHD_PID"
"""
    # pylint: enable=line-too-long


def _build_provision_script(
    cluster_name_on_cloud: str,
    num_nodes: int,
    cpus: int,
    mem_in_mb: int,
    gpu_resource: str,
    accelerator_count: int,
    accelerator_type: Optional[str],
    gpu_type_resource: Optional[str],
    directives: str,
    sky_base_dir: str,
    sky_cluster_home_dir: str,
    ssh_public_key: str,
    tmpdir: Optional[str] = None,
    select_resources: Optional[Dict[str, Any]] = None,
    place: Optional[str] = None,
) -> str:
    """The qsub batch script that is the SkyPilot cluster."""
    select = f'select={num_nodes}:ncpus={cpus}'
    if mem_in_mb > 0:
        select += f':mem={mem_in_mb}mb'
    if accelerator_count > 0:
        select += f':{gpu_resource}={accelerator_count}'
        if accelerator_type is not None and gpu_type_resource is not None:
            select += f':{gpu_type_resource}={accelerator_type}'
    for name, value in sorted((select_resources or {}).items()):
        _validate_directive_value(str(name), str(value))
        if ':' in str(name) or '=' in str(name):
            raise ValueError(f'Invalid select resource name {name!r}.')
        select += f':{name}={value}'
    if place is None:
        place = 'scatter'
    _validate_directive_value('place', place)
    sshd_dir = pbs_utils.sshd_dir(sky_cluster_home_dir)
    host_key = f'{sshd_dir}/{pbs_utils.SSHD_HOST_KEY_FILENAME}'
    cluster_key = f'{sshd_dir}/{pbs_utils.CLUSTER_KEY_FILENAME}'
    authorized_keys = f'{sshd_dir}/{pbs_utils.AUTHORIZED_KEYS_FILENAME}'
    node_agent = f'{sshd_dir}/{pbs_utils.NODE_AGENT_SCRIPT_FILENAME}'
    node_list = f'{sky_cluster_home_dir}/{pbs_utils.NODE_LIST_FILENAME}'
    log_dir = f'{sky_base_dir}/{PROVISION_SCRIPTS_DIRECTORY_NAME}'
    node_agent_script = _node_agent_script(cluster_name_on_cloud,
                                           sky_cluster_home_dir, tmpdir)
    # pylint: disable=line-too-long
    return f"""\
#!/bin/bash
#PBS -N {cluster_name_on_cloud}
#PBS -l {select}
#PBS -l place={place}
#PBS -o {_pbs_spool_path(sky_base_dir, cluster_name_on_cloud)}
#PBS -j oe
#PBS -r n
{directives}

# PBS only delivers the spooled output at the end of the job; write the log
# directly so that it can be read while the allocation runs.
mkdir -p {shlex.quote(log_dir)}
exec >> {shlex.quote(f'{log_dir}/pbs-')}"$PBS_JOBID.out" 2>&1
echo "[sky] PBS job $PBS_JOBID for cluster {cluster_name_on_cloud} started on $(hostname)"

cleanup() {{
    trap '' TERM INT EXIT
    echo "[sky] Cleaning up shared cluster directory"
    rm -rf -- {shlex.quote(sky_cluster_home_dir)}
    exit 0
}}
trap cleanup EXIT
trap 'exit 0' TERM INT

mkdir -p {shlex.quote(sky_cluster_home_dir)}/sky_logs {shlex.quote(sky_cluster_home_dir)}/sky_workdir {shlex.quote(sky_cluster_home_dir)}/.sky
mkdir -p {shlex.quote(sshd_dir)}
chmod 700 {shlex.quote(sshd_dir)}
touch {shlex.quote(sky_cluster_home_dir)}/.hushlogin
# Hosts of the allocation, in exec_host order; the first is this node.
awk '!seen[$0]++' "$PBS_NODEFILE" > {shlex.quote(node_list)}
# Host key of the user-level sshd and the key the head node uses to reach
# the workers; SkyPilot's own public key is authorized alongside.
[ -f {shlex.quote(host_key)} ] || ssh-keygen -q -t ed25519 -N '' -f {shlex.quote(host_key)}
[ -f {shlex.quote(cluster_key)} ] || ssh-keygen -q -t ed25519 -N '' -f {shlex.quote(cluster_key)}
{{ cat {shlex.quote(cluster_key)}.pub; printf '%s\\n' {shlex.quote(ssh_public_key)}; }} > {shlex.quote(authorized_keys)}
chmod 600 {shlex.quote(authorized_keys)}
cat > {shlex.quote(node_agent)} <<'SKYPILOT_NODE_AGENT'
{node_agent_script}
SKYPILOT_NODE_AGENT
chmod 700 {shlex.quote(node_agent)}

# One node agent per host. Rank 0 runs here, as the job's own task; every
# other host gets its agent through "pbsdsh -n <chunk>" (not all PBS builds
# have "-u" for one task per host). The allocation lives as long as the
# agents do.
AGENT_PIDS=()
/bin/bash {shlex.quote(node_agent)} 0 &
AGENT_PIDS+=("$!")
declare -A seen_host
rank=0
chunk=0
while read -r host; do
    if [ -z "${{seen_host[$host]}}" ]; then
        seen_host[$host]=1
        if [ "$rank" -gt 0 ]; then
            pbsdsh -n "$chunk" -- /bin/bash {shlex.quote(node_agent)} "$rank" &
            AGENT_PIDS+=("$!")
        fi
        rank=$((rank + 1))
    fi
    chunk=$((chunk + 1))
done < "$PBS_NODEFILE"
wait "${{AGENT_PIDS[@]}}"
echo "[sky] Node agents exited"
"""
    # pylint: enable=line-too-long


def _wait_for_job_ready(
    login_node_runner: command_runner.CommandRunner,
    client: 'pbs.PbsClient',
    job_id: str,
    sky_cluster_home_dir: str,
    num_nodes: int,
    pbs_log: str,
) -> Dict[int, pbs_utils.NodeEndpoint]:
    """Wait until every node published its sshd port.

    Fails if the job leaves the queue first, or if the agents take longer
    than _NODE_READY_TIMEOUT_SECONDS.
    """
    list_cmd = pbs_utils.list_node_port_files_command(sky_cluster_home_dir)
    deadline = time.time() + _NODE_READY_TIMEOUT_SECONDS
    while True:
        _, stdout = _run_on_login_node(
            login_node_runner, list_cmd,
            'Failed to inspect PBS node readiness on the login node.')
        endpoints = pbs_utils.parse_node_port_files(stdout)
        if all(rank in endpoints for rank in range(num_nodes)):
            return endpoints
        job_state = client.get_job_state(job_id)
        if job_state is None or job_state.upper() in _TERMINAL_JOB_STATES:
            raise RuntimeError(f'PBS job {job_id} exited ({job_state}) before '
                               'its nodes were ready. See the job log for '
                               f'details: {pbs_log}')
        if time.time() > deadline:
            raise RuntimeError(
                f'PBS job {job_id} did not start sshd on all {num_nodes} '
                f'node(s) within {_NODE_READY_TIMEOUT_SECONDS}s '
                f'({len(endpoints)} ready). See the job log for details: '
                f'{pbs_log}')
        time.sleep(1)


def _wait_for_node_ssh(runners: Sequence[command_runner.CommandRunner],
                       pbs_log: str) -> None:
    """Wait until SSH through the login node reaches every node's sshd."""
    deadline = time.time() + _NODE_SSH_TIMEOUT_SECONDS

    def _wait(runner: command_runner.CommandRunner) -> None:
        last_error = ''
        while time.time() < deadline:
            rc, stdout, stderr = runner.run('true',
                                            require_outputs=True,
                                            stream_logs=False,
                                            connect_timeout=15)
            if rc == 0:
                return
            last_error = f'{stdout}\n{stderr}'.strip()
            time.sleep(2)
        raise RuntimeError(
            f'Cannot reach the SkyPilot sshd on node {runner.node_id} within '
            f'{_NODE_SSH_TIMEOUT_SECONDS}s. Last error: {last_error}. See the '
            f'job log for details: {pbs_log}')

    subprocess_utils.run_in_parallel(_wait, list(runners))


def _cluster_info_from_job(
    provider_config: Dict[str, Any],
    cluster_name_on_cloud: str,
    job: pbs.JobInfo,
    endpoints: Dict[int, pbs_utils.NodeEndpoint],
) -> common.ClusterInfo:
    ssh_config = provider_config['ssh']
    instances = {}
    for rank, node in enumerate(job.exec_hosts):
        endpoint = endpoints[rank]
        iid = pbs_utils.instance_id(job.job_id, node)
        instances[iid] = [
            common.InstanceInfo(
                instance_id=iid,
                # The hostname, not an address: it is what the login node
                # and the nodes resolve, and some sites' nodes have no
                # usable IPv4 address.
                internal_ip=node,
                # SkyPilot reaches the node through the login node; the
                # port is that of the node's own sshd.
                external_ip=ssh_config['hostname'],
                ssh_port=endpoint.port,
                tags={
                    constants.TAG_SKYPILOT_CLUSTER_NAME: cluster_name_on_cloud,
                    'job_id': job.job_id,
                    'node': node,
                    'sshd_port': str(endpoint.port),
                    'runtime_dir': endpoint.runtime_dir,
                },
                node_name=iid,
            )
        ]
    return common.ClusterInfo(
        instances=instances,
        head_instance_id=pbs_utils.instance_id(job.job_id, job.exec_hosts[0]),
        provider_name='pbs',
        provider_config=provider_config,
    )


@timeline.event
def _create_virtual_instance(
        region: str, cluster_name: str, cluster_name_on_cloud: str,
        config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Submit (or reuse) the batch job that backs the cluster."""
    provider_config = config.provider_config
    queue = pbs_utils.get_queue_from_config(provider_config)
    pbs_cluster = pbs_utils.get_pbs_cluster_from_config(provider_config)
    client = _make_pbs_client(provider_config)
    login_node_runner = _make_login_node_runner(provider_config)
    num_nodes = config.count

    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    sky_cluster_home_dir = pbs_utils.sky_cluster_home_dir(
        sky_base_dir, cluster_name_on_cloud)
    provision_timeout: int = provider_config['provision_timeout']
    on_pending = _make_pending_callback(cluster_name, queue)

    def _finish(job_id: str, created: bool) -> common.ProvisionRecord:
        _record_allocation(cluster_name, pbs_cluster, job_id)
        job = _wait_for_job_nodes(client, job_id, provision_timeout, queue,
                                  on_pending)
        _record_nodes_allocated(cluster_name, pbs_cluster, job_id)
        rich_utils.force_update_status(
            ux_utils.spinner_message('Launching', cluster_name=cluster_name))
        pbs_log = _pbs_log_path(sky_base_dir, job_id)
        if env_options.Options.SHOW_DEBUG_INFO.get():

            def _stream_logs():
                login_node_runner.run(['tail', '-n', '10', '-f', '--', pbs_log],
                                      require_outputs=False,
                                      stream_logs=True)

            threading.Thread(target=_stream_logs, daemon=True).start()
        try:
            endpoints = _wait_for_job_ready(login_node_runner, client, job_id,
                                            sky_cluster_home_dir, num_nodes,
                                            pbs_log)
            cluster_info = _cluster_info_from_job(provider_config,
                                                  cluster_name_on_cloud, job,
                                                  endpoints)
            runners = get_command_runners(
                cluster_info,
                ssh_private_key=config.authentication_config.get(
                    'ssh_private_key'))
            _wait_for_node_ssh(runners, pbs_log)
        except (RuntimeError, exceptions.CommandError) as e:
            _, stdout, _ = login_node_runner.run(['cat', '--', pbs_log],
                                                 require_outputs=True,
                                                 stream_logs=False)
            if stdout:
                logger.error(f'=== PBS job log ({pbs_log}) ===\n{stdout}'
                             f'=== End of PBS job log ===')
            raise e
        instance_ids = [
            pbs_utils.instance_id(job_id, node) for node in job.exec_hosts
        ]
        return common.ProvisionRecord(
            provider_name='pbs',
            region=region,
            zone=queue,
            cluster_name=cluster_name_on_cloud,
            head_instance_id=instance_ids[0],
            resumed_instance_ids=[],
            created_instance_ids=instance_ids if created else [])

    existing_jobs = client.query_jobs(
        cluster_name_on_cloud,
        list(pbs.PENDING_JOB_STATES) + list(pbs.RUNNING_JOB_STATES))
    if existing_jobs:
        if len(existing_jobs) > 1:
            raise RuntimeError(
                f'Multiple PBS jobs found with name {cluster_name_on_cloud}: '
                f'{existing_jobs}')
        job_id = existing_jobs[0]
        logger.debug(f'PBS job named {cluster_name_on_cloud} already exists '
                     f'(JOBID: {job_id})')
        return _finish(job_id, created=False)

    resources = config.node_config
    accelerator_type = resources.get('accelerator_type')
    if accelerator_type in ('None', ''):
        accelerator_type = None
    accelerator_count_raw = resources.get('accelerator_count')
    try:
        accelerator_count = int(
            accelerator_count_raw) if accelerator_count_raw is not None else 0
    except (TypeError, ValueError):
        logger.warning(
            f'Invalid accelerator_count value: {accelerator_count_raw!r}. '
            'Defaulting to 0 (no accelerators).')
        accelerator_count = 0
    gpu_type_resource = resources.get('gpu_type_resource')
    gpu_resource = resources.get('gpu_resource', pbs_utils.DEFAULT_GPU_RESOURCE)
    ssh_public_key = provider_config.get('ssh_public_key_content', '')
    if not ssh_public_key:
        raise RuntimeError('The cluster config carries no SSH public key for '
                           'the PBS allocation.')

    queue_info = pbs_utils.get_queue_info(pbs_cluster, queue)
    qsub_options = resources.get('qsub_options', {}) or {}
    directives = _build_qsub_directives(qsub_options, queue_info, queue)
    # Memory in MB so fractional GB values work; PBS's mb is MiB.
    mem_in_mb = int(float(resources.get('memory', 0)) * 1024)
    cpus = max(1, int(float(resources['cpus']) + 0.999999))
    provision_script = _build_provision_script(
        cluster_name_on_cloud=cluster_name_on_cloud,
        num_nodes=num_nodes,
        cpus=cpus,
        mem_in_mb=mem_in_mb,
        gpu_resource=gpu_resource,
        accelerator_count=accelerator_count,
        accelerator_type=accelerator_type,
        gpu_type_resource=gpu_type_resource,
        directives=directives,
        sky_base_dir=sky_base_dir,
        sky_cluster_home_dir=sky_cluster_home_dir,
        ssh_public_key=ssh_public_key,
        tmpdir=provider_config.get('tmpdir'),
        select_resources=resources.get('select_resources'),
        place=resources.get('place'),
    )

    provision_script_path = _provision_script_path(sky_base_dir,
                                                   cluster_name_on_cloud)
    provision_scripts_dir = os.path.dirname(provision_script_path)
    _run_on_login_node(
        login_node_runner, ['mkdir', '-p', '--', provision_scripts_dir],
        'Failed to create the provision scripts directory on the login node.')
    # A previous allocation of the same name may have left state behind.
    _run_on_login_node_best_effort(
        login_node_runner, ['rm', '-rf', '--', sky_cluster_home_dir],
        'Failed to remove stale shared state before submitting the PBS job.')
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=True) as f:
        f.write(provision_script)
        f.flush()
        login_node_runner.rsync(f.name,
                                provision_script_path,
                                up=True,
                                stream_logs=False)

    wait_str = ('indefinitely'
                if provision_timeout < 0 else f'for {provision_timeout}s')
    logger.debug(f'Submitting PBS job to queue {queue} and waiting {wait_str} '
                 'for nodes.')
    job_id = client.submit_job(queue, cluster_name_on_cloud,
                               provision_script_path, provision_scripts_dir)
    logger.debug(f'Submitted PBS job {job_id} to queue {queue} for cluster '
                 f'{cluster_name_on_cloud} with {num_nodes} node(s)')
    return _finish(job_id, created=True)


@common_utils.retry
def query_instances(
    cluster_name: str,
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    non_terminated_only: bool = True,
    retry_if_missing: bool = False,
) -> Dict[str, Tuple[Optional[status_lib.ClusterStatus], Optional[str]]]:
    """See sky/provision/__init__.py"""
    del cluster_name, retry_if_missing  # Unused for PBS
    assert provider_config is not None, (cluster_name_on_cloud, provider_config)
    client = _make_pbs_client(provider_config)

    statuses: Dict[str, Tuple[Optional[status_lib.ClusterStatus],
                              Optional[str]]] = {}
    for job in client.list_jobs_by_name(cluster_name_on_cloud,
                                        include_finished=True):
        state = job.state.upper()
        if state in _TERMINAL_JOB_STATES:
            if non_terminated_only:
                logger.debug(f'PBS job {job.job_id} is terminated '
                             f'(state {state}, exit {job.exit_status}).')
                continue
            statuses[job.job_id] = (None, job.comment)
        elif state in pbs.PENDING_JOB_STATES:
            statuses[job.job_id] = (status_lib.ClusterStatus.INIT, job.comment)
        elif job.exec_hosts:
            for node in job.exec_hosts:
                statuses[pbs_utils.instance_id(
                    job.job_id, node)] = (status_lib.ClusterStatus.UP, None)
        else:
            statuses[job.job_id] = (status_lib.ClusterStatus.INIT, job.comment)
    return statuses


def run_instances(region: str, cluster_name: str, cluster_name_on_cloud: str,
                  config: common.ProvisionConfig) -> common.ProvisionRecord:
    """Run instances for the given cluster (a PBS job)."""
    return _create_virtual_instance(region, cluster_name, cluster_name_on_cloud,
                                    config)


def wait_instances(region: str, cluster_name_on_cloud: str,
                   state: Optional[status_lib.ClusterStatus]) -> None:
    """See sky/provision/__init__.py"""
    del region, cluster_name_on_cloud, state
    # run_instances already waits for the nodes.


def get_cluster_info(
        region: str,
        cluster_name_on_cloud: str,
        provider_config: Optional[Dict[str, Any]] = None) -> common.ClusterInfo:
    del region
    assert provider_config is not None, cluster_name_on_cloud
    client = _make_pbs_client(provider_config)
    running_jobs = [
        job for job in client.list_jobs_by_name(cluster_name_on_cloud)
        if job.state.upper() in pbs.RUNNING_JOB_STATES and job.exec_hosts
    ]
    if not running_jobs:
        return common.ClusterInfo(
            instances={},
            head_instance_id=None,
            provider_name='pbs',
            provider_config=provider_config,
        )
    if len(running_jobs) > 1:
        raise RuntimeError(
            f'Multiple running PBS jobs found for cluster '
            f'{cluster_name_on_cloud}: {[j.job_id for j in running_jobs]}')
    job = running_jobs[0]
    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    sky_cluster_home_dir = pbs_utils.sky_cluster_home_dir(
        sky_base_dir, cluster_name_on_cloud)
    login_node_runner = _make_login_node_runner(provider_config)
    _, stdout = _run_on_login_node(
        login_node_runner,
        pbs_utils.list_node_port_files_command(sky_cluster_home_dir),
        'Failed to read the node sshd ports of the PBS cluster.')
    endpoints = pbs_utils.parse_node_port_files(stdout)
    missing = [
        rank for rank in range(len(job.exec_hosts)) if rank not in endpoints
    ]
    if missing:
        raise RuntimeError(
            f'PBS job {job.job_id} is running but node(s) {missing} of '
            f'cluster {cluster_name_on_cloud} have not published an sshd '
            'port; the allocation may still be starting or its node agents '
            'failed.')
    return _cluster_info_from_job(provider_config, cluster_name_on_cloud, job,
                                  endpoints)


def stop_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    del provider_config, worker_only
    raise exceptions.NotSupportedError(
        f'Stopping PBS clusters is not supported ({cluster_name_on_cloud}). '
        'Use `sky down` instead.')


def _wait_for_job_gone(client: 'pbs.PbsClient', job_name: str,
                       timeout: float) -> bool:
    """Wait until no job of this name is left outside a terminal state."""
    deadline = time.time() + timeout
    while True:
        try:
            states: Optional[List[str]] = client.get_jobs_state_by_name(
                job_name)
        except exceptions.CommandError as e:
            logger.debug(f'Failed to query the state of job {job_name}, '
                         f'retrying: {e}')
            states = None
        if states is not None and all(
                s.upper() in _TERMINAL_JOB_STATES for s in states):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_SECONDS)


def _remove_shared_state_script(sky_cluster_home_dir: str) -> str:
    """Remove the shared cluster home, retrying stale NFS handles."""
    quoted = shlex.quote(sky_cluster_home_dir)
    return (f'[ ! -e {quoted} ] && exit 0\n'
            'for attempt in 1 2 3; do\n'
            f'  rm -rf -- {quoted} && [ ! -e {quoted} ] && exit 0\n'
            '  [ "$attempt" = 3 ] || sleep 10\n'
            'done\n'
            'exit 1')


def _delete_job(client: 'pbs.PbsClient', cluster_name_on_cloud: str,
                inside_pbs_cluster: bool) -> None:
    """Delete the allocation and, from outside, verify it leaves the queue."""
    jobs = [
        job for job in client.list_jobs_by_name(cluster_name_on_cloud)
        if job.state.upper() not in _TERMINAL_JOB_STATES
    ]
    if not jobs:
        logger.debug(f'PBS job for cluster {cluster_name_on_cloud} not found; '
                     'it may have been deleted already.')
        return
    for job in jobs:
        client.delete_job(job.job_id)
    if inside_pbs_cluster:
        # qdel reaps the skylet running this; nothing more can be awaited.
        return
    if _wait_for_job_gone(client, cluster_name_on_cloud,
                          _JOB_TERMINATION_TIMEOUT_SECONDS):
        return
    logger.warning(f'PBS job for cluster {cluster_name_on_cloud} did not '
                   f'leave the queue within {_JOB_TERMINATION_TIMEOUT_SECONDS}s'
                   ' of qdel. Forcing the deletion.')
    for job in jobs:
        client.delete_job(job.job_id, force=True)
    if not _wait_for_job_gone(client, cluster_name_on_cloud,
                              _JOB_TERMINATION_TIMEOUT_SECONDS):
        raise RuntimeError(
            f'PBS job for cluster {cluster_name_on_cloud} is still in the '
            f'queue {_JOB_TERMINATION_TIMEOUT_SECONDS}s after a forced qdel. '
            'Check qstat and delete the job manually if needed.')


def terminate_instances(
    cluster_name_on_cloud: str,
    provider_config: Optional[Dict[str, Any]] = None,
    worker_only: bool = False,
) -> None:
    """See sky/provision/__init__.py"""
    assert provider_config is not None, cluster_name_on_cloud
    if worker_only:
        logger.warning(
            'worker_only=True is not supported for PBS, this is a no-op.')
        return
    # Autodown runs this from the cluster's head node (skylet), where the
    # PBS client commands are local and the login node key is absent.
    inside_pbs_cluster = pbs_utils.is_inside_pbs_cluster()
    client, login_node_runner = _make_client_and_login_runner(
        provider_config, inside_pbs_cluster)
    _delete_job(client, cluster_name_on_cloud, inside_pbs_cluster)
    if inside_pbs_cluster:
        return
    # The batch script's EXIT trap removes the shared home; this is the
    # fallback for an allocation that could not run it.
    sky_base_dir = _resolve_sky_base_dir(client, provider_config)
    _run_on_login_node_best_effort(
        login_node_runner,
        _remove_shared_state_script(
            pbs_utils.sky_cluster_home_dir(sky_base_dir,
                                           cluster_name_on_cloud)),
        'Failed to clean up shared PBS cluster state.')


def open_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, ports, provider_config


def cleanup_ports(
    cluster_name_on_cloud: str,
    ports: List[str],
    provider_config: Optional[Dict[str, Any]] = None,
) -> None:
    """See sky/provision/__init__.py"""
    del cluster_name_on_cloud, ports, provider_config


def get_command_runners(
    cluster_info: common.ClusterInfo,
    **credentials: Any,
) -> List[command_runner.PbsCommandRunner]:
    """One runner per allocated node, tunnelled through the login node."""
    assert cluster_info.provider_config is not None, cluster_info
    if cluster_info.head_instance_id is None:
        return []
    head_instance = cluster_info.get_head_instance()
    assert head_instance is not None, 'Head instance not found'
    cluster_name_on_cloud = head_instance.tags.get(
        constants.TAG_SKYPILOT_CLUSTER_NAME, None)
    assert cluster_name_on_cloud is not None, cluster_info
    provider_config = cluster_info.provider_config
    instances = [
        instance_infos[0] for instance_infos in cluster_info.instances.values()
    ]
    login_ssh_config = provider_config['ssh']
    proxy_command = pbs_utils.build_login_node_proxy_command(login_ssh_config)
    # The sshd on the nodes runs as the login user and trusts SkyPilot's own
    # key, the one in the cluster's `auth` section.
    ssh_private_key = credentials.get('ssh_private_key')
    sky_base_dir = _resolve_sky_base_dir(_make_pbs_client(provider_config),
                                         provider_config)
    sky_cluster_home_dir = pbs_utils.sky_cluster_home_dir(
        sky_base_dir, cluster_name_on_cloud)
    return [
        command_runner.PbsCommandRunner(
            (instance_info.tags['node'], instance_info.ssh_port),
            login_ssh_config['user'],
            ssh_private_key,
            sky_dir=sky_cluster_home_dir,
            # Chosen by the node agent: node-local, possibly the job's own
            # $TMPDIR.
            skypilot_runtime_dir=instance_info.tags['runtime_dir'],
            login_node_proxy_command=proxy_command,
            enable_interactive_auth=True,
        ) for instance_info in instances
    ]
