"""Autostop utilities — and lifecycle-hooks storage (see naming note).

Naming note: this module holds both legitimately-autostop state
(``AutostopConfig``, idle-timer indicator, ``set_autostop`` /
``get_autostop_config``, ``set_autostopping_started`` /
``get_is_autostopping``) AND the generalized lifecycle-hooks
list (``set_hooks`` / ``get_hooks`` plus the proto serializers
``hooks_to_protobuf`` / ``hooks_from_protobuf``). The hook helpers
live here because the hooks payload piggybacks on the existing
``SetAutostop`` gRPC (see ``sky/schemas/proto/autostopv1.proto``)
rather than having its own ``SetHooks`` RPC. When a follow-up PR
splits out a dedicated ``SetHooks`` RPC, the hook helpers should
move to a new ``hooks_lib.py`` module alongside ``hook_executor.py``;
the autostop-specific helpers stay here. No external behavior
changes by then — pure refactor, deferred to keep PR1 minimal.
"""
import enum
import json
import os
import pickle
import shlex
import subprocess
import time
import typing
from typing import Any, Dict, List, Optional

import filelock

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
_AUTOSTOP_CONFIG_LOCK_PATH = '~/.sky/locks/.autostop_config.lock'
MAX_DURABLE_ERROR_SUMMARY_LENGTH = 256

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


class AutodownExecutionStrategy(enum.Enum):
    """Where an autodown teardown is allowed to execute."""

    SERVER_ONLY = 'server_only'
    HEAD_WITH_SERVER_FALLBACK = 'head_with_server_fallback'
    LEGACY_HEAD_CREDENTIALS = 'legacy_head_credentials'

    @classmethod
    def from_protobuf(
        cls, protobuf_value: 'autostopv1_pb2.AutodownExecutionStrategy'
    ) -> 'AutodownExecutionStrategy':
        head_with_fallback = (
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_HEAD_WITH_SERVER_FALLBACK
        )
        protobuf_to_enum = {
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_SERVER_ONLY:
                cls.SERVER_ONLY,
            head_with_fallback: cls.HEAD_WITH_SERVER_FALLBACK,
            autostopv1_pb2.AUTODOWN_EXECUTION_STRATEGY_LEGACY_HEAD_CREDENTIALS:
                cls.LEGACY_HEAD_CREDENTIALS,
        }
        if protobuf_value not in protobuf_to_enum:
            raise ValueError('Unknown autodown execution strategy.')
        return protobuf_to_enum[protobuf_value]


DEFAULT_AUTODOWN_EXECUTION_STRATEGY = (
    AutodownExecutionStrategy.LEGACY_HEAD_CREDENTIALS)


class DurableAutodownState(enum.Enum):
    """Durable execution progress visible to the server reconciler."""

    UNSPECIFIED = 'unspecified'
    ARMED = 'armed'
    HEAD_TEARDOWN_STARTED = 'head_teardown_started'
    SERVER_TEARDOWN_REQUIRED = 'server_teardown_required'

    def to_protobuf(self) -> 'autostopv1_pb2.DurableAutodownState':
        enum_to_protobuf = {
            DurableAutodownState.UNSPECIFIED:
                autostopv1_pb2.DURABLE_AUTODOWN_STATE_UNSPECIFIED,
            DurableAutodownState.ARMED:
                autostopv1_pb2.DURABLE_AUTODOWN_STATE_ARMED,
            DurableAutodownState.HEAD_TEARDOWN_STARTED:
                autostopv1_pb2.DURABLE_AUTODOWN_STATE_HEAD_TEARDOWN_STARTED,
            DurableAutodownState.SERVER_TEARDOWN_REQUIRED:
                autostopv1_pb2.DURABLE_AUTODOWN_STATE_SERVER_TEARDOWN_REQUIRED,
        }
        return enum_to_protobuf[self]


class AutostopConfigUpdateResult(enum.Enum):
    """Outcome of a durable autostop configuration update."""

    APPLIED = 'applied'
    REPLAYED = 'replayed'
    REJECTED = 'rejected'


