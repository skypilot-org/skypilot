"""Autostop utilities."""
import enum
import json
import os
import pickle
import shlex
import subprocess
import time
import typing
from typing import Any, Dict, List, Optional

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.skylet import configs
from sky.skylet import constants
from sky.skylet import log_lib
from sky.utils import message_utils
from sky.utils import ux_utils

if typing.TYPE_CHECKING:
    import psutil

    from sky.schemas.generated import autostopv1_pb2
else:
    psutil = adaptors_common.LazyImport('psutil')
    # To avoid requiring protobuf to be installed on the client side.
    autostopv1_pb2 = adaptors_common.LazyImport(
        'sky.schemas.generated.autostopv1_pb2')

logger = sky_logging.init_logger(__name__)

_AUTOSTOP_CONFIG_KEY = 'autostop_config'
_HOOKS_CONFIG_KEY = 'lifecycle_hooks'

# This key-value is stored inside the 'configs' sqlite3 database, because both
# user-issued commands (this module) and the Skylet process running the
# StopEvent need to access that state.
_AUTOSTOP_LAST_ACTIVE_TIME = 'autostop_last_active_time'
# StopEvent sets this to the boot time when the autostop of the cluster
# starts. This is used for checking whether the cluster is in the process
# of autostopping for the current machine.
_AUTOSTOP_INDICATOR = 'autostop_indicator'


class AutostopWaitFor(enum.Enum):
    """Enum for the Autostop behaviour.

    JOBS: Wait for jobs to finish.
    JOBS_AND_SSH: Wait for jobs to finish and all SSH sessions to be closed.
    NONE: Unconditionally stop the cluster after the idle time.
    """
    JOBS_AND_SSH = 'jobs_and_ssh'
    JOBS = 'jobs'
    NONE = 'none'

    @classmethod
    def supported_modes(cls) -> List[str]:
        return [mode.value for mode in cls]

    @classmethod
    def cli_help_message(cls, pair: str) -> str:
        return f"""\
Determines the condition for resetting the idleness timer.
This option works in conjunction with ``--{pair}``. Options:

\b
1. ``jobs_and_ssh`` (default): Wait for in-progress jobs and SSH connections to finish.
2. ``jobs``: Only wait for in-progress jobs.
3. ``none``: Wait for nothing; autostop right after ``{pair}``."""

    @classmethod
    def from_str(cls, mode: str) -> 'AutostopWaitFor':
        """Returns the enum value for the given string."""
        if mode.lower() == cls.JOBS.value:
            return cls.JOBS
        elif mode.lower() == cls.JOBS_AND_SSH.value:
            return cls.JOBS_AND_SSH
        elif mode.lower() == cls.NONE.value:
            return cls.NONE
        else:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unsupported autostop wait mode: '
                                 f'{mode}. The mode must be either '
                                 f'\'{cls.JOBS_AND_SSH.value}\', '
                                 f'\'{cls.JOBS.value}\', or '
                                 f'\'{cls.NONE.value}\'. ')

    @classmethod
    def from_protobuf(
        cls, protobuf_value: 'autostopv1_pb2.AutostopWaitFor'
    ) -> Optional['AutostopWaitFor']:
        """Convert protobuf AutostopWaitFor enum to Python enum value."""
        protobuf_to_enum = {
            autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS_AND_SSH: cls.JOBS_AND_SSH,
            autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS: cls.JOBS,
            autostopv1_pb2.AUTOSTOP_WAIT_FOR_NONE: cls.NONE,
            autostopv1_pb2.AUTOSTOP_WAIT_FOR_UNSPECIFIED: None,
        }
        if protobuf_value not in protobuf_to_enum:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(
                    f'Unknown protobuf AutostopWaitFor value: {protobuf_value}')
        return protobuf_to_enum[protobuf_value]

    def to_protobuf(self) -> 'autostopv1_pb2.AutostopWaitFor':
        """Convert this Python enum value to protobuf enum value."""
        enum_to_protobuf = {
            AutostopWaitFor.JOBS_AND_SSH:
                autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS_AND_SSH,
            AutostopWaitFor.JOBS: autostopv1_pb2.AUTOSTOP_WAIT_FOR_JOBS,
            AutostopWaitFor.NONE: autostopv1_pb2.AUTOSTOP_WAIT_FOR_NONE,
        }
        if self not in enum_to_protobuf:
            with ux_utils.print_exception_no_traceback():
                raise ValueError(f'Unknown AutostopWaitFor value: {self}')
        return enum_to_protobuf[self]


