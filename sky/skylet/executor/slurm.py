"""Slurm distributed task executor for SkyPilot.

This module is invoked on each Slurm compute node via:
    srun python -m sky.skylet.executor.slurm --script=... --log-dir=...
"""
import argparse
import errno
import json
import os
import pathlib
import shutil
import socket
import sys
import time

import colorama
import hostlist

from sky.skylet import constants
from sky.skylet.log_lib import run_bash_command_with_log

# Slurm populates step- and task-scoped SLURM_* variables for the executor's
# own job step (e.g. SLURM_CPU_BIND, SLURM_CPUS_PER_TASK=1,
# SLURM_NTASKS_PER_NODE=1). srun treats many of these as input defaults, so a
# nested srun in the user script gets silently constrained to the executor
# step's shape (1 CPU per task, the executor's CPU binding) instead of the
# full job allocation. Unset them for the user script, keeping job-scoped
# variables (SLURM_JOB_ID, SLURM_JOB_NODELIST, SLURM_GPUS_ON_NODE, ...) so
# patterns like `srun --overlap --jobid=$SLURM_JOB_ID` keep working against
# the full allocation.
# See https://support.schedmd.com/show_bug.cgi?id=14298
UNSET_STEP_SCOPED_SLURM_ENV = (
    'unset "${!SLURM_STEP_@}" "${!SLURM_CPU_BIND@}" "${!SLURM_MEM_BIND@}" '
    'SLURM_CPUS_PER_TASK SLURM_TRES_PER_TASK SLURM_CPUS_ON_NODE '
    'SLURM_NTASKS SLURM_NPROCS SLURM_NTASKS_PER_NODE SLURM_TASKS_PER_NODE '
    'SLURM_DISTRIBUTION SLURM_SRUN_COMM_HOST SLURM_SRUN_COMM_PORT '
    'SLURM_LAUNCH_NODE_IPADDR SLURM_TASK_PID '
    'SLURM_PROCID SLURM_LOCALID SLURM_NODEID SLURM_GTIDS SLURM_STEPID')

# How long the run-done barrier keeps retrying while the shared filesystem
# returns hard errors before giving up. The shared home is typically an NFS
# export, where a client can keep serving a stale view of a directory for its
# attribute cache timeout (acdirmax, 60s by default), so this needs enough
# headroom to outlast that window.
BARRIER_ERROR_TIMEOUT_SECONDS = 300


def _is_proctrack_cgroup_enabled(shared_home_dir: str) -> bool:
    """Check if Slurm uses proctrack/cgroup.

    The proctrack type file is written by the provisioner to the shared
    filesystem home directory. Inside containers, ~ resolves to a
    container-local path (/root/), so we must use the explicit shared
    home directory passed from outside the container.
    """
    proctrack_file = os.path.join(shared_home_dir,
                                  constants.SLURM_PROCTRACK_TYPE_FILE)
    try:
        with open(proctrack_file, 'r', encoding='utf-8') as f:
            proctrack_type = f.read().strip()
            return proctrack_type == 'cgroup'
    except (FileNotFoundError, IOError):
        # If file doesn't exist or can't be read,
        # default to True to be conservative.
        return True


def _get_ip_address() -> str:
    """Get the IP address of the current node."""
    # Use socket.gethostbyname to be consistent with _get_job_node_ips(),
    # which resolves hostnames the same way. Using `hostname -I` can return
    # Docker bridge IPs (172.17.x.x) first, causing IP mismatch errors.
    return socket.gethostbyname(socket.gethostname())


def _get_job_node_ips() -> str:
    """Get IPs of all nodes in the current Slurm job."""
    nodelist = os.environ.get('SLURM_JOB_NODELIST', '')
    assert nodelist, 'SLURM_JOB_NODELIST is not set'

    # Expand compressed nodelist (e.g., "node[1-3,5]" -> "node1\nnode2...")
    # Alternative: `scontrol show hostnames $SLURM_JOB_NODELIST`, but `scontrol`
    # (and Slurm CLI binaries in general) may not exist inside containers.
    hostnames = list(hostlist.expand_hostlist(nodelist))
    ips = []
    for hostname in hostnames:
        try:
            ip = socket.gethostbyname(hostname)
            ips.append(ip)
        except socket.gaierror as e:
            raise RuntimeError('Failed to get IP for hostname: '
                               f'{hostname}') from e

    return '\n'.join(ips)


