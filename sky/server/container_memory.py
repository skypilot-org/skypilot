"""Memory and process accounting for the API server's container.

On Kubernetes the API server often runs without a memory limit, so what
bounds it is its node: when the container's unreclaimable memory nears the
node's allocatable memory, the kernel OOM killer acts. Per-process RSS gauges
cannot show that point coming. They cover only the processes that report
themselves (uvicorn and executor workers), not the managed-job controllers or
the short-lived children those spawn (kubectl, ssh, subprocess_daemon), and
they count shared file pages once per process.

This module reads the container's own cgroup and counts the processes in it
by type. It reads cgroup v2 files at the root of the cgroup namespace, which
inside a container is the container's cgroup. Outside a container, or on
cgroup v1, that root has no memory.current and scan() returns None.

The census reads two /proc files per process, and every read releases the
GIL. In a process with a CPU-bound thread, each read then waits a full GIL
switch interval before it resumes, so a census of a few thousand processes
can take minutes. scan_in_subprocess() runs it in a child interpreter
instead; the module imports only the standard library for that reason.

Measurement only.
"""
import dataclasses
import json
import os
import subprocess
import sys
import time
from typing import Collection, Dict, List, Optional, Set, Tuple

CGROUP_DIR = '/sys/fs/cgroup'
PROC_DIR = '/proc'

# memory.stat keys exported as they are. `kernel` exists from Linux 5.18 on.
MEMORY_STAT_KEYS = ('anon', 'file', 'kernel', 'shmem', 'slab_reclaimable')

# Process types: a fixed set, so `type` label values stay bounded. 'server'
# and 'worker:<group>' match the `type` label of sky_apiserver_process_peak_rss.
TYPE_MAIN = 'main'
TYPE_SERVER = 'server'
TYPE_CONTROLLER = 'controller'
TYPE_KUBECTL_EXEC = 'kubectl_exec'
TYPE_KUBECTL_PORT_FORWARD = 'kubectl_port_forward'
TYPE_KUBECTL_OTHER = 'kubectl_other'
TYPE_SUBPROCESS_DAEMON = 'subprocess_daemon'
TYPE_AWS = 'aws'
TYPE_SHELL = 'shell'
TYPE_SSH_MUX = 'ssh_mux'
TYPE_SSH_OTHER = 'ssh_other'
TYPE_RESOURCE_TRACKER = 'resource_tracker'
TYPE_ZOMBIE = 'zombie'
TYPE_PYTHON_OTHER = 'python_other'
TYPE_OTHER = 'other'
# Values of sky.server.requests.requests.ScheduleType.
WORKER_GROUPS = ('long', 'short')
TYPES = (TYPE_MAIN, TYPE_SERVER) + tuple(
    f'worker:{group}' for group in WORKER_GROUPS) + (
        TYPE_CONTROLLER, TYPE_KUBECTL_EXEC, TYPE_KUBECTL_PORT_FORWARD,
        TYPE_KUBECTL_OTHER, TYPE_SUBPROCESS_DAEMON, TYPE_AWS, TYPE_SHELL,
        TYPE_SSH_MUX, TYPE_SSH_OTHER, TYPE_RESOURCE_TRACKER, TYPE_ZOMBIE,
        TYPE_PYTHON_OTHER, TYPE_OTHER)

# Set by setproctitle in executor_initializer: SkyPilot:executor:<group>:<pid>.
_EXECUTOR_TITLE_PREFIX = 'SkyPilot:executor:'
_CONTROLLER_MODULE = 'sky.jobs.controller'
# multiprocessing's spawn start method passes this flag to every child.
_SPAWN_CHILD_FLAG = '--multiprocessing-fork'
_RESOURCE_TRACKER_MODULE = 'multiprocessing.resource_tracker'
_SUBPROCESS_DAEMON_SCRIPT = 'subprocess_daemon.py'
# OpenSSH retitles a ControlMaster to 'ssh: <control path> [mux]'.
_SSH_MUX_TITLE = 'ssh:'
_SHELLS = frozenset({'sh', 'bash', 'dash', 'ash', 'zsh'})


@dataclasses.dataclass
class TypeUsage:
    """Processes of one type in the container."""
    processes: int = 0
    threads: int = 0
    rss_anon_bytes: int = 0
    max_rss_anon_bytes: int = 0
    # VmRSS: anon, file and shmem pages.
    rss_bytes: int = 0


@dataclasses.dataclass
class Snapshot:
    """One read of the container's cgroup and processes."""
    usage_bytes: int
    # memory.stat values for the MEMORY_STAT_KEYS the kernel reports.
    stat_bytes: Dict[str, int]
    # See unreclaimable_bytes().
    unreclaimable_bytes: Optional[int]
    # memory.max, or None when the container has no memory limit.
    limit_bytes: Optional[int]
    types: Dict[str, TypeUsage]
    duration_seconds: float


