"""Sky subprocess daemon.
Wait for parent_pid to exit, then SIGTERM (or SIGKILL if needed) the child
processes of proc_pid.
"""
import argparse
import os
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
# as wedged and SIGKILL it. Configurable via env var; default 30 s.
# 30 s gives a normal program plenty of time to call wait()/reap a
# briefly-zombie child between fork() and the parent's next instruction;
# real wedges sit at zombie for the entire lifetime of the wedged ancestor.
ZOMBIE_GRACE_ENV_VAR = 'SKYPILOT_ZOMBIE_GRACE_SECONDS'
_DEFAULT_ZOMBIE_GRACE_SECONDS = 30.0


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


def _zombie_wedge_sweep(descendants: List[psutil.Process],
                        zombie_first_seen: Dict[int,
                                                float], grace_seconds: float,
                        proc_pid: int, parent_pid: int) -> None:
    """Age each zombie descendant; SIGKILL the wedged parent of any that
    have outlived the grace period.

    A wedged process — one whose SIGTERM handler raised an exception out
    of the wait path — accumulates zombie children that never get reaped.
    When a zombie has been zombie for > grace_seconds, its parent isn't
    just slow, it's stuck; SIGKILL is the only way out. The PPID-transition
    machinery in the caller then handles the parent's still-alive siblings
    on the next tick (they reparent to the subreaper, which is proc_pid).

    Excluded targets: PID 1, the watched subreaper itself, and the outer
    parent (the SkyPilot orchestrator) — killing any of those would
    bring the whole task down, defeating the daemon's purpose.
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
            print(
                f'Wedge detected: descendant pid={descendant.pid} has been '
                f'zombie for >{grace_seconds:.0f}s under ppid={wedged_ppid} '
                f'({wedged.name()}); the parent has stopped reaping its '
                f'children. SIGKILLing the wedged parent so any still-alive '
                f'siblings reparent to the subreaper (pid={proc_pid}) and '
                f'get cleaned up by the PPID-transition sweep.',
                file=sys.stderr,
                flush=True)
            wedged.kill()
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as e:
            print(
                f'Wedge detected but SIGKILL denied for ppid={wedged_ppid}: '
                f'{e}',
                file=sys.stderr,
                flush=True)
        # Either we killed the wedged parent (zombie will be reaped by
        # the new init), or we hit AccessDenied (won't retry until the
        # zombie pid is reused). Either way, drop the entry.
        zombie_first_seen.pop(descendant.pid, None)


def kill_process_tree(process: psutil.Process,
                      children: List[psutil.Process]) -> bool:
    """Kill the process tree of the target process."""
    if process is not None:
        # Kill the target process first to avoid having more children, or fail
        # the process due to the children being defunct.
        children = [process] + children

    if not children:
        sys.exit()

    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue

    # Wait 30s for the processes to exit gracefully.
    time.sleep(30)

    # SIGKILL if they're still running.
    for child in children:
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
            for child in tmp_children:
                try:
                    new_ppid = child.ppid()
                except psutil.NoSuchProcess:
                    continue
                old_ppid = ppid_history.get(child.pid)
                ppid_history[child.pid] = new_ppid
                if (old_ppid is not None and old_ppid != new_ppid and
                        new_ppid == proc_pid):
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
                                    zombie_grace_seconds, proc_pid, parent_pid)
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