class AutostopConfig:
    """Autostop configuration persisted by the skylet.

    Active autodown settings start in ``ARMED``. Strict execution may advance
    once to ``HEAD_TEARDOWN_STARTED`` and then, on failure, to
    ``SERVER_TEARDOWN_REQUIRED``. Storing any new setting replaces that state;
    cancellation and non-down autostop use ``UNSPECIFIED``.
    """

    def __init__(
        self,
        autostop_idle_minutes: int,
        boot_time: float,
        backend: Optional[str],
        wait_for: AutostopWaitFor,
        down: bool = False,
        hook: Optional[str] = None,
        hook_timeout: Optional[int] = None,
        cluster_hash: Optional[str] = None,
        generation: Optional[int] = None,
        execution_strategy:
        AutodownExecutionStrategy = DEFAULT_AUTODOWN_EXECUTION_STRATEGY):
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
        self.cluster_hash = cluster_hash
        self.generation = generation
        self.execution_strategy = execution_strategy
        if down and autostop_idle_minutes >= 0:
            self.durable_execution_state = DurableAutodownState.ARMED
        else:
            self.durable_execution_state = DurableAutodownState.UNSPECIFIED
        self.error_summary: Optional[str] = None
        self.durable_hooks: Optional[List[Dict[str, Any]]] = None

    def __setstate__(self, state: dict):
        state.setdefault('down', False)
        state.setdefault('hook', None)
        state.setdefault('hook_timeout', constants.DEFAULT_HOOK_TIMEOUT_SECONDS)
        state.setdefault('cluster_hash', None)
        state.setdefault('generation', None)
        state.setdefault('execution_strategy',
                         DEFAULT_AUTODOWN_EXECUTION_STRATEGY)
        default_durable_state = DurableAutodownState.UNSPECIFIED
        if state.get('down',
                     False) and state.get('autostop_idle_minutes', -1) >= 0:
            default_durable_state = DurableAutodownState.ARMED
        state.setdefault('durable_execution_state', default_durable_state)
        state.setdefault('error_summary', None)
        state.setdefault('durable_hooks', None)
        self.__dict__.update(state)


def _get_autostop_config_unlocked() -> AutostopConfig:
    config_str = configs.get_config(_AUTOSTOP_CONFIG_KEY)
    if config_str is None:
        return AutostopConfig(-1, -1, None, DEFAULT_AUTOSTOP_WAIT_FOR)
    config = pickle.loads(config_str)
    # Ensure backward compatibility: set hook and hook_timeout if not present
    if not hasattr(config, 'hook'):
        config.hook = None
    if not hasattr(config, 'hook_timeout'):
        config.hook_timeout = constants.DEFAULT_HOOK_TIMEOUT_SECONDS
    if _is_strict_config(config) and config.durable_hooks is None:
        config.durable_hooks = get_hooks()
    return config


def _get_autostop_config_lock() -> filelock.FileLock:
    lock_path = os.path.expanduser(_AUTOSTOP_CONFIG_LOCK_PATH)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    return filelock.FileLock(lock_path)


def get_autostop_config() -> AutostopConfig:
    with _get_autostop_config_lock():
        return _get_autostop_config_unlocked()


def _has_strict_identity(cluster_hash: Optional[str],
                         generation: Optional[int]) -> bool:
    return bool(cluster_hash) and generation is not None and generation > 0


def _is_strict_execution_strategy(
        execution_strategy: AutodownExecutionStrategy) -> bool:
    return execution_strategy != DEFAULT_AUTODOWN_EXECUTION_STRATEGY


def _is_strict_config(config: AutostopConfig) -> bool:
    return (_is_strict_execution_strategy(config.execution_strategy) and
            _has_strict_identity(config.cluster_hash, config.generation))


def _same_desired_autostop_config(current: AutostopConfig,
                                  requested: AutostopConfig) -> bool:
    return (current.autostop_idle_minutes == requested.autostop_idle_minutes and
            current.backend == requested.backend and
            current.wait_for == requested.wait_for and
            current.down == requested.down and
            current.hook == requested.hook and
            current.hook_timeout == requested.hook_timeout and
            current.cluster_hash == requested.cluster_hash and
            current.generation == requested.generation and
            current.execution_strategy == requested.execution_strategy and
            current.durable_hooks == requested.durable_hooks)


def _requested_hooks_update(
    hook: Optional[str],
    hook_timeout: Optional[int],
    hooks: Optional[List[Dict[str, Any]]],
    clear_hooks: bool,
    down: bool,
) -> Optional[List[Dict[str, Any]]]:
    """Return the effective hook replacement, or None to preserve hooks."""
    if clear_hooks:
        return []
    if hooks:
        return hooks
    if hook:
        legacy_event = 'down' if down else 'stop'
        return [{
            'run': hook,
            'events': [legacy_event],
            'timeout': hook_timeout or constants.DEFAULT_HOOK_TIMEOUT_SECONDS,
        }]
    return None


