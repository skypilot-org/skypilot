"""Cancel signal files of managed jobs.

`sky jobs cancel` tells the controller process that runs a job to cancel it by
writing a file named after the job id into the signal directory
(``CONSOLIDATED_SIGNAL_PATH``). Every controller process polls that directory
and takes the signals of the jobs it runs. An empty file is a plain cancel;
see ``sky.jobs.utils.parse_job_cancel_file`` for the other contents.

When the API server has several replicas, the directory is on a volume they
share, which is usually a network filesystem: a call on it can take
seconds, and listing it costs more the more entries it has. So nothing here
may run on a controller's event loop, and no file may be left behind once its
signal was taken.
"""
import os
import time
from typing import List, Optional
import uuid

import filelock

from sky import sky_logging
from sky.jobs import constants as managed_job_constants
from sky.jobs import state as managed_job_state

logger = sky_logging.init_logger(__name__)

# Until this module existed, writers wrote the signal in place while holding
# a file lock named '<job id>.lock', and nobody removed the lock file.
_LEGACY_LOCK_SUFFIX = '.lock'
# A signal is written under this kind of name first and then renamed.
_TEMPORARY_SUFFIX = '.tmp'

# A file nobody will use again is removed once it is this old. Only its age
# is uncertain to this length: the clocks of this host and of a file server
# may differ.
_STALE_AFTER_SECONDS = 3600
_GC_INTERVAL_SECONDS = 3600


def _signal_path(job_id: int) -> str:
    return os.path.join(managed_job_constants.CONSOLIDATED_SIGNAL_PATH,
                        str(job_id))


def _remove_if_exists(path: str) -> bool:
    try:
        os.remove(path)
    except FileNotFoundError:
        return False
    return True


def write(job_id: int, content: str = '') -> None:
    """Writes the cancel signal of a job, replacing an earlier one.

    The signal appears under its name with its whole content at once, so a
    controller never reads a half-written signal and no lock is needed.

    Raises:
        OSError: the signal could not be written.
    """
    signal_path = _signal_path(job_id)
    # Neither all digits nor a legacy lock name, so no controller takes it
    # for a signal.
    temporary_path = f'{signal_path}.{uuid.uuid4().hex}{_TEMPORARY_SUFFIX}'
    try:
        with open(temporary_path, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(temporary_path, signal_path)
    except OSError:
        _remove_if_exists(temporary_path)
        raise


def exists(job_id: int) -> bool:
    """Whether a job has a cancel signal that was not taken yet."""
    return os.path.exists(_signal_path(job_id))


def _read_and_remove(signal_path: str) -> Optional[str]:
    try:
        with open(signal_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
    except FileNotFoundError:
        return None
    except Exception as e:  # pylint: disable=broad-except
        # An unreadable signal is still a request to cancel.
        logger.debug(f'Problem occurred when reading {signal_path}: {e}')
        content = ''
    _remove_if_exists(signal_path)
    return content


def take(job_id: int) -> Optional[str]:
    """Reads and removes the cancel signal of a job.

    Returns:
        The content of the signal ('' for a plain cancel), or None if the job
        has no signal.
    """
    signal_path = _signal_path(job_id)
    lock_path = signal_path + _LEGACY_LOCK_SUFFIX
    if not os.path.exists(lock_path):
        return _read_and_remove(signal_path)
    # A writer older than write() may be writing this signal in place right
    # now, during a rolling update of the API server. It holds this lock while
    # it does. Wait for it, so that the signal is read whole.
    # TODO(cooperc): Remove the lock handling once no supported API server
    # writes signals under a lock.
    with filelock.FileLock(lock_path):
        content = _read_and_remove(signal_path)
    _remove_if_exists(lock_path)
    return content


def remove(job_id: int) -> None:
    """Removes the cancel signal of a job without reading it."""
    signal_path = _signal_path(job_id)
    _remove_if_exists(signal_path)
    _remove_if_exists(signal_path + _LEGACY_LOCK_SUFFIX)


def _is_stale(path: str, now: float) -> bool:
    try:
        return os.stat(path).st_mtime < now - _STALE_AFTER_SECONDS
    except FileNotFoundError:
        return False


def remove_stale_files() -> int:
    """Removes the files in the signal directory that nobody will use again.

    These are:
      - Legacy lock files, which were never removed. A lock is only held
        while a signal is written or taken, so an old lock file without a
        signal is unused.
      - Temporary files of writers that died before renaming them.
      - Signals of jobs that are over. This happens when a job ends by itself
        just as it is cancelled: its controller process has let go of it and
        never looks at the signal.

    Files that are not named after a job id are left alone: other components
    keep their own files in this directory.

    Returns:
        The number of files removed.
    """
    directory = managed_job_constants.CONSOLIDATED_SIGNAL_PATH
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return 0
    now = time.time()
    signal_job_ids = {int(name) for name in names if name.isdigit()}
    removed = 0
    old_signal_job_ids: List[int] = []
    for name in names:
        path = os.path.join(directory, name)
        if name.isdigit():
            if _is_stale(path, now):
                old_signal_job_ids.append(int(name))
            continue
        if name.endswith(_LEGACY_LOCK_SUFFIX):
            job_id_part = name[:-len(_LEGACY_LOCK_SUFFIX)]
            # The lock file of a signal that is still there goes when the
            # signal is taken.
            unused = (job_id_part.isdigit() and
                      int(job_id_part) not in signal_job_ids)
        else:
            unused = (name.endswith(_TEMPORARY_SUFFIX) and
                      name.split('.', 1)[0].isdigit())
        if unused and _is_stale(path, now):
            removed += int(_remove_if_exists(path))
    if old_signal_job_ids:
        # Only asked for when there is an old signal, which is rare.
        unfinished_job_ids = set(
            managed_job_state.get_nonterminal_job_ids_by_name(None,
                                                              all_users=True))
        for job_id in old_signal_job_ids:
            # An old signal of an unfinished job is kept: the job may be
            # waiting for a controller process, which takes the signal when
            # it starts the job.
            if job_id not in unfinished_job_ids:
                removed += int(_remove_if_exists(_signal_path(job_id)))
                _remove_if_exists(_signal_path(job_id) + _LEGACY_LOCK_SUFFIX)
    return removed


def gc_stale_files() -> None:
    """Removes stale files from the signal directory, forever.

    Runs in the thread of the one controller process that was elected to
    garbage collect (see sky.jobs.log_gc).
    """
    while True:
        try:
            removed = remove_stale_files()
            if removed:
                logger.info(f'Removed {removed} stale files from the cancel '
                            'signal directory.')
        except Exception as e:  # pylint: disable=broad-except
            logger.error(f'Error removing stale cancel signal files: {e}',
                         exc_info=True)
        time.sleep(_GC_INTERVAL_SECONDS)