DEFAULT_AUTOSTOP_WAIT_FOR: AutostopWaitFor = AutostopWaitFor.JOBS_AND_SSH


class AutostopConfig:
    """Autostop configuration."""

    def __init__(self,
                 autostop_idle_minutes: int,
                 boot_time: float,
                 backend: Optional[str],
                 wait_for: AutostopWaitFor,
                 down: bool = False,
                 hook: Optional[str] = None,
                 hook_timeout: Optional[int] = None):
        assert autostop_idle_minutes < 0 or backend is not None, (
            autostop_idle_minutes, backend)
        self.autostop_idle_minutes = autostop_idle_minutes
        self.boot_time = boot_time
        self.backend = backend
        self.wait_for = wait_for
        self.down = down
        self.hook = hook
        # Use the constant if hook_timeout is not specified
        if hook_timeout is None:
            hook_timeout = constants.DEFAULT_HOOK_TIMEOUT_SECONDS
        self.hook_timeout = hook_timeout

    def __setstate__(self, state: dict):
        state.setdefault('down', False)
        state.setdefault('hook', None)
        state.setdefault('hook_timeout', constants.DEFAULT_HOOK_TIMEOUT_SECONDS)
        self.__dict__.update(state)


def get_autostop_config() -> AutostopConfig:
    config_str = configs.get_config(_AUTOSTOP_CONFIG_KEY)
    if config_str is None:
        return AutostopConfig(-1, -1, None, DEFAULT_AUTOSTOP_WAIT_FOR)
    config = pickle.loads(config_str)
    # Ensure backward compatibility: set hook and hook_timeout if not present
    if not hasattr(config, 'hook'):
        config.hook = None
    if not hasattr(config, 'hook_timeout'):
        config.hook_timeout = constants.DEFAULT_HOOK_TIMEOUT_SECONDS
    return config


def set_autostop(idle_minutes: int,
                 backend: Optional[str],
                 wait_for: AutostopWaitFor,
                 down: bool,
                 hook: Optional[str] = None,
                 hook_timeout: Optional[int] = None) -> None:
    """Set autostop configuration.

    Args:
        idle_minutes: Minutes of idleness before autostop.
        backend: Backend name.
        wait_for: Condition for resetting idleness timer.
        down: Whether to tear down (autodown) instead of stop.
        hook: DEPRECATED single-hook string (pre-v7 wire). New callers
            use set_hooks(...) with the full list. Kept so pre-v7
            clients talking to a v7+ skylet still get their autostop
            hook routed into the generalized hooks list.
        hook_timeout: DEPRECATED timeout for the single hook.
    """
    boot_time = psutil.boot_time()

    autostop_config = AutostopConfig(idle_minutes, boot_time, backend, wait_for,
                                     down, hook, hook_timeout)
    configs.set_config(_AUTOSTOP_CONFIG_KEY, pickle.dumps(autostop_config))

    # A pre-v7 client routes its hook via `hook` / `hook_timeout`;
    # translate it into the generalized hooks list so hook_executor
    # sees a single source of truth. Pre-v7 master had a single
    # autostop hook that fired on idle-timer teardown regardless of
    # autodown — route it to ``down`` for autodown and ``stop``
    # otherwise so the new event taxonomy stays consistent.
    # TODO(zpoint): drop the `hook` / `hook_timeout` parameters and
    # this translation once SKYLET_LIB_VERSION's minimum supported
    # client is ≥ 7.
    if hook:
        legacy_event = 'down' if down else 'stop'
        set_hooks([{
            'run': hook,
            'events': [legacy_event],
            'timeout': (hook_timeout or constants.DEFAULT_HOOK_TIMEOUT_SECONDS),
        }])

    logger.debug(
        f'set_autostop(): idle_minutes {idle_minutes}, down {down}, '
        f'wait_for {wait_for.value}, hook {"present" if hook else "none"}, '
        f'hook_timeout {hook_timeout}s.')
    # Reset timer whenever an autostop setting is submitted, i.e. the idle
    # time will be counted from now.
    set_last_active_time_to_now()


