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

# Whether the daemon force-kills an alive-but-wedged direct child of the
# watched process (see _zombie_wedge_sweep). Default enabled; '0' disables.
REAP_WEDGED_PARENTS_ENV_VAR = 'SKYPILOT_REAP_WEDGED_PARENTS'

# How long a zombie descendant must persist before its parent counts as
# wedged. 60 s is far above any well-behaved program's wait() latency, so
# crossing it is an unambiguous signal the parent has stopped reaping.
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


def _same_session(child_pid: int, ref_sid: Optional[int]) -> bool:
    """Return True if `child_pid` belongs to session `ref_sid`.

    This is the boundary that separates an orphan we should reap from a
    daemon we should leave alone. We filter by session, not process group,
    because the watched command runs under an interactive bash (`bash -i`)
    whose job control puts every `cmd &` into its own process group while
    keeping it in the same session; only an explicit setsid() (the real
    detach signal used by ray's GCS, uvicorn workers, `sky api start`, etc.)
    leaves the session. A pgid filter would wrongly treat a `&`-backgrounded
    orphan as detached and leak it.

    Errs on the side of letting the descendant live: returns False (skip)
    on any lookup error or when the reference session is unknown.
    """
    if ref_sid is None:
        return False
    try:
        return os.getsid(child_pid) == ref_sid
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
                        proc_sid: Optional[int]) -> None:
    """Age each zombie descendant; force-kill the wedged workload parent of
    any that have outlived the grace period.

    A wedged process — one whose SIGTERM handler raised an exception out of
    the wait path and is now stuck in signal.pause()/Event.wait() — never
    reaps its children, so a child it forked sits as a zombie indefinitely.
    Once that zombie has aged past grace_seconds we kill the parent: SIGTERM
    first (signal-interruptible wedges exit cleanly on a 2nd SIGTERM), then
    SIGKILL after a short grace. Its still-alive sibling workers then
    reparent to the subreaper (proc_pid) and the PPID-transition reap in the
    caller cleans them up next tick.

    The wedged parent must be a *direct child of proc_pid* — i.e. the
    process the `run:` block backgrounded (`python … &`), which is exactly
    Problem 2's shape. This structural criterion is what keeps the sweep
    from touching SkyPilot's own long-lived daemons: the jobs/serve/pool
    controller and its executors are deep descendants (or in their own
    session), never direct children of the watched run-block bash, so a
    transient zombie under them — common for a busy event-driven daemon —
    can never be mistaken for a wedge. Also excluded: PID 0/1, the watched
    subreaper, the orchestrator, and anything that setsid()'d into its own
    session (a deliberately-detached daemon; see _same_session).
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
        try:
            wedged = psutil.Process(wedged_ppid)
            wedged_ppid_parent = wedged.ppid()
            wedged_name = wedged.name()
        except psutil.NoSuchProcess:
            zombie_first_seen.pop(descendant.pid, None)
            continue
        # Structural gate: only the run-block's own backgrounded workload
        # (a direct child of proc_pid) can be a wedge target. A deeper
        # descendant with a lingering zombie is infrastructure (e.g. a
        # controller executor), not the user's wedged workload — leave it.
        if wedged_ppid_parent != proc_pid:
            zombie_first_seen.pop(descendant.pid, None)
            continue
        # A direct child that setsid()'d into its own session detached on
        # purpose (a daemon meant to outlive the run block) — spare it.
        if not _same_session(wedged_ppid, proc_sid):
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
                      children: List[psutil.Process],
                      proc_sid: Optional[int] = None) -> bool:
    """Kill the process tree of the target process.

    Descendants that setsid()'d into their own session are spared (see
    _same_session) — these are daemons deliberately detached to outlive the
    invocation (ray's GCS, uvicorn workers, `sky api start`). The subreaper
    attribute reparents them into our tree, so without this filter we'd kill
    them. `proc_sid` is passed in (captured by main() while the process was
    alive) rather than recomputed here: this runs after the watch loop, when
    the watched process may already be dead and os.getsid() would fail.
    """
    if process is not None:
        # Kill the target process first to avoid having more children, or fail
        # the process due to the children being defunct.
        children = [process] + children

    if not children:
        sys.exit()

    # Filter out intentionally-detached descendants. `process` itself
    # shares its session, so it's always included.
    targets = [c for c in children if _same_session(c.pid, proc_sid)]

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

    # Capture the watched process's session id now, while it is (likely)
    # still alive. We reuse this in the end-of-life kill_process_tree even
    # if the watched process has since been killed — os.getsid() on a dead
    # pid would fail and disable the detached-daemon filter. See
    # kill_process_tree() and _same_session().
    proc_sid: Optional[int] = None
    if process is not None:
        try:
            proc_sid = os.getsid(args.proc_pid)
        except (OSError, ProcessLookupError):
            proc_sid = None

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
        # proc_sid (captured above) gates the PPID-transition reap below so
        # we don't kill intentionally-detached descendants (setsid'd
        # background daemons like ray's GCS server) that the subreaper
        # attribute has pulled into our tree. See _same_session().
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
            # Exception: descendants in a different session are
            # intentionally detached (setsid'd background daemons like
            # ray's GCS server, uvicorn workers, sky api start daemons).
            # The subreaper attribute pulls them into our tree, but they
            # are *meant* to outlive the invocation that spawned them, so
            # we must not kill them. _same_session() filters them out.
            for child in tmp_children:
                try:
                    new_ppid = child.ppid()
                except psutil.NoSuchProcess:
                    continue
                old_ppid = ppid_history.get(child.pid)
                ppid_history[child.pid] = new_ppid
                if (old_ppid is not None and old_ppid != new_ppid and
                        new_ppid == proc_pid):
                    if not _same_session(child.pid, proc_sid):
                        continue
                    _log(f'Reaping orphaned descendant pid={child.pid}: '
                         f'reparented from dead ppid={old_ppid} to the '
                         f'subreaper (proc_pid={proc_pid}); sending SIGTERM. '
                         f'Escalates to SIGKILL in the end-of-life sweep if '
                         f'it ignores SIGTERM.')
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
                                    proc_sid)
                zombie_first_seen = {
                    pid: ts
                    for pid, ts in zombie_first_seen.items()
                    if pid in active_pids
                }
            time.sleep(1)

    # The loop exited; clean up via the mechanism the caller chose.
    if pgid is not None:
        # kill-pg mode (USE_KILL_PG_ENV_VAR, the coroutine/streaming path:
        # `sky logs` / `sky jobs logs`). The watched process is its own
        # session+group leader, so its group is exactly this invocation's
        # `kubectl exec`/`ssh` log proxy. Fire whenever the loop exits —
        # request cancellation kills the watched process while the API
        # server lives, and gating on orchestrator death would leak the
        # proxy. Detached daemons setsid() into their own group, so killpg
        # leaves them alone.
        kill_process_group(pgid)
    elif parent_process is None or not parent_process.is_running():
        # kill-tree mode (ray job path). Sweep only when the orchestrator
        # died — the original orphaned-bash teardown case. On a clean
        # watched-process exit the live PPID-transition reap and wedge sweep
        # above have already handled real orphans; the session filter in
        # kill_process_tree spares detached daemons either way.
        kill_process_tree(process, children, proc_sid)


if __name__ == '__main__':
    main()
