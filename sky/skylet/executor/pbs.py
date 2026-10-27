"""PBS task executor for SkyPilot.

Invoked on each node of a PBS allocation by the job driver, locally on the
head node and over the allocation's own sshd on the workers:
    python -m sky.skylet.executor.pbs --rank=N --script=... --log-dir=...

Also hosts the per-cluster task resource allocator that the job driver uses
to queue jobs behind each other: a PBS allocation has no scheduler of its
own for the SkyPilot jobs that share it.
"""
import argparse
import json
import os
import signal
import socket
import sys
import time
from typing import Dict, List, Optional, Set

import colorama
import filelock
import psutil

from sky.skylet import constants
from sky.skylet.log_lib import run_bash_command_with_log

# Return code of a task killed because a peer failed, matching Ray's.
CANCELLED_RETURN_CODE = 137

_SLOT_STATE_FILENAME = '.sky/pbs_task_slots.json'
_SLOT_LOCK_TIMEOUT_SECONDS = 30


def _runtime_dir() -> str:
    return os.path.expanduser(
        os.environ.get(constants.SKY_RUNTIME_DIR_ENV_VAR_KEY, '~'))


def default_slot_state_path() -> str:
    """The allocator's state file; node-local, on the head node."""
    return os.path.join(_runtime_dir(), _SLOT_STATE_FILENAME)


def _pid_alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and (process.status() !=
                                         psutil.STATUS_ZOMBIE)
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


class TaskResourceSlot:
    """Reserves per-node CPUs and GPU ids of a cluster for one SkyPilot job.

    Jobs on the same cluster take the first ``num_nodes`` nodes of the
    allocation, so node ``i`` is used by every running job with more than
    ``i`` nodes. A job waits in ``acquire`` until each of its nodes has
    enough free CPUs and GPUs; held reservations are keyed by the driver's
    pid, so a driver that dies releases them.
    """

    def __init__(self,
                 job_id: int,
                 num_nodes: int,
                 cpus_per_node: float,
                 gpus_per_node: int,
                 node_capacity: Dict[str, float],
                 state_path: Optional[str] = None):
        self.job_id = str(job_id)
        self.num_nodes = num_nodes
        self.cpus_per_node = cpus_per_node
        self.gpus_per_node = gpus_per_node
        self.capacity_cpus = float(node_capacity.get('CPU', 0) or 0)
        self.capacity_gpus = int(node_capacity.get('GPU', 0) or 0)
        self.state_path = state_path or default_slot_state_path()
        self.gpu_ids: Optional[List[List[int]]] = None

    def _lock(self) -> filelock.FileLock:
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        return filelock.FileLock(self.state_path + '.lock',
                                 timeout=_SLOT_LOCK_TIMEOUT_SECONDS)

    def _read_state(self) -> Dict[str, dict]:
        try:
            with open(self.state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(state, dict):
            return {}
        # Drop reservations of drivers that are gone.
        return {
            job_id: entry
            for job_id, entry in state.items()
            if isinstance(entry, dict) and _pid_alive(int(entry.get('pid', 0)))
        }

    def _write_state(self, state: Dict[str, dict]) -> None:
        tmp_path = f'{self.state_path}.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(state, f)
        os.replace(tmp_path, self.state_path)

    def _try_reserve(self, state: Dict[str, dict]) -> Optional[List[List[int]]]:
        gpu_ids: List[List[int]] = []
        for node in range(self.num_nodes):
            used_cpus = 0.0
            used_gpus: Set[int] = set()
            for job_id, entry in state.items():
                if job_id == self.job_id:
                    continue
                if int(entry.get('num_nodes', 0)) <= node:
                    continue
                used_cpus += float(entry.get('cpus', 0))
                node_gpus = entry.get('gpu_ids', [])
                if node < len(node_gpus):
                    used_gpus.update(int(g) for g in node_gpus[node])
            if (self.capacity_cpus > 0 and
                    self.capacity_cpus - used_cpus < self.cpus_per_node):
                return None
            free_gpus = [
                g for g in range(self.capacity_gpus) if g not in used_gpus
            ]
            if len(free_gpus) < self.gpus_per_node:
                return None
            gpu_ids.append(free_gpus[:self.gpus_per_node])
        return gpu_ids

    def try_acquire(self) -> Optional[List[List[int]]]:
        """Reserve now if possible; returns the GPU ids per node."""
        with self._lock():
            state = self._read_state()
            gpu_ids = self._try_reserve(state)
            if gpu_ids is None:
                return None
            state[self.job_id] = {
                'pid': os.getpid(),
                'num_nodes': self.num_nodes,
                'cpus': self.cpus_per_node,
                'gpu_ids': gpu_ids,
            }
            self._write_state(state)
        self.gpu_ids = gpu_ids
        return gpu_ids

    def acquire(self, poll_interval: float = 1.0) -> List[List[int]]:
        """Block until the reservation succeeds."""
        while True:
            gpu_ids = self.try_acquire()
            if gpu_ids is not None:
                return gpu_ids
            time.sleep(poll_interval)

    def release(self) -> None:
        with self._lock():
            state = self._read_state()
            if state.pop(self.job_id, None) is not None:
                self._write_state(state)
        self.gpu_ids = None


def _get_ip_address() -> str:
    return socket.gethostbyname(socket.gethostname())


def _become_process_group_leader() -> None:
    """Put this process and its children in their own session.

    The driver cancels a rank by signalling its process group, so the
    executor's pid must be the group id.
    """
    try:
        os.setsid()
    except OSError:
        try:
            os.setpgrp()
        except OSError:
            pass


def _install_signal_handlers() -> None:

    def _handler(signum, _frame):
        signal.signal(signum, signal.SIG_IGN)
        children = psutil.Process().children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(children, timeout=5)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
        sys.exit(CANCELLED_RETURN_CODE)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGHUP, _handler)