def set_autostopping_started() -> None:
    """Sets the boot time of the machine when autostop starts.

    This function should be called when the cluster is started to autostop,
    and the boot time of the machine will be stored in the configs database
    as an autostop indicator, which is used for checking whether the cluster
    is in the process of autostopping. The indicator is valid only when the
    machine has the same boot time as the one stored in the indicator.
    """
    logger.debug('Setting is_autostopping.')
    configs.set_config(_AUTOSTOP_INDICATOR, str(psutil.boot_time()))


def get_is_autostopping() -> bool:
    """Returns whether the cluster is in the process of autostopping."""
    result = configs.get_config(_AUTOSTOP_INDICATOR)
    is_autostopping = (result == str(psutil.boot_time()))
    return is_autostopping


def get_is_autostopping_payload() -> str:
    """Payload for whether the cluster is in the process of autostopping."""
    is_autostopping = get_is_autostopping()
    return message_utils.encode_payload(is_autostopping)


def get_last_active_time() -> float:
    """Returns the last active time, or -1 if none has been set."""
    result = configs.get_config(_AUTOSTOP_LAST_ACTIVE_TIME)
    if result is not None:
        return float(result)
    return -1


_EVENT_TO_PROTO: Dict[str, int] = {}
_PROTO_TO_EVENT: Dict[int, str] = {}


def _ensure_event_maps() -> None:
    """Lazy-populate proto enum conversion maps.

    Avoids importing generated proto at module import time (the
    adaptor pattern already lazy-imports the bindings).
    """
    if _EVENT_TO_PROTO:
        return
    _EVENT_TO_PROTO.update({
        'stop': autostopv1_pb2.EVENT_STOP,
        'preemption': autostopv1_pb2.EVENT_PREEMPTION,
        'down': autostopv1_pb2.EVENT_DOWN,
    })
    _PROTO_TO_EVENT.update({v: k for k, v in _EVENT_TO_PROTO.items()})


def hooks_to_protobuf(hooks: List[Dict[str, Any]]):
    """Convert a list of hook dicts into protobuf ``Hook`` messages."""
    _ensure_event_maps()
    out = []
    for h in hooks:
        events = [_EVENT_TO_PROTO[e] for e in (h.get('events') or [])]
        msg = autostopv1_pb2.Hook(run=h['run'])
        # events field is typed as Iterable[Event] in the .pyi but accepts
        # ints at runtime (Event is an int-backed enum).
        msg.events.extend(events)  # type: ignore[arg-type]
        msg.timeout = h.get('timeout', constants.DEFAULT_HOOK_TIMEOUT_SECONDS)
        out.append(msg)
    return out


def hooks_from_protobuf(proto_hooks) -> List[Dict[str, Any]]:
    """Convert protobuf ``Hook`` messages back into hook dicts.

    Re-applies the ``events`` default on receive: proto3 ``repeated``
    has no presence, so an empty ``events`` list is wire-equivalent to
    "field omitted". Without the default, an empty list would silently
    match no event and the hook would never fire.
    """
    _ensure_event_maps()
    out: List[Dict[str, Any]] = []
    for h in proto_hooks:
        events = [_PROTO_TO_EVENT[e] for e in h.events if e in _PROTO_TO_EVENT]
        if not events:
            # Match Resources._normalize_hook_entry on the send side.
            events = ['stop', 'preemption', 'down']
        out.append({
            'run': h.run,
            'events': events,
            'timeout': h.timeout or constants.DEFAULT_HOOK_TIMEOUT_SECONDS,
        })
    return out


