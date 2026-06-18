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

# Daemon log file (separate per task — sharing a path across daemons would
# interleave concurrent writes). Set by main() so that helper functions
# can log via _log(); when this is None we fall back to /dev/null-equivalent
# silence rather than risk writes to a closed/missing file.
_LOG_PATH: Optional[str] = None


def _same_session(child_pid: int, ref_sid: Optional[int]) -> bool:
    """Return True if `child_pid` belongs to session `ref_sid`.

    The outer boundary that separates an orphan we might reap from a daemon
    we leave alone. We filter by session, not process group, because the
    watched command runs under an interactive bash (`bash -i`) whose job
    control puts every `cmd &` into its own process group while keeping it
    in the same session; only an explicit setsid() (e.g. `sky api start`
    background services, uvicorn supervisors) leaves the session. A pgid
    filter would wrongly treat a `&`-backgrounded orphan as detached and
    leak it.

    Same-session is necessary but NOT sufficient to reap: some daemons stay
    in the watched session (ray's user-space `ray start` does not setsid its
    GCS/raylet). Callers pair this with _process_group_orphaned() to spare
    those. See that function.

    Errs on the side of letting the descendant live: returns False (skip)
    on any lookup error or when the reference session is unknown.
    """
    if ref_sid is None:
        return False
    try:
        return os.getsid(child_pid) == ref_sid
    except (OSError, ProcessLookupError):
        return False


def _process_group_orphaned(pid: int, proc_pgid: Optional[int]) -> bool:
    """Return True if reparented `pid` is a leftover worth reaping.

    The second gate (after _same_session) for the live orphan reap. A
    same-session process that reparents to our subreaper is not necessarily
    a leftover: it may be a daemon a still-running job spawned. We decide by
    its process group:

    1. Same group as the watched process (`pgid == proc_pgid`): a direct
       workload process. When job control is off (the non-interactive shell
       used on VMs) a `&`-backgrounded worker stays in the run-block shell's
       own group, so a killed-parent orphan lands here — reap it. (This is
       the case the pod-only smoke tests don't cover.)
    2. A different group whose leader is DEAD: an orphaned job (e.g. under
       `bash -i` job control the killed `parent.py` led its own group) —
       reap it.
    3. A different group whose leader is ALIVE: a daemon belonging to a
       still-running job. Ray's user-space GCS/raylet do NOT setsid; they
       sit in the launcher's (`start_cluster`) group and reparent to us when
       the intermediate `ray start` exits, while the launcher keeps running
       — spare it.

    Case 1 is safe because the watched process is started as a session/group
    leader (log_lib.run_with_log uses start_new_session=True), so its own
    group holds only direct workload, never a deliberately-detached daemon.

    We only evaluate a process at the instant its ppid transitions to us;
    an already-reparented process is never re-checked. Errs toward sparing
    on any lookup error.
    """
    try:
        pgid = os.getpgid(pid)
    except (OSError, ProcessLookupError):
        return False
    if proc_pgid is not None and pgid == proc_pgid:
        return True  # in the watched process's own group -> workload -> reap
    try:
        # Signal 0 probes existence of the group leader (pid == pgid).
        os.kill(pgid, 0)
    except ProcessLookupError:
        return True  # distinct group, leader gone -> orphaned -> reap
    except (PermissionError, OSError):
        return False  # leader exists (or unknown) -> assume alive -> spare
    return False  # distinct group, leader alive -> daemon -> spare


def _log(message: str) -> None:
    """Append a single log line to the daemon's log file (if set).

    Daemonize() detaches stdout/stderr from the parent (and
    kill_process_daemon spawns us with stdout=DEVNULL/stderr=DEVNULL),
    so plain print() goes nowhere. Route everything through this helper
    so orphan-reap events and other diagnostics are recoverable from
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
    # would be invisible — without this log, orphan-reap events would
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

    # Capture the watched process's process-group id too. The live orphan
    # reap reaps descendants that share this group (direct workload processes,
    # the job-control-off / VM case) while sparing daemons in a separate
    # live-leader group. See _process_group_orphaned().
    proc_pgid: Optional[int] = None
    if process is not None:
        try:
            proc_pgid = os.getpgid(args.proc_pid)
        except (OSError, ProcessLookupError):
            proc_pgid = None

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
        proc_pid = process.pid
        # proc_sid (captured above) gates the PPID-transition reap below so
        # we don't kill intentionally-detached descendants (setsid'd
        # background daemons) that the subreaper attribute has pulled into
        # our tree. See _same_session().
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
            # Reparent detection: a descendant whose ppid just transitioned to
            # our proc_pid was adopted via the subreaper when its parent died.
            # Two gates decide reap-vs-spare (see _same_session and
            # _process_group_orphaned for the full rationale): skip a setsid'd
            # daemon (different session), then skip a daemon of a still-running
            # job (separate process group with a live leader). Survivors get
            # SIGTERM now; the final sweep escalates to SIGKILL if ignored.
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
                    if not _process_group_orphaned(child.pid, proc_pgid):
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
        # watched-process exit the live PPID-transition reap above has
        # already handled real orphans; the session filter in
        # kill_process_tree spares detached daemons either way.
        kill_process_tree(process, children, proc_sid)


if __name__ == '__main__':
    main()
