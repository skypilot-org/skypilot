"""Log-tail streaming for Slurm v1 managed jobs.

Factored out of ``managed_job_runtime.py`` per PLAN.md "File layout":
the runtime adapter's ``tail_logs`` constructs a ``SlurmLogStreamer``
and calls ``.run()`` on it. ``download_logs`` stays inline in the
runtime — it's a single ``cat`` and write-to-disk pair.

Behavior is preserved verbatim from the previous in-line implementation;
this is a layout-only change.
"""
import os
import shlex
import subprocess
import sys
import threading
import typing
from typing import Any, Callable, Dict, List, Optional

from sky import exceptions
from sky import sky_logging
from sky.adaptors import slurm
from sky.utils import command_runner
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    from sky.provision.slurm.managed_job_runtime import _Target

logger = sky_logging.init_logger(__name__)

# Cadence for the side-thread polling sacct during follow-mode log tail.
_TAIL_TERMINAL_POLL_SECONDS = 5
# How long after sacct reports a terminal state we let the SSH tail
# subprocess drain stdout before forcibly closing it.
_TAIL_DRAIN_SECONDS = 5


def _login_node_runner(
        ssh_config: Dict[str, Any]) -> 'command_runner.SSHCommandRunner':
    """Mirror of ``managed_job_runtime._login_node_runner``.

    Duplicated to keep this module free of an import edge back into
    the runtime adapter (which imports us lazily from ``tail_logs``).
    The constructor block is the same as
    ``instance.py::_create_managed_job_v1``'s login-node runner.
    """
    identities_only = bool(ssh_config.get('identities_only', False))
    return command_runner.SSHCommandRunner(
        (ssh_config['hostname'], int(ssh_config.get('port', 22))),
        ssh_config['user'],
        ssh_config.get('private_key'),
        ssh_proxy_command=ssh_config.get('proxycommand'),
        ssh_proxy_jump=ssh_config.get('proxyjump'),
        enable_interactive_auth=True,
        disable_identities_only=not identities_only,
    )