def set_hooks(hooks: Optional[List[Dict[str, Any]]]) -> None:
    """Store the cluster's lifecycle-hooks list.

    Called during launch via the AutostopCodeGen RPC. The list is
    read by hook_executor when any teardown event fires.
    """
    if hooks:
        configs.set_config(_HOOKS_CONFIG_KEY, json.dumps(hooks))
    else:
        # Empty payload clears the key.
        configs.set_config(_HOOKS_CONFIG_KEY, '')


def get_hooks() -> List[Dict[str, Any]]:
    """Load the stored lifecycle-hooks list, or [] if never set."""
    raw = configs.get_config(_HOOKS_CONFIG_KEY)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.warning(f'Could not decode stored hooks: {e}')
        return []


def set_last_active_time_to_now() -> None:
    """Sets the last active time to time.time()."""
    logger.debug('Setting last active time.')
    configs.set_config(_AUTOSTOP_LAST_ACTIVE_TIME, str(time.time()))


def has_active_ssh_sessions() -> bool:
    """Check if any PTY traces back to sshd in the process tree."""
    try:
        # psutil memoizes /dev/{tty*,pts/*} -> rdev at first call to
        # Process.terminal() with no TTL (psutil._psposix.get_terminal_map).
        # devpts entries are dynamic: if skylet's first tick runs while no
        # SSH session is active (e.g. right after `sky stop` + `sky start`),
        # the cache is frozen with no /dev/pts/* entries, and every later
        # Process.terminal() returns None for PTY-attached processes.
        # Clear the cache on each tick so newly-allocated PTYs are visible.
        # See https://github.com/skypilot-org/skypilot/issues/9524.
        # pylint: disable=protected-access
        try:
            cache_clear = psutil._psposix.get_terminal_map.cache_clear
        except AttributeError:
            logger.debug('[has_active_ssh] psutil._psposix.get_terminal_map'
                         ' has no cache_clear; psutil internal API moved.')
        else:
            cache_clear()
        # pylint: enable=protected-access
        pts_to_pid: dict[str, int] = {}
        all_terminal_procs: list = []
        for proc in psutil.process_iter(['pid', 'name', 'terminal']):
            terminal = proc.info['terminal']
            if terminal:
                all_terminal_procs.append(
                    (proc.info['pid'], proc.info.get('name'), terminal))
            if terminal and terminal.startswith('/dev/pts/'):
                pts_to_pid.setdefault(terminal, proc.info['pid'])
        logger.debug(f'[has_active_ssh] processes with non-None terminal: '
                     f'{all_terminal_procs}')
        logger.debug(f'[has_active_ssh] pts_to_pid: {pts_to_pid}')

        for terminal, pid in pts_to_pid.items():
            try:
                for parent in psutil.Process(pid).parents():
                    if parent.name() == 'sshd':
                        logger.debug(
                            f'[has_active_ssh] sshd ancestor found for '
                            f'pid={pid} on {terminal} -> returning True')
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return False
    except Exception as e:  # pylint: disable=broad-except
        logger.warning(f'Error checking active SSH sessions: {e}.')
        return False