def _read(path: str) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _read_memory_stat(cgroup_dir: str) -> Dict[str, int]:
    text = _read(os.path.join(cgroup_dir, 'memory.stat')) or ''
    stats = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in MEMORY_STAT_KEYS:
            stats[parts[0]] = int(parts[1])
    return stats


def _read_memory(cgroup_dir: str) -> Optional[Tuple[int, Dict[str, int]]]:
    """memory.current and memory.stat, read back to back."""
    usage = (_read(os.path.join(cgroup_dir, 'memory.current')) or '').strip()
    if not usage.isdigit():
        return None
    return int(usage), _read_memory_stat(cgroup_dir)


def unreclaimable_bytes(usage_bytes: int,
                        stat_bytes: Dict[str, int]) -> Optional[int]:
    """Memory the kernel cannot reclaim from the container without swap.

    memory.current minus page cache (memory.stat `file`) and reclaimable
    slab (dentry and inode caches), with shmem added back: memory.stat
    counts tmpfs and shared memory inside `file`, but those pages cannot be
    dropped.
    """
    if 'file' not in stat_bytes:
        return None
    return (usage_bytes - stat_bytes['file'] + stat_bytes.get('shmem', 0) -
            stat_bytes.get('slab_reclaimable', 0))


def read_unreclaimable_bytes(cgroup_dir: str = CGROUP_DIR) -> Optional[int]:
    """unreclaimable_bytes() of the container now, without the process census.

    Returns None when the container's cgroup v2 memory files are not
    readable.
    """
    memory = _read_memory(cgroup_dir)
    return None if memory is None else unreclaimable_bytes(*memory)


def _read_limit(cgroup_dir: str) -> Optional[int]:
    text = (_read(os.path.join(cgroup_dir, 'memory.max')) or '').strip()
    return int(text) if text.isdigit() else None


def _read_pids(cgroup_dir: str) -> List[int]:
    text = _read(os.path.join(cgroup_dir, 'cgroup.procs')) or ''
    return [int(line) for line in text.split() if line.isdigit()]


def _read_status(proc_dir: str, pid: int) -> Optional[Dict[str, str]]:
    """Fields of /proc/<pid>/status that the census uses."""
    text = _read(os.path.join(proc_dir, str(pid), 'status'))
    if text is None:
        return None
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(':')
        if key in ('Name', 'State', 'PPid', 'Threads', 'VmRSS', 'RssAnon'):
            fields[key] = value.strip()
    return fields


def _read_argv(proc_dir: str, pid: int) -> List[str]:
    try:
        with open(os.path.join(proc_dir, str(pid), 'cmdline'), 'rb') as f:
            raw = f.read()
    except OSError:
        return []
    return raw.decode('utf-8', errors='replace').split('\0')


def _kubectl_type(args: List[str]) -> str:
    # Words after `--` are the remote command of `kubectl exec`.
    for word in args:
        if word == '--':
            break
        if word == 'exec':
            return TYPE_KUBECTL_EXEC
        if word == 'port-forward':
            return TYPE_KUBECTL_PORT_FORWARD
    return TYPE_KUBECTL_OTHER


def _python_type(ppid: int, args: List[str], main_pid: int) -> str:
    for i, word in enumerate(args):
        if word == f'-m{_CONTROLLER_MODULE}' or (
                word == '-m' and args[i + 1:i + 2] == [_CONTROLLER_MODULE]):
            return TYPE_CONTROLLER
    if _RESOURCE_TRACKER_MODULE in ' '.join(args):
        return TYPE_RESOURCE_TRACKER
    # Executor workers retitle themselves on start, so the remaining spawn
    # children of the main process are the uvicorn workers.
    if _SPAWN_CHILD_FLAG in args:
        return TYPE_SERVER if ppid == main_pid else TYPE_PYTHON_OTHER
    script = os.path.basename(
        next((word for word in args if not word.startswith('-')), ''))
    if script == _SUBPROCESS_DAEMON_SCRIPT:
        return TYPE_SUBPROCESS_DAEMON
    if script == 'aws':
        return TYPE_AWS
    return TYPE_PYTHON_OTHER


def classify(pid: int, ppid: int, argv: List[str], comm: str, state: str,
             main_pid: int) -> str:
    """Return the process type of one process in the container.

    argv is /proc/<pid>/cmdline split on NULs; comm and state are the Name and
    State fields of /proc/<pid>/status.
    """
    if state.startswith('Z'):
        return TYPE_ZOMBIE
    if pid == main_pid:
        return TYPE_MAIN
    # A process that rewrites its title (setproctitle, OpenSSH) can leave the
    # whole title in one argv entry, so split on whitespace too.
    words = ' '.join(argv).split() or comm.split()
    if not words:
        return TYPE_OTHER
    if words[0].startswith(_EXECUTOR_TITLE_PREFIX):
        group = words[0][len(_EXECUTOR_TITLE_PREFIX):].split(':', 1)[0]
        return f'worker:{group}' if group in WORKER_GROUPS else TYPE_OTHER
    if words[0] == _SSH_MUX_TITLE:
        return TYPE_SSH_MUX
    program = os.path.basename(words[0])
    if program in _SHELLS:
        return TYPE_SHELL
    if program == 'kubectl':
        return _kubectl_type(words[1:])
    if program == 'aws':
        return TYPE_AWS
    if program == 'ssh':
        return TYPE_SSH_OTHER
    if program.startswith('python'):
        return _python_type(ppid, words[1:], main_pid)
    return TYPE_OTHER