class SlurmLogStreamer:
    """SSH-tail-based streamer for the v1 sbatch ``--output`` file.

    Constructed by the runtime adapter from a resolved ``_Target`` and
    the optional ``tail`` / ``tail_offset`` / ``follow`` parameters. The
    streamer owns subprocess management, the sacct-watchdog side thread,
    and footer rendering. The runtime adapter is responsible for
    resolving the log path and supplying the terminal-state lookups it
    already implements (passed in via the helpers below).
    """

    def __init__(
        self,
        *,
        target: '_Target',
        log_path: str,
        client: 'slurm.SlurmClient',
        terminal_states: frozenset,
        sacct_get_state: Callable[['slurm.SlurmClient', str], Optional[str]],
        state_to_job_exit_code: Callable[[Optional[str]], int],
        follow: bool,
        tail: Optional[int],
        tail_offset: Optional[int],
        write_fn: Callable[[str], None],
    ) -> None:
        self._target = target
        self._log_path = log_path
        self._client = client
        self._terminal_states = terminal_states
        self._sacct_get_state = sacct_get_state
        self._state_to_job_exit_code = state_to_job_exit_code
        self._follow = follow
        self._tail = tail
        self._tail_offset = tail_offset
        self._write_fn = write_fn

    def run(self) -> int:
        """Stream the log; return the JobExitCode for the controller."""
        runner = _login_node_runner(self._target.ssh_config)
        base_ssh = runner.ssh_base_command(
            ssh_mode=command_runner.SshMode.NON_INTERACTIVE,
            port_forward=None,
            connect_timeout=None)

        quoted_path = shlex.quote(self._log_path)
        if self._tail_offset is not None and self._tail_offset > 0:
            # ``tail -c +<bytes>`` starts at byte offset (1-indexed).
            # Saved-log replay uses line-based offsets; live tail with
            # a byte offset only triggers from controller-side callers
            # that compute the offset against the same file.
            remote_cmd = (
                f'tail -c +{int(self._tail_offset) + 1} '
                f'{"-F" if self._follow else ""} {quoted_path} 2>/dev/null')
        elif self._follow:
            remote_cmd = f'tail -F {quoted_path} 2>/dev/null'
        else:
            n = self._tail if (self._tail is not None and
                               self._tail > 0) else None
            tail_flag = f'-n {n}' if n is not None else '-n +1'
            remote_cmd = f'tail {tail_flag} {quoted_path} 2>/dev/null'

        if not self._follow:
            return self._tail_once(base_ssh, remote_cmd)
        return self._tail_follow(base_ssh, remote_cmd)

    def _tail_once(self, base_ssh: List[str], remote_cmd: str) -> int:
        # Pass ``remote_cmd`` as a single argv element. SSH joins any
        # remaining args with spaces into one command string for the
        # remote shell, so a single arg with shell metachars (``|``,
        # ``2>/dev/null``) is preserved verbatim. Compare
        # ``command_runner.run`` which sets ``shell=True`` locally and
        # therefore needs ``shlex.quote``; here ``subprocess`` uses argv
        # mode so no extra quoting is needed.
        full_cmd = base_ssh + [remote_cmd]
        try:
            proc = subprocess.run(full_cmd,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  check=False)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Slurm v1 tail (one-shot) failed: {e}')
            return exceptions.JobExitCode.FAILED.value
        if proc.stdout:
            self._write_fn(proc.stdout.decode('utf-8', errors='replace'))
        # Render the same footer as the follow path so the saved-log
        # consumer sees a consistent shape.
        state = self._read_terminal_state()
        if state is not None:
            self._write_fn(
                ux_utils.finishing_message(f'Job finished (status: {state}).') +
                '\n')
            return self._state_to_job_exit_code(state)
        # Non-terminal one-shot read — return NOT_FINISHED so the caller
        # falls through to the standard already-running path.
        return exceptions.JobExitCode.NOT_FINISHED.value

    def _tail_follow(self, base_ssh: List[str], remote_cmd: str) -> int:
        # See ``_tail_once`` for argv vs shell-quote reasoning.
        full_cmd = base_ssh + [remote_cmd]
        try:
            proc = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                # Line-buffered text isn't reliable through SSH; read bytes.
                bufsize=0,
            )
        except Exception as e:  # pylint: disable=broad-except
            logger.warning(f'Failed to spawn Slurm v1 tail subprocess: {e}')
            return exceptions.JobExitCode.FAILED.value

        assert proc.stdout is not None
        stdout_fd = proc.stdout.fileno()

        terminal_state: Dict[str, Optional[str]] = {'value': None}
        stop_event = threading.Event()

        def _poll_state() -> None:
            while not stop_event.is_set():
                try:
                    state = self._client.get_job_state(self._target.job_id)
                    if state is None:
                        state = self._sacct_get_state(self._client,
                                                      self._target.job_id)
                except Exception as e:  # pylint: disable=broad-except
                    logger.debug(f'sacct poll failed: {e}')
                    state = None
                if state is not None and state in self._terminal_states:
                    terminal_state['value'] = state
                    # Give the SSH ``tail -F`` subprocess a short window
                    # to flush anything still in flight, then send it a
                    # SIGTERM so the blocking ``os.read`` in the main
                    # thread returns 0 and we proceed to the footer.
                    if stop_event.wait(_TAIL_DRAIN_SECONDS):
                        return
                    try:
                        proc.terminate()
                    except Exception:  # pylint: disable=broad-except
                        pass
                    return
                if stop_event.wait(_TAIL_TERMINAL_POLL_SECONDS):
                    return

        poll_thread = threading.Thread(target=_poll_state, daemon=True)
        poll_thread.start()

        try:
            # Stream bytes as they arrive. The poll thread is
            # responsible for ``proc.terminate()`` once it observes a
            # terminal sacct state, which closes the SSH pipe and lets
            # ``os.read`` return 0 here so we drop out cleanly. Reading
            # the raw fd avoids relying on ``BufferedReader`` behavior
            # across Python versions.
            while True:
                try:
                    chunk = os.read(stdout_fd, 4096)
                except OSError:
                    chunk = b''
                if not chunk:
                    break
                self._write_fn(chunk.decode('utf-8', errors='replace'))
        finally:
            stop_event.set()
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=_TAIL_DRAIN_SECONDS)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=_TAIL_DRAIN_SECONDS)
            except Exception:  # pylint: disable=broad-except
                pass
            poll_thread.join(timeout=_TAIL_DRAIN_SECONDS)

        state = terminal_state['value']
        if state is None:
            # Watchdog never observed terminal; do one last sacct query
            # so the footer reflects whatever Slurm now reports.
            state = self._read_terminal_state()

        if state is not None:
            self._write_fn(
                ux_utils.finishing_message(f'Job finished (status: {state}).') +
                '\n')
            return self._state_to_job_exit_code(state)
        # No terminal observed and tail dropped — surface as failed so
        # the controller's existing decision branches handle it.
        return exceptions.JobExitCode.FAILED.value

    def _read_terminal_state(self) -> Optional[str]:
        try:
            state = self._client.get_job_state(self._target.job_id)
            if state is None:
                state = self._sacct_get_state(self._client, self._target.job_id)
        except Exception as e:  # pylint: disable=broad-except
            logger.debug(f'Terminal state lookup failed: {e}')
            return None
        if state is None or state not in self._terminal_states:
            return None
        return state


def make_streamer_from_target(
    target: '_Target',
    log_path: str,
    client: 'slurm.SlurmClient',
    *,
    terminal_states: frozenset,
    sacct_get_state: Callable[['slurm.SlurmClient', str], Optional[str]],
    state_to_job_exit_code: Callable[[Optional[str]], int],
    follow: bool,
    tail: Optional[int],
    tail_offset: Optional[int],
    out=None,
) -> SlurmLogStreamer:
    """Convenience: build a streamer that writes to the current context.

    Constructs a ``write_fn`` bound to ``sys.stdout`` (or the supplied
    ``out``) so callers don't need to repeat the
    ``context.output_stream`` plumbing.
    """
    if out is None:
        # pylint: disable=import-outside-toplevel
        from sky.utils import context as context_lib
        ctx = context_lib.get()
        out = ctx.output_stream(sys.stdout) if ctx else sys.stdout

    def _write(msg: str) -> None:
        out.write(msg)
        out.flush()

    return SlurmLogStreamer(
        target=target,
        log_path=log_path,
        client=client,
        terminal_states=terminal_states,
        sacct_get_state=sacct_get_state,
        state_to_job_exit_code=state_to_job_exit_code,
        follow=follow,
        tail=tail,
        tail_offset=tail_offset,
        write_fn=_write,
    )