def _wait_for_all_ranks(run_done_dir: str, rank: int, num_nodes: int) -> None:
    """Waits until every rank has written its done file under run_done_dir.

    Never raises. The barrier only exists to hold this task's cgroup open
    until its peers finish, so failing to coordinate must not change the exit
    status of the user's script.
    """
    pending = set(range(num_nodes)) - {rank}
    # When the filesystem first started erroring, or None if the last sweep
    # was clean.
    erroring_since = None
    # TODO(kevin): A peer that never writes its done file leaves every other
    # rank waiting here indefinitely. srun's --kill-on-bad-exit covers the
    # common case, where the peer's task dies and Slurm tears the whole step
    # down, but a peer that stays alive without ever reporting does not reach
    # it. Bounding this by wall clock would be wrong, since ranks can
    # legitimately finish hours apart; it needs a liveness signal such as the
    # peer's Slurm task state.
    while pending:
        last_error = None
        for peer in sorted(pending):
            done_file = os.path.join(run_done_dir, str(peer))
            try:
                # Poll each rank's file by name instead of listing the
                # directory, so that a rank that has not finished yet is an
                # ordinary ENOENT rather than something the caller has to tell
                # apart from a filesystem error.
                os.close(os.open(done_file, os.O_RDONLY))
                pending.discard(peer)
            except FileNotFoundError:
                pass
            except OSError as e:
                last_error = e
        if not pending:
            return
        if last_error is None and not os.path.isdir(run_done_dir):
            # Nothing in this barrier removes the directory, so its absence
            # means something outside removed it and no rank will ever be
            # able to report in. Count it as an error rather than as "not
            # ready yet" so that the wait ends instead of running forever.
            last_error = FileNotFoundError(errno.ENOENT,
                                           'Barrier directory is gone',
                                           run_done_dir)
        if last_error is None:
            # Ranks legitimately finish minutes or hours apart, so a clean but
            # incomplete sweep is not a reason to stop waiting. Only a
            # filesystem that keeps erroring is bounded by a deadline.
            erroring_since = None
        elif erroring_since is None:
            erroring_since = time.monotonic()
        elif time.monotonic() - erroring_since > BARRIER_ERROR_TIMEOUT_SECONDS:
            print(
                f'Gave up waiting for rank(s) {sorted(pending)} after '
                f'{BARRIER_ERROR_TIMEOUT_SECONDS}s of errors reading '
                f'{run_done_dir}: {last_error!r}. Ranks that are still '
                'running may be killed by Slurm when this task exits.',
                file=sys.stderr)
            return
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(
        description='SkyPilot Slurm task runner for distributed execution')
    parser.add_argument('--script', help='User script (inline, shell-quoted)')
    parser.add_argument('--script-path',
                        help='Path to script file (if too long for inline)')
    parser.add_argument('--env-vars',
                        default='{}',
                        help='JSON-encoded environment variables')
    parser.add_argument('--log-dir',
                        required=True,
                        help='Directory for log files')
    parser.add_argument('--cluster-num-nodes',
                        type=int,
                        required=True,
                        help='Total number of nodes in the cluster')
    parser.add_argument('--cluster-ips',
                        required=True,
                        help='Comma-separated list of cluster node IPs')
    parser.add_argument('--task-name',
                        default=None,
                        help='Task name for single-node log prefix')
    parser.add_argument(
        '--is-setup',
        action='store_true',
        help=
        'Whether this is a setup command (affects logging prefix and filename)')
    parser.add_argument('--cluster-home-dir',
                        help='Shared filesystem home directory for the '
                        'cluster. Inside containers, ~ is container-local, '
                        'so this provides the shared path for cross-node '
                        'coordination files.')
    parser.add_argument('--alloc-signal-file',
                        help='Path to allocation signal file')
    parser.add_argument('--setup-done-signal-file',
                        help='Path to setup-done signal file')
    args = parser.parse_args()

    assert args.script is not None or args.script_path is not None, (
        'Either '
        '--script or --script-path must be provided')

    # Task rank, different from index of the node in the cluster.
    rank = int(os.environ['SLURM_PROCID'])
    num_nodes = int(os.environ.get('SLURM_NNODES', 1))
    is_single_node_cluster = (args.cluster_num_nodes == 1)

    # Determine node index from IP (like Ray's cluster_ips_to_node_id)
    cluster_ips = args.cluster_ips.split(',')
    ip_addr = _get_ip_address()
    try:
        node_idx = cluster_ips.index(ip_addr)
    except ValueError as e:
        raise RuntimeError(f'IP address {ip_addr} not found in '
                           f'cluster IPs: {cluster_ips}') from e
    node_name = 'head' if node_idx == 0 else f'worker{node_idx}'

    # Log files are written to a shared filesystem, so each node must use a
    # unique filename to avoid collisions.
    if args.is_setup:
        # TODO(kevin): This is inconsistent with other clouds, where it is
        # simply called 'setup.log'. On Slurm that is obviously not possible,
        # since the ~/sky_logs directory is shared by all nodes, so
        # 'setup.log' will be overwritten by other nodes.
        # Perhaps we should apply this naming convention to other clouds.
        log_filename = f'setup-{node_name}.log'
    elif is_single_node_cluster:
        log_filename = 'run.log'
    else:
        log_filename = f'{rank}-{node_name}.log'
    log_path = os.path.join(args.log_dir, log_filename)

    if args.script_path:
        with open(args.script_path, 'r', encoding='utf-8') as f:
            script = f.read()
    else:
        script = args.script
    script = UNSET_STEP_SCOPED_SLURM_ENV + '\n' + script

    # Parse env vars and add SKYPILOT environment variables
    env_vars = json.loads(args.env_vars)
    if not args.is_setup:
        # For setup, env vars are set in CloudVmRayBackend._setup.
        env_vars['SKYPILOT_NODE_RANK'] = str(rank)
        env_vars['SKYPILOT_NUM_NODES'] = str(num_nodes)
        env_vars['SKYPILOT_NODE_IPS'] = _get_job_node_ips()

    # Signal file coordination for setup/run synchronization
    # Rank 0 touches the allocation signal to indicate resources acquired
    if args.alloc_signal_file is not None and rank == 0:
        pathlib.Path(args.alloc_signal_file).touch()

    # Wait for setup to complete.
    while args.setup_done_signal_file is not None and not os.path.exists(
            args.setup_done_signal_file):
        time.sleep(0.1)

    # Build log prefix
    # For setup on head: (setup pid={pid})
    # For setup on workers: (setup pid={pid}, ip=1.2.3.4)
    # For single-node cluster: (task_name, pid={pid})
    # For multi-node on head: (head, rank=0, pid={pid})
    # For multi-node on workers: (worker1, rank=1, pid={pid}, ip=1.2.3.4)
    # The {pid} placeholder will be replaced by run_with_log
    if args.is_setup:
        # Setup prefix: head (node_idx=0) shows no IP, workers show IP
        if node_idx == 0:
            prefix = (f'{colorama.Fore.CYAN}(setup pid={{pid}})'
                      f'{colorama.Style.RESET_ALL} ')
        else:
            prefix = (f'{colorama.Fore.CYAN}(setup pid={{pid}}, ip={ip_addr})'
                      f'{colorama.Style.RESET_ALL} ')
    elif is_single_node_cluster:
        # Single-node cluster: use task name
        name_str = args.task_name if args.task_name else 'task'
        prefix = (f'{colorama.Fore.CYAN}({name_str}, pid={{pid}})'
                  f'{colorama.Style.RESET_ALL} ')
    else:
        # Multi-node cluster: head (node_idx=0) shows no IP, workers show IP
        if node_idx == 0:
            prefix = (
                f'{colorama.Fore.CYAN}({node_name}, rank={rank}, pid={{pid}})'
                f'{colorama.Style.RESET_ALL} ')
        else:
            prefix = (f'{colorama.Fore.CYAN}'
                      f'({node_name}, rank={rank}, pid={{pid}}, ip={ip_addr})'
                      f'{colorama.Style.RESET_ALL} ')

    returncode = run_bash_command_with_log(script,
                                           log_path,
                                           env_vars=env_vars,
                                           stream_logs=True,
                                           streaming_prefix=prefix)

    # For successful tasks in multi-node Slurm jobs (one task per node), wait
    # for all tasks to complete before exiting, because Slurm's
    # proctrack/cgroup kills all processes in a task's cgroup when that task's
    # main process exits. If one task exits early, child processes (e.g., Ray
    # workers) get killed even while other tasks are still running. Failed
    # tasks must exit immediately so srun's --kill-on-bad-exit can terminate
    # the other tasks.
    # Only needed when proctrack/cgroup is enabled.
    # https://slurm.schedmd.com/cgroups.html#proctrack
    # Use the shared filesystem home for cross-node coordination.
    # Inside containers, ~ is container-local (/root/), not on the
    # shared filesystem, so we use the explicitly passed path.
    shared_home = (args.cluster_home_dir
                   if args.cluster_home_dir else os.path.expanduser('~'))

    if (returncode == 0 and num_nodes > 1 and not args.is_setup and
            _is_proctrack_cgroup_enabled(shared_home)):
        slurm_job_id = os.environ['SLURM_JOB_ID']
        slurm_step_id = os.environ['SLURM_STEP_ID']
        run_done_dir = os.path.join(
            shared_home, f'.sky_run_done_{slurm_job_id}_{slurm_step_id}')
        done_file = f'{run_done_dir}/{rank}'

        if rank == 0:
            # Clear the path first so that leftover done files can never
            # satisfy the barrier early.
            shutil.rmtree(run_done_dir, ignore_errors=True)
            os.makedirs(run_done_dir, exist_ok=True)
        else:
            # Workers wait for dir to exist (rank 0 creates it)
            while not os.path.isdir(run_done_dir):
                time.sleep(0.1)

        pathlib.Path(done_file).touch()

        # All ranks wait for all done files to exist. The directory stays in
        # place until the provision script's cleanup trap removes the cluster
        # home directory at the end of the Slurm job: removing it as ranks
        # leave the barrier takes it away from the ranks still reading it,
        # which on a shared filesystem is long enough for them to never
        # observe the full set.
        _wait_for_all_ranks(run_done_dir, rank, num_nodes)

    sys.exit(returncode)


if __name__ == '__main__':
    main()