def main():
    parser = argparse.ArgumentParser(
        description='SkyPilot PBS task runner for distributed execution')
    parser.add_argument('--script', help='User script (inline, shell-quoted)')
    parser.add_argument('--script-path',
                        help='Path to script file (if too long for inline)')
    parser.add_argument('--env-vars',
                        default='{}',
                        help='JSON-encoded environment variables')
    parser.add_argument('--log-dir',
                        required=True,
                        help='Directory for log files')
    parser.add_argument('--rank',
                        type=int,
                        required=True,
                        help='Rank of this node in the task')
    parser.add_argument('--num-nodes',
                        type=int,
                        required=True,
                        help='Number of nodes the task runs on')
    parser.add_argument('--node-ips',
                        required=True,
                        help='Comma-separated IPs of the task nodes')
    parser.add_argument('--task-name',
                        default=None,
                        help='Task name for single-node log prefix')
    parser.add_argument('--is-setup',
                        action='store_true',
                        help='Whether this runs the setup commands')
    parser.add_argument('--gpu-ids',
                        default=None,
                        help='Comma-separated GPU ids reserved on this node')
    parser.add_argument('--pid-file',
                        default=None,
                        help='File to write the executor pid to')
    args = parser.parse_args()

    assert args.script is not None or args.script_path is not None, (
        'Either --script or --script-path must be provided')

    _become_process_group_leader()
    _install_signal_handlers()
    if args.pid_file is not None:
        os.makedirs(os.path.dirname(args.pid_file) or '.', exist_ok=True)
        with open(args.pid_file, 'w', encoding='utf-8') as f:
            f.write(str(os.getpid()))

    rank = args.rank
    num_nodes = args.num_nodes
    node_ips = [ip for ip in args.node_ips.split(',') if ip]
    node_name = 'head' if rank == 0 else f'worker{rank}'
    ip_addr = node_ips[rank] if rank < len(node_ips) else _get_ip_address()

    # Log files live on the shared filesystem, so each node needs its own.
    if args.is_setup:
        log_filename = f'setup-{node_name}.log'
    elif num_nodes == 1:
        log_filename = 'run.log'
    else:
        log_filename = f'{rank}-{node_name}.log'
    log_path = os.path.join(args.log_dir, log_filename)

    if args.script_path:
        with open(args.script_path, 'r', encoding='utf-8') as f:
            script = f.read()
    else:
        script = args.script

    env_vars = json.loads(args.env_vars)
    if not args.is_setup:
        # For setup, the backend sets the SKYPILOT_SETUP_* variables.
        env_vars['SKYPILOT_NODE_RANK'] = str(rank)
        env_vars['SKYPILOT_NUM_NODES'] = str(num_nodes)
        env_vars['SKYPILOT_NODE_IPS'] = '\n'.join(node_ips)
    if args.gpu_ids is not None:
        env_vars['CUDA_VISIBLE_DEVICES'] = args.gpu_ids

    if args.is_setup:
        if rank == 0:
            prefix = (f'{colorama.Fore.CYAN}(setup pid={{pid}})'
                      f'{colorama.Style.RESET_ALL} ')
        else:
            prefix = (f'{colorama.Fore.CYAN}(setup pid={{pid}}, ip={ip_addr})'
                      f'{colorama.Style.RESET_ALL} ')
    elif num_nodes == 1:
        name_str = args.task_name if args.task_name else 'task'
        prefix = (f'{colorama.Fore.CYAN}({name_str}, pid={{pid}})'
                  f'{colorama.Style.RESET_ALL} ')
    elif rank == 0:
        prefix = (f'{colorama.Fore.CYAN}({node_name}, rank={rank}, pid={{pid}})'
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
    sys.exit(returncode)


if __name__ == '__main__':
    main()
