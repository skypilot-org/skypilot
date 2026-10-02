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

Measurement only.
"""
import dataclasses
import os
import time
from typing import Dict, List, Optional, Tuple

CGROUP_DIR = '/sys/fs/cgroup'
PROC_DIR = '/proc'

# memory.stat keys exported as they are. `kernel` exists from Linux 5.18 on.
MEMORY_STAT_KEYS = ('anon', 'file', 'kernel', 'shmem')

# Process types. 'server' and 'worker:<group>' match the `type` label of
# sky_apiserver_process_peak_rss.
TYPE_MAIN = 'main'
TYPE_SERVER = 'server'
TYPE_CONTROLLER = 'controller'
TYPE_OTHER = 'other'

# Set by setproctitle in executor_initializer: SkyPilot:executor:<group>:<pid>.
_EXECUTOR_TITLE_PREFIX = 'SkyPilot:executor:'
_CONTROLLER_MODULE = 'sky.jobs.controller'
# multiprocessing's spawn start method passes this flag to every child.
_SPAWN_CHILD_FLAG = '--multiprocessing-fork'


@dataclasses.dataclass
class TypeUsage:
    """Processes of one type in the container."""
    processes: int = 0
    threads: int = 0
    rss_anon_bytes: int = 0
    max_rss_anon_bytes: int = 0


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

    memory.current minus page cache (memory.stat `file`), with shmem added
    back: memory.stat counts tmpfs and shared memory inside `file`, but those
    pages cannot be dropped.
    """
    if 'file' not in stat_bytes:
        return None
    return usage_bytes - stat_bytes['file'] + stat_bytes.get('shmem', 0)


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
        if key in ('PPid', 'Threads', 'RssAnon'):
            fields[key] = value.strip()
    return fields


def _read_argv(proc_dir: str, pid: int) -> List[str]:
    try:
        with open(os.path.join(proc_dir, str(pid), 'cmdline'), 'rb') as f:
            raw = f.read()
    except OSError:
        return []
    return raw.decode('utf-8', errors='replace').split('\0')


def classify(pid: int, ppid: int, argv: List[str], main_pid: int) -> str:
    """Return the process type of one process in the container."""
    if pid == main_pid:
        return TYPE_MAIN
    if argv and argv[0].startswith(_EXECUTOR_TITLE_PREFIX):
        group = argv[0][len(_EXECUTOR_TITLE_PREFIX):].split(':', 1)[0]
        return f'worker:{group}'
    for i, arg in enumerate(argv):
        if arg == f'-m{_CONTROLLER_MODULE}' or (
                arg == '-m' and argv[i + 1:i + 2] == [_CONTROLLER_MODULE]):
            return TYPE_CONTROLLER
    # Executor workers retitle themselves on start, so the remaining spawn
    # children of the main process are the uvicorn workers.
    if ppid == main_pid and _SPAWN_CHILD_FLAG in argv:
        return TYPE_SERVER
    return TYPE_OTHER


def _kib_to_bytes(value: str) -> int:
    # /proc/<pid>/status reports sizes as '<n> kB'.
    parts = value.split()
    return int(parts[0]) * 1024 if parts and parts[0].isdigit() else 0


def scan(main_pid: Optional[int] = None,
         cgroup_dir: str = CGROUP_DIR,
         proc_dir: str = PROC_DIR) -> Optional[Snapshot]:
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
    types: Dict[str, TypeUsage] = {}
    for pid in _read_pids(cgroup_dir):
        status = _read_status(proc_dir, pid)
        if status is None:
            # Exited between the cgroup.procs read and now.
            continue
        ppid = int(status.get('PPid', '0') or 0)
        process_type = classify(pid, ppid, _read_argv(proc_dir, pid), main_pid)
        usage_of_type = types.setdefault(process_type, TypeUsage())
        rss_anon = _kib_to_bytes(status.get('RssAnon', ''))
        usage_of_type.processes += 1
        usage_of_type.threads += int(status.get('Threads', '0') or 0)
        usage_of_type.rss_anon_bytes += rss_anon
        usage_of_type.max_rss_anon_bytes = max(usage_of_type.max_rss_anon_bytes,
                                               rss_anon)
    return Snapshot(usage_bytes=usage_bytes,
                    stat_bytes=stat_bytes,
                    unreclaimable_bytes=unreclaimable_bytes(
                        usage_bytes, stat_bytes),
                    limit_bytes=_read_limit(cgroup_dir),
                    types=types,
                    duration_seconds=time.monotonic() - start)