def execute_autostop_hook(hook: Optional[str],
                          hook_timeout: Optional[int] = None) -> bool:
    """Execute the autostop hook script if provided.

    Args:
        hook: The hook script to execute, or None if no hook is set.
        hook_timeout: Timeout in seconds for hook execution. If None, uses
            DEFAULT_HOOK_TIMEOUT_SECONDS (3600 = 1 hour).

    Returns:
        True if hook executed successfully (or no hook), False if hook failed.
    """
    if hook is None or not hook.strip():
        return True

    if hook_timeout is None:
        hook_timeout = constants.DEFAULT_HOOK_TIMEOUT_SECONDS

    logger.info(f'Executing autostop hook (timeout: {hook_timeout}s)...')
    log_path = os.path.expanduser(constants.AUTOSTOP_HOOK_LOG_FILE)
    try:
        # Execute the hook script and log output to file
        returncode, stdout, stderr = log_lib.run_with_log(hook,
                                                          log_path,
                                                          require_outputs=True,
                                                          shell=True,
                                                          process_stream=True,
                                                          timeout=hook_timeout)

        if returncode != 0:
            logger.error(f'Autostop hook failed with return code {returncode}. '
                         f'Check {log_path} for details. '
                         f'stdout: {stdout}, stderr: {stderr}')
            return False

        logger.info(
            f'Autostop hook executed successfully. Logs saved to {log_path}. '
            f'stdout: {stdout}')
        if stderr:
            logger.error(f'Hook stderr: {stderr}')
        return True
    except subprocess.TimeoutExpired:
        logger.error(f'Autostop hook timed out after {hook_timeout} seconds. '
                     f'Check {log_path} for details.')
        return False
    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            f'Error executing autostop hook: {e}. '
            f'Check {log_path} for details.',
            exc_info=True)
        return False


class AutostopCodeGen:
    """Code generator for autostop utility functions.

    Usage:

      >> codegen = AutostopCodeGen.set_autostop(...)
    """
    _PREFIX = ['from sky.skylet import autostop_lib, constants']

    @classmethod
    def set_autostop(cls,
                     idle_minutes: int,
                     backend: str,
                     wait_for: Optional[AutostopWaitFor],
                     down: bool = False,
                     hook: Optional[str] = None,
                     hook_timeout: Optional[int] = None,
                     hooks: Optional[List[Dict[str, Any]]] = None) -> str:
        """Render skylet-side autostop + hooks setup as a Python one-liner.

        Dual-emits for mixed-version environments:
          - skylet < 4 / 5: legacy signatures (no hook / waitless)
          - skylet 5–6: single-hook form via `hook` / `hook_timeout`
          - skylet ≥ 7: set_autostop + set_hooks(full list)
        """
        if wait_for is None:
            wait_for = DEFAULT_AUTOSTOP_WAIT_FOR
        # Pre-v7 flattening: pre-v7 skylets fire their single ``hook``
        # on idle-timer teardown. Match the new-event equivalent for
        # this launch: ``down`` for autodown, ``stop`` for autostop.
        flat_hook = hook
        flat_timeout = hook_timeout
        legacy_event = 'down' if down else 'stop'
        if flat_hook is None and hooks:
            for entry in hooks:
                if legacy_event in (entry.get('events') or []):
                    flat_hook = entry['run']
                    flat_timeout = entry.get('timeout')
                    break
        hooks_payload = hooks or []
        code = [
            '\nskylet_lib_version = getattr(constants, "SKYLET_LIB_VERSION", 1)'
            '\nif skylet_lib_version < 4: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, '
            f'{down})'
            '\nelif skylet_lib_version < 5: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, '
            f'autostop_lib.{wait_for}, {down})'
            '\nelif skylet_lib_version < 7: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, '
            f'autostop_lib.{wait_for}, {down}, hook={flat_hook!r}, '
            f'hook_timeout={flat_timeout})'
            '\nelse: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, '
            f'autostop_lib.{wait_for}, {down})'
            f'\n autostop_lib.set_hooks({hooks_payload!r})',
        ]
        return cls._build(code)

    @classmethod
    def is_autostopping(cls) -> str:
        code = ['print(autostop_lib.get_is_autostopping_payload())']
        return cls._build(code)

    @classmethod
    def _build(cls, code: List[str]) -> str:
        code = cls._PREFIX + code
        code = ';'.join(code)
        return f'{constants.SKY_PYTHON_CMD} -u -c {shlex.quote(code)}'