def _kib_to_bytes(value: str) -> int:
    # /proc/<pid>/status reports sizes as '<n> kB'.
    parts = value.split()
    return int(parts[0]) * 1024 if parts and parts[0].isdigit() else 0


def _count_unlisted_zombies(proc_dir: str, listed: Set[int]) -> int:
    """Zombies of this cgroup, which cgroup v2 leaves out of cgroup.procs."""
    own_cgroup = _read(os.path.join(proc_dir, 'self', 'cgroup'))
    if own_cgroup is None:
        return 0
    try:
        names = os.listdir(proc_dir)
    except OSError:
        return 0
    zombies = 0
    for name in names:
        if not name.isdigit() or int(name) in listed:
            continue
        status = _read_status(proc_dir, int(name))
        if (status is not None and status.get('State', '').startswith('Z') and
                _read(os.path.join(proc_dir, name, 'cgroup')) == own_cgroup):
            zombies += 1
    return zombies


def scan(
    main_pid: Optional[int] = None,
    cgroup_dir: str = CGROUP_DIR,
    proc_dir: str = PROC_DIR,
    exclude_pids: Collection[int] = ()
) -> Optional[Snapshot]:
    """Read the container's memory and count its processes by type.

    Returns None when the container's cgroup v2 memory files are not
    readable.
    """
    start = time.monotonic()
    memory = _read_memory(cgroup_dir)
    if memory is None:
        return None
    usage_bytes, stat_bytes = memory
    if main_pid is None:
        main_pid = os.getpid()
    types = {process_type: TypeUsage() for process_type in TYPES}
    pids = [pid for pid in _read_pids(cgroup_dir) if pid not in exclude_pids]
    for pid in pids:
        status = _read_status(proc_dir, pid)
        if status is None:
            # Exited between the cgroup.procs read and now.
            continue
        ppid = int(status.get('PPid', '0') or 0)
        process_type = classify(pid, ppid, _read_argv(proc_dir, pid),
                                status.get('Name', ''), status.get('State', ''),
                                main_pid)
        usage_of_type = types[process_type]
        rss_anon = _kib_to_bytes(status.get('RssAnon', ''))
        usage_of_type.processes += 1
        usage_of_type.threads += int(status.get('Threads', '0') or 0)
        usage_of_type.rss_anon_bytes += rss_anon
        usage_of_type.max_rss_anon_bytes = max(usage_of_type.max_rss_anon_bytes,
                                               rss_anon)
        usage_of_type.rss_bytes += _kib_to_bytes(status.get('VmRSS', ''))
    unlisted_zombies = _count_unlisted_zombies(proc_dir,
                                               set(pids) | set(exclude_pids))
    types[TYPE_ZOMBIE].processes += unlisted_zombies
    types[TYPE_ZOMBIE].threads += unlisted_zombies
    return Snapshot(usage_bytes=usage_bytes,
                    stat_bytes=stat_bytes,
                    unreclaimable_bytes=unreclaimable_bytes(
                        usage_bytes, stat_bytes),
                    limit_bytes=_read_limit(cgroup_dir),
                    types=types,
                    duration_seconds=time.monotonic() - start)


# A census of a few thousand processes takes well under a second when the
# child is not starved of CPU.
_SUBPROCESS_TIMEOUT_SECONDS = 60


def scan_in_subprocess(main_pid: Optional[int] = None,
                       cgroup_dir: str = CGROUP_DIR,
                       proc_dir: str = PROC_DIR) -> Optional[Snapshot]:
    """scan() in a child interpreter, which does not share this one's GIL.

    The child leaves itself out of the census. duration_seconds includes the
    child's startup.
    """
    start = time.monotonic()
    if main_pid is None:
        main_pid = os.getpid()
    result = subprocess.run([
        sys.executable, '-I', '-S',
        os.path.abspath(__file__),
        str(main_pid), cgroup_dir, proc_dir
    ],
                            capture_output=True,
                            check=True,
                            timeout=_SUBPROCESS_TIMEOUT_SECONDS)
    data = json.loads(result.stdout)
    if data is None:
        return None
    data['types'] = {
        process_type: TypeUsage(**usage)
        for process_type, usage in data['types'].items()
    }
    data['duration_seconds'] = time.monotonic() - start
    return Snapshot(**data)


def _main(argv: List[str]) -> None:
    snapshot = scan(int(argv[0]), argv[1], argv[2], exclude_pids=(os.getpid(),))
    json.dump(None if snapshot is None else dataclasses.asdict(snapshot),
              sys.stdout)


if __name__ == '__main__':
    _main(sys.argv[1:])