def set_autostop(
    idle_minutes: int,
    backend: Optional[str],
    wait_for: AutostopWaitFor,
    down: bool,
    hook: Optional[str] = None,
    hook_timeout: Optional[int] = None,
    cluster_hash: Optional[str] = None,
    generation: Optional[int] = None,
    execution_strategy:
    AutodownExecutionStrategy = DEFAULT_AUTODOWN_EXECUTION_STRATEGY,
    hooks: Optional[List[Dict[str, Any]]] = None,
    clear_hooks: bool = False,
) -> AutostopConfigUpdateResult:
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
        cluster_hash: Durable identity for the cluster incarnation.
        generation: Durable setting generation allocated by the server.
        execution_strategy: Where autodown teardown is allowed to execute.
        hooks: Full lifecycle-hooks list supplied by a v7+ gRPC request.
        clear_hooks: Whether a v7+ gRPC request explicitly clears hooks.
    """
    is_strict_request = _is_strict_execution_strategy(execution_strategy)
    if (is_strict_request and
            not _has_strict_identity(cluster_hash, generation)):
        raise ValueError(
            'Durable autodown requires a non-empty cluster hash and positive '
            'generation.')

    boot_time = psutil.boot_time()

    autostop_config = AutostopConfig(idle_minutes, boot_time, backend, wait_for,
                                     down, hook, hook_timeout, cluster_hash,
                                     generation, execution_strategy)
    with _get_autostop_config_lock():
        current_config = _get_autostop_config_unlocked()
        hooks_update = _requested_hooks_update(hook, hook_timeout, hooks,
                                               clear_hooks, down)
        if is_strict_request:
            autostop_config.durable_hooks = (hooks_update if hooks_update
                                             is not None else get_hooks())
        if _is_strict_config(current_config):
            if not is_strict_request:
                return AutostopConfigUpdateResult.REJECTED
            assert generation is not None
            assert current_config.generation is not None
            if generation < current_config.generation:
                return AutostopConfigUpdateResult.REJECTED
            if generation == current_config.generation:
                if (cluster_hash != current_config.cluster_hash or
                        not _same_desired_autostop_config(
                            current_config, autostop_config)):
                    return AutostopConfigUpdateResult.REJECTED
                return AutostopConfigUpdateResult.REPLAYED
            if (current_config.durable_execution_state !=
                    DurableAutodownState.ARMED):
                return AutostopConfigUpdateResult.REJECTED

        values: Dict[str, Any] = {
            _AUTOSTOP_CONFIG_KEY: pickle.dumps(autostop_config),
            _AUTOSTOP_LAST_ACTIVE_TIME: str(time.time()),
        }
        if hooks_update is not None:
            values[_HOOKS_CONFIG_KEY] = (json.dumps(hooks_update)
                                         if hooks_update else '')
        configs.set_configs(values)

    logger.debug(
        f'set_autostop(): idle_minutes {idle_minutes}, down {down}, '
        f'wait_for {wait_for.value}, hook {"present" if hook else "none"}, '
        f'hook_timeout {hook_timeout}s, execution_strategy '
        f'{execution_strategy.value}.')
    return AutostopConfigUpdateResult.APPLIED


def _matches_current_durable_config(config: AutostopConfig, cluster_hash: str,
                                    generation: int) -> bool:
    return (config.autostop_idle_minutes >= 0 and config.down and
            config.cluster_hash == cluster_hash and
            config.generation == generation)


def _normalize_and_bound_error_summary(
        error_summary: Optional[str]) -> Optional[str]:
    """Bound a caller-provided generic summary for wire transport.

    Callers must not pass raw exceptions or provider responses. This helper
    normalizes whitespace and enforces the storage limit; it cannot identify
    arbitrary credentials embedded in input text.
    """
    if error_summary is None:
        return None
    normalized = ' '.join(error_summary.split())
    return normalized[:MAX_DURABLE_ERROR_SUMMARY_LENGTH]


def mark_head_teardown_started(cluster_hash: str, generation: int) -> bool:
    """Atomically start head teardown for the matching armed setting.

    Returns false without changing storage when the identity is stale or the
    current setting already left ``ARMED``.
    """
    with _get_autostop_config_lock():
        config = _get_autostop_config_unlocked()
        if (not _matches_current_durable_config(config, cluster_hash,
                                                generation) or
                config.durable_execution_state != DurableAutodownState.ARMED):
            return False
        config.durable_execution_state = (
            DurableAutodownState.HEAD_TEARDOWN_STARTED)
        config.error_summary = None
        configs.set_config(_AUTOSTOP_CONFIG_KEY, pickle.dumps(config))
        return True


def mark_server_teardown_required(cluster_hash: str,
                                  generation: int,
                                  error_summary: Optional[str] = None) -> bool:
    """Atomically request server teardown for the matching current setting.

    ``ARMED`` server-only settings and failed ``HEAD_TEARDOWN_STARTED``
    settings may transition. Returns false for stale identities or an already
    completed transition.
    """
    with _get_autostop_config_lock():
        config = _get_autostop_config_unlocked()
        if (not _matches_current_durable_config(config, cluster_hash,
                                                generation) or
                config.durable_execution_state
                == DurableAutodownState.SERVER_TEARDOWN_REQUIRED):
            return False
        config.durable_execution_state = (
            DurableAutodownState.SERVER_TEARDOWN_REQUIRED)
        config.error_summary = _normalize_and_bound_error_summary(error_summary)
        configs.set_config(_AUTOSTOP_CONFIG_KEY, pickle.dumps(config))
        return True


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
    """Convert a list of hook dicts into protobuf ``Hook`` messages.

    Lives in this module because the hooks payload currently rides on
    the ``SetAutostop`` gRPC; see the module docstring for the
    planned move to a dedicated ``hooks_lib.py`` after a future PR
    introduces a parallel ``SetHooks`` RPC.
    """
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

    Same "lives in autostop_lib for wire-compat reasons" caveat as
    :func:`hooks_to_protobuf`; should move to ``hooks_lib.py`` when
    the planned proto split lands.
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

    Called during launch via the ``SetAutostop`` gRPC (which carries
    hooks for wire-compat reasons — see the proto + module docstring).
    The list is read by ``hook_executor`` when any teardown event
    fires. Belongs in ``hooks_lib.py`` once the planned split lands;
    parked here for PR1 minimalism.
    """
    if hooks:
        configs.set_config(_HOOKS_CONFIG_KEY, json.dumps(hooks))
    else:
        # Empty payload clears the key.
        configs.set_config(_HOOKS_CONFIG_KEY, '')


