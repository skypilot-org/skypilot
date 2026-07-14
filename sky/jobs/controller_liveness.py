"""Pluggable liveness checks for managed job controller processes.

A managed job's controller is tracked in the job_info table as
(controller_pid, controller_pid_started_at, controller_server_id). The
default provider here only knows how to check a pid on the local machine,
which is meaningless once a job's controller could have been claimed by a
different server instance (e.g. a rolling update or a scaled-out
deployment): the pid might belong to a different machine entirely, or a new,
unrelated process on this machine could have reused the same pid. A plugin
that can identify controllers across server instances (e.g. via a shared
server registry) can register a provider that does so; see
``sky.server.plugins.ExtensionContext.register_controller_liveness_provider``.

Follows the registry pattern of ``sky.jobs.runner``: a single module-level
provider, ``register()`` to override it, and a lazy default so lookups always
succeed regardless of import ordering. ``register()`` is only expected to be
called during server/plugin startup, before request handling begins, so no
locking is needed.

Import direction: this module may import ``sky.jobs.state`` but must NOT
import ``sky.jobs.utils`` (utils imports this module).
"""
import abc
import dataclasses
import enum
import typing
from typing import Any, Mapping, Optional

from sky import sky_logging
from sky.adaptors import common as adaptors_common
from sky.jobs import state

if typing.TYPE_CHECKING:
    import psutil
else:
    psutil = adaptors_common.LazyImport('psutil')

logger = sky_logging.init_logger(__name__)


class ControllerLiveness(enum.Enum):
    """Verdict for whether a job's controller process is alive.

    UNKNOWN means the provider could not determine an answer (e.g. a
    transient error reaching whatever it checks). Callers must treat UNKNOWN
    as "possibly alive" and fail closed: never reset, terminalize, or
    re-submit a job on an UNKNOWN verdict.
    """
    ALIVE = 'ALIVE'
    DEAD = 'DEAD'
    UNKNOWN = 'UNKNOWN'


@dataclasses.dataclass(frozen=True)
class JobOwnerRecord:
    """A snapshot of who owns a job's controller, as observed by the caller.

    server_id is whatever was stamped in job_info.controller_server_id at
    claim time (None for legacy rows, and for any single-server deployment
    that never sets a server identity). legacy_job_id, if provided, enables
    the pre-#7051 cmdline-based check for controllers that predate
    pid_started_at tracking.
    """
    pid: Optional[int]
    pid_started_at: Optional[float]
    server_id: Optional[str]
    legacy_job_id: Optional[int] = None

    @classmethod
    def from_job_row(cls,
                     row: Mapping[str, Any],
                     legacy_job_id: Optional[int] = None) -> 'JobOwnerRecord':
        """Build a JobOwnerRecord from a job_info-derived row/dict.

        Normalizes a legacy negative controller_pid to its absolute value
        (see state.get_job_controller_process), so liveness checks and
        ownership comparisons always operate on the real pid. Callers that
        need the raw, possibly-negative value actually stored in the
        database -- e.g. reset_job_for_recovery's CAS, which must match
        what's in the column -- should read the row directly instead of
        going through this method.
        """
        pid = row.get('controller_pid')
        if pid is not None and pid < 0:
            # Between #7051 and #7847, the controller pid was negative to
            # indicate a controller process that can handle multiple jobs.
            pid = -pid
        return cls(pid=pid,
                   pid_started_at=row.get('controller_pid_started_at'),
                   server_id=row.get('controller_server_id'),
                   legacy_job_id=legacy_job_id)


class ControllerLivenessProvider(abc.ABC):
    """Determines whether a job's controller process is alive."""

    @abc.abstractmethod
    def check(self, owner: JobOwnerRecord) -> ControllerLiveness:
        """Return the liveness verdict for the given job owner."""
        raise NotImplementedError


class LocalPidLivenessProvider(ControllerLivenessProvider):
    """Default provider: checks a pid on the local machine only.

    Ignores ``owner.server_id`` -- correct only when every controller runs on
    the same machine as the caller, which is the default, single-server
    setup.
    """

    def check(self, owner: JobOwnerRecord) -> ControllerLiveness:
        if owner.pid is None:
            # No controller has ever been stamped for this job.
            return ControllerLiveness.DEAD
        record = state.ControllerPidRecord(pid=owner.pid,
                                           started_at=owner.pid_started_at)
        return local_pid_verdict(record, owner.legacy_job_id)


_provider: Optional[ControllerLivenessProvider] = None


