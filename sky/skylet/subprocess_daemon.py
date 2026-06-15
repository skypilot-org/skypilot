"""Sky subprocess daemon.
Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""
import argparse
import os
import pathlib
import signal
import sys
import time
from typing import Dict, List, Optional

import psutil

# Environment variable to enable kill_pg in subprocess daemon.
USE_KILL_PG_ENV_VAR = 'SKYPILOT_SUBPROCESS_DAEMON_KILL_PG'

# Environment variable controlling whether the daemon force-kills an
# alive-but-wedged ancestor of a long-running zombie descendant. Default
# enabled; set to '0' to disable.
REAP_WEDGED_PARENTS_ENV_VAR = 'SKYPILOT_REAP_WEDGED_PARENTS'

# How long a zombie descendant must persist before we treat its parent
# as wedged and SIGKILL it. Configurable via env var; default 60 s.
# A well-behaved program reaps any child it forked within microseconds
# to milliseconds; the conservative 60 s threshold avoids false-positive
# kills of programs that happen to use unusual reap patterns (raw os.fork
# without wait, very-slow polling loops) while still catching real wedges
# (which sit at zombie for the entire lifetime of the wedged ancestor).
# Users for whom 60 s is too aggressive can disable the sweep entirely
# via SKYPILOT_REAP_WEDGED_PARENTS=0.
ZOMBIE_GRACE_ENV_VAR = 'SKYPILOT_ZOMBIE_GRACE_SECONDS'
_DEFAULT_ZOMBIE_GRACE_SECONDS = 60.0

# How long to wait for a SIGTERM to take effect before escalating to
# SIGKILL on a wedged ancestor. Some wedge shapes are signal-interruptible
# (e.g. signal.pause(), mp.Event().wait()) — a second SIGTERM unwinds them.
# Sending SIGTERM first, then SIGKILL on timeout, lets those processes
# exit cleanly (running their atexit hooks) instead of being force-killed.
_WEDGE_SIGTERM_GRACE_SECONDS = 5.0

# Daemon log file (separate per task — sharing a path across daemons would
# interleave concurrent writes). Set by main() so that helper functions
# can log via _log(); when this is None we fall back to /dev/null-equivalent
# silence rather than risk writes to a closed/missing file.
_LOG_PATH: Optional[str] = None


def _same_pgid(child_pid: int, ref_pgid: Optional[int]) -> bool:
    """Return True if `child_pid` shares process group `ref_pgid`.

    Used to filter out intentionally-detached descendants from cleanup
    sweeps. A process that calls setsid()/setpgid(0, 0) — the canonical
    daemonize() pattern — moves itself into its own process group, even
    if its PID is still inside the watched subreaper's process tree.
    Such a process is meant to outlive the invocation that spawned it
    (e.g. `ray start --head` double-forks its GCS server into a detached
    session; `uvicorn` workers detach similarly), so we must NOT kill it
    when the watched command exits or when the kernel reparents it to
    our subreaper.

    The PR_SET_CHILD_SUBREAPER attribute set by run_with_log's preexec_fn
    pulls these detached descendants back into our tree (they would
    otherwise reparent to PID 1). Without this pgid filter the cleanup
    paths would kill them indiscriminately — see commit message for
    the regression mode this prevents.

    Returns False (i.e. treat as detached, skip) on any lookup error so
    that ambiguity errs on the side of letting the descendant live.
    """
    if ref_pgid is None:
        return True
    try:
        return os.getpgid(child_pid) == ref_pgid
    except (OSError, ProcessLookupError):
        return False


def _log(message: str) -> None:
    """Append a single log line to the daemon's log file (if set).

    Daemonize() detaches stdout/stderr from the parent (and
    kill_process_daemon spawns us with stdout=DEVNULL/stderr=DEVNULL),
    so plain print() goes nowhere. Route everything through this helper
    so wedge-kill events and other diagnostics are recoverable from
    ~/.sky/subprocess_daemon-<pid>.log after the fact.
    """
    if _LOG_PATH is None:
        return
    try:
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {message}\n')
    except OSError:
        # Best-effort logging; never crash the daemon over a log write.
        pass


def daemonize():
    """Detaches the process from its parent process with double-forking.

    This detachment is crucial in the context of SkyPilot and Ray job. When
    'sky cancel' is executed, it uses Ray's stop job API to terminate the job.
    Without daemonization, this subprocess_daemon process will still be a child
    of the parent process which would be terminated along with the parent
    process, ray::task or the cancel request for jobs, which is launched with
    Ray job. Daemonization ensures this process survives the 'sky cancel'
    command, allowing it to prevent orphaned processes of Ray job.
    """
    # First fork: Creates a child process identical to the parent
    if os.fork() > 0:
        # Parent process exits, allowing the child to run independently
        sys.exit()

    # Continues to run from first forked child process.
    # Detach from parent environment.
    os.setsid()

    # Second fork: Creates a grandchild process
    if os.fork() > 0:
        # First child exits, orphaning the grandchild
        sys.exit()
    # Continues execution in the grandchild process
    # This process is now fully detached from the original parent and terminal


def get_pgid_if_leader(pid) -> Optional[int]:
    """Get the process group ID of the target process if it is the leader."""
    try:
        pgid = os.getpgid(pid)
        # Only use process group if the target process is the leader. This is
        # to avoid killing the entire process group while the target process is
        # just a subprocess in the group.
        if pgid == pid:
            print(f'Process group {pgid} is the leader.')
            return pgid
        return None
    except Exception:  # pylint: disable=broad-except
        # Process group is only available in UNIX.
        return None


def kill_process_group(pgid: int) -> bool:
    """Kill the target process group."""
    try:
        print(f'Terminating process group {pgid}...')
        os.killpg(pgid, signal.SIGTERM)
    except Exception:  # pylint: disable=broad-except
        return False

    # Wait 30s for the process group to exit gracefully.
    time.sleep(30)

    try:
        print(f'Force killing process group {pgid}...')
        os.killpg(pgid, signal.SIGKILL)
    except Exception:  # pylint: disable=broad-except
        pass

    return True


def _term_then_kill(target: psutil.Process,
                    grace_seconds: float = _WEDGE_SIGTERM_GRACE_SECONDS) -> str:
    """Send SIGTERM, wait up to grace_seconds for exit, then SIGKILL if alive.

    Returns a short tag describing the outcome ('term' / 'kill' / 'gone' /
    'denied') for logging.

    Why TERM-first even for a "wedged" process: many wedge shapes are
    signal-interruptible. Empirically, processes wedged in signal.pause(),
    mp.Event().wait(), or other blocking-but-signal-aware calls unwind
    cleanly on a second SIGTERM (the handler runs, the originating except
    block has already executed, the exception propagates to top-level and
    the process exits naturally). Force-killing those would skip atexit
    handlers and child-cleanup that the program might still complete.
    For wedges that truly ignore SIGTERM, SIGKILL still fires after
    grace_seconds.
    """
    try:
        target.terminate()
    except psutil.NoSuchProcess:
        return 'gone'
    except psutil.AccessDenied:
        return 'denied'
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            if not target.is_running() or target.status() == (
                    psutil.STATUS_ZOMBIE):
                return 'term'
        except psutil.NoSuchProcess:
            return 'term'
        time.sleep(0.5)
    try:
        target.kill()
        return 'kill'
    except psutil.NoSuchProcess:
        return 'term'
    except psutil.AccessDenied:
        return 'denied'


def _zombie_wedge_sweep(descendants: List[psutil.Process],
                        zombie_first_seen: Dict[int, float],
                        grace_seconds: float, proc_pid: int, parent_pid: int,
                        proc_pgid: Optional[int]) -> None:
    """Age each zombie descendant; force-kill the wedged parent of any
    that have outlived the grace period.

    A wedged process — one whose SIGTERM handler raised an exception out
    of the wait path — accumulates zombie children that never get reaped.
    When a zombie has been zombie for > grace_seconds, its parent isn't
    just slow, it's stuck; SIGTERM is tried first (some wedge shapes are
    signal-interruptible and exit cleanly on a 2nd SIGTERM), then SIGKILL
    after a short grace if still alive. The PPID-transition machinery in
    the caller then handles the parent's still-alive siblings on the next
    tick (they reparent to the subreaper, which is proc_pid).

    Excluded targets: PID 1, the watched subreaper itself, and the outer
    parent (the SkyPilot orchestrator) — killing any of those would bring
    the whole task down, defeating the daemon's purpose.

    False-positive risk note: programs that use raw os.fork() without ever
    calling wait() will leave indefinite zombies that this sweep will
    treat as wedges. The default 60 s grace is conservative; most users
    won't hit this. Set SKYPILOT_REAP_WEDGED_PARENTS=0 to disable, or
    raise SKYPILOT_ZOMBIE_GRACE_SECONDS if the workload is known to leave
    long-lived benign zombies.
    """
    now = time.monotonic()
    for descendant in descendants:
        try:
            status = descendant.status()
        except psutil.NoSuchProcess:
            zombie_first_seen.pop(descendant.pid, None)
            continue
        if status != psutil.STATUS_ZOMBIE:
            zombie_first_seen.pop(descendant.pid, None)
            continue
        first_seen = zombie_first_seen.setdefault(descendant.pid, now)
        if now - first_seen < grace_seconds:
            continue
        try:
            wedged_ppid = descendant.ppid()
        except psutil.NoSuchProcess:
            zombie_first_seen.pop(descendant.pid, None)
            continue
        if wedged_ppid in (0, 1, proc_pid, parent_pid):
            # Either kernel/init owns it, or it's a protected ancestor.
            continue
        # A wedged ancestor in a different process group is an
        # intentionally-detached background daemon (e.g. uvicorn worker
        # supervisor, ray's GCS, sky api start daemon). It's meant to
        # outlive the invocation that spawned it, so don't kill it just
        # because one of its children is zombie. Detached daemons reap
        # their own children; if they're not, that's their own problem
        # to surface, not ours to interfere with.
        if not _same_pgid(wedged_ppid, proc_pgid):
            zombie_first_seen.pop(descendant.pid, None)
            continue
        try:
            wedged = psutil.Process(wedged_ppid)
            wedged_name = wedged.name()
        except psutil.NoSuchProcess:
            zombie_first_seen.pop(descendant.pid, None)
            continue
        _log(f'Wedge detected: descendant pid={descendant.pid} has been '
             f'zombie for >{grace_seconds:.0f}s under ppid={wedged_ppid} '
             f'({wedged_name}); the parent has stopped reaping its children. '
             f'Sending SIGTERM, escalating to SIGKILL after '
             f'{_WEDGE_SIGTERM_GRACE_SECONDS:.0f}s if still alive.')
        outcome = _term_then_kill(wedged)
        _log(f'Wedge cleanup for ppid={wedged_ppid}: outcome={outcome}. '
             f'Any still-alive siblings will reparent to the subreaper '
             f'(pid={proc_pid}) and be cleaned up by the PPID-transition '
             f'sweep.')
        # Either we killed the wedged parent (zombie will be reaped by
        # the new init), or we hit AccessDenied (won't retry until the
        # zombie pid is reused). Either way, drop the entry.
        zombie_first_seen.pop(descendant.pid, None)


def kill_process_tree(process: psutil.Process,
                      children: List[psutil.Process]) -> bool:
    """Kill the process tree of the target process.

    Descendants in a different process group than `process` (i.e. that
    have called setsid()/setpgid(0, 0) to detach) are excluded — these
    are typically daemons spawned via double-fork that are *meant* to
    outlive their invocation (ray's GCS server, uvicorn workers, sky
    api start background services). With the subreaper attribute set
    on the watched process by run_with_log's preexec_fn, such detached
    descendants now reparent into our tree instead of to PID 1; without
    this pgid filter we would unconditionally kill them and break the
    very detachment pattern they rely on.
    """
    proc_pgid: Optional[int] = None
    if process is not None:
        try:
            proc_pgid = os.getpgid(process.pid)
        except (OSError, ProcessLookupError):
            proc_pgid = None
        # Kill the target process first to avoid having more children, or fail
        # the process due to the children being defunct.
        children = [process] + children

    if not children:
        sys.exit()

    # Filter out intentionally-detached descendants. `process` itself
    # shares pgid with itself, so it's always included.
    targets = [c for c in children if _same_pgid(c.pid, proc_pgid)]

    if not targets:
        sys.exit()

    for child in targets:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue

    # Wait 30s for the processes to exit gracefully.
    time.sleep(30)

    # SIGKILL if they're still running.
    for child in targets:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            continue

    return True


def main():
    daemonize()

    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--proc-pid', type=int, required=True)
    parser.add_argument(
        '--initial-children',
        type=str,
        default='',
        help=(
            'Comma-separated list of initial children PIDs. This is to guard '
            'against the case where the target process has already terminated, '
            'while the children are still running.'),
    )
    args = parser.parse_args()

    # Open a per-daemon log file in ~/.sky/. kill_process_daemon spawns
    # us with stdout/stderr both routed to /dev/null, so plain prints
    # would be invisible — without this log, wedge-kill events would
    # leave no audit trail.
    global _LOG_PATH
    try:
        log_dir = pathlib.Path(os.path.expanduser('~/.sky'))
        log_dir.mkdir(parents=True, exist_ok=True)
        _LOG_PATH = str(log_dir / f'subprocess_daemon-{args.proc_pid}.log')
        _log(f'Daemon started: proc_pid={args.proc_pid} '
             f'parent_pid={args.parent_pid} pid={os.getpid()}')
    except OSError:
        # If we cannot open the log file, continue without logging
        # rather than crash the daemon.
        _LOG_PATH = None

    process = None
    parent_process = None
    try:
        process = psutil.Process(args.proc_pid)
        parent_process = psutil.Process(args.parent_pid)
    except psutil.NoSuchProcess:
        pass

    # Initialize children list from arguments
    children = []
    if args.initial_children:
        for pid in args.initial_children.split(','):
            try:
                child = psutil.Process(int(pid))
                children.append(child)
            except (psutil.NoSuchProcess, ValueError):
                pass

    pgid: Optional[int] = None
    if os.environ.get(USE_KILL_PG_ENV_VAR) == '1':
        # Use kill_pg on UNIX system if allowed to reduce the resource usage.
        # Note that both implementations might leave subprocessed uncancelled:
        # - kill_process_tree(default): a subprocess is able to detach itself
        #   from the process tree use the same technique as daemonize(). Also,
        #   since we refresh the process tree per second, if the subprocess is
        #   launched between the [last_poll, parent_die] interval, the
        #   subprocess will not be captured will not be killed.
        # - kill_process_group: kill_pg will kill all the processed in the group
        #   but if a subprocess calls setpgid(0, 0) to detach itself from the
        #   process group (usually to daemonize itself), the subprocess will
        #   not be killed.
        if process is not None:
            pgid = get_pgid_if_leader(process.pid)

    if process is not None and parent_process is not None:
        # PPID history: pid -> last observed ppid. We use this to detect
        # descendants that get reparented to process.pid mid-run, which is
        # the kernel's signal that their original parent died and the
        # PR_SET_CHILD_SUBREAPER attribute on process.pid (set by
        # subprocess_utils.set_child_subreaper via preexec_fn in
        # log_lib.run_with_log) caused them to be adopted by us. Without
        # the subreaper attribute these orphans would have hopped to PID
        # 1 and we'd have no way to find them. Without this detection
        # they'd just sit in the tree holding whatever resource (GPU
        # context, RAM, open sockets, file locks) until the outer process
        # finally exits.
        ppid_history: Dict[int, int] = {}
        # Zombie-first-seen timestamps: pid -> monotonic time we first
        # observed the process in STATUS_ZOMBIE. Used to detect wedged
        # alive ancestors that never call wait() on their dead children
        # (see _zombie_wedge_sweep below).
        zombie_first_seen: Dict[int, float] = {}
        proc_pid = process.pid
        parent_pid = parent_process.pid
        # Watched process's pgid — used to gate the PPID-transition reap
        # below so we don't kill intentionally-detached descendants
        # (setsid'd background daemons like ray's GCS server) that the
        # subreaper attribute has pulled into our tree. See _same_pgid().
        try:
            proc_pgid: Optional[int] = os.getpgid(proc_pid)
        except (OSError, ProcessLookupError):
            proc_pgid = None
        reap_wedged_parents = (os.environ.get(REAP_WEDGED_PARENTS_ENV_VAR, '1')
                               != '0')
        try:
            zombie_grace_seconds = float(
                os.environ.get(ZOMBIE_GRACE_ENV_VAR,
                               _DEFAULT_ZOMBIE_GRACE_SECONDS))
        except ValueError:
            zombie_grace_seconds = _DEFAULT_ZOMBIE_GRACE_SECONDS
        # Wait for either parent or target process to exit
        while process.is_running() and parent_process.is_running():
            try:
                tmp_children = process.children(recursive=True)
            except psutil.NoSuchProcess:
                tmp_children = []
            if pgid is None and tmp_children:
                # Refresh process tree for cleanup if process group is not
                # available.
                children = tmp_children
            # Reparent detection: any descendant whose ppid just transitioned
            # to our proc_pid was adopted via the subreaper attribute and is
            # an orphan of a dead intermediate parent. Terminate it
            # immediately — escalation to SIGKILL happens in the final
            # sweep below if it doesn't honor SIGTERM.
            #
            # Exception: descendants in a different process group are
            # intentionally detached (setsid'd background daemons like
            # ray's GCS server, uvicorn workers, sky api start daemons).
            # The subreaper attribute pulls them into our tree, but they
            # are *meant* to outlive the invocation that spawned them, so
            # we must not kill them. _same_pgid() filters them out.
            for child in tmp_children:
                try:
                    new_ppid = child.ppid()
                except psutil.NoSuchProcess:
                    continue
                old_ppid = ppid_history.get(child.pid)
                ppid_history[child.pid] = new_ppid
                if (old_ppid is not None and old_ppid != new_ppid and
                        new_ppid == proc_pid):
                    if not _same_pgid(child.pid, proc_pgid):
                        continue
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        continue
            # Prune ppid_history to only the currently-alive descendants.
            # Without this we'd accumulate entries for every PID we ever
            # saw (memory growth on long-running jobs) and, more
            # importantly, a stale entry could trigger a false-positive
            # SIGTERM if the OS reuses the PID for a newly-spawned
            # *legitimate* descendant of proc_pid.
            active_pids = {c.pid for c in tmp_children}
            ppid_history = {
                pid: ppid
                for pid, ppid in ppid_history.items()
                if pid in active_pids
            }
            # Wedge detection: an alive ancestor that has stopped calling
            # wait() on its children — typically because a SIGTERM handler
            # raised an exception out of the wait path — accumulates zombie
            # children. The PPID-transition machinery above can't see this
            # case: the children never reparent (their parent is still
            # alive, just stuck). Detect by aging each zombie; once one has
            # been zombie for > zombie_grace_seconds, treat its parent as
            # wedged and SIGKILL it. The kernel reparents the (still-alive)
            # siblings to our proc_pid subreaper, the PPID-transition logic
            # above then cleans those up on the next tick.
            if reap_wedged_parents:
                _zombie_wedge_sweep(tmp_children, zombie_first_seen,
                                    zombie_grace_seconds, proc_pid, parent_pid,
                                    proc_pgid)
                zombie_first_seen = {
                    pid: ts
                    for pid, ts in zombie_first_seen.items()
                    if pid in active_pids
                }
            time.sleep(1)

    if pgid is not None:
        kill_process_group(pgid)
    else:
        kill_process_tree(process, children)


if __name__ == '__main__':
    main()