def get_hooks() -> List[Dict[str, Any]]:
    """Load the stored lifecycle-hooks list, or [] if never set.

    Counterpart to :func:`set_hooks`; see the module docstring for the
    "lives in autostop_lib for wire-compat reasons, move to hooks_lib
    in a follow-up" note.
    """
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

        Emits version-specific calls for mixed-version environments:
          - skylet < 4 / 5: legacy signatures (no hook / waitless)
          - skylet 5–6: single-hook form via `hook` / `hook_timeout`
          - skylet 7: set_autostop + set_hooks(full list)
          - skylet ≥ 8: one atomic set_autostop call with inline hooks
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
        # v7 branch: forward ``hook`` / ``hook_timeout`` so the
        # skylet's set_autostop routing bridges a pre-v7 client's
        # legacy hook arg into the new hooks list (see
        # autostop_lib.set_autostop ~line 200). Only emit
        # ``set_hooks(...)`` when the caller explicitly passes a list:
        # ``hooks=None`` means "leave stored alone" (no explicit
        # opinion); an empty-list emit there would wipe the entry the
        # bridge just stored — exactly the failure mode that surfaced
        # as `TestBackwardCompatibility::
        # test_client_server_compatibility_new_server` timing out
        # waiting for AUTOSTOPPING.
        set_hooks_line = ('' if hooks is None else
                          f'\n autostop_lib.set_hooks({hooks!r})')
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
            '\nelif skylet_lib_version < 8: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, '
            f'autostop_lib.{wait_for}, {down}, hook={hook!r}, '
            f'hook_timeout={hook_timeout})'
            f'{set_hooks_line}'
            '\nelse: '
            f'\n autostop_lib.set_autostop({idle_minutes}, {backend!r}, '
            f'autostop_lib.{wait_for}, {down}, hook={hook!r}, '
            f'hook_timeout={hook_timeout}, hooks={hooks!r}, '
            f'clear_hooks={hooks == []})',
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