def register(provider: ControllerLivenessProvider) -> None:
    """Install ``provider`` as the currently-active liveness provider.

    Last registration wins. Plugins override the default in ``install()``.
    """
    # pylint: disable=global-statement
    global _provider
    _provider = provider
    logger.debug('Registered ControllerLivenessProvider: %s',
                 type(provider).__name__)


def get_provider() -> ControllerLivenessProvider:
    """Return the registered provider, falling back to the default.

    If nothing has been registered, constructs and installs
    ``LocalPidLivenessProvider`` so there's always a usable provider
    regardless of import ordering.
    """
    # pylint: disable=global-statement
    global _provider
    if _provider is None:
        _provider = LocalPidLivenessProvider()
    return _provider


def check_job_owner(owner: JobOwnerRecord) -> ControllerLiveness:
    """Check whether ``owner``'s controller process is alive.

    Wraps the registered provider so that a provider bug or transient failure
    (e.g. an unreachable registry) degrades to UNKNOWN instead of raising
    into the caller.
    """
    try:
        return get_provider().check(owner)
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            'Controller liveness check raised an exception; '
            'treating as UNKNOWN.',
            exc_info=True)
        return ControllerLiveness.UNKNOWN


def local_pid_verdict(
        record: state.ControllerPidRecord,
        legacy_job_id: Optional[int] = None) -> ControllerLiveness:
    """Check if the controller process identified by ``record`` is alive.

    If legacy_job_id is provided, this will also match a legacy single-job
    controller process with that job id, based on the cmdline. This is how
    the old check worked before #7051.
    """
    try:
        process = psutil.Process(record.pid)

        if record.started_at is not None:
            if process.create_time() != record.started_at:
                logger.debug(f'Controller process {record.pid} has started '
                             f'at {record.started_at} but process has '
                             f'started at {process.create_time()}')
                return ControllerLiveness.DEAD
        else:
            # If we can't check the create_time try to check the cmdline
            # instead.
            cmd_str = ' '.join(process.cmdline())
            # pylint: disable=line-too-long
            # Pre-#7051 cmdline: /path/to/python -u -m sky.jobs.controller <dag.yaml_path> --job-id <job_id>
            # Post-#7051 cmdline: /path/to/python -u -msky.jobs.controller
            # pylint: enable=line-too-long
            if ('-m sky.jobs.controller' not in cmd_str and
                    '-msky.jobs.controller' not in cmd_str):
                logger.debug(f'Process {record.pid} is not a controller '
                             'process - missing "-m sky.jobs.controller" '
                             f'from cmdline: {cmd_str}')
                return ControllerLiveness.DEAD
            if (legacy_job_id is not None and '--job-id' in cmd_str and
                    f'--job-id {legacy_job_id}' not in cmd_str):
                logger.debug(f'Controller process {record.pid} has the '
                             f'wrong --job-id (expected {legacy_job_id}) '
                             f'in cmdline: {cmd_str}')
                return ControllerLiveness.DEAD

            # On linux, psutil.Process(pid) will return a valid process
            # object even if the pid is actually a thread ID within the
            # process. This hugely inflates the number of valid-looking pids,
            # increasing the chance that we will falsely believe a controller
            # is alive. The pid file should never contain thread IDs, just
            # process IDs. We can check this with psutil.pid_exists(pid),
            # which is false for TIDs. See pid_exists in psutil/_pslinux.py
            if not psutil.pid_exists(record.pid):
                logger.debug(f'Controller process {record.pid} is not a valid '
                             'process id.')
                return ControllerLiveness.DEAD

        if process.is_running():
            return ControllerLiveness.ALIVE
        return ControllerLiveness.DEAD

    except (psutil.NoSuchProcess, psutil.ZombieProcess) as e:
        # The process is definitely gone.
        logger.debug(f'Controller process {record.pid} is not running: {e}')
        return ControllerLiveness.DEAD
    except (psutil.AccessDenied, OSError) as e:
        # We couldn't answer the question either way (e.g. permissions, or a
        # transient OS-level failure). Unlike psutil.NoSuchProcess/
        # ZombieProcess, this does not tell us the process is gone.
        logger.debug(f'Could not determine liveness of controller process '
                     f'{record.pid}: {e}')
        return ControllerLiveness.UNKNOWN
    except Exception:  # pylint: disable=broad-except
        logger.warning(
            f'Unexpected error checking controller process {record.pid}',
            exc_info=True)
        return ControllerLiveness.UNKNOWN
